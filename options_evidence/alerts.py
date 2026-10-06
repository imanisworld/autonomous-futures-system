"""Alert / surfacing model built only on canonical signal state.

An alert is a *projection* of a ``ProspectiveSignal``: it never reads scanner
rows, never re-derives a trigger, and never decides trade authority. Every
alert states ``trade_authority: False``; authority lives in the fitness /
human-approval layer.

``NEAR_TRIGGER`` is the only derived kind: a WATCHING signal whose last price
is within ``near_trigger_r`` risk units of its trigger. The distance is a
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

from .signal import LifecycleState, ProspectiveSignal

SCHEMA = "options-signal-alert-v1"


class AlertKind(str, Enum):
    WATCHING = "WATCHING"
    NEAR_TRIGGER = "NEAR_TRIGGER"
    TRIGGERED = "TRIGGERED"
    MISSED_GAP = "MISSED_GAP"
    MISSED_LATE = "MISSED_LATE"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


_STATE_TO_KIND = {
    LifecycleState.WATCHING: AlertKind.WATCHING,
    LifecycleState.TRIGGERED: AlertKind.TRIGGERED,
    LifecycleState.MISSED_GAP: AlertKind.MISSED_GAP,
    LifecycleState.MISSED_LATE: AlertKind.MISSED_LATE,
    LifecycleState.INVALIDATED: AlertKind.INVALIDATED,
    LifecycleState.EXPIRED: AlertKind.EXPIRED,
    # OUTCOME_CLOSED is research bookkeeping, not a surfaced alert.
}


@dataclass(frozen=True)
class SignalAlert:
    signal_id: str
    structure_id: str
    kind: AlertKind
    ticker: str
    direction: str
    timeframe: str
    pattern: str
    strategy: str
    strategy_epoch: str
    trigger: float
    invalidation: float
    as_of: datetime
    distance_to_trigger_r: float | None = None

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
            "trigger": self.trigger,
            "invalidation": self.invalidation,
            "as_of": self.as_of.isoformat(),
            "distance_to_trigger_r": self.distance_to_trigger_r,
            "trade_authority": False,
            "advisory_note": "Lifecycle notice only. Trade authority is decided elsewhere.",
        }


def distance_to_trigger_r(signal: ProspectiveSignal, last_price: float) -> float:
    """Remaining move to the trigger in the signal's own risk units (>= 0 before the break)."""
    unit = abs(signal.trigger - signal.invalidation)
    if signal.direction == "LONG":
        return (signal.trigger - last_price) / unit
    return (last_price - signal.trigger) / unit


def alert_for(
    signal: ProspectiveSignal,
    *,
    as_of: datetime,
    last_price: float | None = None,
    near_trigger_r: float | None = None,
) -> SignalAlert | None:
    """The single alert the canonical state supports right now, or None."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    kind = _STATE_TO_KIND.get(signal.state)
    if kind is None:
        return None
    distance = None
    if signal.state is LifecycleState.WATCHING and last_price is not None:
        if near_trigger_r is None or not (math.isfinite(near_trigger_r) and near_trigger_r > 0):
            raise ValueError("near_trigger_r must be an explicit positive policy value")
        if not math.isfinite(float(last_price)) or float(last_price) <= 0:
            raise ValueError("last_price must be finite and > 0")
        distance = distance_to_trigger_r(signal, float(last_price))
        # Past the trigger while still WATCHING means the watcher has not
        # resolved it yet; surface NEAR_TRIGGER, never a synthetic TRIGGERED.
        if distance <= near_trigger_r:
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
        trigger=signal.trigger,
        invalidation=signal.invalidation,
        as_of=as_of,
        distance_to_trigger_r=distance,
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

    def deliverable(self, alert: SignalAlert) -> bool:
        return bool(self.enabled and self.channel.strip() and alert.kind in self.kinds)
