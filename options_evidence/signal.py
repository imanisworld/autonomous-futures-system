"""Canonical prospective signal: one market structure, one identity, one lifecycle.

Source of truth for structure detection is the merged setup-capture observer
(#1145, ``alert_ranker/setup_capture*.py``). This module does not re-detect
anything; it is the canonical record that downstream consumers (scanner
sightings, context, contract plan, outcome, fitness, alerts) key on, and
``options_evidence.capture_adapter`` folds #1145 journal rows into it.

Identity (aligned to #1145):

* ``structure_id`` is **exactly** the #1145 ``structure_key``:
  ``TICKER|timeframe|structure_close(UTC Z)|pattern``. Levels are attributes,
  not identity, so a completed-bar revision (#1145 ``SOURCE_DRIFT``) updates
  the same structure instead of creating a duplicate.
* ``signal_id`` is that structure judged under one ``strategy`` /
  ``strategy_epoch``. Several strategies on one structure are several signals,
  never a merge.
* WATCHING is **two-sided**: direction, trigger and invalidation are unknown
  until the first break resolves them (LONG: trigger = boundary_high,
  invalidation = boundary_low; SHORT: the reverse), as in #1145.

Lifecycle (event-sourced, append-only):

    WATCHING ─┬─> TRIGGERED ───┬─> OUTCOME_CLOSED
              │                └─> DATA_BLOCKED   (SIP reconciliation failed)
              ├─> MISSED_LATE ─┬─> OUTCOME_CLOSED (counterfactual)
              │                └─> DATA_BLOCKED
              ├─> MISSED_GAP ──┬─> OUTCOME_CLOSED (counterfactual; #1145 GAP_THROUGH_OPEN)
              │                └─> DATA_BLOCKED
              ├─> INVALIDATED / EXPIRED / DATA_BLOCKED / AMBIGUOUS (terminal)

Capture evidence that #1145 refines after resolution (``capture_late``,
``prospective_catch``, ``sip_crossed_at``, lag) arrives as OBSERVATION events;
it never changes identity or state.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .strategy_epochs import LEGACY_UNVERSIONED, UNREGISTERED_EPOCH

SCHEMA = "options-prospective-signal-v2"
LEVEL_DECIMALS = 4


class LifecycleState(str, Enum):
    WATCHING = "WATCHING"
    TRIGGERED = "TRIGGERED"
    MISSED_LATE = "MISSED_LATE"
    MISSED_GAP = "MISSED_GAP"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    DATA_BLOCKED = "DATA_BLOCKED"
    AMBIGUOUS = "AMBIGUOUS"
    OUTCOME_CLOSED = "OUTCOME_CLOSED"


RESOLVED_WITH_DIRECTION = frozenset(
    {LifecycleState.TRIGGERED, LifecycleState.MISSED_LATE, LifecycleState.MISSED_GAP}
)
TERMINAL_STATES = frozenset(
    {
        LifecycleState.INVALIDATED,
        LifecycleState.EXPIRED,
        LifecycleState.DATA_BLOCKED,
        LifecycleState.AMBIGUOUS,
        LifecycleState.OUTCOME_CLOSED,
    }
)
ALLOWED_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    LifecycleState.WATCHING: frozenset(
        {
            LifecycleState.TRIGGERED,
            LifecycleState.MISSED_LATE,
            LifecycleState.MISSED_GAP,
            LifecycleState.INVALIDATED,
            LifecycleState.EXPIRED,
            LifecycleState.DATA_BLOCKED,
            LifecycleState.AMBIGUOUS,
        }
    ),
    LifecycleState.TRIGGERED: frozenset({LifecycleState.OUTCOME_CLOSED, LifecycleState.DATA_BLOCKED}),
    LifecycleState.MISSED_LATE: frozenset({LifecycleState.OUTCOME_CLOSED, LifecycleState.DATA_BLOCKED}),
    LifecycleState.MISSED_GAP: frozenset({LifecycleState.OUTCOME_CLOSED, LifecycleState.DATA_BLOCKED}),
    LifecycleState.INVALIDATED: frozenset(),
    LifecycleState.EXPIRED: frozenset(),
    LifecycleState.DATA_BLOCKED: frozenset(),
    LifecycleState.AMBIGUOUS: frozenset(),
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
    if value is None or value == "":
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


def _level(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise LifecycleError(f"{label} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LifecycleError(f"{label} must be a number") from exc
    if not math.isfinite(number) or number <= 0:
        raise LifecycleError(f"{label} must be finite and > 0")
    return round(number, LEVEL_DECIMALS)


def capture_structure_key(ticker: str, timeframe: str, structure_close: datetime, pattern: str) -> str:
    """Byte-identical to #1145 ``alert_ranker.setup_capture.structure_key`` (parity-tested)."""
    close = _utc(structure_close, "structure_close_time")
    assert close is not None
    return "|".join((ticker.strip().upper(), timeframe, close.strftime("%Y-%m-%dT%H:%M:%SZ"), pattern))


