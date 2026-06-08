"""Small shared helpers."""
from __future__ import annotations
import json
import re
from typing import Optional


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of an LLM response.

    Tolerates ```json fences and surrounding prose.
    """
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        candidate = text[start:end + 1] if start != -1 and end > start else "{}"
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return {}


def parse_caliper_result(stdout: str) -> Optional[dict]:
    """Executed code emits a line `CALIPER_RESULT:{...json...}`. Parse the last one."""
    found = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("CALIPER_RESULT:"):
            try:
                found = json.loads(line[len("CALIPER_RESULT:"):])
            except json.JSONDecodeError:
                continue
    return found
