"""Outcome / position-management evidence for one canonical prospective signal.

Answers "did the underlying setup translate into a viable option?" without
manufacturing evidence:

* every measured quantity is a ``Measured`` value with an explicit
  ``EvidenceStatus``. A value that does not exist is ``UNAVAILABLE`` (or
  ``NOT_APPLICABLE``) **with a reason** -- never a zero, never a guess;
* option premium paths are only observed quotes (``PremiumMark`` with a
  source); there is no interpolation and no synthetic mark;
* R multiples are derived from the signal's own trigger/invalidation, so the
  outcome cannot redefine its risk unit after the fact;
* spread and decay effects are derived only when the observed path contains
  the marks they need; otherwise they are ``UNAVAILABLE``.

Pure: no I/O, no provider calls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from .signal import IntegrityStatus, ProspectiveSignal

SCHEMA = "options-outcome-evidence-v1"


class EvidenceStatus(str, Enum):
    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class OutcomeError(ValueError):
    pass


@dataclass(frozen=True)
class Measured:
    value: Any
    status: EvidenceStatus
    reason: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if self.status in (EvidenceStatus.UNAVAILABLE, EvidenceStatus.NOT_APPLICABLE):
            if self.value is not None:
                raise OutcomeError(f"{self.status.value} evidence must not carry a value")
            if not self.reason.strip():
                raise OutcomeError(f"{self.status.value} evidence requires a reason")
        else:
            if self.value is None:
                raise OutcomeError(f"{self.status.value} evidence requires a value")
            if isinstance(self.value, float) and not math.isfinite(self.value):
                raise OutcomeError("measured value must be finite")
            if self.status is EvidenceStatus.OBSERVED and not self.source.strip():
                raise OutcomeError("OBSERVED evidence requires a source")

    @property
    def known(self) -> bool:
        return self.status in (EvidenceStatus.OBSERVED, EvidenceStatus.DERIVED)

    @staticmethod
    def observed(value: Any, source: str) -> "Measured":
        return Measured(value, EvidenceStatus.OBSERVED, source=source)

    @staticmethod
    def derived(value: Any, reason: str = "") -> "Measured":
        return Measured(value, EvidenceStatus.DERIVED, reason=reason)

    @staticmethod
    def unavailable(reason: str) -> "Measured":
        return Measured(None, EvidenceStatus.UNAVAILABLE, reason=reason)

    @staticmethod
    def not_applicable(reason: str) -> "Measured":
        return Measured(None, EvidenceStatus.NOT_APPLICABLE, reason=reason)


@dataclass(frozen=True)
class PremiumMark:
    """One observed option quote. Synthetic/interpolated marks do not exist."""

    at: datetime
    bid: float
    ask: float
    source: str

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise OutcomeError("premium mark time must be timezone-aware")
        if not self.source.strip():
            raise OutcomeError("premium mark requires a quote source")
        for name in ("bid", "ask"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
                raise OutcomeError(f"premium mark {name} must be finite and >= 0")
        if self.ask < self.bid:
            raise OutcomeError("premium mark is crossed (ask < bid)")

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


class PathStatus(str, Enum):
    CAPTURED = "CAPTURED"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


def underlying_r(signal: ProspectiveSignal, price: float) -> float:
    """Signed R of an underlying price relative to the signal's own risk unit."""
    unit = abs(signal.trigger - signal.invalidation)
    if signal.direction == "LONG":
        return (price - signal.trigger) / unit
    return (signal.trigger - price) / unit


def _seconds_between(start: datetime | None, end: Measured) -> Measured:
    if start is None:
        return Measured.unavailable("signal never triggered")
    if not end.known:
        return Measured.unavailable(end.reason or "event time unavailable")
    return Measured.derived((end.value - start).total_seconds())


