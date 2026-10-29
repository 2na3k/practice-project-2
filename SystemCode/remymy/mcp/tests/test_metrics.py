import unittest

from eval.metrics import ranking_metrics


class RankingMetricsTests(unittest.TestCase):
    def test_computes_ranking_metrics_at_configured_k(self):
        metrics = ranking_metrics(["a", "b", "c"], {"a": 3, "b": 2, "c": 0}, k=2)

        self.assertAlmostEqual(metrics["nDCG@2"], 1.0)
        self.assertAlmostEqual(metrics["Precision@2"], 1.0)
        self.assertAlmostEqual(metrics["Recall@2"], 1.0)
        self.assertAlmostEqual(metrics["pairwise_accuracy"], 1.0)
        self.assertFalse(metrics["pool_lt_k"])

    def test_undefined_relevance_metrics_are_none_and_short_pool_flagged(self):
        metrics = ranking_metrics(["a", "b"], {"a": 0, "b": 1}, k=6)

        self.assertIsNone(metrics["nDCG@6"])
        self.assertIsNone(metrics["Recall@6"])
        self.assertTrue(metrics["pool_lt_k"])

    def test_pairwise_accuracy_is_none_when_no_pair_has_different_grades(self):
        metrics = ranking_metrics(["a", "b"], {"a": 2, "b": 2}, k=2)

        self.assertIsNone(metrics["pairwise_accuracy"])


if __name__ == "__main__":
    unittest.main()
