import json
import os
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from eval.evaluation import evaluation_db
from eval.review_api import create_review_app


class ReviewApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "eval.sqlite3")
        self.evaluation_id = "review-1"
        with patch.dict(os.environ, {"REMYMY_EVALUATION_DB": self.db_path, "REMYMY_EVALUATION_TOKEN": "secret"}):
            with evaluation_db() as db:
                snapshot = {"request": {"query": "rice"}, "candidates": [
                    {"recipe_id": str(i), "title": f"Meal {i}", "score": 0.9,
                     "content_score": 0.8, "source": "cold_start", "popularity": 4}
                    for i in range(5)
                ]}
                db.execute("INSERT INTO ranking_evaluations (id, trace_id, snapshot, grades) VALUES (?, ?, ?, ?)",
                           (self.evaluation_id, "private-trace", json.dumps(snapshot), '{"0": 3}'))

    def tearDown(self):
        self.temp.cleanup()

    def test_get_returns_shuffled_blind_candidates_and_post_saves_grades(self):
        with patch.dict(os.environ, {"REMYMY_EVALUATION_DB": self.db_path, "REMYMY_EVALUATION_TOKEN": "secret"}):
            client = TestClient(create_review_app())
            response = client.get(f"/eval/{self.evaluation_id}", headers={"Authorization": "Bearer secret"})
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(len(body["candidates"]), 5)
            for candidate in body["candidates"]:
                for field in ("score", "content_score", "popularity", "source", "rank", "trace_id", "grade"):
                    self.assertNotIn(field, candidate)
            self.assertEqual(body["grade_range"], [0, 3])
            posted = client.post(f"/eval/{self.evaluation_id}/grades",
                                 headers={"Authorization": "Bearer secret"}, json={"grades": {"0": 3}})
            self.assertEqual(posted.status_code, 200)

    def test_missing_and_wrong_tokens_are_rejected(self):
        client = TestClient(create_review_app())
        with patch.dict(os.environ, {"REMYMY_EVALUATION_DB": self.db_path}, clear=True):
            self.assertEqual(client.get(f"/eval/{self.evaluation_id}").status_code, 503)
        with patch.dict(os.environ, {"REMYMY_EVALUATION_DB": self.db_path, "REMYMY_EVALUATION_TOKEN": "secret"}):
            response = client.get(f"/eval/{self.evaluation_id}", headers={"Authorization": "Bearer wrong"})
            self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
