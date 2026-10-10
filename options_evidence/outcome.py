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
  the marks they need; otherwise they are ``UNAVAILABLE``;
* every field is type-checked on construction: prices, R and P&L are finite
  non-bool numbers, hit times are timezone-aware datetimes, ``executed`` is an
  exact bool -- nothing is coerced;
* only a prospective catch (``ProspectiveSignal.is_prospective_catch``) can be
  an executed outcome. A miss (MISSED_LATE / MISSED_GAP, including after
  OUTCOME_CLOSED) is recorded with ``pnl_basis="counterfactual"``: no executed
  flag, no P&L, no OBSERVED R, and ``result_r_value`` never reads it as a
  trade result. An outcome can never report better signal integrity than its
  signal.

Pure: no I/O, no provider calls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from .signal import MISSED_STATES, IntegrityStatus, ProspectiveSignal
from .strategy_epochs import EpochRegistry

SCHEMA = "options-outcome-evidence-v1"


class EvidenceStatus(str, Enum):
    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class OutcomeError(ValueError):
    pass


PNL_BASES = ("executed", "paper_equivalent", "counterfactual")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value: Any, label: str) -> float:
    if not _is_number(value):
        raise OutcomeError(f"{label} must be a number, not {type(value).__name__}")
    try:
        number = float(value)
    except OverflowError as exc:
        raise OutcomeError(f"{label} is out of range") from exc
    if not math.isfinite(number):
        raise OutcomeError(f"{label} must be finite")
    return number


@dataclass(frozen=True)
class Measured:
    value: Any
    status: EvidenceStatus
    reason: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, EvidenceStatus):
            raise OutcomeError("Measured.status must be an EvidenceStatus")
        if not isinstance(self.reason, str) or not isinstance(self.source, str):
            raise OutcomeError("Measured reason/source must be strings")
        if self.status in (EvidenceStatus.UNAVAILABLE, EvidenceStatus.NOT_APPLICABLE):
            if self.value is not None:
                raise OutcomeError(f"{self.status.value} evidence must not carry a value")
            if not self.reason.strip():
                raise OutcomeError(f"{self.status.value} evidence requires a reason")
        else:
            if self.value is None:
                raise OutcomeError(f"{self.status.value} evidence requires a value")
            if _is_number(self.value):
                _finite(self.value, "measured value")
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
        if not isinstance(self.at, datetime) or self.at.tzinfo is None or self.at.utcoffset() is None:
            raise OutcomeError("premium mark time must be timezone-aware")
        if not isinstance(self.source, str) or not self.source.strip():
            raise OutcomeError("premium mark requires a quote source")
        for name in ("bid", "ask"):
            if _finite(getattr(self, name), f"premium mark {name}") < 0:
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
    if signal.direction is None or signal.trigger is None or signal.invalidation is None:
        raise OutcomeError("R requires a resolved signal (direction, trigger, invalidation)")
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

    def __post_init__(self) -> None:
        # B3: exact types; anything that could manufacture a nonsensical R fails here.
        for name in ("signal_id", "strategy_epoch", "pnl_basis", "premium_path_reason"):
            if not isinstance(getattr(self, name), str):
                raise OutcomeError(f"{name} must be a string")
        if not isinstance(self.executed, bool):
            raise OutcomeError(f"executed must be a bool, not {self.executed!r}")
        if self.pnl_basis not in PNL_BASES:
            raise OutcomeError(f"pnl_basis must be one of {PNL_BASES}")
        if _finite(self.invalidation, "invalidation") <= 0:
            raise OutcomeError("invalidation must be > 0")
        for name in _PRICE_FIELDS + _TIME_FIELDS + _NUMBER_FIELDS + _TEXT_FIELDS:
            measured = getattr(self, name)
            if not isinstance(measured, Measured):
                raise OutcomeError(f"{name} must be Measured")
            if not measured.known:
                continue
            value = measured.value
            if name in _PRICE_FIELDS:
                floor_ok = _finite(value, name) > 0 if name not in _PREMIUM_FIELDS else _finite(value, name) >= 0
                if not floor_ok:
                    raise OutcomeError(f"{name} is out of range")
            elif name in _NUMBER_FIELDS:
                _finite(value, name)
            elif name in _TIME_FIELDS:
                if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                    raise OutcomeError(f"{name} must be a timezone-aware datetime")
            elif not isinstance(value, str) or not value.strip():
                raise OutcomeError(f"{name} must be a non-empty string")
        if not isinstance(self.premium_path, tuple) or not all(isinstance(m, PremiumMark) for m in self.premium_path):
            raise OutcomeError("premium_path must be a tuple of PremiumMark")
        if not isinstance(self.premium_path_status, PathStatus):
            raise OutcomeError("premium_path_status must be a PathStatus")
        for name in ("data_integrity", "signal_integrity", "execution_integrity"):
            if not isinstance(getattr(self, name), IntegrityStatus):
                raise OutcomeError(f"{name} must be an IntegrityStatus")
        if not isinstance(self.notes, tuple) or not all(isinstance(n, str) for n in self.notes):
            raise OutcomeError("notes must be a tuple of strings")

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