@dataclass(frozen=True)
class OutcomeEvidence:
    signal_id: str
    strategy_epoch: str
    executed: bool
    pnl_basis: str  # "executed" | "paper_equivalent"
    target_1: Measured
    target_2: Measured
    invalidation: float
    premium_entry: Measured
    premium_stop: Measured
    mae_price: Measured
    mfe_price: Measured
    t1_hit_at: Measured
    t2_hit_at: Measured
    invalidation_hit_at: Measured
    trim_event: Measured
    runner_outcome: Measured
    result_r: Measured
    gross_pnl: Measured
    net_pnl: Measured
    premium_path: tuple[PremiumMark, ...] = ()
    premium_path_status: PathStatus = PathStatus.UNAVAILABLE
    premium_path_reason: str = "no observed option quotes"
    data_integrity: IntegrityStatus = IntegrityStatus.UNKNOWN
    signal_integrity: IntegrityStatus = IntegrityStatus.UNKNOWN
    execution_integrity: IntegrityStatus = IntegrityStatus.NOT_APPLICABLE
    notes: tuple[str, ...] = field(default_factory=tuple)

    # ── derived views (never stored as independent truth) ──────────────────

    def time_to_trigger(self, signal: ProspectiveSignal) -> Measured:
        if signal.trigger_market_time is None:
            return Measured.unavailable("signal never triggered")
        return Measured.derived((signal.trigger_market_time - signal.setup_ready_time).total_seconds())

    def time_to_t1(self, signal: ProspectiveSignal) -> Measured:
        return _seconds_between(signal.trigger_market_time, self.t1_hit_at)

    def time_to_t2(self, signal: ProspectiveSignal) -> Measured:
        return _seconds_between(signal.trigger_market_time, self.t2_hit_at)

    def time_to_invalidation(self, signal: ProspectiveSignal) -> Measured:
        return _seconds_between(signal.trigger_market_time, self.invalidation_hit_at)

    def mae_r(self, signal: ProspectiveSignal) -> Measured:
        if not self.mae_price.known:
            return Measured.unavailable(self.mae_price.reason or "MAE price unavailable")
        return Measured.derived(underlying_r(signal, self.mae_price.value))

    def mfe_r(self, signal: ProspectiveSignal) -> Measured:
        if not self.mfe_price.known:
            return Measured.unavailable(self.mfe_price.reason or "MFE price unavailable")
        return Measured.derived(underlying_r(signal, self.mfe_price.value))

    def entry_spread_cost(self) -> Measured:
        """Half-spread paid at the first observed mark, in premium dollars per share."""
        if self.premium_path_status is PathStatus.UNAVAILABLE or not self.premium_path:
            return Measured.unavailable(self.premium_path_reason)
        first = self.premium_path[0]
        return Measured.derived(first.ask - first.mid)

    def premium_change_mid(self) -> Measured:
        """Observed mid-to-mid premium change across the captured path.

        This mixes delta, theta and vega; it is reported as observed premium
        change, not attributed to decay, because the path cannot separate them.
        """
        if self.premium_path_status is not PathStatus.CAPTURED or len(self.premium_path) < 2:
            return Measured.unavailable(
                "premium path not fully captured" if self.premium_path else self.premium_path_reason
            )
        return Measured.derived(self.premium_path[-1].mid - self.premium_path[0].mid)


