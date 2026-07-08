"""options_manager/validation/management_cases.py

Advisory-only trade-management validation — a layer separate from
RealSetupFixture (base.py/fixtures.py). RealSetupFixture asks "did the
setup trigger correctly?"; a ManagementCase asks a different question
entirely: "was the decision made after entry correct, given what the
underlying thesis actually did?" A hold, a trim, a full exit, an add, or
letting a contract expire are all decisions this layer can describe --
none of them are a setup-detection question, and none of them are
evaluated by re-running the existing setup-scanner path.

This module never imports the setup-scanner package or its row/bars
models, and never calls its evaluation entry points -- a ManagementCase
has no candle sequence, no entry-trigger/invalidation/target validation
of its own, and is not scored, triggered, or classified by anything in
that package. `related_setup_fixture_id` is the only permitted link back
to a RealSetupFixture, and it is a loose, optional string reference only
-- never a functional or import dependency.

`classification` is required, not derived: this layer has no
auto-classification function yet. A human's own read of what happened is
the source of truth for every case here -- there is no default
classifier underneath a case's stated classification to fall back on.

`evidence_status` is a separate axis from `classification`:
`classification` describes the management *lesson* a case is meant to
teach; `evidence_status` describes whether the case's own numbers have
been checked against an independent record (a brokerage's own order
history). Increment 23 shipped all four original cases (NOK, EBAY, ADP,
ARM) without that check having been done. A subsequent broker-record
reconciliation (Increment 25A/25C) found:

- NOK and EBAY were mechanically wrong in specific fields (blended exit
  price, exit count/cadence, entry premium, position size) but the
  underlying management lesson survived the correction -- both are now
  `evidence_status="corrected_from_broker_records"` with the corrected
  numbers in place.
- ADP and ARM were not just off in a field or two: the *lesson itself*
  doesn't match the real trade. The real ADP position was 2 contracts
  (not 8) and declined in a plain straight line to roughly -63% (not a
  +116% peak reversing to -93%). The real ARM position was a same-day,
  33-minute, 1-DTE trade that lost $380 net -- not a multi-day hold where
  a $624 profit was cut short by an external recommendation. Rewriting
  either case's numbers in place would misrepresent it as "the same
  lesson, slightly corrected," when the lesson the case was built to
  teach isn't what happened. Both are kept at
  `evidence_status="contradicted_as_described"`, with their original
  (contradicted) claim preserved unchanged in their fields -- for the
  historical record of what was claimed -- and the real broker-backed
  shape described in `notes`. Both are excluded from
  `build_active_management_case_dataset()`. A corrected replacement case
  for either, built from the real 2-contract ADP trade or the real
  same-day ARM scalp, may be added in a future increment; it does not
  exist yet.

Every field other than the small required set is optional and left None
when not actually known -- nothing here is fabricated to fill a gap.
Cases whose real history blends more than one distinct trade/decision
into one narrative (an AMZN-shaped case, per the source review that
motivated this module) are not represented here at all until the
underlying trades are decomposed into separate decisions; forcing a
blended history into one case would misrepresent it.

Performs no I/O of any kind: no candle fetch, no option-chain fetch, no
market-data fetch, no broker call, no order placement, no execution, no
alert sending, no file access at runtime, no network calls, no MCP calls.
Does not import replay/replay_engine.py, the live context.market_context
loader, alert_ranker, options_companion, execution, webhook, broker
systems, or risk/risk_engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from .base import DataProvenance

DecisionType = Literal["hold", "trim", "full_exit", "add", "let_expire"]

DecisionBasis = Literal[
    "rule_based",
    "emotional",
    "external_recommendation",
    "no_rule_defined",
]

ThesisStatus = Literal["intact", "broken", "unknown"]

PositionSizing = Literal["defined_risk", "oversized", "undefined_risk"]

ManagementClassification = Literal[
    "correct_rule_based_exit",
    "correct_rule_based_hold_or_scale",
    "premature_exit_thesis_intact",
    "held_too_long_thesis_broken",
    "oversized_no_exit_rule",
    "mixed_or_ambiguous",
    "unclassified",
]

EvidenceStatus = Literal[
    "broker_verified",
    "corrected_from_broker_records",
    "contradicted_as_described",
]


@dataclass(frozen=True, kw_only=True)
class ManagementCase:
    """One real, human-labeled trade-management decision. `classification`
    is always the human's own stated read of what happened -- this
    module does not derive it from the other fields. `evidence_status` is
    separate: it records whether this case's fields have been checked
    against an independent record (see module docstring)."""

    id: str
    ticker: str
    direction: Literal["CALL", "PUT"]
    provenance: DataProvenance
    decision_type: DecisionType
    decision_basis: DecisionBasis
    thesis_status_at_decision: ThesisStatus
    position_sizing: PositionSizing
    classification: ManagementClassification
    evidence_status: EvidenceStatus
    contract_strike: Optional[float] = None
    contract_expiration: Optional[str] = None
    entry_premium: Optional[float] = None
    exit_premium: Optional[float] = None
    position_size_contracts: Optional[int] = None
    exit_tranche_count: Optional[int] = None
    realized_pnl_dollars: Optional[float] = None
    realized_pnl_percent: Optional[float] = None
    post_decision_price_action: str = ""
    thesis_notes: str = ""
    notes: str = ""
    related_setup_fixture_id: Optional[str] = None


@dataclass(kw_only=True)
class ManagementCaseSummary:
    """Deterministic rollup of a management-case dataset. Purely computed
    from the cases' own fields -- no side effects."""

    total_cases: int
    placeholder_cases: int
    partial_real_cases: int
    user_supplied_cases: int
    counts_by_classification: dict[str, int] = field(default_factory=dict)
    counts_by_decision_type: dict[str, int] = field(default_factory=dict)
    counts_by_evidence_status: dict[str, int] = field(default_factory=dict)


