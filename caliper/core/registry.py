"""Domain Pack registry.

A pack is a thin, versioned list of vetted tools for a field. We deliberately keep
it small — frontier models already know most of the toolchain — and curate only the
correctness-critical, version-sensitive tools. For packs under ~100 tools we render
the whole registry into the planning context (no dense retriever needed).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import yaml  # PyYAML
except ImportError:  # pragma: no cover
    yaml = None

_PACKS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "packs")


@dataclass
class ToolSpec:
    name: str
    when_to_use: str
    install: str = ""
    version: str = ""
    invocation: str = ""
    inputs: str = ""
    outputs: str = ""

    def render(self) -> str:
        v = f" (v{self.version})" if self.version else ""
        line = f"- {self.name}{v}: {self.when_to_use}"
        if self.invocation:
            line += f"\n    invoke: {self.invocation}"
        if self.inputs or self.outputs:
            line += f"\n    io: {self.inputs} -> {self.outputs}"
        return line


@dataclass
class Pack:
    name: str
    description: str = ""
    status: str = "active"
    tools: List[ToolSpec] = field(default_factory=list)

    def as_context(self) -> str:
        """Render the pack as a tool catalogue for the planning prompt."""
        head = f"# Available tools — {self.name} pack ({len(self.tools)})\n"
        return head + "\n".join(t.render() for t in self.tools)

    def tool_names(self) -> List[str]:
        return [t.name for t in self.tools]


def load_pack(name: str, path: Optional[str] = None) -> Pack:
    """Load a pack by name (caliper/packs/<name>/pack.yaml) or explicit path."""
    if yaml is None:  # pragma: no cover
        raise RuntimeError("PyYAML is required to load packs (`pip install pyyaml`).")
    path = path or os.path.join(_PACKS_DIR, name, "pack.yaml")
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    tools = [ToolSpec(**t) for t in data.get("tools", [])]
    return Pack(
        name=data.get("name", name),
        description=data.get("description", ""),
        status=data.get("status", "active"),
        tools=tools,
    )
