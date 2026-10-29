import os
import tempfile
import unittest
from unittest.mock import patch

from eval.config import eval_enabled
from eval.evaluation import save_evaluation
from eval.judge_eval import main as judge_eval_main


class EvaluationSafetyTests(unittest.TestCase):
    def test_capture_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True), tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"REMYMY_EVALUATION_DB": os.path.join(directory, "eval.sqlite3")}):
                self.assertFalse(eval_enabled())
                self.assertIsNone(save_evaluation("trace", "plan", {}, [], []))
                self.assertFalse(os.path.exists(os.environ["REMYMY_EVALUATION_DB"]))

    def test_offline_script_fails_when_jev_key_is_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(judge_eval_main(["--no-push"]), 2)


if __name__ == "__main__":
    unittest.main()
