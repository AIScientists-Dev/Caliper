"""LLM clients — provider-agnostic.

Use `make_llm(provider=...)` everywhere; the concrete classes are an implementation
detail. Supported providers: "anthropic" (default), "openai", "mock" (offline).
"""
from __future__ import annotations

import os
from typing import Optional

from .config import DEFAULT_PROVIDER, PROVIDER_DEFAULT_MODEL

_DEFAULT_SYSTEM = "You are Caliper, a careful scientific analysis planner."


class BaseLLM:
    """Interface: turn a prompt (+ optional system) into a text completion."""
    name = "base"

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        raise NotImplementedError


class AnthropicLLM(BaseLLM):
    name = "anthropic"

    def __init__(self, model: str = "claude-opus-4-8",
                 api_key: Optional[str] = None, max_tokens: int = 4000,
                 timeout: float = 120.0):
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None

    def _ensure(self):
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("`pip install anthropic` to use the anthropic provider") from e
            self._client = Anthropic(api_key=self._api_key, timeout=self.timeout)

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        self._ensure()
        msg = self._client.messages.create(
            model=self.model, max_tokens=self.max_tokens,
            system=system or _DEFAULT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(getattr(b, "text", "") for b in msg.content
                       if getattr(b, "type", None) == "text")


class OpenAILLM(BaseLLM):
    name = "openai"

    def __init__(self, model: str = "gpt-5",
                 api_key: Optional[str] = None, max_tokens: int = 16000,  # headroom for reasoning tokens
                 timeout: float = 180.0):  # reasoning models are slow
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self._client = None

    def _ensure(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("`pip install openai` to use the openai provider") from e
            self._client = OpenAI(api_key=self._api_key, timeout=self.timeout)

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        self._ensure()
        messages = [{"role": "system", "content": system or _DEFAULT_SYSTEM},
                    {"role": "user", "content": prompt}]
        try:
            resp = self._client.chat.completions.create(
                model=self.model, max_tokens=self.max_tokens, messages=messages)
        except Exception as e:  # GPT-5+ require max_completion_tokens (TypeError or 400)
            if "max_completion_tokens" in str(e) or "max_tokens" in str(e):
                resp = self._client.chat.completions.create(
                    model=self.model, max_completion_tokens=self.max_tokens, messages=messages)
            else:
                raise
        return resp.choices[0].message.content or ""


# --- Offline deterministic stand-in -------------------------------------------------

_MOCK_PLAN = """```json
{
  "summary": "Differential expression between two conditions via log2 fold-change.",
  "steps": [
    {
      "tool": "deseq2",
      "rationale": "Two-condition bulk RNA-seq count matrix -> identify up/down-regulated genes.",
      "code": "import os, csv, json, math\\ninp = json.loads(os.environ['CALIPER_INPUTS'])[0]['path']\\nrows = list(csv.reader(open(inp)))\\nheader = rows[0][1:]\\nhalf = len(header) // 2\\ngenes = []\\nfor r in rows[1:]:\\n    name = r[0]; vals = [float(x) for x in r[1:]]\\n    a, b = vals[:half], vals[half:]\\n    ma = sum(a)/len(a) + 1.0; mb = sum(b)/len(b) + 1.0\\n    genes.append((name, math.log2(mb/ma)))\\nde = sorted([g for g in genes if abs(g[1]) > 1.0], key=lambda x: -abs(x[1]))\\nprint('CALIPER_RESULT:' + json.dumps({'n_genes': len(genes), 'n_de': len(de), 'top': [{'gene': g, 'log2fc': round(l, 3)} for g, l in de[:10]]}))"
    }
  ]
}
```"""

_MOCK_TRUST = '{"trust": 0.78, "rationale": "Clean two-group design, adequate replicates; '\
              'standard DE call. Mild uncertainty: no multiple-testing correction applied."}'


class MockLLM(BaseLLM):
    """Returns canned, deterministic responses keyed off prompt sentinels."""
    name = "mock"

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        if "RESPOND_WITH: plan_json" in prompt:
            return _MOCK_PLAN
        if "RESPOND_WITH: trust_json" in prompt:
            return _MOCK_TRUST
        return "{}"


def make_llm(provider: Optional[str] = None, model: Optional[str] = None, **kw) -> BaseLLM:
    """Factory. provider defaults to env CALIPER_PROVIDER or config.DEFAULT_PROVIDER."""
    provider = (provider or os.environ.get("CALIPER_PROVIDER") or DEFAULT_PROVIDER).lower()
    model = model or PROVIDER_DEFAULT_MODEL.get(provider)
    if provider == "anthropic":
        return AnthropicLLM(model=model, **kw)
    if provider == "openai":
        return OpenAILLM(model=model, **kw)
    if provider == "mock":
        return MockLLM()
    raise ValueError(f"Unknown provider {provider!r}; use anthropic | openai | mock")
