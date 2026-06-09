"""The self-evolve loop: a missing pack tool is auto-installed (allow-listed) and retried."""
import unittest

from caliper import CaliperAgent, Judge, MockLLM, load_pack
from caliper.core.executor import ExecResult


class _StubExec:
    """Fails the first run with 'command not found', succeeds after provisioning."""
    def __init__(self):
        self.calls = 0
        self.provisioned = None

    def run(self, code, inputs=None, on_output=None):
        self.calls += 1
        if self.calls == 1:
            return ExecResult(False, "", "deseq2: command not found", 127)
        return ExecResult(True, 'CALIPER_RESULT:{"n_de": 3}', "", 0)

    def provision(self, cmd, on_output=None):
        self.provisioned = cmd
        return ExecResult(True, "installed", "", 0)


class _NullProv:
    def record(self, *a, **k):
        return None


class TestSelfProvision(unittest.TestCase):
    def test_missing_tool_is_installed_then_retried(self):
        ex = _StubExec()
        agent = CaliperAgent(load_pack("bio"), MockLLM(), judge=Judge(MockLLM()),
                             executor=ex, provenance=_NullProv())
        events = []
        result = agent.run("find DE genes", [{"path": "/tmp/x", "label": "x"}],
                           on_event=events.append)
        self.assertIsNotNone(ex.provisioned)          # installed the missing pack tool
        self.assertIn("DESeq2", ex.provisioned)        # via the registry's vetted command
        self.assertEqual(ex.calls, 2)                  # ran -> failed -> provisioned -> retried
        self.assertTrue(any(e["type"] == "provision" for e in events))
        self.assertTrue(any(e["type"] == "experience" for e in events))
        self.assertEqual(result.answer.get("n_de"), 3)  # succeeded after self-heal


if __name__ == "__main__":
    unittest.main()