@dataclass(frozen=True)
class StructureIdentity:
    ticker: str
    timeframe: str
    pattern: str
    structure_close_time: datetime

    def __post_init__(self) -> None:
        ticker = self.ticker.strip().upper()
        if not ticker or not self.timeframe.strip() or not self.pattern.strip():
            raise LifecycleError("ticker, timeframe and pattern are required")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "timeframe", self.timeframe.strip())
        object.__setattr__(self, "pattern", self.pattern.strip())
        object.__setattr__(
            self, "structure_close_time", _utc(self.structure_close_time, "structure_close_time")
        )

    @property
    def structure_id(self) -> str:
        return capture_structure_key(self.ticker, self.timeframe, self.structure_close_time, self.pattern)


def make_signal_id(structure_id: str, strategy: str, strategy_epoch: str) -> str:
    return json.dumps([structure_id, strategy, strategy_epoch], separators=(",", ":"))


@dataclass(frozen=True)
class Levels:
    """Two-sided structure boundaries (attributes, not identity)."""

    boundary_high: float
    boundary_low: float
    revision: int = 0

    def __post_init__(self) -> None:
        high = _level(self.boundary_high, "boundary_high")
        low = _level(self.boundary_low, "boundary_low")
        if not low < high:
            raise LifecycleError("boundary_low must be below boundary_high")
        object.__setattr__(self, "boundary_high", high)
        object.__setattr__(self, "boundary_low", low)

    def resolve(self, direction: str) -> tuple[float, float]:
        """(trigger, invalidation) for a resolved direction, per #1145 geometry."""
        if direction == "LONG":
            return self.boundary_high, self.boundary_low
        if direction == "SHORT":
            return self.boundary_low, self.boundary_high
        raise LifecycleError("direction must be LONG or SHORT")


