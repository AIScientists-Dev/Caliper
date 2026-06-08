"""End-to-end Caliper demo on the bio pack.

Runs offline with MockLLM by default (no API key, no heavy tools needed) so anyone
can `python examples/bio_demo.py` and see the full loop:
  plan -> execute -> trust score -> calibrated gate -> accept/escalate.

Set CALIPER_REAL=1 (with ANTHROPIC_API_KEY) to drive it with a live model instead.
"""
import os

from caliper import CaliperAgent, load_pack, calibrate, Judge, make_llm

HERE = os.path.dirname(__file__)


def main():
    pack = load_pack("bio")

    # Offline by default. Set CALIPER_REAL=1 and CALIPER_PROVIDER=anthropic|openai
    # to drive it with a live model.
    provider = "mock" if os.environ.get("CALIPER_REAL") != "1" else None
    llm = make_llm(provider=provider)
    print(f"LLM provider: {llm.name}"
          + (f" / {getattr(llm, 'model', '')}" if llm.name != "mock" else " (offline)"))

    # A calibration set the expert has adjudicated (~80 cases — a realistic pilot
    # size). High trust => usually right; the gate learns where to draw the line.
    calib = []
    for i in range(80):
        s = 0.30 + 0.68 * i / 79.0            # scores spread 0.30 .. 0.98
        correct = s >= 0.62                   # high band is reliable
        if i % 7 == 0 and s < 0.62:           # a few lucky low-score rights (harmless)
            correct = True
        calib.append((round(s, 3), correct))
    gate = calibrate(calib, alpha=0.10, delta=0.10)
    print(f"Calibrated gate: tau={gate.tau:.3f}  feasible={gate.feasible}  "
          f"accepted_err_bound={gate.accepted_error_bound:.3f}")

    agent = CaliperAgent(pack=pack, llm=llm, judge=Judge(llm), gate=gate)

    data = [{"path": os.path.join(HERE, "data", "counts.csv"),
             "label": "bulk RNA-seq counts, 3 ctrl + 3 treated"}]
    task = ("Find differentially expressed genes between control and treated "
            "samples in this bulk RNA-seq count matrix.")

    result = agent.run(task, data)

    print("\n=== Caliper result ===")
    print("tools used :", [s.tool for s in result.steps])
    print("answer     :", result.answer)
    print(f"trust      : {result.trust:.3f}")
    print("decision   :", result.decision.upper())
    print("provenance :", result.provenance_path)


if __name__ == "__main__":
    main()
