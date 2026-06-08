"""Show the mutualistic loop: the gate tightens as the expert adjudicates results.

Offline (no API needed). We stream expert verdicts into a FeedbackStore and watch
the calibrated threshold move as evidence accumulates.
"""
import os
import tempfile

from caliper import FeedbackStore, calibrate


def main():
    tmp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    tmp.close()
    store = FeedbackStore(tmp.name)

    # The expert reviews escalated results over time. High judge-trust cases tend to
    # be correct; low ones often wrong. Each verdict is one (trust, correct) pair.
    stream = [(0.9, True), (0.85, True), (0.4, False), (0.92, True), (0.3, False),
              (0.88, True), (0.7, True), (0.5, False), (0.95, True), (0.6, False)] * 8

    print(f"{'#fb':>4} | {'tau':>6} | {'feasible':>8} | {'err_bound':>9}")
    for i, (trust, correct) in enumerate(stream, 1):
        store.add(trust, correct)
        if i % 16 == 0 or i == len(stream):
            g = calibrate(store.samples(), alpha=0.10, delta=0.10)
            tau = f"{g.tau:.3f}" if g.feasible else "inf"
            print(f"{i:>4} | {tau:>6} | {str(g.feasible):>8} | {g.accepted_error_bound:>9.3f}")

    os.unlink(tmp.name)
    print("\nMore feedback -> more cases clear the bar at the same risk level, so the "
          "gate can safely accept (auto) at a lower trust, i.e. it escalates less.")


if __name__ == "__main__":
    main()
