"""Code executor — runs a step's Python in a subprocess with a timeout.

Inputs are passed to the child via the CALIPER_INPUTS env var (a JSON list of
{"path": ..., "label": ...}). The child emits its result on a line prefixed
`CALIPER_RESULT:`.

NOTE: this is a thin MVP executor. Hardening (containerisation, resource limits,
network egress control) is a tracked TODO before any untrusted deployment.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import List, Optional

from ..config import DEFAULT_TIMEOUT_SEC


@dataclass
class ExecResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int


class Executor:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT_SEC, python: Optional[str] = None):
        self.timeout = timeout
        self.python = python or sys.executable

    def run(self, code: str, inputs: Optional[List[dict]] = None) -> ExecResult:
        env = dict(os.environ)
        env["CALIPER_INPUTS"] = json.dumps(inputs or [])
        with tempfile.TemporaryDirectory(prefix="caliper_") as workdir:
            script = os.path.join(workdir, "step.py")
            with open(script, "w") as f:
                f.write(code)
            try:
                proc = subprocess.run(
                    [self.python, script],
                    cwd=workdir, env=env, capture_output=True, text=True,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired as e:
                return ExecResult(False, e.stdout or "", f"timeout after {self.timeout}s", -1)
            return ExecResult(proc.returncode == 0, proc.stdout, proc.stderr, proc.returncode)