def _nok_case() -> ManagementCase:
    """Real trade: 3x $14C Jun 18 NOK calls, entered at $1.27 average.
    Broker order history (Increment 25C reconciliation) shows the exit was
    not one cut -- it was three separate sells spread across five weeks:
    2026-05-18 (1 @ $0.95), 2026-05-18 (1 @ $0.95), 2026-06-12 (1 @ $0.90),
    blending to ~$0.93/share and a net realized loss of -$101 (not the
    originally recalled single $1.02 exit / -$75). GEX nodes were
    reportedly still flashing at the $14 level at the time of the first
    cut, meaning the original thesis had not actually broken."""
    return ManagementCase(
        id="nok_management_case_001",
        ticker="NOK",
        direction="CALL",
        provenance="user_supplied",
        contract_strike=14.0,
        contract_expiration="2026-06-18",
        entry_premium=1.27,
        exit_premium=0.93,
        position_size_contracts=3,
        exit_tranche_count=3,
        decision_type="full_exit",
        decision_basis="emotional",
        thesis_status_at_decision="intact",
        position_sizing="undefined_risk",
        classification="premature_exit_thesis_intact",
        evidence_status="corrected_from_broker_records",
        realized_pnl_dollars=-101.0,
        post_decision_price_action=(
            "GEX showed flashing nodes at $14 at the time of the first cut "
            "-- the thesis had not actually broken. Broker records show the "
            "position was closed across three separate sells over five "
            "weeks (2026-05-18 x2, 2026-06-12), not one clean exit."
        ),
        thesis_notes="",
        notes=(
            "Cut was emotional, not rule-based -- no stop was ever defined "
            "at entry. The invalidation level had not broken at the time "
            "of the first cut. Corrected by Increment 25C broker-record "
            "reconciliation: exit_premium and realized_pnl_dollars were "
            "originally recalled as a single $1.02 exit for -$75; the "
            "account's actual order history shows three sells (0.95, 0.95, "
            "0.90) blending to ~0.93 and a net loss of -$101."
        ),
    )


