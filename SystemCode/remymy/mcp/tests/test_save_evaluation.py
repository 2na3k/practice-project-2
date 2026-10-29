import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from eval.evaluation import save_evaluation
import main


class SaveEvaluationTests(unittest.TestCase):
    def test_persists_full_pool_and_selected_ids_without_hydrated_details(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "REMYMY_EVAL_ENABLED": "true",
            "REMYMY_EVALUATION_DB": os.path.join(directory, "eval.sqlite3"),
        }, clear=False):
            pool = [
                {"recipe_id": str(i), "title": f"Meal {i}", "content_score": 0.5,
                 "popularity": 0.2, "source": "cold_start"}
                for i in range(4)
            ]
            ranked = [{"recipe_id": str(i), "score": 0.9 - i / 10} for i in range(4)]

            evaluation_id = save_evaluation("trace-1", "plan-1", {"limit": 2}, pool, ranked)

            self.assertIsNotNone(evaluation_id)
            with sqlite3.connect(os.environ["REMYMY_EVALUATION_DB"]) as db:
                raw = db.execute("SELECT snapshot FROM ranking_evaluations WHERE id = ?", (evaluation_id,)).fetchone()[0]
            snapshot = json.loads(raw)
            self.assertEqual(len(snapshot["candidates"]), 4)
            self.assertEqual(snapshot["selected_ids"], ["0", "1"])
            self.assertNotIn("ingredients", snapshot["candidates"][0])
            self.assertNotIn("steps", snapshot["candidates"][0])
            self.assertEqual(snapshot["candidates"][0]["content_score"], 0.5)

    def test_skips_duplicate_recipe_ids(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "REMYMY_EVAL_ENABLED": "true",
            "REMYMY_EVALUATION_DB": os.path.join(directory, "eval.sqlite3"),
        }, clear=False):
            pool = [{"recipe_id": "1", "title": "A"}, {"recipe_id": "1", "title": "A again"}]

            self.assertIsNone(save_evaluation("trace-1", "plan-1", {}, pool, pool))
            self.assertFalse(os.path.exists(os.environ["REMYMY_EVALUATION_DB"]))

    def test_recommend_meals_ranks_and_saves_pool_but_returns_only_limit(self):
        rows = [main.Recipe(i, 10, ("main dish",), ("rice",), f"Meal {i}", "description", 400, 20, i)
                for i in range(5)]
        rank_rows = [{"recipe_id": i, "score": 0.9 - i / 10, "content_score": 0.5,
                      "source": "cold_start", "popularity": i / 10} for i in range(5)]
        hydrated = {i: ([], ["rice"], ["Cook rice"]) for i in range(5)}
        client = Mock()
        client.get_current_trace_id.return_value = "trace-id"

        with patch.object(main._catalog_ready, "wait", return_value=True), \
             patch.object(main, "_load_catalog", return_value=rows), \
             patch.object(main, "_hydrate", return_value=hydrated), \
             patch.object(main, "POOL_SIZE", 5), \
             patch.object(main, "rank_foodcom_meals", return_value={"recommendations": rank_rows}) as rank, \
             patch.object(main, "save_evaluation") as save, \
             patch.object(main, "get_client", return_value=client):
            meals = json.loads(main.recommend_meals({}, "rice", limit=2))

        self.assertEqual(len(meals), 2)
        self.assertEqual(rank.call_args.kwargs["limit"], 5)
        save.assert_called_once()
        self.assertEqual(len(save.call_args.args[3]), 5)
        self.assertEqual(len(save.call_args.args[4]), 5)
        self.assertNotIn("content_score", meals[0]["meal"])
        self.assertNotIn("popularity", meals[0]["meal"])
        self.assertNotIn("source", meals[0]["meal"])


if __name__ == "__main__":
    unittest.main()
