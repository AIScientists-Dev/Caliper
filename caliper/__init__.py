"""Caliper — an AI research analyst that knows when to trust itself.

Thin architecture, three layers:
  1. Domain Pack   — a versioned registry of vetted tools (caliper.core.registry)
  2. Agent Core    — plan -> select tools -> execute -> result (caliper.core.agent)
  3. Trust & Feedback — calibrated confidence, risk-controlled escalation,
                        recalibration from human feedback (caliper.trust.*)

The first two layers are deliberately commodity. The trust layer is the moat.
"""
from .core.registry import load_pack, Pack, ToolSpec
from .core.agent import CaliperAgent, CaliperResult
from .core.executor import Executor
from .trust.gate import calibrate, CalibratedGate, GateDecision
from .trust.judge import Judge
from .trust.feedback import FeedbackStore
from .trust.review import calibrate_from_runs, export_review_sheet
from .llm import make_llm, BaseLLM, AnthropicLLM, OpenAILLM, MockLLM

__version__ = "0.0.1"
__all__ = [
    "load_pack", "Pack", "ToolSpec",
    "CaliperAgent", "CaliperResult", "Executor",
    "calibrate", "CalibratedGate", "GateDecision",
    "Judge", "FeedbackStore", "calibrate_from_runs", "export_review_sheet",
    "make_llm", "BaseLLM", "AnthropicLLM", "OpenAILLM", "MockLLM",
]
