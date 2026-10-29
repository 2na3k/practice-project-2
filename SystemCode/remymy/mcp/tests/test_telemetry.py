import unittest
from unittest.mock import Mock, patch

from eval.telemetry import push_scores


class TelemetryTests(unittest.TestCase):
    def test_pushes_metrics_with_source_and_skips_none(self):
        for source in ("jev-judge-pool", "jev-judge-output"):
            with self.subTest(source=source):
                client = Mock()
                with patch("eval.telemetry.get_client", return_value=client):
                    push_scores("trace-1", {"nDCG@6": 0.75, "Recall@6": None}, source, "judge scores")

                client.create_score.assert_called_once_with(
                    name="nDCG@6", value=0.75, trace_id="trace-1", data_type="NUMERIC",
                    comment="judge scores", metadata={"source": source},
                )

    def test_no_push_skips_langfuse_client(self):
        with patch("eval.telemetry.get_client") as get_client:
            push_scores("trace-1", {"nDCG@6": 0.75}, "jev-judge-pool", "judge scores", no_push=True)

        get_client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
