import csv
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from eval.judge_eval import judge_snapshot


class JudgeEvalTests(unittest.TestCase):
    def test_hydrates_recipe_from_catalog_and_caches_judgment(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = os.path.join(directory, "recipes.csv")
            with open(catalog_path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["id", "name", "ingredients", "steps", "nutrition"])
                writer.writeheader()
                writer.writerow({"id": 10, "name": "Rice bowl", "ingredients": "['rice', 'beans']",
                                 "steps": "['Cook rice']", "nutrition": "[300, 10, 4, 5, 20]"})
            db = sqlite3.connect(os.path.join(directory, "eval.sqlite3"))
            db.execute("CREATE TABLE jev_judgments (evaluation_id TEXT, recipe_id TEXT, judge_version TEXT, score REAL, probabilities TEXT, model TEXT, PRIMARY KEY (evaluation_id, recipe_id, judge_version))")
            client = Mock(model="jev-test")
            snapshot = {"request": {"query": "rice dinner"}, "candidates": [{"recipe_id": "10", "title": "Rice bowl"}]}
            judge = Mock(return_value={"score": 2.5, "probabilities": {"2": 0.5}, "model": "jev-test"})

            with patch("eval.judge_eval.judge_one", judge):
                first = judge_snapshot(db, client, "evaluation-1", snapshot, catalog_path=catalog_path)
                second = judge_snapshot(db, client, "evaluation-1", snapshot, catalog_path=catalog_path)

            self.assertEqual(first, second)
            self.assertEqual(judge.call_count, 1)
            recipe = judge.call_args.args[2]
            self.assertEqual(recipe["ingredients"], ["rice", "beans"])
            self.assertEqual(recipe["steps"], ["Cook rice"])
            self.assertNotIn("ingredients", snapshot["candidates"][0])
            db.close()


if __name__ == "__main__":
    unittest.main()
