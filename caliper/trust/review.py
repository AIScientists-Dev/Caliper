"""The pilot calibration workflow: turn Caliper runs + expert verdicts into a gate.

Steps:
  1. Run Caliper on ~100-150 cases (each writes a provenance JSON into runs/).
  2. export_review_sheet("runs", "review.csv") -> a spreadsheet the expert fills:
     one row per run with task, answer, trust, and a blank `correct` column.
  3. The expert marks each row correct/wrong (1/0, yes/no, correct/wrong).
  4. calibrate_from_runs("runs", "review.csv") -> a CalibratedGate backed by REAL data.
"""
from __future__ import annotations

import csv
import glob
import json
import os
from typing import List, Tuple

from .gate import calibrate, CalibratedGate, Sample

_TRUE = {"1", "true", "t", "yes", "y", "correct", "right", "pass", "ok"}
_FALSE = {"0", "false", "f", "no", "n", "wrong", "incorrect", "fail", "bad"}


def _load_runs(runs_dir: str) -> dict:
    runs = {}
    for p in glob.glob(os.path.join(runs_dir, "*.json")):
        try:
            d = json.load(open(p))
        except (OSError, json.JSONDecodeError):
            continue
        rid = d.get("run_id") or os.path.splitext(os.path.basename(p))[0]
        runs[rid] = d
    return runs


def export_review_sheet(runs_dir: str, out_csv: str) -> int:
    """Write one row per run for the expert to adjudicate. Returns row count."""
    runs = _load_runs(runs_dir)
    fields = ["run_id", "trust", "decision", "task", "answer", "correct"]
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for rid, d in sorted(runs.items()):
            w.writerow({
                "run_id": rid,
                "trust": round(float(d.get("trust", 0.0)), 4),
                "decision": d.get("decision", ""),
                "task": (d.get("task", "") or "")[:200],
                "answer": json.dumps(d.get("answer", {}))[:300],
                "correct": "",  # expert fills: 1/0, yes/no, correct/wrong
            })
    return len(runs)


def _parse_correct(v) -> "bool | None":
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def calibrate_from_runs(runs_dir: str, verdicts_csv: str,
                        alpha: float = 0.10, delta: float = 0.05
                        ) -> Tuple[CalibratedGate, dict]:
    """Join run trust scores with expert verdicts and fit the gate.

    Returns (gate, stats) where stats reports how many rows matched/were skipped.
    """
    runs = _load_runs(runs_dir)
    samples: List[Sample] = []
    matched = skipped = 0
    with open(verdicts_csv, newline="") as f:
        for row in csv.DictReader(f):
            rid = (row.get("run_id") or "").strip()
            correct = _parse_correct(row.get("correct", ""))
            if rid in runs and correct is not None:
                samples.append((float(runs[rid].get("trust", 0.0)), correct))
                matched += 1
            else:
                skipped += 1
    gate = calibrate(samples, alpha=alpha, delta=delta)
    return gate, {"matched": matched, "skipped": skipped, "n_runs": len(runs)}
