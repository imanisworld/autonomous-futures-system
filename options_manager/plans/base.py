"""Pure Phase-1 options thesis/plan state.

This package is advisory-only.  It does not fetch market data, select a broker
order, send an alert, size a live position, or mutate any external state.

The model deliberately separates three ideas that old repeated alerts blurred:

* lifecycle status -- WATCHING/TRIGGERED/ACTIVE/terminal states;
* actionability -- whether the required independent proof is present now; and
* conviction -- an observational label that cannot change risk or execution.

Signa is telemetry only.  A repeated Signa state is never an additional piece
of proof and is never counted toward conviction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional

Direction = Literal["CALL", "PUT"]
LevelSide = Literal["RESISTANCE", "SUPPORT"]


class PlanStatus(str, Enum):
    WATCHING = "watching"
    TRIGGERED = "triggered"
    INVALIDATED = "invalidated"
    ACTIVE = "active"
    EXITED = "exited"
    EXPIRED = "expired"


class ConvictionBand(str, Enum):
    """Display/evidence label only; never a sizing or execution instruction."""

    OBSERVATIONAL = "observational"
    STANDARD = "standard"
    HIGH_CONVICTION_CANDIDATE = "high_conviction_candidate"


@dataclass(frozen=True)
class StructuralLevel:
    """One caller-supplied structural level with provenance.

    Gamma-labelled levels are usable only when ``verified_gamma`` is true.  A
    Signa pivot must therefore never be smuggled into the target finder by
    calling it a gamma wall.
    """

    price: float
    side: LevelSide
    source: str
    is_gamma: bool = False
    verified_gamma: bool = False


@dataclass(frozen=True)
class SignaObservation:
    """Raw-ish Signa telemetry retained for forward evidence and deduping.

    Retrieval time and stale age are intentionally not part of ``fingerprint``:
    repeated polling of the same upstream state must remain one Signa event.
    """

    direction: Optional[str] = None
    grade: Optional[str] = None
    score: Optional[float] = None
    requested_tf: Optional[str] = None
    signal_timestamp: Optional[str] = None
    technicals_as_of: Optional[str] = None
    stale_minutes: Optional[float] = None
    retrieved_at: Optional[str] = None
    parser_version: Optional[str] = None

    def fingerprint(self) -> tuple[object, ...]:
        return (
            self.direction,
            self.grade,
            self.score,
            self.requested_tf,
            self.signal_timestamp,
            self.technicals_as_of,
        )


@dataclass(frozen=True)
class ConvictionProofs:
    """Independent *extra* proof used only for a candidate conviction label.

    These are intentionally separate from minimum actionability proof.  No
    threshold is supplied here and Signa is intentionally absent.
    """

    full_timeframe_continuity: bool = False
    clean_continuation_or_retest: bool = False
    strong_level_confluence: bool = False
    exceptional_liquidity: bool = False
    strong_target_room: bool = False

    def count(self) -> int:
        return sum(
            (
                self.full_timeframe_continuity,
                self.clean_continuation_or_retest,
                self.strong_level_confluence,
                self.exceptional_liquidity,
                self.strong_target_room,
            )
        )

    @staticmethod
    def maximum() -> int:
        return 5


@dataclass(frozen=True)
class PlanPolicy:
    """Explicit plan-label policy.

    There is deliberately no default high-conviction threshold.  Until an
    operator chooses one for a forward shadow campaign, an actionable plan is
    STANDARD.  The label never changes risk or contract count.
    """

    high_conviction_min_confirmations: Optional[int] = None
    min_rr_threshold: Optional[float] = None
    min_distance_to_target: Optional[float] = None

    def validate(self) -> None:
        threshold = self.high_conviction_min_confirmations
        if threshold is not None and not (1 <= threshold <= ConvictionProofs.maximum()):
            raise ValueError(
                "high_conviction_min_confirmations must be between 1 and "
                f"{ConvictionProofs.maximum()} when configured"
            )
        if self.min_rr_threshold is not None and self.min_rr_threshold <= 0:
            raise ValueError("min_rr_threshold must be > 0 when configured")
        if self.min_distance_to_target is not None and self.min_distance_to_target <= 0:
            raise ValueError("min_distance_to_target must be > 0 when configured")


@dataclass(frozen=True)
class PlanObservation:
    """Low-level observation used to create/update one thesis.

    Integration code should prefer the canonical proof adapter rather than
    hand-setting the proof booleans below.  This low-level shape remains useful
    for pure state-machine tests and explicit/manual advisory tooling.
    """

    ticker: str
    direction: Direction
    setup_type: str
    timeframe: str
    observed_at: str
    mechanical_triggered: bool = False
    entry_trigger: Optional[float] = None
    underlying_invalidation: Optional[float] = None
    levels: tuple[StructuralLevel, ...] = ()
    contract_valid: bool = False
    portfolio_risk_valid: bool = False
    spy_qqq_aligned: bool = False
    htf_aligned: bool = False
    event_risk_clear: bool = False
    conviction_proofs: ConvictionProofs = field(default_factory=ConvictionProofs)
    signa: Optional[SignaObservation] = None
    mark_active: bool = False
    mark_exited: bool = False
    invalidation_hit: bool = False
    expired: bool = False
    # Kept for backward compatibility with existing direct callers.
    source_reference: Optional[str] = None
    # Canonical proof packets carry multiple references; preserve them all.
    source_references: tuple[str, ...] = ()


@dataclass(frozen=True)
class TradePlanSnapshot:
    """Current advisory state for one ticker/direction/setup/timeframe thesis."""

    ticker: str
    direction: Direction
    setup_type: str
    timeframe: str
    observed_at: str
    status: PlanStatus
    actionable: bool
    conviction: ConvictionBand
    conviction_confirmation_count: int
    entry_trigger: Optional[float]
    underlying_invalidation: Optional[float]
    target_1: Optional[float]
    target_2: Optional[float]
    target_1_source: Optional[str]
    target_2_source: Optional[str]
    rr_1: Optional[float]
    rr_2: Optional[float]
    target_status: str
    target_reason_code: str
    blocking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    signa_event_count: int = 0
    signa_repeat_count: int = 0
    last_signa_fingerprint: Optional[tuple[object, ...]] = None
    latest_signa: Optional[SignaObservation] = None
    source_references: tuple[str, ...] = ()

    @property
    def terminal(self) -> bool:
        return self.status in {
            PlanStatus.INVALIDATED,
            PlanStatus.EXITED,
            PlanStatus.EXPIRED,
        }


@dataclass(frozen=True)
class PlanUpdate:
    """One pure plan-manager result.

    ``should_emit_update`` is only a recommendation to a future notifier.  This
    module never sends anything itself.  A Signa-only change is telemetry-only.
    """

    snapshot: TradePlanSnapshot
    should_emit_update: bool
    material_reasons: tuple[str, ...]
    telemetry_only: bool
    signa_changed: bool
    signa_repeated: bool
