"""Multiple-testing / selection-bias corrections for this repo's evidence corpus.

EVIDENCE TOOLING ONLY. Imports nothing from strategy/, risk/, execution/,
webhook/, config/ or replay/. Changes no runtime behaviour. Pure stdlib.

Why this file exists
--------------------
The repo has a BINDING rule -- "never report a PF without its null band" -- and a
pre-registered analysis plan (docs/prereg-context-permission-layer-analysis-plan-2026-07-16.md
section 12) that specifies Holm-Bonferroni over exactly 22 tests. Neither was
implemented anywhere in the codebase. This module implements both, plus the
selection-bias corrections the finance literature actually uses, so an evidence
claim can be checked instead of asserted.

The single most important function here is `null_band_threshold_quantile`.
It answers the question the existing null band does NOT answer.

    The recorded null band describes ONE random run:
        P(a single random run has PF >= x).
    The question that matters after searching M strategy variants is:
        P(the BEST of M random runs has PF >= x).
    These differ enormously, and the second is the one an evidence claim must
    clear. Comparing a best-of-M result to a single-run band is the textbook
    selection-bias error -- the same error the Deflated Sharpe Ratio exists to
    correct.

Sources (retrieved 2026-08-08; see docs/batch2-evidence-audit-2026-08-08.md for
SHA-256 provenance):
  S1  Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio: Correcting for
      Selection Bias, Backtest Overfitting and Non-Normality", JPM 40(5).
      Eq. (1) expected maximum Sharpe, Eq. (2) DSR, Appendix A.3 Eq. (9)
      implied independent trials.
  S2  Bailey, Borwein, Lopez de Prado & Zhu (2015), "The Probability of Backtest
      Overfitting" -- CSCV / PBO.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Iterable, Sequence

__all__ = [
    "EULER_MASCHERONI",
    "implied_independent_trials",
    "expected_max_sharpe",
    "probabilistic_sharpe_ratio",
    "deflated_sharpe_ratio",
    "min_track_record_length",
    "holm_bonferroni",
    "benjamini_hochberg",
    "null_band_threshold_quantile",
    "null_band_family_wise_error",
    "TrialCount",
]

EULER_MASCHERONI = 0.5772156649015329

_N = NormalDist()


# ─── Selection bias: how many trials were really run? ────────────────────────

def implied_independent_trials(m_trials: int, avg_correlation: float) -> float:
    """S1 Appendix A.3, Eq. (9):  N_hat = rho_bar + (1 - rho_bar) * M.

    M dependent trials behave like N_hat independent ones. Interpolates between
    the two limits the paper proves: rho_bar -> 1 gives N_hat -> 1 (every trial
    is the same trial); rho_bar -> 0 gives N_hat -> M.

    Use this when trials are variants of one another -- e.g. the same strategy
    swept over stop widths, or ten shadow families on the same instrument and
    the same bars. Those are NOT independent, and pretending they are
    over-penalises. Pretending they are one trial under-penalises far worse.
    """
    if m_trials < 1:
        raise ValueError("m_trials must be >= 1")
    rho = float(avg_correlation)
    if not -1.0 <= rho <= 1.0:
        raise ValueError("avg_correlation must be in [-1, 1]")
    return rho + (1.0 - rho) * float(m_trials)


def expected_max_sharpe(
    n_independent_trials: float,
    *,
    var_sharpe: float,
    mean_sharpe: float = 0.0,
) -> float:
    """S1 Eq. (1): expected maximum Sharpe after N independent trials.

        E[max SR] ~= E[SR] + sqrt(V[SR]) * ( (1-g) * Z^-1[1 - 1/N]
                                             + g   * Z^-1[1 - 1/(N*e)] )

    with g the Euler-Mascheroni constant. This is the threshold a *genuine*
    discovery must beat: it is what pure luck produces as the best of N tries.
    """
    n = float(n_independent_trials)
    if n < 1.0:
        raise ValueError("n_independent_trials must be >= 1")
    if var_sharpe < 0:
        raise ValueError("var_sharpe must be >= 0")
    if n == 1.0:
        # Z^-1[0] is -inf; the best of one trial is just the trial.
        return float(mean_sharpe)
    z1 = _N.inv_cdf(1.0 - 1.0 / n)
    z2 = _N.inv_cdf(1.0 - 1.0 / (n * math.e))
    return mean_sharpe + math.sqrt(var_sharpe) * (
        (1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2
    )


def probabilistic_sharpe_ratio(
    sharpe: float,
    *,
    n_observations: int,
    benchmark_sharpe: float = 0.0,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """PSR: probability the true Sharpe exceeds `benchmark_sharpe`.

    `kurtosis` is NON-excess (3.0 = Normal). All Sharpes must share one
    periodicity (do not mix a daily SR with an annualised benchmark).
    """
    if n_observations < 2:
        raise ValueError("n_observations must be >= 2")
    denom_sq = (
        1.0
        - skewness * sharpe
        + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe
    )
    if denom_sq <= 0:
        raise ValueError(
            "non-positive PSR variance term; skew/kurtosis are inconsistent "
            f"with sharpe={sharpe}"
        )
    z = (sharpe - benchmark_sharpe) * math.sqrt(n_observations - 1) / math.sqrt(denom_sq)
    return _N.cdf(z)


def deflated_sharpe_ratio(
    sharpe: float,
    *,
    n_observations: int,
    n_independent_trials: float,
    var_sharpe_across_trials: float,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    mean_sharpe_across_trials: float = 0.0,
) -> float:
    """S1 Eq. (2). "DSR is a PSR where the rejection threshold is adjusted to
    reflect the multiplicity of trials." Returns P(true SR > selection-adjusted
    threshold). Conventionally DSR > 0.95 is required.
    """
    sr_star = expected_max_sharpe(
        n_independent_trials,
        var_sharpe=var_sharpe_across_trials,
        mean_sharpe=mean_sharpe_across_trials,
    )
    return probabilistic_sharpe_ratio(
        sharpe,
        n_observations=n_observations,
        benchmark_sharpe=sr_star,
        skewness=skewness,
        kurtosis=kurtosis,
    )


def min_track_record_length(
    sharpe: float,
    *,
    benchmark_sharpe: float = 0.0,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    confidence: float = 0.95,
) -> float:
    """Observations needed before `sharpe` is distinguishable from the benchmark
    at `confidence`. Returns +inf when sharpe <= benchmark (never distinguishable).
    """
    if sharpe <= benchmark_sharpe:
        return math.inf
    z = _N.inv_cdf(confidence)
    denom_sq = 1.0 - skewness * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe
    return 1.0 + denom_sq * (z / (sharpe - benchmark_sharpe)) ** 2


# ─── Familywise / FDR corrections over a fixed test family ───────────────────

@dataclass(frozen=True)
class _Adjusted:
    index: int
    p_value: float
    adjusted: float
    reject: bool


def holm_bonferroni(p_values: Sequence[float], alpha: float = 0.05) -> list[_Adjusted]:
    """Holm-Bonferroni step-down, controlling FAMILYWISE error at `alpha`.

    This is the correction the repo's own pre-registered plan commits to
    (prereg section 12, over exactly 22 tests). Returned in INPUT order, each
    carrying its adjusted p-value and reject flag.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    running = 0.0
    adjusted = [0.0] * m
    for rank, idx in enumerate(order):
        val = (m - rank) * p_values[idx]
        running = max(running, min(val, 1.0))  # enforce monotonicity
        adjusted[idx] = running
    return [
        _Adjusted(i, float(p_values[i]), adjusted[i], adjusted[i] <= alpha)
        for i in range(m)
    ]


