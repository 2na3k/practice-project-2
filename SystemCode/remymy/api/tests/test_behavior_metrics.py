from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from eval import behavior_metrics
from eval.behavior_metrics import aggregate_event_metrics, normalize_recipe_id, score_event


def timestamp(minutes: int = 0) -> datetime:
    return datetime(2026, 10, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def test_foodcom_impressions_normalize_to_saved_recipe_namespace():
    assert normalize_recipe_id("foodcom:42") == "openrecipes:42"
    assert normalize_recipe_id("openrecipes:42") == "openrecipes:42"
    assert normalize_recipe_id("170138") == "170138"

    hr, mrr = score_event(
        ["foodcom:42", "foodcom:10"],
        {"openrecipes:42": timestamp(1)},
        shown_at=timestamp(),
        k=6,
    )

    assert hr == 1.0
    assert mrr == 1.0


def test_hr_hit_and_miss_respect_the_top_k_and_save_time():
    hit = score_event(
        [f"foodcom:{recipe_id}" for recipe_id in range(1, 8)],
        {"openrecipes:6": timestamp(1)},
        shown_at=timestamp(),
        k=6,
    )
    late_save = score_event(
        ["foodcom:1"], {"openrecipes:1": timestamp(-1)}, shown_at=timestamp(), k=6,
    )
    namespace_miss = score_event(
        ["foodcom:1"], {"user-recipe:1": timestamp(1)}, shown_at=timestamp(), k=6,
    )

    assert hit == (1.0, 1.0 / 6)
    assert late_save == (0.0, 0.0)
    assert namespace_miss == (0.0, 0.0)


def test_mrr_uses_the_first_saved_rank_and_zero_for_no_hit():
    rank_one = score_event(["foodcom:1", "foodcom:2"], {"openrecipes:1": timestamp()}, timestamp(), k=6)
    rank_three = score_event(
        ["foodcom:1", "foodcom:2", "foodcom:3"], {"openrecipes:3": timestamp()}, timestamp(), k=6,
    )
    miss = score_event(["foodcom:1"], {}, timestamp(), k=6)

    assert rank_one == (1.0, 1.0)
    assert rank_three[0] == 1.0
    assert rank_three[1] == pytest.approx(1 / 3)
    assert miss == (0.0, 0.0)


def test_aggregate_metrics_are_means_over_all_impression_events():
    assert aggregate_event_metrics([(1.0, 1.0), (0.0, 0.0), (1.0, 1 / 3)]) == {
        "HR@6": pytest.approx(2 / 3),
        "MRR@6": pytest.approx(4 / 9),
    }


class FakeLangfuse:
    def __init__(self):
        self.scores = []
        self.flush_calls = 0

    def create_score(self, **score):
        self.scores.append(score)

    def flush(self):
        self.flush_calls += 1


def fake_langfuse(monkeypatch):
    client = FakeLangfuse()
    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(get_client=lambda: client))
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse.test")
    return client


def behavior_db(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE recipe_impressions (
            event_id TEXT, user_id INTEGER, trace_id TEXT, recipe_id TEXT, rank INTEGER, shown_at TEXT
        );
        CREATE TABLE saved_recipe (user_id INTEGER, recipe_id TEXT, saved_at TEXT);
    """)
    connection.executemany(
        "INSERT INTO recipe_impressions VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("event-1", 1, "trace-1", "foodcom:1", 1, "2026-10-01T12:00:00.000Z"),
            ("event-2", 1, None, "foodcom:2", 1, "2026-10-01T12:00:00.000Z"),
        ],
    )
    connection.execute(
        "INSERT INTO saved_recipe VALUES (?, ?, ?)",
        (1, "openrecipes:1", "2026-10-01T12:01:00.000Z"),
    )
    connection.commit()
    connection.close()


def test_push_scores_uses_trace_id_and_behavior_source(monkeypatch):
    client = fake_langfuse(monkeypatch)

    pushed = behavior_metrics.push_scores(
        "trace-1", {"HR@6": 1.0, "MRR@6": 1 / 3}, "behavior", "save behavior",
    )

    assert pushed == 2
    assert [(score["name"], score["value"], score["trace_id"]) for score in client.scores] == [
        ("HR@6", 1.0, "trace-1"), ("MRR@6", pytest.approx(1 / 3), "trace-1"),
    ]
    assert all(score["data_type"] == "NUMERIC" and score["metadata"]["source"] == "behavior"
               for score in client.scores)


def test_main_pushes_only_traceable_events_and_no_push_skips_all(tmp_path, monkeypatch):
    client = fake_langfuse(monkeypatch)
    database = tmp_path / "behavior.db"
    behavior_db(database)
    report = tmp_path / "report.json"

    assert behavior_metrics.main(["--db", str(database), "--out", str(report)]) == 0
    assert [(score["name"], score["trace_id"]) for score in client.scores] == [
        ("HR@6", "trace-1"), ("MRR@6", "trace-1"),
    ]
    assert json.loads(report.read_text(encoding="utf-8"))["skipped_trace_id_count"] == 1

    client.scores.clear()
    assert behavior_metrics.main(["--db", str(database), "--out", str(report), "--no-push"]) == 0
    assert client.scores == []