def _ebay_case() -> ManagementCase:
    """Real trade: 5x $105C May 15 EBAY calls, entered at $1.18 (broker
    order history; originally recalled as 4 contracts at $1.06). Scaled
    out across four partial sells for a combined realized gain of $1,884.
    Target 1 (~$106) was reached during the regular session before the
    exits; target 2 ($108) did not print until post-market, after the
    position was already fully closed, so it must not be counted as a
    live target hit the trade actually captured."""
    return ManagementCase(
        id="ebay_management_case_001",
        ticker="EBAY",
        direction="CALL",
        provenance="user_supplied",
        contract_strike=105.0,
        contract_expiration="2026-05-15",
        entry_premium=1.18,
        position_size_contracts=5,
        decision_type="full_exit",
        decision_basis="rule_based",
        thesis_status_at_decision="intact",
        position_sizing="defined_risk",
        classification="correct_rule_based_hold_or_scale",
        evidence_status="corrected_from_broker_records",
        realized_pnl_dollars=538.0 + 470.0 + 455.0 + 421.0,
        post_decision_price_action=(
            "Target 1 (~$106) was reached during the regular session before "
            "the exits; target 2 ($108) only printed in post-market, after "
            "the position was already fully closed, so it was not a target "
            "the trade actually captured -- the exit itself was rule-based "
            "against the target that did confirm live, not a missed "
            "continuation."
        ),
        thesis_notes=(
            "GEX radar flagged it, Signa confirmed; waited for a PDL-reclaim "
            "before entering rather than chasing the ask."
        ),
        notes=(
            "Corrected by Increment 25C broker-record reconciliation: entry "
            "premium was originally recalled as $1.06 on 4 contracts; the "
            "account's actual order history shows $1.18 on 5 contracts "
            "($590 total). The realized P&L figure ($1,884) matches the "
            "account's four exit fills exactly and is unchanged."
        ),
    )


def _adp_case() -> ManagementCase:
    """Originally recalled trade: 8x $230C May 15 ADP calls, explicitly
    called oversized with no exit rule ever defined, said to have peaked
    at +116% before reversing to -93% by expiry. Broker order history
    (Increment 25C reconciliation) does not corroborate this: the
    account's only ADP $230C 2026-05-15 position is 2 contracts, entered
    at $0.60 and closed at $0.22 eight days later -- a plain straight-line
    decline to roughly -63%, with no intraday peak on record. This is a
    contradiction of the trade's shape and size, not a rounding
    difference, so the original narrative is kept unchanged below (for
    the historical record of what was claimed) but flagged
    `evidence_status="contradicted_as_described"` and excluded from
    `build_active_management_case_dataset()`. A corrected case built from
    the real 2-contract trade may be added later; it does not exist yet."""
    return ManagementCase(
        id="adp_management_case_001",
        ticker="ADP",
        direction="CALL",
        provenance="partial_real",
        contract_strike=230.0,
        contract_expiration="2026-05-15",
        position_size_contracts=8,
        decision_type="hold",
        decision_basis="no_rule_defined",
        thesis_status_at_decision="unknown",
        position_sizing="oversized",
        classification="oversized_no_exit_rule",
        evidence_status="contradicted_as_described",
        realized_pnl_percent=-93.0,
        post_decision_price_action=(
            "Peaked at roughly +116% before reversing all the way to -93% "
            "by expiry."
        ),
        thesis_notes="Signal sourced from a community contact.",
        notes=(
            "8 contracts was oversized with no exit rule defined -- held "
            "through a full reversal into a near-total loss. A recurring "
            "pattern, per the trader's own review. CONTRADICTED BY BROKER "
            "RECORDS (Increment 25C): the account's only ADP $230C "
            "2026-05-15 position is 2 contracts, entered 2026-04-29 at "
            "$0.60 and closed 2026-05-07 at $0.22 -- a straight decline to "
            "about -63%, with no evidence of an intraday +116% peak. The "
            "claim above is preserved as the original recollection, not as "
            "verified fact."
        ),
    )


def _arm_case() -> ManagementCase:
    """Originally recalled trade: $215C ARM calls, said to have been
    exited early on an external (Claude) recommendation at a $624 profit,
    with the invalidation level never breaking and the underlying
    recovering significantly afterward. Broker order history (Increment
    25C reconciliation) does not corroborate this: the account's only
    $215C ARM position was a same-day, 33-minute, 1-DTE trade (bought and
    sold 2026-04-30, expiring the next day) that cost $1,004 and returned
    $624 in exit proceeds -- a net realized loss of -$380, not a $624
    gain. This is a contradiction of the trade's direction and shape, not
    a rounding difference, so the original narrative is kept unchanged
    below (for the historical record of what was claimed) but flagged
    `evidence_status="contradicted_as_described"` and excluded from
    `build_active_management_case_dataset()`. A corrected case built from
    the real same-day scalp may be added later; it does not exist yet."""
    return ManagementCase(
        id="arm_management_case_001",
        ticker="ARM",
        direction="CALL",
        provenance="partial_real",
        contract_strike=215.0,
        decision_type="full_exit",
        decision_basis="external_recommendation",
        thesis_status_at_decision="intact",
        position_sizing="defined_risk",
        classification="premature_exit_thesis_intact",
        evidence_status="contradicted_as_described",
        realized_pnl_dollars=624.0,
        post_decision_price_action=(
            "Underlying recovered significantly after the exit -- large "
            "further gains were left on the table."
        ),
        thesis_notes="Strong flow, clean structure at entry.",
        notes=(
            "Exited early on an external recommendation, not because the "
            "invalidation level broke -- it never did. CONTRADICTED BY "
            "BROKER RECORDS (Increment 25C): the account's only $215C ARM "
            "position was a same-day, 33-minute, 1-DTE trade (2026-04-30, "
            "expiring 2026-05-01) costing $1,004 with $624 in exit proceeds "
            "-- a net realized loss of -$380, not a $624 gain, and not a "
            "multi-day hold at all. The claim above is preserved as the "
            "original recollection, not as verified fact."
        ),
    )