@dataclass(frozen=True)
class SignalLinks:
    capture_structure_key: str | None = None
    scanner_sighting_ids: tuple[str, ...] = ()
    watcher_refs: tuple[str, ...] = ()
    context_snapshot_ids: tuple[str, ...] = ()
    contract_plan_refs: tuple[str, ...] = ()
    outcome_ref: str | None = None

    def merged(self, other: "SignalLinks") -> "SignalLinks":
        def union(a: tuple[str, ...], b: tuple[str, ...]) -> tuple[str, ...]:
            return tuple(dict.fromkeys((*a, *b)))

        for name in ("outcome_ref", "capture_structure_key"):
            mine, theirs = getattr(self, name), getattr(other, name)
            if mine and theirs and mine != theirs:
                raise LifecycleError(f"a signal links to exactly one {name}")
        return SignalLinks(
            capture_structure_key=self.capture_structure_key or other.capture_structure_key,
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


# Capture evidence keys an OBSERVATION event may carry (never identity/state).
OBSERVATION_KEYS = frozenset(
    {
        "capture_late",
        "prospective_catch",
        "gap_through",
        "data_delayed",
        "sip_crossed_at",
        "true_lag_seconds",
        "iex_lag_seconds",
        "trigger_crossed_at",
        "trigger_trade_price",
        "trigger_feed",
        "trigger_source",
        "trigger_resolution",
        "first_print_price",
        "status_reason",
        "setup_type",
        "capture_version",
    }
)


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
    levels: Levels
    state: LifecycleState = LifecycleState.WATCHING
    direction: str | None = None
    trigger_market_time: datetime | None = None
    trigger_detection_time: datetime | None = None
    data_integrity: IntegrityStatus = IntegrityStatus.UNKNOWN
    signal_integrity: IntegrityStatus = IntegrityStatus.UNKNOWN
    execution_integrity: IntegrityStatus = IntegrityStatus.NOT_APPLICABLE
    links: SignalLinks = field(default_factory=SignalLinks)
    capture: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    history: tuple[StateChange, ...] = ()

    @property
    def ticker(self) -> str:
        return self.identity.ticker

    @property
    def timeframe(self) -> str:
        return self.identity.timeframe

    @property
    def pattern(self) -> str:
        return self.identity.pattern

    @property
    def structure_close_time(self) -> datetime:
        return self.identity.structure_close_time

    @property
    def trigger(self) -> float | None:
        return self.levels.resolve(self.direction)[0] if self.direction else None

    @property
    def invalidation(self) -> float | None:
        return self.levels.resolve(self.direction)[1] if self.direction else None

    @property
    def prearmed(self) -> bool | None:
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
        """False for the reserved legacy/unregistered labels only.

        Registry membership is checked where it matters (research population,
        fitness, readiness) via ``strategy_epochs``; this guard only stops the
        reserved labels from ever carrying authority.
        """
        return self.strategy_epoch not in (LEGACY_UNVERSIONED, UNREGISTERED_EPOCH)


# ── events ───────────────────────────────────────────────────────────────────


class EventType(str, Enum):
    OPENED = "OPENED"
    STATE = "STATE"
    LEVELS = "LEVELS"
    OBSERVATION = "OBSERVATION"
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
    setup_ready_time: datetime | str,
    first_seen_time: datetime | str,
    data_source: str,
    levels: Levels,
    observation_only: bool = True,
    links: SignalLinks = SignalLinks(),
) -> SignalEvent:
    """OPENED event for a structure first seen WATCHING. Never carries authority."""
    if not strategy.strip() or not strategy_epoch.strip() or not data_source.strip():
        raise LifecycleError("strategy, strategy_epoch and data_source are required")
    ready = _utc(setup_ready_time, "setup_ready_time")
    seen = _utc(first_seen_time, "first_seen_time")
    if ready is None or seen is None:
        raise LifecycleError("setup_ready_time and first_seen_time are required")
    if ready < identity.structure_close_time:
        raise LifecycleError("setup_ready_time cannot precede structure_close_time")
    if seen < identity.structure_close_time:
        raise LifecycleError("first_seen_time cannot precede structure_close_time")
    return SignalEvent(
        signal_id=make_signal_id(identity.structure_id, strategy, strategy_epoch),
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
            "levels": levels,
            "links": links.merged(SignalLinks(capture_structure_key=identity.structure_id)),
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
    if target in RESOLVED_WITH_DIRECTION:
        direction = event.payload.get("direction")
        if direction not in ("LONG", "SHORT"):
            raise LifecycleError(f"{target.value} requires a resolved direction")
        if market_time is None:
            raise LifecycleError(f"{target.value} requires the trigger market time")
        if market_time < signal.structure_close_time:
            raise LifecycleError("trigger market time cannot precede structure close")
        detected = _utc(event.payload.get("trigger_detected_at"), "trigger_detected_at") or event.detected_at
        if detected < market_time:
            raise LifecycleError("trigger detection cannot precede the market trigger")
        if target is LifecycleState.TRIGGERED and signal.first_seen_time > market_time:
            raise LifecycleError("structure first seen after the trigger traded; record MISSED_LATE")
        if target is LifecycleState.MISSED_GAP and event.payload.get("gap_through") is not True:
            raise LifecycleError("MISSED_GAP requires gap_through evidence")
        updates.update(direction=direction, trigger_market_time=market_time, trigger_detection_time=detected)
    if target is LifecycleState.OUTCOME_CLOSED and not event.payload.get("outcome_ref"):
        raise LifecycleError("OUTCOME_CLOSED requires outcome_ref")
    links = signal.links
    if target is LifecycleState.OUTCOME_CLOSED:
        links = links.merged(SignalLinks(outcome_ref=str(event.payload["outcome_ref"])))
    change = StateChange(target, market_time, event.detected_at, event.reason)
    return replace(signal, **updates, links=links, history=(*signal.history, change))


def _apply_levels(signal: ProspectiveSignal, event: SignalEvent) -> ProspectiveSignal:
    if signal.state is not LifecycleState.WATCHING:
        raise LifecycleError("levels may only be revised while WATCHING")
    levels: Levels = event.payload["levels"]
    if levels.revision <= signal.levels.revision:
        raise LifecycleError("level revision must increase")
    return replace(signal, levels=levels)


def _apply_observation(signal: ProspectiveSignal, event: SignalEvent) -> ProspectiveSignal:
    unknown = set(event.payload) - OBSERVATION_KEYS
    if unknown:
        raise LifecycleError(f"OBSERVATION may only carry capture evidence, not {sorted(unknown)}")
    merged = {**signal.capture, **{k: v for k, v in event.payload.items()}}
    return replace(signal, capture=MappingProxyType(merged))


def _apply_integrity(signal: ProspectiveSignal, event: SignalEvent) -> ProspectiveSignal:
    updates: dict[str, Any] = {}
    for key in ("data_integrity", "signal_integrity", "execution_integrity"):
        if key in event.payload:
            updates[key] = IntegrityStatus(event.payload[key])
    if not updates and "execution_authority" not in event.payload:
        raise LifecycleError("INTEGRITY event must set an integrity status or authority")
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

    def by_structure(self, structure_id: str) -> tuple[ProspectiveSignal, ...]:
        return tuple(s for s in self._signals.values() if s.structure_id == structure_id)

    def append(self, event: SignalEvent) -> ProspectiveSignal:
        existing = self._events.get(event.signal_id, [])
        if event.seq < len(existing):
            if existing[event.seq] == event:
                return self._signals[event.signal_id]  # idempotent replay
            raise LifecycleError(f"{event.signal_id} seq {event.seq} already holds a different event")
        if event.seq != len(existing):
            raise LifecycleError(f"{event.signal_id} expected seq {len(existing)}, got {event.seq}")
        detected = _utc(event.detected_at, "detected_at")
        # LINK events are timeless metadata (a scanner sighting may be recorded
        # late); every other event must follow detection-time order.
        if event.event_type is not EventType.LINK:
            ordered = [e for e in existing if e.event_type is not EventType.LINK]
            if ordered and detected < _utc(ordered[-1].detected_at, "detected_at"):  # type: ignore[operator]
                raise LifecycleError("events must be appended in detection-time order")

        if event.event_type is EventType.OPENED:
            if existing:
                raise LifecycleError(f"duplicate OPENED for {event.signal_id}")
            p = event.payload
            identity: StructureIdentity = p["identity"]
            if make_signal_id(identity.structure_id, p["strategy"], p["strategy_epoch"]) != event.signal_id:
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
                levels=p["levels"],
                links=p.get("links", SignalLinks()),
                history=(StateChange(LifecycleState.WATCHING, None, detected, "opened"),),  # type: ignore[arg-type]
            )
        else:
            current = self._signals.get(event.signal_id)
            if current is None:
                raise LifecycleError(f"{event.signal_id} has no OPENED event")
            if current.terminal and event.event_type not in (EventType.LINK, EventType.INTEGRITY):
                raise LifecycleError(f"{event.signal_id} is terminal ({current.state.value})")
            if current.terminal and event.payload.get("execution_authority"):
                raise LifecycleError("a terminal signal cannot gain execution authority")
            handler = {
                EventType.STATE: _apply_state,
                EventType.LEVELS: _apply_levels,
                EventType.OBSERVATION: _apply_observation,
                EventType.INTEGRITY: _apply_integrity,
                EventType.LINK: lambda s, e: replace(s, links=s.links.merged(e.payload["links"])),
            }[event.event_type]
            signal = handler(current, event)

        self._events.setdefault(event.signal_id, []).append(event)
        self._signals[event.signal_id] = signal
        return signal


