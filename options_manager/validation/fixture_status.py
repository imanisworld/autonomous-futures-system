"""options_manager/validation/fixture_status.py

Advisory-only fixture-status vocabulary and static candidate inventory
-- Increment 25B. A separate concern from both RealSetupFixture
(base.py/fixtures.py) and ManagementCase (management_cases.py): neither
of those models has a place to record "is this real trade candidate
usable as a scanner-identification proof yet, and if not, why not and
what would it take?" That question spans real-trade status, management
quality, and scanner-proof readiness all at once, and conflating any two
of those has been the recurring failure mode this module exists to stop:

- A trade can be real and profitable without its management being a
  clean lesson (see ManagementCase's own evidence_status split).
- A trade can be a clean, well-managed real trade without its *setup*
  being a usable scanner-identification proof (a documented trigger/
  invalidation/target the scanner could have evaluated in advance).
- A recalled trade can turn out, on reconciliation against a real
  independent record, to not match its own description at all.

`FixtureStatus` names these outcomes explicitly so a candidate is never
silently treated as "verified" in one sense because it is verified in
another. `FixtureCandidate` is a static, hand-authored record of one
ticker/window's current status -- nothing here is computed from candle
data, broker calls, or the scanner path; every field is a caller-supplied
description of what has already been established elsewhere (see each
candidate's `notes` for where).

This module does not decide when a candidate should be promoted to a new
status -- promotion is a human call, made and recorded by editing
`FIXTURE_CANDIDATE_INVENTORY` directly, the same way ManagementCase's
`classification`/`evidence_status` fields are hand-set rather than
derived. `promotion_requirements` documents what would need to be true
for the *next* status change, not a trigger this module watches for.

Performs no I/O of any kind: no candle fetch, no option-chain fetch, no
market-data fetch, no broker call, no order placement, no execution, no
alert sending, no file access at runtime, no network calls, no MCP calls.
Does not import replay/replay_engine.py, the live context.market_context
loader, alert_ranker, options_companion, execution, webhook, broker
systems, options_manager.scanner, or risk/risk_engine.py. Nothing in
options_manager.scanner, execution, or webhook imports this module --
fixture status is a labeling/reporting layer, not an input to any live
decision path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FixtureStatus(str, Enum):
    """A trade candidate's current standing as a *scanner-identification
    proof fixture* -- distinct from whether it was a real trade, whether
    it was profitable, or whether its management was sound. Being a str
    subclass, values compare and serialize as plain strings (e.g.
    `FixtureStatus("pending_proof_fixture") == FixtureStatus.PENDING_PROOF_FIXTURE`,
    `json.dumps(status)` needs no custom encoder)."""

    CLEAN_COMPLETE_FIXTURE = "clean_complete_fixture"
    PENDING_PROOF_FIXTURE = "pending_proof_fixture"
    SPECIAL_CASE_FIXTURE = "special_case_fixture"
    MANAGEMENT_CASE = "management_case"
    SCALP_NOISE = "scalp_noise"
    INCOMPLETE = "incomplete"
    REJECT = "reject"


@dataclass(frozen=True, kw_only=True)
class FixtureCandidate:
    """One ticker/window's current fixture-status record. `status` is
    the human's own current call -- this module has no classifier
    underneath it. `proof_confirmed` and `proof_missing` are the two
    halves of what is/isn't independently established; at least one must
    be non-empty (a candidate with nothing confirmed and nothing named as
    missing has not actually been looked at). `reason_not_first_proof` is
    required for every status except CLEAN_COMPLETE_FIXTURE, since that
    is the one status for which "why not" has no answer to give."""

    ticker: str
    window: str
    status: FixtureStatus
    best_future_use: str
    proof_confirmed: tuple[str, ...] = ()
    proof_missing: tuple[str, ...] = ()
    reason_not_first_proof: str = ""
    promotion_requirements: tuple[str, ...] = ()
    notes: str = ""


@dataclass(kw_only=True)
class FixtureCandidateSummary:
    """Deterministic rollup of a fixture-candidate inventory. Purely
    computed from the candidates' own fields -- no side effects."""

    total_candidates: int
    counts_by_status: dict[str, int] = field(default_factory=dict)


