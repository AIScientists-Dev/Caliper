"""Provenance log — a reproducible record of every run.

Reproducibility/auditability is a thing the existing science agents do not
guarantee, and exactly what a scientist needs to trust (and re-run) an analysis.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid import cycle
    from .agent import CaliperResult


class ProvenanceLog:
    def __init__(self, root: str = "runs"):
        self.root = root

    def record(self, result: "CaliperResult", pack_name: str) -> str:
        os.makedirs(self.root, exist_ok=True)
        run_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        path = os.path.join(self.root, f"{run_id}.json")
        payload = {
            "run_id": run_id,
            "pack": pack_name,
            "task": result.task,
            "steps": [asdict(s) for s in result.steps],
            "answer": result.answer,
            "trust": result.trust,
            "decision": result.decision,
            "accepted": result.accepted,
            "stdout_tail": result.raw_stdout[-2000:],
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
        return path
