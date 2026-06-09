"""Agent core — plan -> select tools -> execute -> result, then trust-gate it.

Deliberately thin: the LLM plans (choosing from the pack rendered in-context) and
emits executable code; the executor runs it; the judge scores trust; the calibrated
gate decides auto-accept vs. escalate. The value Caliper adds over a bare agent is
the last two steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..util import extract_json, parse_caliper_result
from .executor import Executor
from .provenance import ProvenanceLog
from .registry import Pack

PLAN_SYSTEM = (
    "You are Caliper, a careful data analyst working on a private server. Read the user's "
    "request and do EXACTLY what is asked — it is NOT always a genomics or differential-"
    "expression task; it may be a question about what data exists, a summary, or any "
    "analysis. Make a short plan with runnable Python step(s) that INSPECT the data or run "
    "the requested work. To explore, list and summarize files under the data root with "
    "os/glob/pandas (READ-ONLY) and print what you find. A step may print plain text; for a "
    "structured result or a figure, also print one line `CALIPER_RESULT:` followed by "
    "compact JSON (optionally with a 'figures' list of PNG paths). You will write the final "
    "plain-language answer AFTER the steps run, so steps should gather the facts you need. "
    "Read inputs from env var CALIPER_INPUTS (a list of {'path','label'}). Do not invent "
    "file paths. Write outputs/temp ONLY to the current working directory; input data is "
    "READ-ONLY — never modify or delete it. If a task needs no code (pure conversation), "
    "return an empty steps list."
)

# What the executor can actually run.
DEFAULT_ENVIRONMENT = (
    "Python 3 with numpy, pandas, scipy and the standard library. Field-specific CLI tools "
    "may be on PATH (try them; a missing tool can be installed on demand). Prefer simple, "
    "robust code. For exploration use os/glob/pandas to list and characterize files."
)

# Turns the raw step outputs into a clear answer for the user (the thing they read).
ANSWER_SYSTEM = (
    "You are Caliper, talking to a scientist who is not a programmer. Using their request "
    "and the outputs of the steps you ran, write a clear, helpful answer in Markdown. Be "
    "concrete and HONEST: if data was missing or a step failed, say so plainly and suggest "
    "the next step. Never invent results, numbers, or file names that aren't in the outputs. "
    "Keep it tight — lead with the answer, then the supporting detail."
)


@dataclass
class Step:
    tool: str
    rationale: str
    code: str


@dataclass
class CaliperResult:
    task: str
    answer: dict
    trust: float
    accepted: bool
    decision: str  # "auto-accept" | "escalate"
    answer_text: str = ""  # the plain-language answer the user reads
    steps: List[Step] = field(default_factory=list)
    raw_stdout: str = ""
    provenance_path: Optional[str] = None


class CaliperAgent:
    def __init__(self, pack: Pack, llm, judge=None, gate=None,
                 executor: Optional[Executor] = None,
                 provenance: Optional[ProvenanceLog] = None,
                 feedback=None, alpha: float = 0.10, delta: float = 0.05,
                 environment: Optional[str] = None, repair: bool = True,
                 self_provision: bool = True, data_root: str = ""):
        from ..trust.judge import Judge  # local import avoids cycle
        self.pack = pack
        self.llm = llm
        self.data_root = data_root  # where the user's data lives (for exploration hints)
        self.environment = environment or DEFAULT_ENVIRONMENT
        self.judge = judge or Judge(llm)
        self.gate = gate  # CalibratedGate or None (uncalibrated => always escalate)
        self.executor = executor or Executor()
        self.provenance = provenance or ProvenanceLog()
        # Live mutualistic loop: if a feedback store is attached, the gate is
        # re-fit from all accumulated expert adjudications before every decision,
        # so each correction immediately tightens the gate (recalibration is ~ms).
        self.feedback = feedback
        self.alpha = alpha
        self.delta = delta
        self.repair = repair
        self.self_provision = self_provision

    @staticmethod
    def _looks_missing_tool(res) -> bool:
        blob = (res.stdout + " " + res.stderr).lower()
        return any(s in blob for s in ("command not found", "no module named",
                                       "not found", "no such file"))

    def _plan_prompt(self, task: str, data_files: List[dict]) -> str:
        files = "\n".join(f"  - {f.get('label', '?')}: {f['path']}" for f in data_files) \
            or "  (none selected — if the task needs data, explore the data root below)"
        root = (f"# Data root on this server (explore READ-ONLY if no files were selected)\n"
                f"{self.data_root}\n\n") if self.data_root else ""
        return (
            f"{self.pack.as_context()}\n\n"
            f"# Execution environment\n{self.environment}\n\n"
            f"# Task\n{task}\n\n"
            f"# Input data files\n{files}\n\n"
            f"{root}"
            f"Return JSON: {{\"summary\": str, \"steps\": "
            f"[{{\"tool\": str, \"rationale\": str, \"code\": str}}]}}.\n"
            f"RESPOND_WITH: plan_json"
        )

    def _synthesize(self, task: str, stdout: str) -> str:
        """Turn the step outputs into the plain-language answer the user reads."""
        prompt = (f"# The user asked\n{task}\n\n"
                  f"# Outputs from the steps you ran\n{stdout[-6000:] or '(no output)'}\n\n"
                  f"Write the answer for the user now.\nRESPOND_WITH: answer_md")
        try:
            return self.llm.complete(prompt, system=ANSWER_SYSTEM).strip()
        except Exception:  # noqa: BLE001  -- never let synthesis break the run
            return ""

    def _repair(self, task: str, data_files: List[dict], stdout: str):
        """Single corrective attempt: re-prompt with the failure, get one fixed step."""
        prompt = (
            f"# Execution environment\n{self.environment}\n\n"
            f"# Task\n{task}\n\n"
            f"# Your previous attempt produced NO parseable result. Output/errors:\n"
            f"{stdout[-1500:]}\n\n"
            f"Write ONE self-contained Python script for this environment that completes "
            f"the task and prints, on a single final line, `CALIPER_RESULT:` immediately "
            f"followed by compact JSON. Reads inputs from env CALIPER_INPUTS.\n"
            f"Return JSON: {{\"summary\": str, \"steps\": "
            f"[{{\"tool\": str, \"rationale\": str, \"code\": str}}]}}.\n"
            f"RESPOND_WITH: plan_json"
        )
        plan = extract_json(self.llm.complete(prompt, system=PLAN_SYSTEM))
        steps = plan.get("steps", [])
        if not steps:
            return None
        s = steps[0]
        return Step(tool=s.get("tool", "repair"), rationale=s.get("rationale", "repair retry"),
                    code=s.get("code", ""))

    def run(self, task: str, data_files: List[dict], on_event=None) -> CaliperResult:
        emit = on_event or (lambda e: None)
        emit({"type": "status", "text": "Planning the analysis…"})
        plan = extract_json(self.llm.complete(self._plan_prompt(task, data_files),
                                              system=PLAN_SYSTEM))
        steps = [Step(tool=s.get("tool", "?"), rationale=s.get("rationale", ""),
                      code=s.get("code", "")) for s in plan.get("steps", [])]
        emit({"type": "plan", "summary": plan.get("summary", ""),
              "tools": [s.tool for s in steps]})

        stdout, answer = "", {}
        provisioned = set()
        stream = lambda c: emit({"type": "stdout", "text": c[-800:]})
        for st in steps:
            emit({"type": "step", "tool": st.tool, "rationale": st.rationale})
            res = self.executor.run(st.code, inputs=data_files, on_output=stream)
            # self-evolve: if a needed pack tool is missing, install it (allow-listed) and retry once
            if (self.self_provision and not res.ok and not getattr(res, "blocked", False)
                    and self._looks_missing_tool(res) and st.tool not in provisioned
                    and hasattr(self.executor, "provision")):
                cmd = self.pack.install_command(st.tool)
                if cmd:
                    provisioned.add(st.tool)
                    emit({"type": "provision", "tool": st.tool, "cmd": cmd})
                    pres = self.executor.provision(cmd)
                    emit({"type": "experience", "tool": st.tool, "action": "install",
                          "ok": pres.ok, "error": (res.stderr or res.stdout)[-300:]})
                    if pres.ok:
                        res = self.executor.run(st.code, inputs=data_files, on_output=stream)
            stdout += res.stdout
            if res.stderr:
                stdout += f"\n[stderr] {res.stderr}"
            emit({"type": "exec", "tool": st.tool, "ok": res.ok,
                  "blocked": getattr(res, "blocked", False), "stdout_tail": res.stdout[-600:]})
            parsed = parse_caliper_result(res.stdout)
            if parsed is not None:
                answer = parsed

        # One corrective retry: some models fail to emit the result contract or call a
        # missing tool. Show them the failure and ask for a single fixed script.
        if not answer and self.repair:
            fixed = self._repair(task, data_files, stdout)
            if fixed is not None:
                steps.append(fixed)
                res = self.executor.run(fixed.code, inputs=data_files)
                stdout += res.stdout + (f"\n[stderr] {res.stderr}" if res.stderr else "")
                parsed = parse_caliper_result(res.stdout)
                if parsed is not None:
                    answer = parsed

        emit({"type": "status", "text": "Writing the answer…"})
        answer_text = self._synthesize(task, stdout)
        if answer_text:
            emit({"type": "answer", "markdown": answer_text})

        emit({"type": "status", "text": "Checking how much to trust it…"})
        trust = self.judge.score(task, steps, answer_text or answer, stdout)
        emit({"type": "trust", "trust": trust})
        if self.feedback is not None and len(self.feedback) > 0:
            self.gate = self.feedback.recalibrate(self.alpha, self.delta)
        accepted = bool(self.gate and self.gate.decide(trust).accept)
        decision = "auto-accept" if accepted else "escalate"
        emit({"type": "decision", "decision": decision, "accepted": accepted,
              "answer": answer, "answer_text": answer_text,
              "calibrated": self.feedback is not None and len(self.feedback) > 0})

        result = CaliperResult(task=task, answer=answer, trust=trust,
                               accepted=accepted, decision=decision,
                               answer_text=answer_text, steps=steps, raw_stdout=stdout)
        result.provenance_path = self.provenance.record(result, self.pack.name)
        return result