def _hood_candidate() -> FixtureCandidate:
    return FixtureCandidate(
        ticker="HOOD",
        window="2026-06-12/2026-06-15",
        status=FixtureStatus.PENDING_PROOF_FIXTURE,
        best_future_use=(
            "Support-hold continuation/pullback-reclaim scanner-proof "
            "fixture, if the planned-level source surfaces."
        ),
        proof_confirmed=(
            "fills confirmed: 1x $100C exp 2026-06-18, entry $1.22, exit "
            "$3.20, +$198/+162%",
            "5-minute candle sequence fully reconstructed: morning spike "
            "to $96.10, flush to $90.22, reclaim through $92, entry at "
            "$93.3, weekend gap, Monday exit through $100 intrabar",
            "no unrelated same-ticker trades in the account",
            "recalled levels (92/95/100) line up with the reconstructed "
            "candles: 92 held as support post-entry, 95 hit via Monday "
            "premarket gap, 100 touched intrabar in the exact exit bar",
        ),
        proof_missing=(
            "a dated source (screenshot, note, chart, alert) showing "
            "92/95/100 were the planned levels before or during the trade, "
            "not a post-hoc match to real candles",
            "confirmation of entry-rule intent: support-hold continuation "
            "vs. 2u continuation vs. reclaim",
        ),
        reason_not_first_proof=(
            "the levels fit the candles almost exactly, but 'levels fit in "
            "hindsight' and 'levels were the stated plan' are different "
            "claims -- without a contemporaneous source this is a "
            "plausible reconstruction, not a proof"
        ),
        promotion_requirements=(
            "any dated artifact predating or concurrent with the "
            "2026-06-12 entry that names 92/95/100 as planned levels",
        ),
        notes=(
            "Working entry-rule framing: support-hold continuation/"
            "pullback reclaim. A separate, unrelated HOOD $70P exp "
            "2026-05-01 trade (BTO 2026-04-30, STC 2026-05-01, net ~-$30) "
            "also exists in the May 2026 broker statement -- it is a "
            "distinct position from this $100C exp 2026-06-18 fixture "
            "window and must not be conflated with it."
        ),
    )


def _ebay_candidate() -> FixtureCandidate:
    return FixtureCandidate(
        ticker="EBAY",
        window="2026-04-30/2026-05-01",
        status=FixtureStatus.SPECIAL_CASE_FIXTURE,
        best_future_use=(
            "Management-case test for distinguishing a real target hit from "
            "a post-exit print, and for scaled-exit P&L handling. Also "
            "tracked as the active `ebay_management_case` in "
            "management_cases.py (corrected by PR #221)."
        ),
        proof_confirmed=(
            "fills confirmed: 5x $105C exp 2026-05-15, entry $1.18, four "
            "partial exits ($538/$421/$455/$470), realized +$1,884",
            "candle reconstruction complete: PDL=$100.20, ~55-minute "
            "whipsaw reclaim, target 1 ($106) confirmed during RTH before "
            "exits, target 2 ($108) never confirmed during RTH -- only "
            "post-market, after the position was already closed",
            "corrected in options_manager/validation/management_cases.py "
            "(PR #221): entry premium and position size fixed, target-2 "
            "claim narrowed to what actually confirmed live",
        ),
        proof_missing=(),
        reason_not_first_proof=(
            "the PDL reclaim was not a single clean trigger candle -- price "
            "closed back below PDL multiple times in the 55 minutes before "
            "the real entry -- and target 2 was never live-confirmed before "
            "exit, so this tests convention handling (whipsaw triggers, "
            "post-exit prints) more than clean scanner-identification skill"
        ),
        promotion_requirements=(),
        notes="Nothing will promote this to CLEAN_COMPLETE_FIXTURE -- the whipsaw is a permanent structural feature of the real trade, not a data gap.",
    )


