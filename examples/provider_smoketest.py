"""Smoke-test each LLM provider with a trivial live call.

Requires the relevant key in the environment (ANTHROPIC_API_KEY / OPENAI_API_KEY)
and the SDK installed (`pip install anthropic openai`).
"""
import os

from caliper import make_llm


def try_provider(provider: str):
    if provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"[{provider}] skipped — no ANTHROPIC_API_KEY"); return
    if provider == "openai" and not os.environ.get("OPENAI_API_KEY"):
        print(f"[{provider}] skipped — no OPENAI_API_KEY"); return
    try:
        llm = make_llm(provider=provider)
        out = llm.complete("Reply with exactly the word: OK").strip()
        print(f"[{provider}] {llm.model} -> {out!r}")
    except Exception as e:  # noqa: BLE001
        print(f"[{provider}] ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    for p in ("anthropic", "openai"):
        try_provider(p)
