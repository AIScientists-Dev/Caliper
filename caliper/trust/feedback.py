"""Feedback store — the mutualistic loop.

Every result the expert adjudicates (was it actually correct?) is appended here,
together with the trust score the judge assigned. `recalibrate()` re-fits the gate
on the accumulated evidence, so the gate gets tighter as the expert teaches it.
"""
from __future__ import annotations

import json
import os
from typing import List

from .gate import calibrate, CalibratedGate, Sample


class FeedbackStore:
    def __init__(self, path: str = "feedback.jsonl"):
        self.path = path

    def add(self, trust: float, correct: bool, meta: dict | None = None) -> None:
        rec = {"trust": float(trust), "correct": bool(correct), "meta": meta or {}}
        with open(self.path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def samples(self) -> List[Sample]:
        if not os.path.exists(self.path):
            return []
        out: List[Sample] = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                out.append((float(r["trust"]), bool(r["correct"])))
        return out

    def recalibrate(self, alpha: float = 0.10, delta: float = 0.05) -> CalibratedGate:
        return calibrate(self.samples(), alpha=alpha, delta=delta)

    def __len__(self) -> int:
        return len(self.samples())
