import os
import unittest
from unittest.mock import patch

from eval.config import eval_k, pool_size


class EvalConfigTests(unittest.TestCase):
    def test_eval_k_defaults_to_six(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(eval_k(), 6)

    def test_eval_k_reads_environment_override(self):
        with patch.dict(os.environ, {"REMYMY_EVAL_K": "12"}):
            self.assertEqual(eval_k(), 12)

    def test_pool_size_defaults_to_five_hundred(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pool_size(), 500)

    def test_pool_size_reads_environment_override(self):
        with patch.dict(os.environ, {"REMYMY_POOL_SIZE": "80"}):
            self.assertEqual(pool_size(), 80)


if __name__ == "__main__":
    unittest.main()