def validate_outcome(outcome: OutcomeEvidence, signal: ProspectiveSignal) -> list[str]:
    """Consistency problems; an empty list means the record is honest and complete enough to store."""
    problems: list[str] = []
    if outcome.signal_id != signal.signal_id:
        problems.append("outcome signal_id does not match the signal")
    if outcome.strategy_epoch != signal.strategy_epoch:
        problems.append("outcome strategy_epoch does not match the signal")
    if abs(outcome.invalidation - signal.invalidation) > 1e-9:
        problems.append("outcome invalidation differs from the signal's frozen invalidation")
    if outcome.pnl_basis not in ("executed", "paper_equivalent"):
        problems.append("pnl_basis must be executed or paper_equivalent")
    if outcome.executed and outcome.pnl_basis != "executed":
        problems.append("an executed outcome must use pnl_basis=executed")
    if not outcome.executed and outcome.pnl_basis == "executed":
        problems.append("a non-executed outcome cannot claim executed P&L")
    if not outcome.executed and outcome.execution_integrity not in (
        IntegrityStatus.NOT_APPLICABLE,
        IntegrityStatus.UNKNOWN,
    ):
        problems.append("execution_integrity applies only to executed outcomes")
    if outcome.premium_stop.known and outcome.premium_entry.known:
        if not 0 <= outcome.premium_stop.value < outcome.premium_entry.value:
            problems.append("premium_stop must be below premium_entry")
    # Premium path honesty.
    if outcome.premium_path_status is PathStatus.UNAVAILABLE and outcome.premium_path:
        problems.append("UNAVAILABLE premium path must not carry marks")
    if outcome.premium_path_status is not PathStatus.UNAVAILABLE and not outcome.premium_path:
        problems.append(f"{outcome.premium_path_status.value} premium path has no marks")
    if outcome.premium_path_status is PathStatus.UNAVAILABLE and not outcome.premium_path_reason.strip():
        problems.append("UNAVAILABLE premium path requires a reason")
    times = [m.at for m in outcome.premium_path]
    if times != sorted(times) or len(set(times)) != len(times):
        problems.append("premium path must be strictly time-ordered")
    if signal.trigger_market_time is not None and times and times[0] < signal.trigger_market_time:
        problems.append("premium path starts before the trigger")
    # Event-time ordering.
    trigger = signal.trigger_market_time
    for label, measured in (
        ("t1_hit_at", outcome.t1_hit_at),
        ("t2_hit_at", outcome.t2_hit_at),
        ("invalidation_hit_at", outcome.invalidation_hit_at),
    ):
        if measured.known:
            if not isinstance(measured.value, datetime) or measured.value.tzinfo is None:
                problems.append(f"{label} must be a timezone-aware datetime")
            elif trigger is None or measured.value < trigger:
                problems.append(f"{label} precedes the trigger")
    if outcome.t2_hit_at.known and outcome.t1_hit_at.known and outcome.t2_hit_at.value < outcome.t1_hit_at.value:
        problems.append("t2 hit before t1")
    return problems


def to_record(outcome: OutcomeEvidence, signal: ProspectiveSignal) -> dict[str, Any]:
    def m(measured: Measured) -> dict[str, Any]:
        value = measured.value.isoformat() if isinstance(measured.value, datetime) else measured.value
        return {
            "value": value,
            "status": measured.status.value,
            "reason": measured.reason,
            "source": measured.source,
        }

    return {
        "schema": SCHEMA,
        "signal_id": outcome.signal_id,
        "structure_id": signal.structure_id,
        "strategy_epoch": outcome.strategy_epoch,
        "executed": outcome.executed,
        "pnl_basis": outcome.pnl_basis,
        "target_1": m(outcome.target_1),
        "target_2": m(outcome.target_2),
        "invalidation": outcome.invalidation,
        "premium_entry": m(outcome.premium_entry),
        "premium_stop": m(outcome.premium_stop),
        "mae_price": m(outcome.mae_price),
        "mfe_price": m(outcome.mfe_price),
        "mae_r": m(outcome.mae_r(signal)),
        "mfe_r": m(outcome.mfe_r(signal)),
        "time_to_trigger_s": m(outcome.time_to_trigger(signal)),
        "time_to_t1_s": m(outcome.time_to_t1(signal)),
        "time_to_t2_s": m(outcome.time_to_t2(signal)),
        "time_to_invalidation_s": m(outcome.time_to_invalidation(signal)),
        "trim_event": m(outcome.trim_event),
        "runner_outcome": m(outcome.runner_outcome),
        "result_r": m(outcome.result_r),
        "gross_pnl": m(outcome.gross_pnl),
        "net_pnl": m(outcome.net_pnl),
        "premium_path_status": outcome.premium_path_status.value,
        "premium_path_reason": outcome.premium_path_reason,
        "premium_path": [
            {"at": p.at.astimezone(timezone.utc).isoformat(), "bid": p.bid, "ask": p.ask, "source": p.source}
            for p in outcome.premium_path
        ],
        "entry_spread_cost": m(outcome.entry_spread_cost()),
        "premium_change_mid": m(outcome.premium_change_mid()),
        "data_integrity": outcome.data_integrity.value,
        "signal_integrity": outcome.signal_integrity.value,
        "execution_integrity": outcome.execution_integrity.value,
    }


def result_r_value(record: Mapping[str, Any]) -> float | None:
    """Stored result_r if it is known evidence; None otherwise (never 0)."""
    result = record.get("result_r") or {}
    if result.get("status") in (EvidenceStatus.OBSERVED.value, EvidenceStatus.DERIVED.value):
        value = result.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
    return None