def _event(journal: SignalJournal, signal_id: str, kind: EventType, detected_at: datetime, **kw: Any) -> SignalEvent:
    return SignalEvent(signal_id=signal_id, seq=journal.next_seq(signal_id), event_type=kind, detected_at=detected_at, **kw)


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
    return _event(
        journal, signal_id, EventType.STATE, detected_at,
        state=state, market_time=market_time, reason=reason, payload=dict(payload or {}),
    )


def levels_event(journal: SignalJournal, signal_id: str, levels: Levels, *, detected_at: datetime) -> SignalEvent:
    return _event(journal, signal_id, EventType.LEVELS, detected_at, payload={"levels": levels})


def observation_event(
    journal: SignalJournal, signal_id: str, *, detected_at: datetime, **evidence: Any
) -> SignalEvent:
    return _event(journal, signal_id, EventType.OBSERVATION, detected_at, payload=dict(evidence))


def link_event(journal: SignalJournal, signal_id: str, links: SignalLinks, *, detected_at: datetime) -> SignalEvent:
    return _event(journal, signal_id, EventType.LINK, detected_at, payload={"links": links})


def integrity_event(journal: SignalJournal, signal_id: str, *, detected_at: datetime, **statuses: Any) -> SignalEvent:
    return _event(journal, signal_id, EventType.INTEGRITY, detected_at, payload=dict(statuses))


