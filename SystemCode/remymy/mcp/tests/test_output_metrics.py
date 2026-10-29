import unittest

from eval.output_metrics import output_metrics


class OutputMetricTests(unittest.TestCase):
    def test_computes_plan_grade_repeat_diversity_and_coverage(self):
        metrics = output_metrics(
            ["a", "b", "b"], {"a": 3, "b": 2},
            {"a": ["rice", "beans"], "b": ["rice", "corn"]}, ["a", "b", "c", "d"],
        )

        self.assertAlmostEqual(metrics["plan_mean_grade"], 7 / 3)
        self.assertAlmostEqual(metrics["plan_share_good"], 1.0)
        self.assertAlmostEqual(metrics["plan_repeat_rate"], 1 / 3)
        self.assertAlmostEqual(metrics["diversity"], 2 / 3)
        self.assertAlmostEqual(metrics["coverage"], 0.5)

    def test_empty_plan_repeat_rate_is_none(self):
        metrics = output_metrics([], {}, {}, [])

        self.assertIsNone(metrics["plan_repeat_rate"])


if __name__ == "__main__":
    unittest.main()
