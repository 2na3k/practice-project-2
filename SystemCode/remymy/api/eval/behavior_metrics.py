"""Compute offline HR@K and MRR@K from impressions and later saves."""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from numbers import Real
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

logger = logging.getLogger(__name__)


def normalize_recipe_id(recipe_id: str) -> str:
    """Map a displayed Food.com id to the canonical id used by saved_recipe."""
    if recipe_id.startswith("foodcom:"):
        return f"openrecipes:{recipe_id.removeprefix('foodcom:')}"
    return recipe_id


def _as_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def score_event(
    recipe_ids: Sequence[str],
    saved_at_by_recipe: Mapping[str, datetime | str],
    shown_at: datetime | str,
    k: int = 6,
) -> tuple[float, float]:
    """Return (HR@K, MRR@K) for one ranked impression event."""
    if k < 1:
        raise ValueError("k must be at least 1")
    shown_time = _as_datetime(shown_at)
    for rank, recipe_id in enumerate(recipe_ids[:k], start=1):
        saved_at = saved_at_by_recipe.get(normalize_recipe_id(recipe_id))
        if saved_at is not None and _as_datetime(saved_at) >= shown_time:
            return 1.0, 1.0 / rank
    return 0.0, 0.0


def aggregate_event_metrics(
    event_scores: Iterable[tuple[float, float]], k: int = 6,
) -> dict[str, float | None]:
    """Average per-event hit and reciprocal-rank scores, including misses."""
    scores = list(event_scores)
    if not scores:
        return {f"HR@{k}": None, f"MRR@{k}": None}
    return {
        f"HR@{k}": sum(hit for hit, _ in scores) / len(scores),
        f"MRR@{k}": sum(reciprocal_rank for _, reciprocal_rank in scores) / len(scores),
    }


def read_events(connection: sqlite3.Connection, limit: int | None = None) -> list[dict[str, Any]]:
    """Read ordered impression rows, grouping each event and using its first shown_at."""
    rows = connection.execute(
        "SELECT event_id, user_id, trace_id, recipe_id, rank, shown_at "
        "FROM recipe_impressions ORDER BY shown_at, event_id, rank"
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row | tuple[Any, ...]]] = defaultdict(list)
    for row in rows:
        grouped[row[0]].append(row)
    events = []
    for event_id, event_rows in grouped.items():
        ordered = sorted(event_rows, key=lambda row: row[4])
        events.append({
            "event_id": event_id,
            "user_id": ordered[0][1],
            "trace_id": ordered[0][2],
            "recipe_ids": [row[3] for row in ordered],
            "shown_at": min((_as_datetime(row[5]) for row in ordered)),
        })
    events.sort(key=lambda event: (event["shown_at"], event["event_id"]))
    return events if limit is None else events[:limit]


def read_saved_recipes(connection: sqlite3.Connection) -> dict[int, dict[str, datetime]]:
    saved: dict[int, dict[str, datetime]] = defaultdict(dict)
    for user_id, recipe_id, saved_at in connection.execute(
        "SELECT user_id, recipe_id, saved_at FROM saved_recipe"
    ):
        saved[user_id][recipe_id] = _as_datetime(saved_at)
    return saved


def compute_report(connection: sqlite3.Connection, k: int = 6, limit: int | None = None) -> dict[str, Any]:
    events = read_events(connection, limit)
    saved_by_user = read_saved_recipes(connection)
    scored_events = []
    all_scores = []
    skipped_trace_id_count = 0
    for event in events:
        score = score_event(
            event["recipe_ids"], saved_by_user.get(event["user_id"], {}), event["shown_at"], k,
        )
        all_scores.append(score)
        if not event["trace_id"] or not event["trace_id"].strip():
            skipped_trace_id_count += 1
        scored_events.append({
            "event_id": event["event_id"],
            "trace_id": event["trace_id"],
            f"HR@{k}": score[0],
            f"MRR@{k}": score[1],
        })
    return {
        "k": k,
        "event_count": len(events),
        "skipped_trace_id_count": skipped_trace_id_count,
        **aggregate_event_metrics(all_scores, k),
        "events": scored_events,
    }


def _langfuse_configured() -> bool:
    return all(os.getenv(key, "").strip() for key in (
        "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL",
    ))


def push_scores(
    trace_id: str | None,
    metrics: Mapping[str, float | None],
    source: str,
    comment: str,
    *,
    no_push: bool = False,
) -> int:
    """Push finite numeric scores to Langfuse without flushing the shared client."""
    if no_push or not trace_id or not trace_id.strip() or not _langfuse_configured():
        return 0
    from langfuse import get_client

    client = get_client()
    pushed = 0
    for name, value in metrics.items():
        if value is None or isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
            continue
        try:
            client.create_score(
                trace_id=trace_id,
                name=name,
                value=float(value),
                data_type="NUMERIC",
                score_id=str(uuid5(NAMESPACE_URL, f"remymy/{trace_id}/{source}/{name}")),
                comment=comment,
                metadata={"source": source},
            )
            pushed += 1
        except Exception:
            logger.exception("Pushing Langfuse score %r failed.", name)
    return pushed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.getenv("DATABASE_PATH", "/state/remymy.db"))
    parser.add_argument("--k", type=int, default=int(os.getenv("REMYMY_BEHAVIOR_K", "6")))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--limit", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.k < 1 or (args.limit is not None and args.limit < 1):
        logger.error("--k and --limit must be positive integers.")
        return 2
    try:
        with sqlite3.connect(args.db) as connection:
            report = compute_report(connection, k=args.k, limit=args.limit)
    except sqlite3.Error:
        logger.exception("Reading impression and save data failed.")
        return 1
    scores_pushed = 0
    if not args.no_push and _langfuse_configured():
        for event in report["events"]:
            metrics = {key: event[key] for key in (f"HR@{args.k}", f"MRR@{args.k}")}
            scores_pushed += push_scores(
                event["trace_id"], metrics, "behavior",
                f"Saved recipe behavior for impression {event['event_id']}",
            )
        try:
            from langfuse import get_client

            get_client().flush()
        except Exception:
            logger.exception("Flushing Langfuse scores failed.")
    report["scores_pushed_to_langfuse"] = scores_pushed
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(encoded + "\n", encoding="utf-8")
    else:
        print(encoded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