# ── serialisation (pure) ─────────────────────────────────────────────────────


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
    "boundary_high",
    "boundary_low",
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


def to_record(signal: ProspectiveSignal) -> dict[str, Any]:
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
        "boundary_high": signal.levels.boundary_high,
        "boundary_low": signal.levels.boundary_low,
        "level_revision": signal.levels.revision,
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
        "capture_structure_key": signal.links.capture_structure_key,
        "outcome_ref": signal.links.outcome_ref,
        "capture": dict(signal.capture),
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


def verify_record(record: Mapping[str, Any]) -> list[str]:
    """Problems with a stored snapshot; recomputes ids so edits are detected."""
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
            structure_close_time=record["structure_close_time"],
        )
        levels = Levels(record["boundary_high"], record["boundary_low"])
    except LifecycleError as exc:
        return [f"identity invalid: {exc}"]
    if identity.structure_id != record["structure_id"]:
        problems.append("structure_id does not match the record's structure fields")
    if make_signal_id(identity.structure_id, record["strategy"], record["strategy_epoch"]) != record["signal_id"]:
        problems.append("signal_id does not match structure/strategy/epoch")
    if record["direction"] is not None:
        try:
            trigger, invalidation = levels.resolve(record["direction"])
        except LifecycleError as exc:
            problems.append(str(exc))
        else:
            if (record["trigger"], record["invalidation"]) != (trigger, invalidation):
                problems.append("trigger/invalidation do not match direction and levels")
    elif record["trigger"] is not None or record["invalidation"] is not None:
        problems.append("an unresolved (two-sided) structure cannot carry trigger/invalidation")
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
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get("structure_id")), []).append(record)
    return grouped