def _amd_candidate() -> FixtureCandidate:
    return FixtureCandidate(
        ticker="AMD",
        window="2026-02-05/2026-02-06",
        status=FixtureStatus.SPECIAL_CASE_FIXTURE,
        best_future_use=(
            "Regression test for premarket-trigger / RTH-already-through-"
            "target convention handling."
        ),
        proof_confirmed=(
            "candle reconstruction complete: invalidation touch at exactly "
            "$187.00 (2026-02-05 16:55 ET), premarket reclaim candle "
            "(2026-02-06 04:00 ET, close $193.01), full RTH decision "
            "sequence into the 09:30 bar, target ($198) hit inside the "
            "first RTH bar",
        ),
        proof_missing=(),
        reason_not_first_proof=(
            "the trigger fired premarket; by the official RTH decision bar "
            "(09:30), price had already traded through and closed above the "
            "target -- a same-bar trigger-and-target-hit that tests "
            "convention handling, not scanner skill"
        ),
        promotion_requirements=(),
        notes="Not promotable as-is; this is permanently a convention-edge-case regression fixture by design.",
    )


def _orcl_candidate() -> FixtureCandidate:
    return FixtureCandidate(
        ticker="ORCL",
        window="unknown",
        status=FixtureStatus.INCOMPLETE,
        best_future_use=(
            "Scanner-identification proof candidate, only if a real source "
            "packet (score/grade/thesis/date) is ever located."
        ),
        proof_confirmed=(
            "real membership on the account's \"BPCWS\" Robinhood watchlist",
        ),
        proof_missing=(
            "trigger, invalidation, target levels",
            "any Signa score/grade or Minervini count -- no such scoring "
            "system exists anywhere in this repo or account (checked: no "
            "'Minervini' hits at all; 'Signa' only matches the substring "
            "'signal' in unrelated webhook code)",
            "date/time of identification, contract, fill data",
        ),
        reason_not_first_proof=(
            "watchlist membership only proves attention, not a recorded "
            "setup -- no scanner output, alert log, or dated thesis exists "
            "anywhere checked"
        ),
        promotion_requirements=(
            "the actual source of the claimed Signa/Minervini scoring (a "
            "named tool, export, or screenshot) -- not yet located",
        ),
        notes=(
            "Statement activity for ORCL exists across the Jan-May 2026 "
            "broker statements but has not yet been analyzed or "
            "reconciled against the recalled claims above. NOT_RECONCILED."
        ),
    )


def _fitb_candidate() -> FixtureCandidate:
    return FixtureCandidate(
        ticker="FITB",
        window="2026-05-04/2026-05-08",
        status=FixtureStatus.SPECIAL_CASE_FIXTURE,
        best_future_use=(
            "Management case test for early rule-based invalidation "
            "followed by a recovery and +30% MFE before later failure -- "
            "not a first scanner-identification proof fixture."
        ),
        proof_confirmed=(
            "May 2026 broker statement confirms FITB $50C exp 2026-06-18: "
            "BTO 2026-05-04 1x $2.20 + 1x $2.10 ($430.08 total cost), STC "
            "2026-05-08 1x $1.75 + 1x $1.76 ($350.88 total proceeds), net "
            "-$79.20",
            "exact order timestamps recovered from live order history: "
            "buys at 9:34:30 ET / 9:55:04 ET on 2026-05-04, sells at "
            "2:28:59 ET / 2:34:45 ET on 2026-05-08",
            "underlying traded within ~15 cents of $50 at both entry fills",
            "$49.50 invalidation level breached intraday same-day as entry "
            "(2026-05-04 low $49.19), then reclaimed the next session and "
            "held firmly above $50 for a full day (2026-05-06) before "
            "failing again",
            "option premium (from actual contract price bars): MFE +$0.65 "
            "(+30%, 2026-05-06), MAE -$0.60 (-28%, 2026-05-08 pre-exit), "
            "final exit realized at roughly -18% blended",
        ),
        proof_missing=(
            "no dated source for the recalled institutional call-flow/OI "
            "claim -- no historical OI/flow data source exists in the "
            "toolset used to check this",
            "no target was ever defined or reached",
        ),
        reason_not_first_proof=(
            "the $49.50 invalidation was breached same-day as entry, but "
            "price then recovered and produced a +30% MFE before failing "
            "again days later -- a same-day invalidation breach followed "
            "by recovery, not a clean single trigger-to-invalidation loser"
        ),
        promotion_requirements=(),
        notes=(
            "BROKER_VERIFIED_LOSER / RECONSTRUCTED. Best modeled going "
            "forward as a management_cases.py case (early-invalidation-"
            "then-recovery decision logic), not pursued for "
            "CLEAN_COMPLETE_FIXTURE."
        ),
    )


