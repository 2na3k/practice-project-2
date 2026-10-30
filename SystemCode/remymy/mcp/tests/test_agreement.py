import unittest

from eval.agreement import quadratic_weighted_kappa


class AgreementTests(unittest.TestCase):
    def test_perfect_agreement_is_one(self):
        self.assertEqual(quadratic_weighted_kappa([0, 1, 2, 3], [0, 1, 2, 3]), 1.0)

    def test_large_ordinal_mistakes_are_penalized_more(self):
        human = [0, 1, 2, 3]
        close = quadratic_weighted_kappa(human, [1, 1, 2, 2])
        distant = quadratic_weighted_kappa(human, [3, 2, 1, 0])

        self.assertGreater(close, distant)


if __name__ == "__main__":
    unittest.main()
