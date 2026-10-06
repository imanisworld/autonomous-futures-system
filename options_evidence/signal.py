"""Canonical prospective signal: one market structure, one identity, one lifecycle.

Before this module, the scanner row, the prospective watcher record, and the
evidence journal each carried their own notion of "the setup". Downstream
consumers (context, contract plan, outcome, fitness, alerts) now key on:

* ``structure_id`` -- derived only from the market structure itself (ticker,
  timeframe, pattern, direction, structure close time, trigger, invalidation).
  Every component that sees the same structure computes the same id.
* ``signal_id`` -- ``structure_id`` evaluated under one ``strategy`` /
  ``strategy_epoch``. One structure may be judged by several strategies; each
  gets its own signal, never a merged one.

The lifecycle is event-sourced and append-only. ``SignalJournal`` folds
events into a ``ProspectiveSignal`` snapshot and refuses illegal transitions,
identity drift, clock inversions, duplicate opens, and authority claims the
signal cannot carry. Serialisation is pure (``to_record`` / ``from_record``);
this module owns no file writer, so it cannot compete with the runtime
collector's persistence.

The runtime watcher (WATCHING -> TRIGGERED / INVALIDATED / EXPIRED, dedupe,
MISSED_LATE, gap-through) is implemented separately. This module is the
schema it should emit into; field mapping from that runtime is a follow-up
once it lands.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping

from .strategy_epochs import LEGACY_UNVERSIONED, UNREGISTERED_EPOCH

SCHEMA = "options-prospective-signal-v1"
PRICE_DECIMALS = 4


class LifecycleState(str, Enum):
    WATCHING = "WATCHING"
    TRIGGERED = "TRIGGERED"
    MISSED_LATE = "MISSED_LATE"
    MISSED_GAP = "MISSED_GAP"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    OUTCOME_CLOSED = "OUTCOME_CLOSED"


TERMINAL_STATES = frozenset(
    {LifecycleState.INVALIDATED, LifecycleState.EXPIRED, LifecycleState.OUTCOME_CLOSED}
)

# MISSED_* signals keep an outcome (counterfactual, executed=False) so that
# timing failures are measured rather than silently dropped.
ALLOWED_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.WATCHING: frozenset(
        {
            LifecycleState.TRIGGERED,
            LifecycleState.MISSED_LATE,
            LifecycleState.MISSED_GAP,
            LifecycleState.INVALIDATED,
            LifecycleState.EXPIRED,
        }
    ),
    LifecycleState.TRIGGERED: frozenset({LifecycleState.OUTCOME_CLOSED}),
    LifecycleState.MISSED_LATE: frozenset({LifecycleState.OUTCOME_CLOSED}),
    LifecycleState.MISSED_GAP: frozenset({LifecycleState.OUTCOME_CLOSED}),
    LifecycleState.INVALIDATED: frozenset(),
    LifecycleState.EXPIRED: frozenset(),
    LifecycleState.OUTCOME_CLOSED: frozenset(),
}


class IntegrityStatus(str, Enum):
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class LifecycleError(ValueError):
    """An event would corrupt the canonical signal; it is refused, not applied."""


def _utc(value: datetime | str | None, label: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LifecycleError(f"{label} is not ISO-8601: {value!r}") from exc
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise LifecycleError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _price(value: float, label: str) -> float:
    if isinstance(value, bool):
        raise LifecycleError(f"{label} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LifecycleError(f"{label} must be a number") from exc
    if not math.isfinite(number) or number <= 0:
        raise LifecycleError(f"{label} must be finite and > 0")
    return round(number, PRICE_DECIMALS)


def _digest(prefix: str, payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True)
class StructureIdentity:
    ticker: str
    timeframe: str
    pattern: str
    direction: str
    structure_close_time: datetime
    trigger: float
    invalidation: float

    def __post_init__(self) -> None:
        ticker = self.ticker.strip().upper()
        direction = self.direction.strip().upper()
        if not ticker or not self.timeframe.strip() or not self.pattern.strip():
            raise LifecycleError("ticker, timeframe and pattern are required")
        if direction not in ("LONG", "SHORT"):
            raise LifecycleError("direction must be LONG or SHORT")
        trigger = _price(self.trigger, "trigger")
        invalidation = _price(self.invalidation, "invalidation")
        if direction == "LONG" and not invalidation < trigger:
            raise LifecycleError("LONG invalidation must be below trigger")
        if direction == "SHORT" and not invalidation > trigger:
            raise LifecycleError("SHORT invalidation must be above trigger")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "timeframe", self.timeframe.strip())
        object.__setattr__(self, "pattern", self.pattern.strip())
        object.__setattr__(self, "trigger", trigger)
        object.__setattr__(self, "invalidation", invalidation)
        object.__setattr__(
            self, "structure_close_time", _utc(self.structure_close_time, "structure_close_time")
        )

    @property
    def structure_id(self) -> str:
        return _digest(
            "st",
            {
                "ticker": self.ticker,
                "timeframe": self.timeframe,
                "pattern": self.pattern,
                "direction": self.direction,
                "structure_close_time": _iso(self.structure_close_time),
                "trigger": f"{self.trigger:.{PRICE_DECIMALS}f}",
                "invalidation": f"{self.invalidation:.{PRICE_DECIMALS}f}",
            },
        )


def make_signal_id(structure_id: str, strategy: str, strategy_epoch: str) -> str:
    return _digest("sg", {"structure_id": structure_id, "strategy": strategy, "strategy_epoch": strategy_epoch})


@dataclass(frozen=True)
class SignalLinks:
    scanner_sighting_ids: tuple[str, ...] = ()
    watcher_refs: tuple[str, ...] = ()
    context_snapshot_ids: tuple[str, ...] = ()
    contract_plan_refs: tuple[str, ...] = ()
    outcome_ref: str | None = None

    def merged(self, other: "SignalLinks") -> "SignalLinks":
        def union(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(dict.fromkeys((*a, *b)))

        if self.outcome_ref and other.outcome_ref and self.outcome_ref != other.outcome_ref:
            raise LifecycleError("a signal links to exactly one outcome record")
        return SignalLinks(
            scanner_sighting_ids=union(self.scanner_sighting_ids, other.scanner_sighting_ids),
            watcher_refs=union(self.watcher_refs, other.watcher_refs),
            context_snapshot_ids=union(self.context_snapshot_ids, other.context_snapshot_ids),
            contract_plan_refs=union(self.contract_plan_refs, other.contract_plan_refs),
            outcome_ref=self.outcome_ref or other.outcome_ref,
        )


@dataclass(frozen=True)
class StateChange:
    state: LifecycleState
    market_time: datetime | None
    detected_at: datetime
    reason: str


@dataclass(frozen=True)
class ProspectiveSignal:
    signal_id: str
    structure_id: str
    identity: StructureIdentity
    strategy: str
    strategy_epoch: str
    setup_ready_time: datetime
    first_seen_time: datetime
    data_source: str
    observation_only: bool
    execution_authority: bool
    state: LifecycleState = LifecycleState.WATCHING
    trigger_market_time: datetime | None = None
    trigger_detection_time: datetime | None = None
    data_integrity: IntegrityStatus = IntegrityStatus.UNKNOWN
    signal_integrity: IntegrityStatus = IntegrityStatus.UNKNOWN
    execution_integrity: IntegrityStatus = IntegrityStatus.NOT_APPLICABLE
    links: SignalLinks = field(default_factory=SignalLinks)
    history: tuple[StateChange, ...] = ()

    # Convenience views of the identity required by downstream consumers.
    @property
    def ticker(self) -> str:
        return self.identity.ticker

    @property
    def direction(self) -> str:
        return self.identity.direction

    @property
    def timeframe(self) -> str:
        return self.identity.timeframe

    @property
    def pattern(self) -> str:
        return self.identity.pattern

    @property
    def trigger(self) -> float:
        return self.identity.trigger

    @property
    def invalidation(self) -> float:
        return self.identity.invalidation

    @property
    def structure_close_time(self) -> datetime:
        return self.identity.structure_close_time

    @property
    def prearmed(self) -> bool | None:
        """Seen before the trigger traded (None until a trigger time exists)."""
        if self.trigger_market_time is None:
            return None
        return self.first_seen_time <= self.trigger_market_time

    @property
    def detection_lag_seconds(self) -> float | None:
        if self.trigger_market_time is None or self.trigger_detection_time is None:
            return None
        return (self.trigger_detection_time - self.trigger_market_time).total_seconds()

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def epoch_registered(self) -> bool:
        return self.strategy_epoch not in (LEGACY_UNVERSIONED, UNREGISTERED_EPOCH)


# ── events ───────────────────────────────────────────────────────────────────


class EventType(str, Enum):
    OPENED = "OPENED"
    STATE = "STATE"
    LINK = "LINK"
    INTEGRITY = "INTEGRITY"


@dataclass(frozen=True)
class SignalEvent:
    signal_id: str
    seq: int
    event_type: EventType
    detected_at: datetime
    state: LifecycleState | None = None
    market_time: datetime | None = None
    reason: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)


def open_signal(
    identity: StructureIdentity,
    *,
    strategy: str,
    strategy_epoch: str,
    setup_ready_time: datetime,
    first_seen_time: datetime,
    data_source: str,
    observation_only: bool = True,
    links: SignalLinks = SignalLinks(),
) -> SignalEvent:
    """Build the OPENED event for a structure first seen in WATCHING.

    Execution authority is never set here. A signal records authority only
    through ``authority_event`` with a reference to a separate, human-approved
    authority record.
    """
    if not strategy.strip() or not strategy_epoch.strip() or not data_source.strip():
        raise LifecycleError("strategy, strategy_epoch and data_source are required")
    ready = _utc(setup_ready_time, "setup_ready_time")
    seen = _utc(first_seen_time, "first_seen_time")
    assert ready is not None and seen is not None
    if ready < identity.structure_close_time:
        raise LifecycleError("setup_ready_time cannot precede structure_close_time")
    if seen < identity.structure_close_time:
        raise LifecycleError("first_seen_time cannot precede structure_close_time")
    signal_id = make_signal_id(identity.structure_id, strategy, strategy_epoch)
    return SignalEvent(
        signal_id=signal_id,
        seq=0,
        event_type=EventType.OPENED,
        detected_at=seen,
        state=LifecycleState.WATCHING,
        payload={
            "identity": identity,
            "strategy": strategy,
            "strategy_epoch": strategy_epoch,
            "setup_ready_time": ready,
            "data_source": data_source,
            "observation_only": bool(observation_only),
            "links": links,
        },
    )


def _apply_state(signal: ProspectiveSignal, event: SignalEvent) -> ProspectiveSignal:
    target = event.state
    if target is None:
        raise LifecycleError("STATE event requires a state")
    if target not in ALLOWED_TRANSITIONS[signal.state]:
        raise LifecycleError(f"illegal transition {signal.state.value} -> {target.value}")
    if not event.reason.strip():
        raise LifecycleError(f"{target.value} requires a reason")
    market_time = _utc(event.market_time, "market_time")
    updates: dict[str, Any] = {"state": target}
    if target in (LifecycleState.TRIGGERED, LifecycleState.MISSED_LATE, LifecycleState.MISSED_GAP):
        if market_time is None:
            raise LifecycleError(f"{target.value} requires the trigger market time")
        if market_time < signal.structure_close_time:
            raise LifecycleError("trigger market time cannot precede structure close")
        if event.detected_at < market_time:
            raise LifecycleError("trigger detection cannot precede the market trigger")
        updates["trigger_market_time"] = market_time
        updates["trigger_detection_time"] = event.detected_at
    if target is LifecycleState.MISSED_GAP and not event.payload.get("gap_open_price"):
        raise LifecycleError("MISSED_GAP requires the gap open price that skipped the trigger")
    if target is LifecycleState.TRIGGERED and signal.first_seen_time > market_time:  # type: ignore[operator]
        # Seen only after the trigger traded: that is a late capture, never a
        # clean trigger. Callers must emit MISSED_LATE instead.
        raise LifecycleError("structure first seen after the trigger traded; record MISSED_LATE")
    if target is LifecycleState.OUTCOME_CLOSED and not event.payload.get("outcome_ref"):
        raise LifecycleError("OUTCOME_CLOSED requires outcome_ref")
    links = signal.links
    if target is LifecycleState.OUTCOME_CLOSED:
        links = links.merged(SignalLinks(outcome_ref=str(event.payload["outcome_ref"])))
    change = StateChange(target, market_time, event.detected_at, event.reason)
    return replace(signal, **updates, links=links, history=(*signal.history, change))


def _apply_integrity(signal: ProspectiveSignal, event: SignalEvent) -> ProspectiveSignal:
    updates: dict[str, Any] = {}
    for key in ("data_integrity", "signal_integrity", "execution_integrity"):
        if key in event.payload:
            updates[key] = IntegrityStatus(event.payload[key])
    if not updates:
        raise LifecycleError("INTEGRITY event must set at least one integrity status")
    if "execution_authority" in event.payload:
        claimed = bool(event.payload["execution_authority"])
        if claimed:
            if signal.observation_only:
                raise LifecycleError("an observation-only signal cannot carry execution authority")
            if not signal.epoch_registered:
                raise LifecycleError("execution authority requires a registered strategy epoch")
            if not str(event.payload.get("authority_ref", "")).strip():
                raise LifecycleError("execution authority requires an authority_ref")
        updates["execution_authority"] = claimed
    return replace(signal, **updates)


class SignalJournal:
    """In-memory, append-only fold of signal events with structure-level dedupe."""

    def __init__(self) -> None:
        self._signals: dict[str, ProspectiveSignal] = {}
        self._events: dict[str, list[SignalEvent]] = {}

    def __contains__(self, signal_id: str) -> bool:
        return signal_id in self._signals

    def get(self, signal_id: str) -> ProspectiveSignal | None:
        return self._signals.get(signal_id)

    def events(self, signal_id: str) -> tuple[SignalEvent, ...]:
        return tuple(self._events.get(signal_id, ()))

    def signals(self) -> tuple[ProspectiveSignal, ...]:
        return tuple(self._signals.values())

    def next_seq(self, signal_id: str) -> int:
        return len(self._events.get(signal_id, ()))

    def append(self, event: SignalEvent) -> ProspectiveSignal:
        existing = self._events.get(event.signal_id, [])
        if event.seq < len(existing):
            if existing[event.seq] == event:
                return self._signals[event.signal_id]  # idempotent replay
            raise LifecycleError(f"{event.signal_id} seq {event.seq} already holds a different event")
        if event.seq != len(existing):
            raise LifecycleError(f"{event.signal_id} expected seq {len(existing)}, got {event.seq}")
        detected = _utc(event.detected_at, "detected_at")
        if existing and detected < existing[-1].detected_at:  # type: ignore[operator]
            raise LifecycleError("events must be appended in detection-time order")

        if event.event_type is EventType.OPENED:
            if existing:
                raise LifecycleError(f"duplicate OPENED for {event.signal_id}")
            p = event.payload
            identity: StructureIdentity = p["identity"]
            expected = make_signal_id(identity.structure_id, p["strategy"], p["strategy_epoch"])
            if expected != event.signal_id:
                raise LifecycleError("signal_id does not match identity/strategy/epoch")
            signal = ProspectiveSignal(
                signal_id=event.signal_id,
                structure_id=identity.structure_id,
                identity=identity,
                strategy=p["strategy"],
                strategy_epoch=p["strategy_epoch"],
                setup_ready_time=p["setup_ready_time"],
                first_seen_time=detected,  # type: ignore[arg-type]
                data_source=p["data_source"],
                observation_only=p["observation_only"],
                execution_authority=False,
                links=p.get("links", SignalLinks()),
                history=(StateChange(LifecycleState.WATCHING, None, detected, "opened"),),  # type: ignore[arg-type]
            )
        else:
            current = self._signals.get(event.signal_id)
            if current is None:
                raise LifecycleError(f"{event.signal_id} has no OPENED event")
            if current.terminal:
                raise LifecycleError(f"{event.signal_id} is terminal ({current.state.value})")
            if event.event_type is EventType.STATE:
                signal = _apply_state(current, event)
            elif event.event_type is EventType.LINK:
                signal = replace(current, links=current.links.merged(event.payload["links"]))
            elif event.event_type is EventType.INTEGRITY:
                signal = _apply_integrity(current, event)
            else:  # pragma: no cover - enum is closed
                raise LifecycleError(f"unknown event type {event.event_type}")

        self._events.setdefault(event.signal_id, []).append(event)
        self._signals[event.signal_id] = signal
        return signal

    def by_structure(self, structure_id: str) -> tuple[ProspectiveSignal, ...]:
        return tuple(s for s in self._signals.values() if s.structure_id == structure_id)


def state_event(
    journal: SignalJournal,
    signal_id: str,
    state: LifecycleState,
    *,
    detected_at: datetime,
    reason: str,
    market_time: datetime | None = None,
    payload: Mapping[str, Any] | None = None,
) -> SignalEvent:
    return SignalEvent(
        signal_id=signal_id,
        seq=journal.next_seq(signal_id),
        event_type=EventType.STATE,
        detected_at=detected_at,
        state=state,
        market_time=market_time,
        reason=reason,
        payload=dict(payload or {}),
    )


def link_event(journal: SignalJournal, signal_id: str, links: SignalLinks, *, detected_at: datetime) -> SignalEvent:
    return SignalEvent(
        signal_id=signal_id,
        seq=journal.next_seq(signal_id),
        event_type=EventType.LINK,
        detected_at=detected_at,
        payload={"links": links},
    )


def integrity_event(
    journal: SignalJournal, signal_id: str, *, detected_at: datetime, **statuses: Any
) -> SignalEvent:
    return SignalEvent(
        signal_id=signal_id,
        seq=journal.next_seq(signal_id),
        event_type=EventType.INTEGRITY,
        detected_at=detected_at,
        payload=dict(statuses),
    )


# ── serialisation (pure) ─────────────────────────────────────────────────────


def to_record(signal: ProspectiveSignal) -> dict[str, Any]:
    """Flat JSON-safe snapshot carrying every required identity/context field."""
    return {
        "schema": SCHEMA,
        "signal_id": signal.signal_id,
        "structure_id": signal.structure_id,
        "ticker": signal.ticker,
        "strategy": signal.strategy,
        "strategy_epoch": signal.strategy_epoch,
        "direction": signal.direction,
        "timeframe": signal.timeframe,
        "pattern": signal.pattern,
        "structure_close_time": _iso(signal.structure_close_time),
        "setup_ready_time": _iso(signal.setup_ready_time),
        "first_seen_time": _iso(signal.first_seen_time),
        "trigger": signal.trigger,
        "invalidation": signal.invalidation,
        "trigger_market_time": _iso(signal.trigger_market_time),
        "trigger_detection_time": _iso(signal.trigger_detection_time),
        "detection_lag_seconds": signal.detection_lag_seconds,
        "prearmed": signal.prearmed,
        "data_source": signal.data_source,
        "context_snapshot_ids": list(signal.links.context_snapshot_ids),
        "scanner_sighting_ids": list(signal.links.scanner_sighting_ids),
        "watcher_refs": list(signal.links.watcher_refs),
        "contract_plan_refs": list(signal.links.contract_plan_refs),
        "outcome_ref": signal.links.outcome_ref,
        "observation_only": signal.observation_only,
        "execution_authority": signal.execution_authority,
        "data_integrity": signal.data_integrity.value,
        "signal_integrity": signal.signal_integrity.value,
        "execution_integrity": signal.execution_integrity.value,
        "lifecycle_state": signal.state.value,
        "history": [
            {
                "state": c.state.value,
                "market_time": _iso(c.market_time),
                "detected_at": _iso(c.detected_at),
                "reason": c.reason,
            }
            for c in signal.history
        ],
    }


REQUIRED_RECORD_FIELDS = (
    "signal_id",
    "structure_id",
    "ticker",
    "strategy",
    "strategy_epoch",
    "direction",
    "timeframe",
    "pattern",
    "structure_close_time",
    "setup_ready_time",
    "first_seen_time",
    "trigger",
    "invalidation",
    "trigger_market_time",
    "trigger_detection_time",
    "data_source",
    "context_snapshot_ids",
    "observation_only",
    "execution_authority",
    "data_integrity",
    "signal_integrity",
    "execution_integrity",
    "lifecycle_state",
)


def verify_record(record: Mapping[str, Any]) -> list[str]:
    """Problems with a stored snapshot record; empty list means consistent.

    Recomputes structure_id/signal_id from the record's own fields, so a
    record whose identity was edited after the fact is detected.
    """
    problems = [f"missing {name}" for name in REQUIRED_RECORD_FIELDS if name not in record]
    if record.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA}")
    if problems:
        return problems
    try:
        identity = StructureIdentity(
            ticker=record["ticker"],
            timeframe=record["timeframe"],
            pattern=record["pattern"],
            direction=record["direction"],
            structure_close_time=record["structure_close_time"],
            trigger=record["trigger"],
            invalidation=record["invalidation"],
        )
    except LifecycleError as exc:
        return [f"identity invalid: {exc}"]
    if identity.structure_id != record["structure_id"]:
        problems.append("structure_id does not match the record's structure fields")
    if make_signal_id(identity.structure_id, record["strategy"], record["strategy_epoch"]) != record["signal_id"]:
        problems.append("signal_id does not match structure/strategy/epoch")
    if record["execution_authority"] and record["observation_only"]:
        problems.append("observation-only record claims execution authority")
    try:
        LifecycleState(record["lifecycle_state"])
        for key in ("data_integrity", "signal_integrity", "execution_integrity"):
            IntegrityStatus(record[key])
    except ValueError as exc:
        problems.append(str(exc))
    return problems


def dedupe_records(records: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """Group stored records by structure_id (read-only; never rewrites rows)."""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get("structure_id")), []).append(record)
    return grouped