_PREMIUM_FIELDS = ("premium_entry", "premium_stop")
_PRICE_FIELDS = ("target_1", "target_2", "mae_price", "mfe_price", *_PREMIUM_FIELDS)
_TIME_FIELDS = ("t1_hit_at", "t2_hit_at", "invalidation_hit_at")
_NUMBER_FIELDS = ("result_r", "gross_pnl", "net_pnl")
_TEXT_FIELDS = ("trim_event", "runner_outcome")


def validate_outcome(
    outcome: OutcomeEvidence, signal: ProspectiveSignal, registry: EpochRegistry | None = None
) -> list[str]:
    """Consistency problems; an empty list means the record is honest and complete enough to store.

    With ``registry``, a signal's validated epoch must be that registry's
    entry (a hand-built ``StrategyEpoch`` cannot certify a catch).
    """
    problems: list[str] = []
    if registry is not None and signal.registered_epoch is not None:
        if registry.get(signal.strategy, signal.strategy_epoch) != signal.registered_epoch:
            problems.append("signal epoch is not the registry's epoch definition")
    if signal.direction is None or signal.invalidation is None:
        return ["outcome requires a resolved signal; a two-sided WATCHING structure has no risk unit"]
    if outcome.signal_id != signal.signal_id:
        problems.append("outcome signal_id does not match the signal")
    if outcome.strategy_epoch != signal.strategy_epoch:
        problems.append("outcome strategy_epoch does not match the signal")
    if abs(outcome.invalidation - signal.invalidation) > 1e-9:
        problems.append("outcome invalidation differs from the signal's frozen invalidation")
    if outcome.pnl_basis not in PNL_BASES:
        problems.append(f"pnl_basis must be one of {PNL_BASES}")
    if outcome.executed and outcome.pnl_basis != "executed":
        problems.append("an executed outcome must use pnl_basis=executed")
    if not outcome.executed and outcome.pnl_basis == "executed":
        problems.append("a non-executed outcome cannot claim executed P&L")
    if not outcome.executed and outcome.execution_integrity not in (
        IntegrityStatus.NOT_APPLICABLE,
        IntegrityStatus.UNKNOWN,
    ):
        problems.append("execution_integrity applies only to executed outcomes")
    # B2: only a prospective catch can be a trade; a miss stays a miss.
    resolution = signal.resolution
    if outcome.executed and not signal.is_prospective_catch:
        problems.append(
            f"an executed outcome requires a prospective catch; {resolution.value if resolution else 'unresolved'} "
            "signal is never a trade"
        )
    # Anything that is not a verified prospective catch (a miss, a late or
    # gapped TRIGGERED capture, one pending SIP, an unregistered epoch, ...)
    # is counterfactual only: hypothetical DERIVED analytics may be kept, but
    # never a trade basis, P&L or a realised R.
    if not signal.is_prospective_catch:
        missed = resolution in MISSED_STATES
        label = resolution.value if missed else "non-catch"  # type: ignore[union-attr]
        noun = "a missed signal" if missed else "a non-catch outcome"
        if outcome.pnl_basis != "counterfactual":
            problems.append(f"a {label} outcome must use pnl_basis=counterfactual")
        if outcome.gross_pnl.known or outcome.net_pnl.known:
            problems.append(f"{noun} has no P&L")
        if outcome.result_r.status is EvidenceStatus.OBSERVED:
            problems.append(f"{noun} has no observed (realised) R")
    if outcome.signal_integrity is IntegrityStatus.VALID and signal.signal_integrity is not IntegrityStatus.VALID:
        problems.append("outcome cannot report VALID signal integrity for a signal that is not VALID")
    if outcome.data_integrity is not IntegrityStatus.INVALID and signal.data_integrity is IntegrityStatus.INVALID:
        problems.append("outcome cannot launder INVALID signal data integrity")
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
        "resolution_state": signal.resolution.value if signal.resolution else None,
        "prospective_catch": signal.is_prospective_catch,
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
    """Stored result_r if it is known trade evidence; None otherwise (never 0).

    Only a verified prospective catch (``prospective_catch is True``) with a
    non-counterfactual basis is a trade result. A counterfactual, late, missed
    or otherwise non-catch outcome is never read as one.
    """
    if not isinstance(record, Mapping) or record.get("pnl_basis") not in ("executed", "paper_equivalent"):
        return None
    if record.get("prospective_catch") is not True:
        return None
    result = record.get("result_r") or {}
    if not isinstance(result, Mapping):
        return None
    if result.get("status") in (EvidenceStatus.OBSERVED.value, EvidenceStatus.DERIVED.value):
        value = result.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
    return None
