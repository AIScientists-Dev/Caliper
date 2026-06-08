"""Tests for the pilot calibration workflow (runs + verdicts -> gate)."""
import csv
import json
import os
import tempfile
import unittest

from caliper.trust.review import export_review_sheet, calibrate_from_runs


class TestReview(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.runs = os.path.join(self.dir, "runs")
        os.makedirs(self.runs)
        # 30 fake runs: high trust correct, low trust wrong.
        self.expected = {}
        for i in range(30):
            rid = f"run_{i:02d}"
            trust = 0.9 if i % 2 == 0 else 0.3
            correct = i % 2 == 0
            self.expected[rid] = correct
            json.dump({"run_id": rid, "trust": trust, "task": f"task {i}",
                       "answer": {"n_de": i}, "decision": "escalate"},
                      open(os.path.join(self.runs, f"{rid}.json"), "w"))

    def test_export_then_calibrate(self):
        sheet = os.path.join(self.dir, "review.csv")
        n = export_review_sheet(self.runs, sheet)
        self.assertEqual(n, 30)

        # Simulate the expert filling the `correct` column.
        rows = list(csv.DictReader(open(sheet)))
        self.assertEqual(len(rows), 30)
        with open(sheet, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            for r in rows:
                r["correct"] = "yes" if self.expected[r["run_id"]] else "no"
                w.writerow(r)

        # 15 clean cases can't certify alpha=0.10; use alpha=0.15/delta=0.2 (feasible at n=15).
        gate, stats = calibrate_from_runs(self.runs, sheet, alpha=0.15, delta=0.20)
        self.assertEqual(stats["matched"], 30)
        self.assertEqual(stats["skipped"], 0)
        self.assertTrue(gate.feasible)
        self.assertLessEqual(gate.accepted_error_bound, 0.15)
        # High-trust band is accepted, low-trust escalated.
        self.assertTrue(gate.decide(0.9).accept)
        self.assertFalse(gate.decide(0.3).accept)

    def test_unmatched_rows_skipped(self):
        sheet = os.path.join(self.dir, "v.csv")
        with open(sheet, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["run_id", "correct"])
            w.writerow(["run_00", "yes"])      # matches
            w.writerow(["ghost_99", "yes"])    # no such run -> skipped
            w.writerow(["run_01", ""])          # blank verdict -> skipped
        _, stats = calibrate_from_runs(self.runs, sheet)
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["skipped"], 2)


if __name__ == "__main__":
    unittest.main()