def _bac_candidate() -> FixtureCandidate:
    return FixtureCandidate(
        ticker="BAC",
        window="2026-05-01/2026-05-04",
        status=FixtureStatus.INCOMPLETE,
        best_future_use="Low-priority candidate pending candle-level reconstruction.",
        proof_confirmed=(
            "May 2026 broker statement confirms BAC $55C exp 2026-05-08: "
            "BTO 2026-05-01 3x $0.12 ($36.12 debit), STC 2026-05-04 2x "
            "$0.05 + 1x $0.05 ($14.84 total proceeds), net -$21.28",
        ),
        proof_missing=(
            "candle-level reconstruction (underlying spot/range, 5-minute "
            "context, MFE/MAE) -- not yet performed",
        ),
        reason_not_first_proof="broker-verified real loser, but not yet candle-reconstructed",
        promotion_requirements=(
            "the same candle reconstruction pass already run for FITB",
        ),
        notes="BROKER_VERIFIED_LOSER / PENDING_CANDLE_RECONSTRUCTION.",
    )


def _spxw_candidate() -> FixtureCandidate:
    return FixtureCandidate(
        ticker="SPXW",
        window="unknown",
        status=FixtureStatus.SCALP_NOISE,
        best_future_use="Not recommended for the scanner-identification proof lane.",
        proof_confirmed=(
            "real, extremely high-volume 0DTE activity confirmed in the "
            "account (dozens of same-session closing trades per day)",
        ),
        proof_missing=("any single clean trigger-to-target trade",),
        reason_not_first_proof=(
            "same-session, high-frequency, multi-leg 0DTE scalping is a "
            "structurally different trading style than a level-trigger "
            "fixture needs"
        ),
        promotion_requirements=(),
        notes="",
    )


def _nvda_candidate() -> FixtureCandidate:
    return FixtureCandidate(
        ticker="NVDA",
        window="2026-07-07/2026-07-08",
        status=FixtureStatus.SCALP_NOISE,
        best_future_use="Not pursued for the scanner-identification proof lane.",
        proof_confirmed=(
            "three distinct real positions identified on 2026-07-07/08: "
            "$200C 0DTE (9-minute hold), $195P 0DTE (opened, unresolved at "
            "time of check), $220C exp 2026-07-17 (~1-day hold)",
        ),
        proof_missing=(
            "reconciliation of a separately recalled '192ish/107/198/200' "
            "description against any of the actual fills -- never matched",
        ),
        reason_not_first_proof="dominant pattern is same-day/minutes-long 0DTE scalping",
        promotion_requirements=(),
        notes="",
    )


def _nok_candidate() -> FixtureCandidate:
    return FixtureCandidate(
        ticker="NOK",
        window="2026-05-15/2026-06-12",
        status=FixtureStatus.MANAGEMENT_CASE,
        best_future_use=(
            "Management-case test for scaled-exit-over-weeks behavior. "
            "Tracked as the active `nok_management_case` in "
            "management_cases.py (corrected by PR #221)."
        ),
        proof_confirmed=(
            "fills confirmed: 3x $14C exp 2026-06-18, entry $1.27 average, "
            "3 separate exits over 5 weeks blending to ~$0.93, net -$101",
            "corrected in options_manager/validation/management_cases.py "
            "(PR #221): exit_premium, realized_pnl_dollars, and "
            "exit_tranche_count all fixed to match broker records",
        ),
        proof_missing=(),
        reason_not_first_proof=(
            "the exit is a multi-week scale-out (3 sells over 5 weeks), "
            "not a single trigger-to-target-to-exit event"
        ),
        promotion_requirements=(),
        notes="Not promotable to a clean fixture -- the scaled exit is inherent to what happened.",
    )