def benjamini_hochberg(p_values: Sequence[float], q: float = 0.05) -> list[_Adjusted]:
    """Benjamini-Hochberg step-up, controlling FALSE DISCOVERY RATE at `q`.

    Less strict than Holm. Appropriate for screening many candidate strategies
    where some false positives are tolerable; NOT appropriate for a go/no-go
    deployment gate, where Holm (or DSR) is the right control.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 1.0
    for rank in range(m - 1, -1, -1):
        idx = order[rank]
        val = p_values[idx] * m / (rank + 1)
        running = min(running, min(val, 1.0))
        adjusted[idx] = running
    return [
        _Adjusted(i, float(p_values[i]), adjusted[i], adjusted[i] <= q)
        for i in range(m)
    ]


# ─── Bridging the EXISTING null band to a multiplicity-aware threshold ───────
#
# The repo's null band is an empirical distribution of profit factor under
# randomisation. Sharpe-based corrections (DSR/PSR) need a return series and do
# not apply to a PF directly. But the order-statistic correction does, exactly,
# and needs nothing but the band's own quantiles.

def null_band_threshold_quantile(n_trials: float, alpha: float = 0.05) -> float:
    """Single-run null quantile that a best-of-N result must clear.

    If N trials are independent draws from the null, then
        P(max of N <= x) = F(x)^N,
    so a familywise false-positive rate of `alpha` requires the single-run
    quantile q with 1 - q^N = alpha, i.e.

        q = (1 - alpha) ** (1 / N)

    Returns q in [0, 1]. Read it as: "you must beat the q-th percentile of the
    EXISTING null band, not its p95." Exact under independence; use
    `implied_independent_trials` first when trials are correlated.
    """
    n = float(n_trials)
    if n < 1.0:
        raise ValueError("n_trials must be >= 1")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be in (0, 1)")
    return (1.0 - alpha) ** (1.0 / n)


def null_band_family_wise_error(n_trials: float, single_run_quantile: float) -> float:
    """Inverse of the above: probability that AT LEAST ONE of N null trials
    exceeds the given single-run quantile.  1 - q^N.

    Applying this to the recorded band's p95 shows how quickly "beat the p95"
    stops meaning anything once more than a handful of variants are searched.
    """
    n = float(n_trials)
    if n < 1.0:
        raise ValueError("n_trials must be >= 1")
    if not 0.0 <= single_run_quantile <= 1.0:
        raise ValueError("single_run_quantile must be in [0, 1]")
    return 1.0 - single_run_quantile ** n


# ─── Trial accounting ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TrialCount:
    """One accounted source of multiplicity in the evidence corpus."""

    label: str
    m_trials: int
    avg_correlation: float
    note: str = ""

    @property
    def independent(self) -> float:
        return implied_independent_trials(self.m_trials, self.avg_correlation)


def total_independent_trials(counts: Iterable[TrialCount]) -> float:
    """Sum of implied independent trials across accounted sources.

    Additive because the sources are different searches, not variants of one
    another. Within a source, correlation is already handled by TrialCount.
    """
    return sum(c.independent for c in counts)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def _demo() -> None:
    print("=" * 78)
    print("HOW FAR THE EXISTING NULL BAND IS FROM A MULTIPLICITY-AWARE THRESHOLD")
    print("=" * 78)
    print("Recorded band (single random run): p5 1.02 / median 1.47 / p95 1.94")
    print("91.8% of random runs are profitable (PF > 1).\n")
    print(f"{'N indep trials':>14} | {'P(>=1 null run beats p95)':>26} | "
          f"{'quantile needed for FWER 5%':>28}")
    print("-" * 78)
    for n in (1, 5, 10, 22, 50, 100, 1_000, 41_750):
        fwe = null_band_family_wise_error(n, 0.95)
        q = null_band_threshold_quantile(n, 0.05)
        print(f"{n:>14,} | {fwe:>25.2%} | {q:>27.6%}")
    print()
    print("Read the last column as a percentile of the SAME null distribution.")
    print("At N=22 you need its 99.767th percentile, not its 95th. The band was")
    print("never measured that far into its own tail, so the threshold is not")
    print("merely unmet -- it is unmeasured.")
    print()
    print("BUT a usable interim floor already exists and needs no new computation:")
    print("  afs-evidence/null_baseline/null_analysis.json records 500 seeds with")
    print("  null_pf.max = 2.5515. That IS an empirical best-of-500 statistic.")
    print("    single pre-registered test  -> PF >= 1.94  (recorded p95)")
    print("    best of a ~500-variant search -> PF >= 2.55  (recorded max of 500)")
    print("  With 500 samples the observed max estimates ~the 99.8th percentile,")
    print("  while FWER 5% at N=500 needs the 99.99th. So 2.55 is a FLOOR on the")
    print("  correct bar, not the bar itself -- better than 1.94, still generous.")
    print()

    print("=" * 78)
    print("IMPLIED INDEPENDENT TRIALS (S1 Appendix A.3, Eq. 9)")
    print("=" * 78)
    print(f"{'M trials':>10} | {'rho_bar':>8} | {'N_hat':>12}")
    print("-" * 78)
    for m in (10, 22, 100, 41_750):
        for rho in (0.0, 0.5, 0.9, 0.99):
            print(f"{m:>10,} | {rho:>8.2f} | {implied_independent_trials(m, rho):>12,.1f}")
    print()

    print("=" * 78)
    print("EXPECTED MAXIMUM SHARPE FROM LUCK ALONE (S1 Eq. 1), V[SR]=0.25")
    print("=" * 78)
    print(f"{'N indep trials':>14} | {'E[max SR] (same periodicity)':>30}")
    print("-" * 78)
    for n in (2, 5, 10, 22, 100, 1_000, 41_750):
        print(f"{n:>14,} | {expected_max_sharpe(n, var_sharpe=0.25):>30.3f}")
    print()
    print("NOTE: DSR/PSR need a RETURN SERIES. This repo reports profit factor,")
    print("so the Sharpe functions above apply only once a per-trade or per-period")
    print("return series is exported. The null-band order-statistic correction")
    print("above needs nothing new and applies to the corpus as it stands today.")


if __name__ == "__main__":  # pragma: no cover
    _demo()
