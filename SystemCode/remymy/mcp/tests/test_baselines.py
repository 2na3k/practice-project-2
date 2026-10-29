import unittest

from eval.baselines import content_order, popularity_order


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.pool = [
            {"recipe_id": "a", "content_score": 0.2, "popularity": 3},
            {"recipe_id": "b", "content_score": 0.9, "popularity": 1},
            {"recipe_id": "c", "content_score": 0.5, "popularity": 8},
        ]

    def test_orders_full_pool_by_content_and_popularity(self):
        self.assertEqual(content_order(self.pool), ["b", "c", "a"])
        self.assertEqual(popularity_order(self.pool), ["c", "a", "b"])


if __name__ == "__main__":
    unittest.main()
