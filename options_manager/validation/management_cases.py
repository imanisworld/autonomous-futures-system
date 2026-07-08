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


@dataclass(frozen=True, kw_only=True)
class ManagementCase:
    """One real, human-labeled trade-management decision. `classification`
    is always the human's own stated read of what happened -- this
    module does not derive it from the other fields."""

    id: str
    ticker: str
    direction: Literal["CALL", "PUT"]
    provenance: DataProvenance
    decision_type: DecisionType
    decision_basis: DecisionBasis
    thesis_status_at_decision: ThesisStatus
    position_sizing: PositionSizing
    classification: ManagementClassification
    contract_strike: Optional[float] = None
    contract_expiration: Optional[str] = None
    entry_premium: Optional[float] = None
    exit_premium: Optional[float] = None
    position_size_contracts: Optional[int] = None
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


def _nok_case() -> ManagementCase:
    """Real trade: 3x $14C Jun 18 NOK calls, entered at $1.27 average.
    Cut at $1.02 (-$75 total) with no stop ever defined -- an emotional
    exit. GEX nodes were still flashing at the $14 level at the time of
    the cut, meaning the original thesis had not actually broken."""
    return ManagementCase(
        id="nok_management_case_001",
        ticker="NOK",
        direction="CALL",
        provenance="user_supplied",
        contract_strike=14.0,
        contract_expiration="2026-06-18",
        entry_premium=1.27,
        exit_premium=1.02,
        position_size_contracts=3,
        decision_type="full_exit",
        decision_basis="emotional",
        thesis_status_at_decision="intact",
        position_sizing="undefined_risk",
        classification="premature_exit_thesis_intact",
        realized_pnl_dollars=-75.0,
        post_decision_price_action=(
            "GEX showed flashing nodes at $14 at the time of the exit -- "
            "the thesis had not actually broken."
        ),
        thesis_notes="",
        notes=(
            "Cut was emotional, not rule-based -- no stop was ever defined "
            "at entry. The invalidation level had not broken at the time "
            "of the cut."
        ),
    )


def _ebay_case() -> ManagementCase:
    """Real trade: 4x $105C May 15 EBAY calls, entered at $1.06 (waited
    for a PDL-reclaim confirmation rather than chasing the $2.26 ask).
    Scaled out across two pre-defined GEX-wall targets ($106, $108) for
    a combined realized gain of $1,884. The underlying later ran to $120
    on acquisition news after the exit -- the exit was rule-based, not a
    missed continuation."""
    return ManagementCase(
        id="ebay_management_case_001",
        ticker="EBAY",
        direction="CALL",
        provenance="user_supplied",
        contract_strike=105.0,
        contract_expiration="2026-05-15",
        entry_premium=1.06,
        position_size_contracts=4,
        decision_type="full_exit",
        decision_basis="rule_based",
        thesis_status_at_decision="intact",
        position_sizing="defined_risk",
        classification="correct_rule_based_hold_or_scale",
        realized_pnl_dollars=538.0 + 470.0 + 455.0 + 421.0,
        post_decision_price_action=(
            "Underlying later ran to $120 on acquisition news after the "
            "exit -- the exit itself was rule-based against pre-defined "
            "GEX-wall targets, not a missed continuation."
        ),
        thesis_notes=(
            "GEX radar flagged it, Signa confirmed; waited for PDL-reclaim "
            "confirmation instead of chasing the ask."
        ),
        notes="Scaled out at both pre-defined GEX-wall targets ($106, $108).",
    )


def _adp_case() -> ManagementCase:
    """Real trade: 8x $230C May 15 ADP calls -- explicitly called
    oversized by the trader, with no exit rule ever defined. Went from a
    +116% peak to -93% by expiry. Whether the underlying thesis itself
    technically broke before expiry, or simply decayed on theta while
    being held with no rule forcing an exit, is not established by the
    source material -- left "unknown" rather than guessed at."""
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
        realized_pnl_percent=-93.0,
        post_decision_price_action=(
            "Peaked at roughly +116% before reversing all the way to -93% "
            "by expiry."
        ),
        thesis_notes="Signal sourced from a community contact.",
        notes=(
            "8 contracts was oversized with no exit rule defined -- held "
            "through a full reversal into a near-total loss. A recurring "
            "pattern, per the trader's own review."
        ),
    )


def _arm_case() -> ManagementCase:
    """Real trade: $215C ARM calls, exited early on an external (Claude)
    recommendation rather than on any broken invalidation level. The
    underlying recovered significantly after the exit, and the
    trader's own account states the invalidation level never broke --
    the setup was not wrong, the exit timing was."""
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
        realized_pnl_dollars=624.0,
        post_decision_price_action=(
            "Underlying recovered significantly after the exit -- large "
            "further gains were left on the table."
        ),
        thesis_notes="Strong flow, clean structure at entry.",
        notes=(
            "Exited early on an external recommendation, not because the "
            "invalidation level broke -- it never did."
        ),
    )


_MANAGEMENT_CASE_BUILDERS = (
    ("nok_management_case", _nok_case),
    ("ebay_management_case", _ebay_case),
    ("adp_management_case", _adp_case),
    ("arm_management_case", _arm_case),
)


def build_management_case_dataset() -> dict[str, ManagementCase]:
    """Returns a fresh dict of the 4 approved management cases, keyed by
    a fixed case name. Each call rebuilds the cases from the individual
    builder functions above rather than returning a shared/cached dict,
    so nothing here can accumulate mutated state across calls (the cases
    themselves are also frozen dataclasses).

    An AMZN case is deliberately not included: the recovered history
    blends two distinct contracts and four distinct outcomes into one
    narrative, with no single decision instant to represent honestly.
    It stays excluded until the underlying trades are decomposed into
    separate cases."""
    return {name: builder() for name, builder in _MANAGEMENT_CASE_BUILDERS}


def summarize_management_case_dataset(
    cases: dict[str, ManagementCase] | None = None,
) -> ManagementCaseSummary:
    """Deterministic rollup of a management-case dataset (or `cases`, if
    supplied)."""
    if cases is None:
        cases = build_management_case_dataset()

    values = list(cases.values())
    counts_by_classification: dict[str, int] = {}
    counts_by_decision_type: dict[str, int] = {}
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
    )
