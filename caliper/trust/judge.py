"""Trust score — an LLM judge rates how reliable a completed analysis is.

The raw score need not be a calibrated probability; the gate (gate.py) only needs
it to *rank* cases. Richer scores (judge ensembles, self-consistency, internal
signals) reduce the escalation rate at fixed risk but do not affect validity.
"""
from __future__ import annotations

from typing import List

from ..util import extract_json

_JUDGE_SYSTEM = (
    "You are Caliper's reliability judge. You assess whether a completed scientific "
    "analysis can be trusted without expert review. Be skeptical: reward correctness, "
    "appropriate method choice, and sufficient data; penalise unjustified leaps."
)


class Judge:
    def __init__(self, llm):
        self.llm = llm

    def _prompt(self, task: str, steps: List, answer: dict, stdout: str) -> str:
        steps_desc = "\n".join(f"  - {getattr(s, 'tool', '?')}: {getattr(s, 'rationale', '')}"
                               for s in steps)
        return (
            f"TASK:\n{task}\n\n"
            f"STEPS TAKEN:\n{steps_desc}\n\n"
            f"RESULT:\n{answer}\n\n"
            f"Rate how much this result can be trusted WITHOUT expert review.\n"
            f"Return JSON: {{\"trust\": <float 0..1>, \"rationale\": <string>}}.\n"
            f"RESPOND_WITH: trust_json"
        )

    def score(self, task: str, steps: List, answer: dict, stdout: str = "") -> float:
        raw = self.llm.complete(self._prompt(task, steps, answer, stdout))
        data = extract_json(raw)
        try:
            t = float(data.get("trust"))
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, t))