def _adp_candidate() -> FixtureCandidate:
    return FixtureCandidate(
        ticker="ADP",
        window="2026-04-29/2026-05-07",
        status=FixtureStatus.REJECT,
        best_future_use=(
            "Reject as originally described. Could become a new INCOMPLETE "
            "candidate only if reframed around the real 2-contract trade "
            "shape."
        ),
        proof_confirmed=(
            "a real ADP $230C exp 2026-05-15 trade exists: 2 contracts, "
            "entry $0.60, exit $0.22, net -$74, straight-line decline over "
            "8 days",
            "contradiction documented in options_manager/validation/"
            "management_cases.py (PR #221): evidence_status="
            "contradicted_as_described, excluded from "
            "build_active_management_case_dataset()",
        ),
        proof_missing=(),
        reason_not_first_proof=(
            "the described version (8 contracts, +116% peak, -93% "
            "reversal) does not match the real 2-contract straight-line "
            "loss -- a contradiction of size and P&L shape, not a rounding "
            "difference"
        ),
        promotion_requirements=(),
        notes="",
    )


def _arm_candidate() -> FixtureCandidate:
    return FixtureCandidate(
        ticker="ARM",
        window="2026-04-30",
        status=FixtureStatus.REJECT,
        best_future_use=(
            "Reject as originally described. Could become a new INCOMPLETE "
            "candidate only if reframed around the real same-day scalp."
        ),
        proof_confirmed=(
            "a real ARM $215C exp 2026-05-01 trade exists: 4 contracts, "
            "same-day 33-minute hold, cost $1,004, exit proceeds $624, net "
            "-$380",
            "contradiction documented in options_manager/validation/"
            "management_cases.py (PR #221): evidence_status="
            "contradicted_as_described, excluded from "
            "build_active_management_case_dataset()",
        ),
        proof_missing=(),
        reason_not_first_proof=(
            "described as a multi-day hold with a $624 profit cut short by "
            "an external recommendation; the real trade is a same-day 1-DTE "
            "scalp that lost money -- the $624 was the exit credit, not net "
            "P&L, directly contradicting the claimed sign of the outcome"
        ),
        promotion_requirements=(),
        notes="",
    )


def _qcom_candidate() -> FixtureCandidate:
    return FixtureCandidate(
        ticker="QCOM",
        window="unknown",
        status=FixtureStatus.REJECT,
        best_future_use="Reject unless the real intended contract is identified.",
        proof_confirmed=(
            "three unrelated real QCOM trades exist in the account: $180C "
            "0DTE, $230C exp 2026-06-18, $210C 0DTE",
        ),
        proof_missing=("any $200-strike QCOM trade -- does not exist in the account",),
        reason_not_first_proof="the named contract ($200C exp Jun 18) does not exist anywhere in the account's order history",
        promotion_requirements=(
            "confirmation of which of the three real QCOM trades (if any) "
            "was actually meant",
        ),
        notes="",
    )


_FIXTURE_CANDIDATE_BUILDERS = (
    ("HOOD", _hood_candidate),
    ("EBAY", _ebay_candidate),
    ("AMD", _amd_candidate),
    ("ORCL", _orcl_candidate),
    ("FITB", _fitb_candidate),
    ("BAC", _bac_candidate),
    ("SPXW", _spxw_candidate),
    ("NVDA", _nvda_candidate),
    ("NOK", _nok_candidate),
    ("ADP", _adp_candidate),
    ("ARM", _arm_candidate),
    ("QCOM", _qcom_candidate),
)


def build_fixture_candidate_inventory() -> dict[str, FixtureCandidate]:
    """Returns a fresh dict of all 12 tracked fixture candidates, keyed by
    ticker. Each call rebuilds the candidates from the individual builder
    functions above rather than returning a shared/cached dict, so
    nothing here can accumulate mutated state across calls (the
    candidates themselves are also frozen dataclasses)."""
    return {name: builder() for name, builder in _FIXTURE_CANDIDATE_BUILDERS}


def summarize_fixture_candidate_inventory(
    candidates: dict[str, FixtureCandidate] | None = None,
) -> FixtureCandidateSummary:
    """Deterministic rollup of a fixture-candidate inventory (or
    `candidates`, if supplied)."""
    if candidates is None:
        candidates = build_fixture_candidate_inventory()

    values = list(candidates.values())
    counts_by_status: dict[str, int] = {}
    for candidate in values:
        counts_by_status[candidate.status.value] = (
            counts_by_status.get(candidate.status.value, 0) + 1
        )

    return FixtureCandidateSummary(
        total_candidates=len(values),
        counts_by_status=counts_by_status,
    )
