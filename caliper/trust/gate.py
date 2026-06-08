"""Calibrated escalation gate — Caliper's trust core.

Given a calibration set of (trust_score, correct) pairs, pick the most permissive
acceptance threshold `tau` such that, with confidence >= 1 - delta, the error rate
among AUTO-ACCEPTED results (trust_score >= tau) is at most `alpha`.

Design notes
------------
* This is a ONE-SIDED risk control: we police only what the system silently passes
  (false reassurance). Everything below `tau` is escalated to the human and is free
  to be wrong.
* Validity rests on a rank / permutation construction over the calibration scores
  (exchangeability of calibration and deployment cases) PLUS a one-sided upper
  confidence bound on the accepted-set error rate. It does NOT require the trust
  score to be a calibrated probability — only to *rank* cases usefully. That is
  why a biased LLM judge can still drive a trustworthy gate.
* We use an exact one-sided Clopper-Pearson upper bound on the accepted-set error
  rate (stdlib-only, via bisection on the binomial CDF). This is far tighter than
  Hoeffding in the small-calibration regime that real labs live in (~10^2 labels),
  which is precisely where a loose bound would force needless escalation.
* Exchangeability is exactly what breaks under distribution shift in deployment;
  the planned repairs (covariate reweighting, group strata, anytime e-value
  monitors) attach here without changing the interface.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

Sample = Tuple[float, bool]  # (trust_score, correct)


@dataclass
class GateDecision:
    accept: bool
    trust: float
    tau: float
    reason: str


@dataclass
class CalibratedGate:
    tau: float                 # accept iff trust >= tau ; inf means "escalate everything"
    alpha: float
    delta: float
    n_calibration: int
    n_accepted: int            # accepted in calibration
    accepted_error: float      # empirical error among accepted (calibration)
    accepted_error_bound: float  # (1-delta) upper bound on that error
    feasible: bool             # whether any threshold met the target

    def decide(self, trust: float) -> GateDecision:
        accept = self.feasible and trust >= self.tau
        if not self.feasible:
            reason = (f"No threshold met alpha={self.alpha} on calibration data; "
                      f"escalating all results.")
        elif accept:
            reason = (f"trust {trust:.3f} >= tau {self.tau:.3f}; auto-accept "
                      f"(<= {self.alpha:.0%} confident-wrong, {1 - self.delta:.0%} conf).")
        else:
            reason = f"trust {trust:.3f} < tau {self.tau:.3f}; escalate to expert."
        return GateDecision(accept=accept, trust=trust, tau=self.tau, reason=reason)


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p), computed stably in log-space."""
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 1.0 if k >= n else 0.0
    lp, lq = math.log(p), math.log1p(-p)
    terms = [
        math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
        + i * lp + (n - i) * lq
        for i in range(0, k + 1)
    ]
    m = max(terms)
    return math.exp(m + math.log(sum(math.exp(t - m) for t in terms)))


def _error_upper_bound(failures: int, n: int, delta: float) -> float:
    """Exact one-sided (1-delta) Clopper-Pearson upper bound on a Bernoulli error rate.

    Solves for the largest error probability p consistent with observing `failures`
    among `n` at confidence 1-delta, i.e. P(Binomial(n, p) <= failures) = delta.
    Returns 1.0 for n == 0 (no evidence => no guarantee) or all-failures.
    """
    if n == 0 or failures >= n:
        return 1.0
    lo, hi = failures / n, 1.0
    for _ in range(80):  # bisection to ~1e-24 precision
        mid = (lo + hi) / 2.0
        if _binom_cdf(failures, n, mid) > delta:
            lo = mid  # too few low-count outcomes => p must be larger
        else:
            hi = mid
    return hi


def calibrate(samples: Sequence[Sample], alpha: float = 0.10,
              delta: float = 0.05) -> CalibratedGate:
    """Fit the acceptance threshold from labeled calibration data.

    Picks the SMALLEST tau (maximising auto-accept coverage) whose accepted-set
    error upper bound is <= alpha. If none qualifies, the gate escalates everything.
    """
    if not samples:
        return CalibratedGate(math.inf, alpha, delta, 0, 0, 0.0, 1.0, False)

    # Scan thresholds from the highest score downward, accumulating the accepted set
    # incrementally (O(n log n) total). Lowering tau adds more (lower-confidence)
    # cases => more coverage but higher error. We keep the LOWEST tau (max coverage)
    # whose Clopper-Pearson error bound is still <= alpha.
    ordered = sorted(samples, key=lambda x: x[0], reverse=True)
    n_total = len(samples)

    best: Optional[CalibratedGate] = None
    n_acc = failures = 0
    i = 0
    while i < n_total:
        tau = ordered[i][0]
        while i < n_total and ordered[i][0] == tau:  # absorb ties at this score
            n_acc += 1
            if not ordered[i][1]:
                failures += 1
            i += 1
        emp = failures / n_acc
        if emp > alpha and best is not None:
            break  # past the feasible boundary; lower tau only adds error
        bound = _error_upper_bound(failures, n_acc, delta)
        if bound <= alpha:
            best = CalibratedGate(
                tau=tau, alpha=alpha, delta=delta, n_calibration=n_total,
                n_accepted=n_acc, accepted_error=emp, accepted_error_bound=bound,
                feasible=True,
            )

    if best is None:
        return CalibratedGate(math.inf, alpha, delta, n_total, 0, 0.0, 1.0, False)
    return best
