"""Tests for the calibrated escalation gate — the trust moat. Stdlib only."""
import math
import random
import unittest

from caliper.trust.gate import calibrate, _error_upper_bound


class TestGate(unittest.TestCase):
    def test_monotone_separable(self):
        # High scores correct, low scores wrong, with enough samples to certify.
        samples = [(i / 200.0, i >= 140) for i in range(201)]  # correct iff score >= 0.70
        g = calibrate(samples, alpha=0.10, delta=0.10)
        self.assertTrue(g.feasible)
        self.assertLessEqual(g.accepted_error_bound, 0.10)
        # Gate maximises coverage within the risk budget, so it accepts down to the
        # boundary (slightly below 0.70) while keeping the certified error <= alpha.
        self.assertGreaterEqual(g.tau, 0.65)

    def test_cp_tighter_than_hoeffding_small_n(self):
        # 0 errors in 30 samples: CP should certify alpha=0.10 at delta=0.10
        # where Hoeffding (~0.28) could not. This is the small-n win.
        self.assertLess(_error_upper_bound(0, 30, 0.10), 0.10)
        self.assertAlmostEqual(_error_upper_bound(0, 30, 0.10),
                               1 - 0.10 ** (1 / 30), places=4)  # exact CP for k=0

    def test_all_wrong_is_infeasible(self):
        samples = [(i / 100.0, False) for i in range(101)]
        g = calibrate(samples, alpha=0.10, delta=0.05)
        self.assertFalse(g.feasible)
        self.assertEqual(g.tau, math.inf)
        self.assertFalse(g.decide(0.99).accept)  # escalate everything

    def test_empty(self):
        g = calibrate([], alpha=0.1, delta=0.05)
        self.assertFalse(g.feasible)

    def test_decision_threshold(self):
        samples = [(i / 200.0, i >= 120) for i in range(201)]  # correct iff >= 0.60
        g = calibrate(samples, alpha=0.10, delta=0.10)
        self.assertTrue(g.feasible)
        self.assertTrue(g.decide(g.tau).accept)          # at threshold -> accept
        self.assertFalse(g.decide(g.tau - 0.01).accept)  # below -> escalate

    def test_empirical_risk_held_out(self):
        # Synthetic generator: P(correct) rises with score. Calibrate on one draw,
        # check accepted-set error on a fresh draw stays under alpha (CP is exact).
        rng = random.Random(0)

        def draw(n):
            out = []
            for _ in range(n):
                s = rng.random()
                correct = rng.random() < (0.2 + 0.75 * s)
                out.append((s, correct))
            return out

        alpha = 0.15
        g = calibrate(draw(2000), alpha=alpha, delta=0.05)
        self.assertTrue(g.feasible)
        test = draw(2000)
        accepted = [c for s, c in test if s >= g.tau]
        err = 1 - (sum(accepted) / len(accepted))
        self.assertLess(err, alpha + 0.03)


if __name__ == "__main__":
    unittest.main()
