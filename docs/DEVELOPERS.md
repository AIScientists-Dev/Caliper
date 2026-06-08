# Caliper — developer documentation

Caliper is an AI research analyst whose distinguishing feature is a **calibrated,
risk-controlled trust layer**: it attaches a confidence score to every result,
auto-accepts only what it can stand behind under a provable error bound, escalates the
rest to a human, and recalibrates from each human correction.

## Architecture — three thin layers

| Layer | Modules | Status |
|-------|---------|--------|
| **① Domain Pack** — versioned registry of vetted tools (metadata, not vendored code) | `caliper/packs/*`, `caliper.core.registry` | commodity, light |
| **② Agent Core** — plan → select tools → execute code → reproducible provenance | `caliper.core.agent`, `.executor`, `.provenance` | commodity |
| **③ Trust & Feedback** — calibrated confidence, risk-controlled escalation, recalibration | `caliper.trust.gate`, `.judge`, `.feedback` | **the moat** |

Flow: the model plans (choosing from the pack rendered in-context) and emits executable
Python; the executor runs it in a subprocess; the judge scores trust; the calibrated
gate decides auto-accept vs. escalate; a provenance record is written. One corrective
retry fires if a run yields no parseable result.

### What a "pack" is

Metadata only — name, *when to use*, install command, pinned version, invocation
template, I/O shape (`caliper/packs/bio/pack.yaml`). Caliper does not bundle STAR /
DESeq2 / Astropy; it tells the agent they exist and how to call them, and the agent
generates the code to invoke whatever is installed in the executor environment. For
packs under ~100 tools the whole registry is rendered into the planning prompt (no
retriever needed). `bio` is a working 20-tool pack; `astro` is a 10-tool skeleton
proving the core is domain-agnostic.

## The trust gate — the math

Goal, in one line: with the threshold τ̂ chosen from calibration data,
`Pr(Y=0 | g ≥ τ̂) ≤ α` with confidence `1−δ` — distribution-free, finite-sample.

- **Inputs.** A calibration set of `(g, Y)` pairs, where `g ∈ [0,1]` is the judge's
  trust score (machine-generated, same scorer as deployment) and `Y ∈ {correct, wrong}`
  is an expert label. The expert provides only `Y`; the score is free. This is what
  makes the method label-efficient.
- **Construction.** Accept iff `g ≥ τ`. Among the accepted calibration points, mistakes
  behave like `Binomial(n, p)` with `p = Pr(Y=0 | g≥τ)`. We take an **exact one-sided
  Clopper–Pearson** upper bound on `p` and pick the most permissive τ whose bound ≤ α.
  CP is far tighter than Hoeffding in the small-calibration regime real labs live in;
  for the zero-error case it reduces to `1 − δ^{1/n}`, giving the sample-size rule
  `n ≳ ln(1/δ)/α`.
- **Why a biased judge is safe.** The guarantee is computed from labels, not from the
  score's value; any monotone transform of `g` leaves the accepted set unchanged. The
  judge only has to *rank*, not be calibrated. A poor judge yields a *less efficient*
  gate (more escalation), never an *unsafe* one.
- **One-sided.** Only false reassurance (auto-accepting a wrong result) is bounded;
  escalation is free. That matches the clinical cost asymmetry.
- **Rigor caveat / roadmap.** τ̂ is data-chosen, so per-threshold CP has a
  multiple-testing gap. The exact fix is **Learn-then-Test** with fixed-sequence
  testing over thresholds (monotone risk ⇒ negligible α-spend). Not yet implemented.
- **Where it breaks.** Exchangeability. Repairs that slot in without interface change:
  covariate-shift reweighting, group-conditional (Mondrian) calibration, and
  anytime-valid e-value / betting monitors for drift.

See `caliper/trust/gate.py` (calibration + CP bound), `judge.py` (scoring),
`feedback.py` (the live recalibration loop).

## The mutualistic (live) loop

Attach a `FeedbackStore` and the gate is re-fit from all accumulated expert verdicts
before every decision — each correction tightens it instantly (recalibration is ms):

```python
from caliper import CaliperAgent, FeedbackStore
store = FeedbackStore("feedback.jsonl")
agent = CaliperAgent(pack, llm, feedback=store, alpha=0.10, delta=0.10)
store.add(trust=result.trust, correct=True)   # next run uses the updated gate
```

## Model providers

Provider-agnostic via `make_llm(provider=..., model=...)` or the `CALIPER_PROVIDER` env
var. Defaults: overall provider `anthropic` / model `claude-opus-4-8`; OpenAI default
`gpt-5`.

```python
from caliper import make_llm
llm = make_llm()                              # anthropic / claude-opus-4-8 (default)
llm = make_llm("openai")                       # openai / gpt-5
llm = make_llm("openai", model="gpt-5-chat-latest")  # faster, non-reasoning variant
```

Notes on reasoning models (GPT-5 family): they require `max_completion_tokens` (handled
automatically) and their reasoning tokens count against that budget, so the OpenAI
client uses a 16k cap to leave room for the plan. Per-call timeouts (120s Anthropic /
180s OpenAI) prevent a slow call from blocking a run. For routine, high-volume planning,
`gpt-5-chat-latest` (non-reasoning) is much faster than `gpt-5`.

## Execution environment

The agent is told what the executor can actually run (see `DEFAULT_ENVIRONMENT` in
`agent.py`) so it doesn't emit code depending on uninstalled tools. The bundled demo
environment is Python + numpy/pandas/scipy; real pipelines (DESeq2, STAR, …) require
those tools installed and the environment string updated to match.

## Quickstart

```bash
pip install -e ".[llm]"               # pyyaml + anthropic + openai
python examples/bio_demo.py           # offline end-to-end (mock model)
python examples/feedback_loop.py      # gate tightening as feedback accrues
python examples/provider_smoketest.py # ping providers (needs keys)
python -m unittest discover -s tests -v

# Live runs:
CALIPER_REAL=1 python examples/bio_demo.py                        # opus-4.8
CALIPER_REAL=1 CALIPER_PROVIDER=openai python examples/bio_demo.py # gpt-5
```

## Roadmap

- [x] Thin core + `bio` pack + offline demo + calibrated gate (tested)
- [x] Multi-provider models + reasoning-model handling + timeouts
- [x] Live feedback recalibration + one corrective retry
- [ ] Learn-then-Test fixed-sequence wrapper (exact validity for the data-chosen τ)
- [ ] `calibrate_from_runs()` — build the gate from provenance runs + an expert-verdict CSV
- [ ] Real bio executor environment (conda: DESeq2/STAR/MACS2) + reproduce a published study
- [ ] Exchangeability repairs (reweighting, group strata, drift monitor)
- [ ] Flesh out `astro` pack with a design partner
- [ ] Executor hardening (containerisation, resource/network limits)
