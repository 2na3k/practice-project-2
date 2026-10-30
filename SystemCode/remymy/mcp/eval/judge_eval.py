"""Offline Jev grading for stored recommendation snapshots."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

from langfuse import get_client

from core.config import eval_k
from core.jev import JevClient, JevConfigError, JevError, require_jev_key
from .baselines import content_order, popularity_order
from .constraint_metrics import constraint_metrics
from .evaluation import evaluation_db
from .judge import JUDGE_VERSION, judge_grades, judge_one
from .metrics import ranking_metrics
from .output_metrics import output_metrics
from .telemetry import push_scores


def hydrate_catalog(recipe_ids: set[str], catalog_path: str | Path | None = None) -> dict[str, dict]:
    path = Path(catalog_path or os.getenv("RECIPE_CATALOG_PATH", "data/raw/food-com/RAW_recipes.csv"))
    hydrated = {}
    if not path.is_file():
        raise FileNotFoundError(f"Recipe catalog is missing: {path}")
    with path.open(newline="", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            recipe_id = str(row.get("id", ""))
            if recipe_id not in recipe_ids:
                continue
            try:
                hydrated[recipe_id] = {
                    "recipe_id": recipe_id,
                    "title": row.get("name", "").strip(),
                    "ingredients": [str(value) for value in ast.literal_eval(row.get("ingredients", "[]"))],
                    "steps": [str(value) for value in ast.literal_eval(row.get("steps", "[]"))],
                    "nutrition": ast.literal_eval(row.get("nutrition", "[]")),
                }
            except (ValueError, SyntaxError, TypeError):
                continue
    missing = recipe_ids - hydrated.keys()
    if missing:
        raise ValueError(f"recipes missing from catalog: {', '.join(sorted(missing))}")
    return hydrated


def ensure_judge_table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS jev_judgments (
        evaluation_id TEXT NOT NULL, recipe_id TEXT NOT NULL, judge_version TEXT NOT NULL,
        score REAL NOT NULL, probabilities TEXT NOT NULL, model TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (evaluation_id, recipe_id, judge_version)
    )""")


def judge_snapshot(db, client: JevClient, evaluation_id: str, snapshot: dict,
                   catalog_path: str | Path | None = None) -> dict[str, dict]:
    candidates = snapshot["candidates"]
    ids = [str(candidate["recipe_id"]) for candidate in candidates]
    duplicate_ids = [recipe_id for recipe_id, count in Counter(ids).items() if count > 1]
    if duplicate_ids:
        raise ValueError(f"duplicate recipe ids: {', '.join(duplicate_ids)}")

    results = {}
    uncached = []
    for recipe_id in ids:
        row = db.execute(
            "SELECT score, probabilities, model FROM jev_judgments "
            "WHERE evaluation_id = ? AND recipe_id = ? AND judge_version = ?",
            (evaluation_id, recipe_id, JUDGE_VERSION),
        ).fetchone()
        if row is None:
            uncached.append(recipe_id)
        else:
            results[recipe_id] = {"score": row[0], "probabilities": json.loads(row[1]), "model": row[2]}

    if uncached:
        hydrated = hydrate_catalog(set(uncached), catalog_path)
        metadata = {str(candidate["recipe_id"]): candidate for candidate in candidates}
        for recipe_id in uncached:
            recipe = {**metadata[recipe_id], **hydrated[recipe_id]}
            judged = judge_one(client, snapshot["request"], recipe)
            db.execute(
                "INSERT INTO jev_judgments (evaluation_id, recipe_id, judge_version, score, probabilities, model) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (evaluation_id, recipe_id, JUDGE_VERSION, judged["score"],
                 json.dumps(judged["probabilities"]), judged["model"]),
            )
            results[recipe_id] = judged
    return results


def mean_of(rows: list[dict], key: str):
    values = [row[key] for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("target/eval/jev_report.json"))
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args(argv)
    try:
        client = JevClient(require_jev_key())
    except JevConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    plans, aggregate_rows, scores_pushed = [], [], 0
    try:
        with evaluation_db() as db:
            ensure_judge_table(db)
            rows = db.execute(
                "SELECT id, trace_id, snapshot FROM ranking_evaluations ORDER BY created_at DESC LIMIT ?",
                (args.limit,),
            ).fetchall()
            for evaluation_id, trace_id, raw in rows:
                snapshot = json.loads(raw)
                ids = [str(candidate["recipe_id"]) for candidate in snapshot.get("candidates", [])]
                if len(ids) != len(set(ids)):
                    print(f"skip {evaluation_id}: duplicate recipe ids", file=sys.stderr)
                    continue
                if not ids:
                    continue
                judged = judge_snapshot(db, client, evaluation_id, snapshot)
                grades = judge_grades(judged)
                metrics = ranking_metrics(ids, grades, k=eval_k())
                pool = hydrate_catalog(set(ids))
                output = output_metrics(
                    snapshot.get("selected_ids", []), grades,
                    {recipe_id: pool[recipe_id]["ingredients"] for recipe_id in ids}, ids,
                )
                constraints = constraint_metrics(snapshot["request"], list(pool.values()),
                                                 snapshot.get("selected_ids", []))
                baselines = {
                    "content": ranking_metrics(content_order(snapshot["candidates"]), grades, k=eval_k()),
                    "popularity": ranking_metrics(popularity_order(snapshot["candidates"]), grades, k=eval_k()),
                }
                row = {
                    "evaluation_id": evaluation_id, "trace_id": trace_id,
                    "candidates": len(ids), **metrics,
                    "output_metrics": output, "constraint_metrics": constraints,
                    "baseline_metrics": baselines,
                }
                plans.append(row)
                if not metrics["pool_lt_k"]:
                    aggregate_rows.append(row)
                if not args.no_push and trace_id:
                    scores_pushed += push_scores(
                        trace_id, metrics, "jev-judge-pool", "Jev grades for the full candidate pool"
                    )
                    scores_pushed += push_scores(
                        trace_id, {**output, **constraints}, "jev-judge-output",
                        "Jev grades and constraint checks for selected recommendations",
                    )
    except (JevError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()

    k = eval_k()
    metric_names = (f"nDCG@{k}", f"Precision@{k}", f"Recall@{k}", "pairwise_accuracy")
    report = {
        "judge_version": JUDGE_VERSION,
        "plans": len(plans),
        "averaged_plans": len(aggregate_rows),
        "averages": {name: mean_of(aggregate_rows, name) for name in metric_names},
        "pool_lt_k": [row["evaluation_id"] for row in plans if row["pool_lt_k"]],
        "scores_pushed_to_langfuse": scores_pushed,
        "per_plan": plans,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("plans", "averaged_plans", "averages")}, indent=2))
    print(f"report written to {args.out}")
    if not args.no_push:
        get_client().flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
