import unittest

from eval.constraint_metrics import constraint_metrics


class ConstraintMetricTests(unittest.TestCase):
    def test_independent_screen_catches_vegan_violation(self):
        metrics = constraint_metrics(
            {"preferences": {"diet": "vegan"}, "limit": 2},
            [{"recipe_id": "a", "ingredients": ["lentils"]},
             {"recipe_id": "b", "ingredients": ["lentils", "chicken broth"]}],
            selected_ids=["a", "b"],
        )

        self.assertEqual(metrics["violation_rate"], 0.5)
        self.assertEqual(metrics["over_filtering_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
