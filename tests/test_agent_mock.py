"""End-to-end agent test on the offline MockLLM + real executor + real bio data."""
import os
import unittest

from caliper import CaliperAgent, load_pack, calibrate, Judge, MockLLM

HERE = os.path.dirname(__file__)
DATA = os.path.join(HERE, "..", "examples", "data", "counts.csv")


class TestAgentMock(unittest.TestCase):
    def test_rnaseq_de_endtoend(self):
        pack = load_pack("bio")
        llm = MockLLM()
        gate = calibrate([(0.9, True)] * 40 + [(0.3, False)] * 40, alpha=0.1, delta=0.1)
        agent = CaliperAgent(pack=pack, llm=llm, judge=Judge(llm), gate=gate,
                             provenance=_TmpProvenance())

        result = agent.run(
            "Find differentially expressed genes between control and treated.",
            [{"path": os.path.abspath(DATA), "label": "bulk RNA-seq counts"}],
        )

        self.assertEqual([s.tool for s in result.steps], ["deseq2"])
        self.assertIn("n_de", result.answer)
        self.assertGreater(result.answer["n_de"], 0)
        # Known up-regulated genes in the fixture should surface.
        top_genes = {g["gene"] for g in result.answer["top"]}
        self.assertTrue({"MYC", "EGFR", "KRAS"} & top_genes)
        self.assertAlmostEqual(result.trust, 0.78, places=2)
        self.assertIn(result.decision, ("auto-accept", "escalate"))


class _TmpProvenance:
    """Provenance stub that writes nowhere (keeps the test dir clean)."""
    def record(self, result, pack_name):
        return None


if __name__ == "__main__":
    unittest.main()