_MANAGEMENT_CASE_BUILDERS = (
    ("nok_management_case", _nok_case),
    ("ebay_management_case", _ebay_case),
    ("adp_management_case", _adp_case),
    ("arm_management_case", _arm_case),
)

_CONTRADICTED_EVIDENCE_STATUS: EvidenceStatus = "contradicted_as_described"


def build_management_case_dataset() -> dict[str, ManagementCase]:
    """Returns a fresh dict of all 4 management cases, keyed by a fixed
    case name -- including the two (ADP, ARM) whose original narrative is
    now known to be contradicted by broker records. This is the full
    historical record; use `build_active_management_case_dataset()` for
    only the cases whose management lesson is broker-corroborated.

    Each call rebuilds the cases from the individual builder functions
    above rather than returning a shared/cached dict, so nothing here can
    accumulate mutated state across calls (the cases themselves are also
    frozen dataclasses).

    An AMZN case is deliberately not included: the recovered history
    blends two distinct contracts and four distinct outcomes into one
    narrative, with no single decision instant to represent honestly.
    It stays excluded until the underlying trades are decomposed into
    separate cases."""
    return {name: builder() for name, builder in _MANAGEMENT_CASE_BUILDERS}


def build_active_management_case_dataset() -> dict[str, ManagementCase]:
    """Same as `build_management_case_dataset()`, filtered to exclude any
    case whose `evidence_status` is `"contradicted_as_described"`. Use
    this wherever a case is being treated as a verified management lesson
    rather than a historical record of what was claimed -- as of
    Increment 25C this excludes the ADP and ARM cases."""
    return {
        name: case
        for name, case in build_management_case_dataset().items()
        if case.evidence_status != _CONTRADICTED_EVIDENCE_STATUS
    }


def summarize_management_case_dataset(
    cases: dict[str, ManagementCase] | None = None,
) -> ManagementCaseSummary:
    """Deterministic rollup of a management-case dataset (or `cases`, if
    supplied). Defaults to the full dataset (including contradicted
    cases) -- pass `build_active_management_case_dataset()` explicitly to
    summarize only broker-corroborated cases."""
    if cases is None:
        cases = build_management_case_dataset()

    values = list(cases.values())
    counts_by_classification: dict[str, int] = {}
    counts_by_decision_type: dict[str, int] = {}
    counts_by_evidence_status: dict[str, int] = {}
    placeholder_cases = 0
    partial_real_cases = 0
    user_supplied_cases = 0

    for case in values:
        counts_by_classification[case.classification] = (
            counts_by_classification.get(case.classification, 0) + 1
        )
        counts_by_decision_type[case.decision_type] = (
            counts_by_decision_type.get(case.decision_type, 0) + 1
        )
        counts_by_evidence_status[case.evidence_status] = (
            counts_by_evidence_status.get(case.evidence_status, 0) + 1
        )
        if case.provenance == "placeholder":
            placeholder_cases += 1
        elif case.provenance == "partial_real":
            partial_real_cases += 1
        elif case.provenance == "user_supplied":
            user_supplied_cases += 1

    return ManagementCaseSummary(
        total_cases=len(values),
        placeholder_cases=placeholder_cases,
        partial_real_cases=partial_real_cases,
        user_supplied_cases=user_supplied_cases,
        counts_by_classification=counts_by_classification,
        counts_by_decision_type=counts_by_decision_type,
        counts_by_evidence_status=counts_by_evidence_status,
    )
