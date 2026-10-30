"""SQLite persistence for reproducible, blind ranking evaluations."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from core.config import ranking_version

logger = logging.getLogger(__name__)


@contextmanager
def evaluation_db():
    path = Path(os.getenv("REMYMY_EVALUATION_DB", "target/evaluations.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS ranking_evaluations (
            id TEXT PRIMARY KEY, trace_id TEXT, snapshot TEXT NOT NULL,
            grades TEXT, metrics TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS jev_judgments (
            evaluation_id TEXT NOT NULL, recipe_id TEXT NOT NULL, judge_version TEXT NOT NULL,
            score REAL NOT NULL, probabilities TEXT NOT NULL, model TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (evaluation_id, recipe_id, judge_version)
        )""")
        db.commit()
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _recipe_id(item: dict) -> str:
    return str(item.get("recipe_id", item.get("id")))


def save_evaluation(trace_id, plan_id, request, pool, ranked):
    """Persist a full ranker pool and the selected ids, without recipe details."""
    from core.config import eval_enabled

    if not eval_enabled():
        return None

    ranked_rows = ranked.get("recommendations", []) if isinstance(ranked, dict) else ranked
    pool_by_id = {_recipe_id(item): item for item in pool}
    ranked_ids = [_recipe_id(item) for item in ranked_rows]
    if len(ranked_ids) != len(set(ranked_ids)) or len(pool_by_id) != len(pool):
        logger.warning("Skipping evaluation snapshot with duplicate recipe ids")
        return None

    request_data = dict(request)
    limit = request_data.get("limit", 6)
    candidates = []
    for item in ranked_rows:
        rid = _recipe_id(item)
        metadata = pool_by_id.get(rid, {})
        candidates.append({
            "recipe_id": rid,
            "title": item.get("title", metadata.get("title", "")),
            "score": item.get("score"),
            "content_score": item.get("content_score", metadata.get("content_score")),
            "popularity": item.get("popularity", metadata.get("popularity")),
            "source": item.get("source", metadata.get("source")),
        })

    evaluation_id = str(uuid4())
    snapshot = {
        "plan_id": str(plan_id),
        "request": request_data,
        "ranking_version": ranking_version(),
        "candidates": candidates,
        "selected_ids": ranked_ids[:limit],
    }
    with evaluation_db() as db:
        db.execute(
            "INSERT INTO ranking_evaluations (id, trace_id, snapshot) VALUES (?, ?, ?)",
            (evaluation_id, trace_id, json.dumps(snapshot, allow_nan=False)),
        )
    return evaluation_id
