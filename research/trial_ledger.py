"""Trial ledger: the effective search-family size M behind each evidence family.

EVIDENCE GOVERNANCE TOOLING ONLY. Read-only. Imports no runtime module.
Pairs with research/multiple_testing.py, which consumes the M values recorded here.

WHAT M IS, AND IS NOT
---------------------
M is the number of materially distinct SPECIFICATIONS that were examined and
could have influenced which result was reported. It is NOT the number of
candidates, signals, arms, trades, or observations produced by those
specifications.

The distinction is load-bearing and is the single easiest way to get this wrong:

  * shadow lane        41,750 CANDIDATES  ->  M is 22-132, not 41,750
  * orb_breakout_entry    522 "n_arms"    ->  M is 8 per instrument; n_arms
                                              literally is `len(arms)`, the
                                              detected setups
                                              (scripts/orb_breakout_entry_study.py:189)
  * 4HR retrigger          81 candidates  ->  M is 3 slippage tiers

Every count below is sourced to an artifact on disk. Where the artifact does not
support an exact count, the entry says so and is classified LOWER_BOUND or
UNRECOVERABLE. Nothing here is estimated.

CLASSES (per the task specification)
    PRE_REGISTERED    fixed before observing results
    CONFIRMATORY      same frozen hypothesis, independent/held-out evidence
    VARIANT_SEARCH    materially different specification examined after/alongside
    DIAGNOSTIC        not promotion-eligible, not part of the selection family
    UNKNOWN           unrecoverable

CONFIDENCE
    KNOWN         exact M enumerable from a committed/preserved artifact
    LOWER_BOUND   floor proven; the true M may be larger
    UNRECOVERABLE M cannot be determined from surviving artifacts
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Literal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

Confidence = Literal["KNOWN", "LOWER_BOUND", "UNRECOVERABLE"]
Klass = Literal[
    "PRE_REGISTERED", "CONFIRMATORY", "VARIANT_SEARCH", "DIAGNOSTIC", "UNKNOWN"
]


@dataclass(frozen=True)
class Family:
    key: str
    reported_result: str
    frozen_hypothesis: bool
    klass: Klass
    m_min: int                     # minimum PROVEN specifications searched
    m_max: int | None              # maximum defensible, None if unbounded
    confidence: Confidence
    family_definition: str         # what one "specification" is here
    selected_after_comparison: bool
    evidence_refs: tuple[str, ...]
    observations: str = ""         # the candidate/arm/trade count, if it exists
    note: str = ""
    classification_changes: bool = False


LEDGER: list[Family] = [

    # ── Shadow lane — the family the M-vs-observations distinction turns on ──
    Family(
        key="shadow_lane_null_test",
        reported_result="pooled PF 0.944 vs flipped-null 0.863; 3 of 22 cells 'pass'",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=22, m_max=132, confidence="LOWER_BOUND",
        family_definition="one detector family x one instrument (11 x 2 = 22); the "
                          "geometry sweep re-ran all 22 at target R in "
                          "{0.5, 1, 1.5, 2, 3}, giving at most 22 + 110 = 132",
        selected_after_comparison=True,
        observations="41,750 journaled candidates; 32,326 resolved",
        evidence_refs=(
            "memory project-shadow-families-null-tested-2026-07-31 "
            "('Cells passing ... 3 of 22'; geometry sweep 0.5/1/1.5/2/3 R)",
            "strategy/shadow_setups.py: 9 detector fns -> 14 RISK_MATRIX labels",
            "logs/replay_corpus_v1/*/journal_*.jsonl",
        ),
        note="THE headline correction. 41,750 is an OBSERVATION count. The three "
             "'passing' cells were selected after comparing all 22, which is "
             "precisely a best-of-M selection. The harness scripts "
             "(shadow_null.py / shadow_stats.py / shadow_geom.py) were written to "
             "a session scratchpad and are GONE, so the exact sweep cardinality "
             "cannot be re-enumerated from code -- hence LOWER_BOUND, not KNOWN.",
    ),
    Family(
        key="shadow_tranche2",
        reported_result="per-cell half1/half2 signal counts and PF",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=20, m_max=20, confidence="KNOWN",
        family_definition="instrument x strategy x session filter = 2 x 5 x 2",
        selected_after_comparison=True,
        observations="signals_raw per cell",
        evidence_refs=("scripts/strat_shadow_tranche2_results.json (20 top-level cells)",),
    ),
    Family(
        key="shadow_gate_choke_sweep_622d",
        reported_result="WEAK_BAR_CLOSE gate choke classification",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=1, m_max=None, confidence="UNRECOVERABLE",
        family_definition="one gate examined for choke effect",
        selected_after_comparison=True,
        observations="3,182 structure-present rows; 1,274 excluded",
        evidence_refs=("logs/shadow_gate_choke_sweep_622d.json (by_gate has 1 key)",),
        note="Only WEAK_BAR_CLOSE survives in the artifact. Whether other gates "
             "were examined and dropped is not recorded. UNRECOVERABLE.",
    ),

    # ── Strat family ──
    Family(
        key="mes_strat_212",
        reported_result="PF 1.385, 101 trades, +$1,223.75 (project best-ever)",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=8, m_max=11, confidence="LOWER_BOUND",
        family_definition="one preserved run directory = one specification "
                          "(entry rule x target rule x slippage tier x gate set)",
        selected_after_comparison=True,
        observations="201 canonical isolated population",
        evidence_refs=(
            "afs-evidence/mes_strat_212_closed_2026-07-28/runs/ -- 9 dirs: "
            "baseline_canonical_touch_entry_201, close_confirmed_mixed_target_slip{1,2,3}, "
            "close_confirmed_nobreaker_diag, unified_2r_slip{1,2,3}, "
            "validation_gate_mintarget15",
            "afs-evidence/null_baseline/null_analysis.json (500 seeds)",
        ),
        note="8 promotion-eligible (close_confirmed_nobreaker_diag is DIAGNOSTIC). "
             "Upper bound adds the 3 successive corrected re-scorings below.",
    ),
    Family(
        key="strat_212_122_canonical",
        reported_result="canonical evidence, rule BROKEN",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=4, m_max=15, confidence="LOWER_BOUND",
        family_definition="strategy x instrument = 2 x 2, re-scored across 3 "
                          "successive code revisions plus a 3-tier slippage sensitivity",
        selected_after_comparison=True,
        observations="see per_instrument_per_strategy breakdowns",
        evidence_refs=(
            "scripts/strat_212_122_canonical_evidence_results.json",
            "scripts/..._results_pre_pr338_superseded.json",
            "scripts/..._results_pre_pr339_partially_corrected.json",
            "scripts/strat_212_122_slippage_sensitivity_results.json",
        ),
        note="The 3 revisions are reruns AFTER eligibility logic changed, which the "
             "task specification says to count. They are corrections rather than "
             "market hypotheses, so they inflate the upper bound, not the floor.",
    ),

    # ── ORB family ──
    Family(
        key="mnq_4hr_matrix",
        reported_result="24/24 compatibility cells; stop cap is the sole binding constraint",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=24, m_max=25, confidence="KNOWN",
        family_definition="stop tier {0,120,160,200,240,320} x strong{on,off} x "
                          "trend{on,off} = 6 x 2 x 2",
        selected_after_comparison=True,
        observations="per-cell trades.json",
        evidence_refs=("afs-evidence/mnq_4hr_matrix/cells/ -- 24 tier*_strong*_trend*_s1 dirs",),
        note="Upper bound adds the separately-reported uncapped variant "
             "(PF 4.78, n=12, already OVERFIT + INCOMPATIBLE).",
    ),
    Family(
        key="orb_breakout_canonical",
        reported_result="WAIT; runner vs static under 1-4 tick slippage",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=8, m_max=8, confidence="KNOWN",
        family_definition="exit mode {static, runner} x slippage {1,2,3,4} ticks",
        selected_after_comparison=True,
        observations="see full_breakdowns",
        evidence_refs=("scripts/orb_breakout_canonical_evidence_results.json "
                       "(slippage_sweep: 8 keys)",),
    ),
    Family(
        key="orb_breakout_entry_study",
        reported_result="entry-cap comparison per instrument",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=16, m_max=16, confidence="KNOWN",
        family_definition="cap {2,4,8,unbounded} x exit {static,runner} x "
                          "instrument {MES,MNQ}",
        selected_after_comparison=True,
        observations="n_arms = 522 (MES) / 316 (MNQ) DETECTED SETUPS",
        evidence_refs=(
            "scripts/orb_breakout_entry_study_results.json (8 cells per instrument)",
            "scripts/orb_breakout_entry_study.py:189 -- `{'n_arms': len(arms)}`",
        ),
        note="Textbook observations-vs-specifications trap: n_arms reads like a "
             "trial count and is not one.",
    ),
    Family(
        key="orb_reclaim_v4r_and_counterfactuals",
        reported_result="V4-R WAIT; #354/#355/#356 counterfactuals REJECT faster entry",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=4, m_max=None, confidence="LOWER_BOUND",
        family_definition="V4-R study + 3 distinct counterfactual specifications "
                          "(market entry @165, stop-market level, causal 5m triggers)",
        selected_after_comparison=True,
        observations="per-study",
        evidence_refs=(
            "archive/claude-orb-reclaim-v4r-study-pr368-2026-08-08",
            "archive/claude-market-entry-counterfactual-165-2026-07-27",
            "archive/claude-stop-market-level-counterfactual-2026-07-27",
            "archive/claude-orb-reclaim-causal-5m-triggers-2026-07-27",
        ),
    ),

    # ── VWAP family ──
    Family(
        key="vwap_hold",
        reported_result="Hold=UNPROVEN; IOC-close chosen as canonical reference price",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=12, m_max=36, confidence="LOWER_BOUND",
        family_definition="entry arm {ioc_open, ioc_close, market} x exit "
                          "{static, runner, partial_2ct, exit_bar_diag} = 12; "
                          "re-scored over 3 populations (blended 348 / ny_only / non_ny)",
        selected_after_comparison=True,
        observations="348-arm frozen population (sha256-fingerprinted)",
        evidence_refs=(
            "scripts/vwap_hold_evidence_package_results.json (matrix: 3 arms x 4 cells)",
            "scripts/vwap_hold_ioc_close_concentration_results.json (3 populations)",
            "scripts/vwap_hold_paired_fill_comparison_results.json (348 pairs)",
            "scripts/vwap_hold_isolated_fill_model_comparison_results.json",
        ),
        note="An operator decision explicitly SELECTED ioc_close from among the "
             "three entry arms after seeing results. That is a textbook "
             "selected-after-comparison event and must be corrected as such.",
    ),
    Family(
        key="vwap_reclaim_canonical",
        reported_result="WAIT -- fails both-halves-positive walk-forward",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=3, m_max=3, confidence="KNOWN",
        family_definition="slippage tier {1,2,3} ticks",
        selected_after_comparison=True,
        observations="n=50 corpus v1 comparator",
        evidence_refs=("scripts/vwap_reclaim_canonical_evidence_results.json "
                       "(slippage_tiers: 3 keys)",),
        note="Slippage tiers are ROBUSTNESS checks on one hypothesis, not three "
             "hypotheses. Counted, but they inflate M least defensibly of any "
             "entry in this ledger. Flagged for the operator.",
    ),

    # ── Execution / fill-model family ──
    Family(
        key="execution_mode_corpus_comparison",
        reported_result="NO modeled execution mode makes the frozen system profitable",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=4, m_max=4, confidence="KNOWN",
        family_definition="entry fill model {ioc_limit, market, marketable_limit, "
                          "stop_market}; stop_limit explicitly NOT MODELED",
        selected_after_comparison=True,
        observations="corpus v1, 626 files",
        evidence_refs=("scripts/execution_mode_corpus_comparison_results.json "
                       "(meta.arms: 4 modeled + 1 declined)",),
        note="Exemplary: the declined arm is named and justified in the artifact "
             "instead of silently omitted. This is the standard the rest should meet.",
    ),
    Family(
        key="ioc_baseline_622d",
        reported_result="legacy edge is a fill-model artifact",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=8, m_max=8, confidence="KNOWN",
        family_definition="entry {market, ioc_limit} x exit {static, runner} x "
                          "breaker {as_configured, off}",
        selected_after_comparison=True,
        observations="622-day corpus",
        evidence_refs=(
            "scripts/ioc_baseline_622d_results_as_configured.json",
            "scripts/ioc_baseline_622d_results_breaker_off.json",
        ),
    ),
    Family(
        key="corrected_ioc_corpus_pr346",
        reported_result="PF 0.752958 after commission; edge BROKEN under honest IOC",
        frozen_hypothesis=True,
        klass="CONFIRMATORY",
        m_min=1, m_max=1, confidence="KNOWN",
        family_definition="ONE frozen specification: entry_fill_model='ioc_limit' "
                          "over the pinned corpus and pinned risk_rules.yaml",
        selected_after_comparison=False,
        observations="165 attempts",
        evidence_refs=(
            "scripts/corrected_ioc_corpus_evidence.py (fail-closed on 626 files, "
            "canonical slippage/tolerance, risk_rules hash)",
            "scripts/corrected_ioc_corpus_results.json",
            "regenerated 2026-08-08 from 69ec77f: 165/165 rows identical",
        ),
        note="M = 1, and this is the ONLY family in the ledger where that is true. "
             "One hypothesis, frozen in advance, one run, byte-verified inputs, "
             "reproduced exactly. No multiple-testing correction is owed. Its "
             "negative verdict is the most trustworthy result in the corpus.",
    ),
    Family(
        key="entry_detached_sweep_622d",
        reported_result="UNDERFILLING_NOT_ENTRY_DRIVEN",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=3, m_max=36, confidence="LOWER_BOUND",
        family_definition="fill model {market, ioc_limit, stop_market}; reported "
                          "combined, but broken out by 6 strategies x 2 instruments",
        selected_after_comparison=True,
        observations="5,268 cases",
        evidence_refs=("logs/entry_detached_sweep_622d.json",),
    ),

    # ── Exit / stop family ──
    Family(
        key="stop_rule_sweep",
        reported_result="RUNNER is the lever, not breakeven",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=14, m_max=14, confidence="KNOWN",
        family_definition="rule {static, be_0.5R, be_1R, be_1.5R, trail_1R, "
                          "run_trail_1R, run_trail_0.5R} x instrument {MES, MNQ}",
        selected_after_comparison=True,
        observations="per-rule pnl lists",
        evidence_refs=("scripts/stop_rule_sweep.py:126 -- RULES list of 7",),
    ),
    Family(
        key="strategy_matrix_tranche1",
        reported_result="per-strategy exit/stop/filter matrix",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=60, m_max=60, confidence="KNOWN",
        family_definition="strategy (6 MNQ + 4 MES) x spec {static, runner, partial, "
                          "stop_2x, stop_0.5x, filters_on_runner}; the *_after_cost "
                          "keys are the SAME spec reported twice and are not counted",
        selected_after_comparison=True,
        observations="n_arms per strategy",
        evidence_refs=("scripts/strategy_matrix_tranche1_results.json",),
        note="Largest single KNOWN family in the ledger.",
    ),
    Family(
        key="four_hr_retrigger_stop_study",
        reported_result="4HR re-trigger classified BROKEN",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=6, m_max=6, confidence="KNOWN",
        family_definition="instrument {MNQ, MES} x slippage {1,2,3} ticks",
        selected_after_comparison=True,
        observations="81 (MNQ) / 76 (MES) candidates detected",
        evidence_refs=("scripts/four_hr_retrigger_stop_study_results.json "
                       "(instruments[*].variants: 3 slippage tiers)",),
    ),

    # ── Study family ──
    Family(
        key="mnq_entry_refresh_study",
        reported_result="1 fill / 63 arms, expectancy -26.98; lane never ran live",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=20, m_max=20, confidence="KNOWN",
        family_definition="refresh policy (10 variants: static_reject, translate x "
                          "cap{8t,16t,32t,0.25R,0.5R,1.0R,unbounded}, structural_minrr, "
                          "confirm5m_16t) x exit {static, runner}",
        selected_after_comparison=True,
        observations="63 arms",
        evidence_refs=("scripts/mnq_entry_refresh_results.json (cells: 20 keys)",),
    ),
    Family(
        key="structural_level_5m_study",
        reported_result="CLOSED, REJECTED",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=10, m_max=10, confidence="KNOWN",
        family_definition="setup type {break_and_retest, failed_breakdown, "
                          "failed_reclaim, reclaim, rejection} x entry mode "
                          "{momentum_close, retest}",
        selected_after_comparison=True,
        observations="considered/accepted/resolved counts",
        evidence_refs=("scripts/structural_level_5m_results.json "
                       "(by_setup_type: 5, by_entry_mode: 2)",),
    ),
    Family(
        key="mnq_5m_impulse_pullback_continuation",
        reported_result="continuation study across RR targets",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=3, m_max=3, confidence="KNOWN",
        family_definition="target RR {1.5, 2.0, 3.0}",
        selected_after_comparison=True,
        observations="resolved_rows_sample per RR",
        evidence_refs=("scripts/mnq_5m_impulse_pullback_continuation_results.json",),
    ),
    Family(
        key="missed_move_gate_sweep_622d",
        reported_result="per-instrument missed-move thresholds (MES 15.0, MNQ 60.0)",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=2, m_max=None, confidence="UNRECOVERABLE",
        family_definition="one threshold per instrument in the artifact",
        selected_after_comparison=True,
        observations="9,779 windows; 39,112 classifications",
        evidence_refs=("logs/missed_move_gate_sweep_622d.json (thresholds: 2 keys)",),
        note="Only the CHOSEN thresholds survive in the artifact. A sweep that "
             "reports two round numbers (15.0 / 60.0) almost certainly evaluated "
             "more, but the candidate grid is not recorded. UNRECOVERABLE.",
    ),
    Family(
        key="miyagi_12hr",
        reported_result="Miyagi BROKEN (4/6 global gates were parity defects)",
        frozen_hypothesis=False,
        klass="VARIANT_SEARCH",
        m_min=4, m_max=None, confidence="LOWER_BOUND",
        family_definition="executable stop tier (4 tiers)",
        selected_after_comparison=True,
        observations="~45,761 distinct shadow candidates per tier (byte-identical "
                     "across tiers -- shadow brackets do not depend on the stop tier)",
        evidence_refs=(
            "logs/replay_miyagi_12hr_causal_stop/ (4 tiers, sha d8248a16)",
            "memory project-shadow-families-null-tested-2026-07-31",
        ),
        note="The 4 tiers produce byte-identical shadow outcomes, so for the SHADOW "
             "question they are 1 specification, not 4. For the EXECUTABLE question "
             "they are 4. Which count applies depends on the claim being made.",
    ),

    # ── The one properly pre-registered family ──
    Family(
        key="prereg_context_permission_layer",
        reported_result="NONE -- no results exist",
        frozen_hypothesis=True,
        klass="PRE_REGISTERED",
        m_min=22, m_max=22, confidence="KNOWN",
        family_definition="12 univariate features (F1-F12) + 10 interactions "
                          "(I1-I10), fixed in advance; section 11 caps the "
                          "interaction count explicitly",
        selected_after_comparison=False,
        observations="none -- the study does not appear to have run",
        evidence_refs=(
            "docs/prereg-context-permission-layer-analysis-plan-2026-07-16.md "
            "sections 7, 11, 12, 13",
        ),
        note="The ONLY family whose M was fixed before observing results, and the "
             "only one with no results. Its section 13 fences everything outside "
             "the 22 as EXPLORATORY and ineligible for gate candidacy. M=22 is "
             "legitimate HERE and must not be borrowed by any other family.",
    ),

    # ── Reference distribution, not a search family ──
    Family(
        key="null_baseline_500_seeds",
        reported_result="null band p5 1.02 / median 1.47 / p95 1.94 / max 2.55",
        frozen_hypothesis=True,
        klass="DIAGNOSTIC",
        m_min=0, m_max=0, confidence="KNOWN",
        family_definition="500 seeded direction-flip controls of ONE specification; "
                          "this DEFINES the reference distribution rather than "
                          "searching for a result",
        selected_after_comparison=False,
        observations="500 seeds",
        evidence_refs=("afs-evidence/null_baseline/null_analysis.json "
                       "(null_seeds: 500, null_pf.max: 2.5515)",),
        note="M=0 by construction: no result was selected from these runs. Their "
             "MAXIMUM (2.5515) is the best-of-500 statistic that other families' "
             "results should be measured against.",
    ),
]


# ─── Aggregation ─────────────────────────────────────────────────────────────

def selection_families() -> list[Family]:
    """Families whose reported result was chosen from a compared set."""
    return [f for f in LEDGER if f.klass == "VARIANT_SEARCH"]


def totals() -> dict:
    sel = selection_families()
    return {
        "families_total": len(LEDGER),
        "families_selection": len(sel),
        "m_min_total": sum(f.m_min for f in sel),
        "m_max_total_bounded": sum((f.m_max if f.m_max is not None else f.m_min) for f in sel),
        "unbounded_families": [f.key for f in sel if f.m_max is None],
        "by_confidence": {
            c: [f.key for f in LEDGER if f.confidence == c]
            for c in ("KNOWN", "LOWER_BOUND", "UNRECOVERABLE")
        },
    }


def _print_table() -> None:
    hdr = (f"{'family':<38}{'class':<15}{'M min':>6}{'M max':>11}  "
           f"{'confidence':<14}{'selected?':<9}")
    print(hdr)
    print("-" * len(hdr))
    for f in LEDGER:
        mx = "unbounded" if f.m_max is None else str(f.m_max)
        print(f"{f.key:<38}{f.klass:<15}{f.m_min:>6}{mx:>11}  "
              f"{f.confidence:<14}{'yes' if f.selected_after_comparison else 'no':<9}")


def main() -> int:
    print("=" * 90)
    print("TRIAL LEDGER -- effective search-family size M")
    print("=" * 90)
    _print_table()

    t = totals()
    print()
    print("=" * 90)
    print("TOTALS ACROSS SELECTION FAMILIES (VARIANT_SEARCH only)")
    print("=" * 90)
    print(f"  families in ledger                : {t['families_total']}")
    print(f"  families that selected a result   : {t['families_selection']}")
    print(f"  PROVEN FLOOR on total M           : {t['m_min_total']}")
    print(f"  defensible bounded upper on M     : {t['m_max_total_bounded']}")
    print(f"  families with NO upper bound      : {', '.join(t['unbounded_families'])}")
    print()
    print(f"  KNOWN         ({len(t['by_confidence']['KNOWN']):>2}): "
          f"{', '.join(t['by_confidence']['KNOWN'])}")
    print(f"  LOWER_BOUND   ({len(t['by_confidence']['LOWER_BOUND']):>2}): "
          f"{', '.join(t['by_confidence']['LOWER_BOUND'])}")
    print(f"  UNRECOVERABLE ({len(t['by_confidence']['UNRECOVERABLE']):>2}): "
          f"{', '.join(t['by_confidence']['UNRECOVERABLE'])}")

    print()
    print("=" * 90)
    print("WHAT THE FLOOR IMPLIES")
    print("=" * 90)
    try:
        from research.multiple_testing import (
            null_band_family_wise_error, null_band_threshold_quantile,
            implied_independent_trials,
        )
    except ImportError:  # pragma: no cover
        print("  (run from the repo root so research.multiple_testing imports)")
        return 0

    m = t["m_min_total"]
    for rho, label in ((0.0, "independent"), (0.5, "moderately correlated"),
                       (0.9, "highly correlated")):
        n = implied_independent_trials(m, rho)
        fwe = null_band_family_wise_error(n, 0.95)
        q = null_band_threshold_quantile(n, 0.05)
        print(f"  rho={rho:.1f} ({label:<21}) -> N_hat={n:>8,.1f}  "
              f"P(a null run beats p95)={fwe:>7.2%}  need q={q:.6%}")
    print()
    print("  The recorded null band tops out at its p95 (1.94) and its observed")
    print("  max over 500 seeds (2.55). Under EVERY correlation assumption above,")
    print("  the required quantile is past both. 2.55 is therefore a floor on the")
    print("  promotion bar, and a generous one.")
    print()
    print("  This floor EXCLUDES: shadow_gate_choke, missed_move_gate,")
    print("  orb_reclaim counterfactuals, and miyagi_12hr, all of which are")
    print("  unbounded above. The true M is larger than any number printed here.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
