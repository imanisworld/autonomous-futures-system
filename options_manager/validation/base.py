"""options_manager/validation/base.py

Advisory-only real-setup validation model — Increment 23. Shared,
caller-supplied data contract for manually-authored historical trade
setups (fixtures.py). Every fixture here is caller-supplied: this module
never fetches a candle, a quote, an option chain, or market data, and
performs no I/O of any kind.

A RealSetupFixture carries two independent halves that must never be
blended together: the setup packet (bars, entry/invalidation/target
inputs, optional market-context/contract inputs — everything the scanner
needs to produce a verdict) and the fixture's own recorded outcome
(`actual_outcome`, `actual_outcome_notes` — what a caller says actually
happened afterward). The scanner never sees the outcome fields, and the
outcome fields are never derived from the scanner's own verdict.

`provenance` exists so no fixture can silently claim to be a real trade
example it isn't: "placeholder" means the values are synthetic
stand-ins awaiting replacement with an actual historical setup;
"user_supplied" means a caller has confirmed the values came from a real
trade. Nothing in this package upgrades a fixture from one to the other on
its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from options_manager.context import MarketContextInputs
from options_manager.contracts import ContractConstraintsInputs
from options_manager.levels import LevelFinderInputs
from options_manager.strategies import StrategyContractConstraints, StrategyMarketContext

DataProvenance = Literal["placeholder", "user_supplied"]

RealSetupOutcome = Literal[
    "hit_target_1",
    "hit_target_2",
    "hit_stop",
    "no_resolution",
    "unknown",
]

RealSetupClassification = Literal[
    "valid_triggered_winner",
    "valid_triggered_loser",
    "valid_no_follow_through",
    "rejected_correctly",
    "rejected_incorrectly",
    "false_positive",
    "false_negative",
    "unclassified",
]


@dataclass(frozen=True)
class RealSetupFixture:
    """One manually-authored historical trade setup, plus its recorded
    real-world outcome. Nothing here is fetched — bars, entry/
    invalidation/target inputs, optional level/market-context/contract
    inputs, and the outcome fields are all supplied by whoever authors the
    fixture.

    `human_classification_override`, when supplied, wins over whatever
    the automatic outcome-derived classification would produce (the same
    explicit-value-wins convention used by StrategyMarketContext.confirmed
    and StrategyContractConstraints.constraints_met elsewhere in this
    buildout) — it exists because "false_positive"/"false_negative" are
    judgments about whether the setup's own read was right, which a bare
    scan-status/outcome comparison cannot determine on its own.
    """

    id: str
    ticker: str
    setup_datetime: str
    direction: Literal["CALL", "PUT"]
    provenance: DataProvenance
    two_bars_back_type: Literal["two_up", "two_down"]
    two_bars_back_high: float
    two_bars_back_low: float
    previous_high: float
    previous_low: float
    current_high: float
    current_low: float
    actual_outcome: RealSetupOutcome
    entry_trigger: Optional[float] = None
    underlying_invalidation: Optional[float] = None
    spot_at_setup: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    level_inputs: Optional[LevelFinderInputs] = None
    market_context: StrategyMarketContext = field(default_factory=StrategyMarketContext)
    market_context_inputs: Optional[MarketContextInputs] = None
    contract_constraints: StrategyContractConstraints = field(
        default_factory=StrategyContractConstraints
    )
    contract_constraints_inputs: Optional[ContractConstraintsInputs] = None
    human_classification_override: Optional[RealSetupClassification] = None
    notes: str = ""
    actual_outcome_notes: str = ""


@dataclass(kw_only=True)
class RealSetupValidationEntry:
    """One fixture's scan verdict paired with its recorded outcome and
    resulting classification. `scan_status`/`reason_code` come only from
    the scanner's own verdict; `actual_outcome`/`actual_outcome_notes`
    come only from the fixture's recorded real-world result — this model
    keeps the two sourced separately rather than merging them into one
    field."""

    fixture_id: str
    ticker: str
    provenance: DataProvenance
    scan_status: str
    reason_code: str
    actual_outcome: RealSetupOutcome
    actual_outcome_notes: str = ""
    classification: RealSetupClassification


@dataclass(kw_only=True)
class RealSetupValidationSummary:
    """Deterministic rollup of a real-setup validation run. Purely
    computed from the per-fixture entries -- no side effects."""

    total_cases: int
    placeholder_cases: int
    user_supplied_cases: int
    counts_by_classification: dict[str, int]
    counts_by_scan_status: dict[str, int]


def classify_real_setup_outcome(
    *,
    scan_status: str,
    actual_outcome: RealSetupOutcome,
    human_classification_override: Optional[RealSetupClassification] = None,
) -> RealSetupClassification:
    """Pure function of its explicit inputs -> RealSetupClassification.

    If `human_classification_override` is supplied, it is used as-is
    (explicit-value-wins, unchanged from every other override in this
    buildout). Otherwise the classification is derived only from
    `scan_status` and `actual_outcome`: a TRIGGERED setup that hit either
    target is a winner, one that hit the stop is a loser, one that never
    resolved had no follow-through. An INVALID or NO_TRADE setup that
    would have hit the stop (or never resolved) was rejected correctly;
    one that would have hit a target was rejected incorrectly. A WATCH
    setup never reached a final verdict, so it is left unclassified. Any
    combination this function does not explicitly recognize is left
    unclassified rather than guessed at -- "false_positive"/
    "false_negative" can only ever come from an explicit override, since
    they are judgments about whether the setup's own read was right, not
    something a bare status/outcome pairing can prove.
    """
    if human_classification_override is not None:
        return human_classification_override

    if scan_status == "TRIGGERED":
        if actual_outcome in ("hit_target_1", "hit_target_2"):
            return "valid_triggered_winner"
        if actual_outcome == "hit_stop":
            return "valid_triggered_loser"
        if actual_outcome == "no_resolution":
            return "valid_no_follow_through"
        return "unclassified"

    if scan_status in ("INVALID", "NO_TRADE"):
        if actual_outcome in ("hit_stop", "no_resolution"):
            return "rejected_correctly"
        if actual_outcome in ("hit_target_1", "hit_target_2"):
            return "rejected_incorrectly"
        return "unclassified"

    return "unclassified"
