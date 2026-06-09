"""Long-running jobs: a 'long' plan is dispatched detached (not run inline), and
finalize() completes it into an answer + trust once the job is done."""
import unittest

from caliper import CaliperAgent, Judge, load_pack


class _LongLLM:
    name = "stub"

    def complete(self, prompt, system=None):
        if "RESPOND_WITH: plan_json" in prompt:
            return ('{"summary":"quantify all samples","long":true,"eta_seconds":3600,'
                    '"steps":[{"tool":"salmon","rationale":"quantify","code":"print(1)"}]}')
        if "RESPOND_WITH: trust_json" in prompt:
            return '{"trust":0.8}'
        if "RESPOND_WITH: answer_md" in prompt:
            return "Done — 3 samples quantified."
        return "{}"


class _StubExec:
    def __init__(self):
        self.launched = None

    def launch_job(self, code, inputs=None, eta_seconds=None):
        self.launched = {"code": code, "eta": eta_seconds}
        return {"job_id": "job_test", "state": "running"}

    def run(self, *a, **k):
        raise AssertionError("a long job must be dispatched, not run inline")


class _Null:
    def record(self, *a, **k):
        return None


class TestJobs(unittest.TestCase):
    def _agent(self):
        llm = _LongLLM()
        return CaliperAgent(load_pack("bio"), llm, judge=Judge(llm),
                            executor=_StubExec(), provenance=_Null())

    def test_long_plan_dispatches_detached_job(self):
        agent = self._agent()
        ev = []
        r = agent.run("quantify all samples", [{"path": "/d", "label": "d"}],
                      on_event=ev.append, allow_jobs=True)
        self.assertEqual(r.decision, "job")
        self.assertEqual(r.provenance_path, "job_test")
        self.assertEqual(agent.executor.launched["eta"], 3600)
        self.assertTrue(any(e["type"] == "job" and e["job_id"] == "job_test" for e in ev))

    def test_finalize_builds_answer_and_trust(self):
        res = self._agent().finalize("task", 'CALIPER_RESULT:{"n":3}\nlog', {"n": 3})
        self.assertEqual(res.answer.get("n"), 3)
        self.assertTrue(res.answer_text)
        self.assertEqual(res.decision, "escalate")  # no calibration yet


if __name__ == "__main__":
    unittest.main()
