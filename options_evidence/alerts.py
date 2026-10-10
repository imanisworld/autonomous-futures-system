"""Alert / surfacing model built only on canonical signal state.

An alert is a *projection* of a ``ProspectiveSignal``: it never reads scanner
rows, never re-derives a trigger, and never decides trade authority. Every
alert states ``trade_authority: False``; authority lives in the fitness /
human-approval layer.

``NEAR_TRIGGER`` is the only derived kind. WATCHING is two-sided (#1145): a
watching structure has no direction, trigger or invalidation until the first
break, so the distance is to the *nearest* boundary in units of the setup
range (``boundary_high - boundary_low``), and the alert names that side
(``near_side`` HIGH/LOW). It never implies a direction. The threshold is a
required policy input -- there is no default.

Delivery is separate and **off by default** (``DeliveryPolicy.enabled=False``).
``AlertLedger`` suppresses repeats of the same (signal_id, kind), so a signal
re-observed every cycle cannot spam a channel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Iterable

from .signal import IntegrityStatus, LifecycleState, ProspectiveSignal

SCHEMA = "options-signal-alert-v2"


class AlertKind(str, Enum):
    WATCHING = "WATCHING"
    NEAR_TRIGGER = "NEAR_TRIGGER"
    TRIGGERED = "TRIGGERED"
    MISSED_GAP = "MISSED_GAP"
    MISSED_LATE = "MISSED_LATE"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    DATA_BLOCKED = "DATA_BLOCKED"
    AMBIGUOUS = "AMBIGUOUS"


_STATE_TO_KIND = {
    LifecycleState.WATCHING: AlertKind.WATCHING,
    LifecycleState.TRIGGERED: AlertKind.TRIGGERED,
    LifecycleState.MISSED_GAP: AlertKind.MISSED_GAP,
    LifecycleState.MISSED_LATE: AlertKind.MISSED_LATE,
    LifecycleState.INVALIDATED: AlertKind.INVALIDATED,
    LifecycleState.EXPIRED: AlertKind.EXPIRED,
    LifecycleState.DATA_BLOCKED: AlertKind.DATA_BLOCKED,
    LifecycleState.AMBIGUOUS: AlertKind.AMBIGUOUS,
    # OUTCOME_CLOSED is research bookkeeping, not a surfaced alert.
}


@dataclass(frozen=True)
class SignalAlert:
    signal_id: str
    structure_id: str
    kind: AlertKind
    ticker: str
    direction: str | None  # None while WATCHING (two-sided)
    timeframe: str
    pattern: str
    strategy: str
    strategy_epoch: str
    boundary_high: float
    boundary_low: float
    trigger: float | None  # None while WATCHING
    invalidation: float | None
    as_of: datetime
    data_integrity: IntegrityStatus
    signal_integrity: IntegrityStatus
    prospective_catch: bool
    distance_to_trigger_r: float | None = None
    near_side: str | None = None  # WATCHING only: "HIGH" / "LOW"

    @property
    def trade_authority(self) -> bool:
        return False

    def to_record(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "signal_id": self.signal_id,
            "structure_id": self.structure_id,
            "kind": self.kind.value,
            "ticker": self.ticker,
            "direction": self.direction,
            "timeframe": self.timeframe,
            "pattern": self.pattern,
            "strategy": self.strategy,
            "strategy_epoch": self.strategy_epoch,
            "boundary_high": self.boundary_high,
            "boundary_low": self.boundary_low,
            "trigger": self.trigger,
            "invalidation": self.invalidation,
            "as_of": self.as_of.isoformat(),
            "data_integrity": self.data_integrity.value,
            "signal_integrity": self.signal_integrity.value,
            "prospective_catch": self.prospective_catch,
            "distance_to_trigger_r": self.distance_to_trigger_r,
            "near_side": self.near_side,
            "trade_authority": False,
            "advisory_note": (
                "Lifecycle notice only. A TRIGGERED state is not a validated trade unless "
                "prospective_catch is true and integrity is VALID. Trade authority is decided elsewhere."
            ),
        }


def _finite_number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} is out of range") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    if positive and number <= 0:
        raise ValueError(f"{label} must be > 0")
    return number


def distance_to_trigger_r(signal: ProspectiveSignal, last_price: float) -> tuple[float, str | None]:
    """Remaining move to a trigger in units of the setup range.

    WATCHING (two-sided): distance to the nearer boundary and that side;
    <= 0 means price is through it but the watcher has not resolved yet.
    Resolved: distance to the resolved trigger (side None).
    """
    price = _finite_number(last_price, "last_price", positive=True)
    unit = signal.levels.boundary_high - signal.levels.boundary_low
    if not (math.isfinite(unit) and unit > 0):
        raise ValueError("setup range must be finite and > 0")
    if signal.direction is None:
        to_high = (signal.levels.boundary_high - price) / unit
        to_low = (price - signal.levels.boundary_low) / unit
        return (to_high, "HIGH") if to_high <= to_low else (to_low, "LOW")
    if signal.direction == "LONG":
        return (signal.trigger - price) / unit, None  # type: ignore[operator]
    return (price - signal.trigger) / unit, None  # type: ignore[operator]


def alert_for(
    signal: ProspectiveSignal,
    *,
    as_of: datetime,
    last_price: float | None = None,
    near_trigger_r: float | None = None,
) -> SignalAlert | None:
    """The single alert the canonical state supports right now, or None."""
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    kind = _STATE_TO_KIND.get(signal.state)
    if kind is None:
        return None
    distance = side = None
    if signal.state is LifecycleState.WATCHING and last_price is not None:
        if near_trigger_r is None:
            raise ValueError("near_trigger_r must be an explicit positive policy value")
        threshold = _finite_number(near_trigger_r, "near_trigger_r", positive=True)
        price = _finite_number(last_price, "last_price", positive=True)
        distance, side = distance_to_trigger_r(signal, price)
        # Past the trigger while still WATCHING means the watcher has not
        # resolved it yet; surface NEAR_TRIGGER, never a synthetic TRIGGERED.
        if distance <= threshold:
            kind = AlertKind.NEAR_TRIGGER
    return SignalAlert(
        signal_id=signal.signal_id,
        structure_id=signal.structure_id,
        kind=kind,
        ticker=signal.ticker,
        direction=signal.direction,
        timeframe=signal.timeframe,
        pattern=signal.pattern,
        strategy=signal.strategy,
        strategy_epoch=signal.strategy_epoch,
        boundary_high=signal.levels.boundary_high,
        boundary_low=signal.levels.boundary_low,
        trigger=signal.trigger,
        invalidation=signal.invalidation,
        as_of=as_of,
        data_integrity=signal.data_integrity,
        signal_integrity=signal.signal_integrity,
        prospective_catch=signal.is_prospective_catch,
        distance_to_trigger_r=distance,
        near_side=side,
    )


@dataclass
class AlertLedger:
    """Suppresses repeats: each (signal_id, kind) surfaces at most once."""

    _seen: set[tuple[str, AlertKind]] = field(default_factory=set)

    def admit(self, alert: SignalAlert | None) -> SignalAlert | None:
        if alert is None:
            return None
        key = (alert.signal_id, alert.kind)
        if key in self._seen:
            return None
        self._seen.add(key)
        return alert

    def admit_many(self, alerts: Iterable[SignalAlert | None]) -> list[SignalAlert]:
        return [a for a in (self.admit(x) for x in alerts) if a is not None]


@dataclass(frozen=True)
class DeliveryPolicy:
    """Where admitted alerts may be sent. Off unless an operator turns it on."""

    enabled: bool = False
    kinds: frozenset[AlertKind] = frozenset()
    channel: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("DeliveryPolicy.enabled must be an exact bool")
        if not isinstance(self.kinds, frozenset) or not all(isinstance(kind, AlertKind) for kind in self.kinds):
            raise ValueError("DeliveryPolicy.kinds must be a frozenset of AlertKind")
        if not isinstance(self.channel, str):
            raise ValueError("DeliveryPolicy.channel must be a string")

    def deliverable(self, alert: SignalAlert) -> bool:
        if not isinstance(alert, SignalAlert):
            raise ValueError("deliverable requires a SignalAlert")
        return self.enabled and bool(self.channel.strip()) and alert.kind in self.kinds
