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

Fail-closed invariants (enforced on every fold step, including direct
construction and ``dataclasses.replace``):

* **No authority.** A signal is structurally observation-only:
  ``observation_only is True`` and ``execution_authority is False`` always.
  No event, payload or constructor can change that; INTEGRITY events may only
  carry integrity statuses. Execution authority lives outside the signal.
* **Exact types.** Levels, capture evidence and integrity values are checked
  by exact type; strings, bools-as-numbers and non-finite numbers are refused
  rather than coerced.
* **Provenance is not overwritten.** ``capture_late`` / ``gap_through`` once
  true stay true; a revoked ``prospective_catch`` never comes back; trigger
  cross times are write-once; ``signal_integrity`` can only be demoted once
  known; the resolution (TRIGGERED / MISSED_LATE / MISSED_GAP) stays readable
  after OUTCOME_CLOSED via ``resolution``.
* **Chronology.** structure close <= setup ready <= first seen; a TRIGGERED
  resolution needs the setup knowable and first seen at or before the market
  trigger (otherwise it is MISSED_LATE); detection follows the market event.
* **Registered epochs only.** ``strategy_epoch`` is checked against the
  #1150 epoch registry: the epoch must exist, be FROZEN/RETIRED, declare a
  setup ``timeframe`` and ``family`` matching the structure, and cover the
  structure's close and first-seen times. Anything else is opened under
  ``UNREGISTERED_EPOCH`` (the requested label is kept in ``requested_epoch``
  with the reason) and can never reach ``signal_integrity == VALID``.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .strategy_epochs import (
    LEGACY_UNVERSIONED,
    UNREGISTERED_EPOCH,
    EpochRegistry,
    EpochStatus,
    RegistryError,
    StrategyEpoch,
    assert_observation_only,
    load_registry,
)

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


RESERVED_EPOCHS = frozenset({LEGACY_UNVERSIONED, UNREGISTERED_EPOCH})
MISSED_STATES = frozenset({LifecycleState.MISSED_LATE, LifecycleState.MISSED_GAP})
BLOCKED_STATES = frozenset({LifecycleState.DATA_BLOCKED, LifecycleState.AMBIGUOUS})
# Lower is better. UNKNOWN / NOT_APPLICABLE are "not judged", not a rank.
_SIGNAL_RANK = {IntegrityStatus.VALID: 0, IntegrityStatus.DEGRADED: 1, IntegrityStatus.INVALID: 2}
_FAMILY = re.compile(r"STRAT_([0-9])_([0-9])_([0-9])")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value: Any, label: str) -> float:
    if not _is_number(value):
        raise LifecycleError(f"{label} must be a number, not {type(value).__name__}")
    try:
        number = float(value)
    except OverflowError as exc:
        raise LifecycleError(f"{label} is out of range") from exc
    if not math.isfinite(number):
        raise LifecycleError(f"{label} must be finite")
    return number


def _status(value: Any, label: str) -> IntegrityStatus:
    if isinstance(value, IntegrityStatus):
        return value
    if isinstance(value, str) and value in IntegrityStatus.__members__:
        return IntegrityStatus(value)
    raise LifecycleError(f"{label} must be one of {[s.value for s in IntegrityStatus]}, not {value!r}")


def _utc(value: datetime | str | None, label: str) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LifecycleError(f"{label} is not ISO-8601: {value!r}") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LifecycleError(f"{label} must be timezone-aware")
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise LifecycleError(f"{label} is not representable in UTC") from exc


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _level(value: Any, label: str) -> float:
    number = _finite(value, label)
    if number <= 0:
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
        for name in ("ticker", "timeframe", "pattern"):
            if not isinstance(getattr(self, name), str):
                raise LifecycleError(f"{name} must be a string")
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


# ── epoch validation against the #1150 registry ─────────────────────────────

_DEFAULT_REGISTRY: EpochRegistry | None = None


def default_registry() -> EpochRegistry:
    """The committed epoch registry, loaded once. A broken registry raises."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = load_registry()
    return _DEFAULT_REGISTRY


def epoch_context_problem(
    epoch: StrategyEpoch, identity: "StructureIdentity", first_seen_time: datetime
) -> str | None:
    """Why ``identity`` cannot be an observation of ``epoch`` (None if it can).

    The epoch must be FROZEN/RETIRED, declare ``definition.setup.timeframe`` and
    ``definition.setup.family`` (``STRAT_a_b_c``), match the structure's
    timeframe and pattern family (``abc:...``), and cover both the structure
    close and the first-seen time.
    """
    try:
        assert_observation_only(epoch)
    except RegistryError as exc:
        return f"epoch fails registry invariants: {exc}"
    if epoch.status is EpochStatus.DRAFT:
        return f"epoch {epoch.epoch} is DRAFT"
    setup = epoch.definition.get("setup")
    timeframe = setup.get("timeframe") if isinstance(setup, Mapping) else None
    family = setup.get("family") if isinstance(setup, Mapping) else None
    if not isinstance(timeframe, str) or not isinstance(family, str):
        return f"epoch {epoch.epoch} does not declare setup timeframe and family"
    if timeframe.lower() != identity.timeframe.lower():
        return f"structure timeframe {identity.timeframe} is not epoch timeframe {timeframe}"
    match = _FAMILY.fullmatch(family)
    if match is None:
        return f"epoch family {family!r} is not STRAT_a_b_c"
    if identity.pattern.split(":")[0] != "".join(match.groups()):
        return f"structure pattern {identity.pattern} is not epoch family {family}"
    for label, at in (("structure close", identity.structure_close_time), ("first seen", first_seen_time)):
        if not epoch.covers(at):
            return f"{label} {at.isoformat()} is outside epoch {epoch.epoch}'s effective window"
    return None


def resolve_signal_epoch(
    registry: EpochRegistry,
    *,
    strategy: Any,
    strategy_epoch: Any,
    identity: "StructureIdentity",
    first_seen_time: datetime,
) -> tuple[StrategyEpoch | None, str]:
    """(registered epoch, reason). None means the label must not be trusted."""
    if not isinstance(strategy, str) or not isinstance(strategy_epoch, str):
        return None, "strategy and strategy_epoch must be strings"
    if strategy_epoch in RESERVED_EPOCHS:
        return None, f"{strategy_epoch} is a reserved label"
    if not isinstance(registry, EpochRegistry):
        return None, "no epoch registry"
    epoch = registry.get(strategy, strategy_epoch)
    if epoch is None:
        return None, f"{strategy}/{strategy_epoch} is not in the epoch registry"
    problem = epoch_context_problem(epoch, identity, first_seen_time)
    if problem is not None:
        return None, problem
    return epoch, "registered"


@dataclass(frozen=True)
class Levels:
    """Two-sided structure boundaries (attributes, not identity)."""

    boundary_high: float
    boundary_low: float
    revision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 0:
            raise LifecycleError("level revision must be a non-negative int")
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


# Capture evidence keys an OBSERVATION event may carry (never identity/state),
# grouped by their exact value type.
OBSERVATION_BOOL_KEYS = frozenset({"capture_late", "prospective_catch", "gap_through", "data_delayed"})
OBSERVATION_NUMBER_KEYS = frozenset(
    {"true_lag_seconds", "iex_lag_seconds", "trigger_trade_price", "first_print_price"}
)
OBSERVATION_TIME_KEYS = frozenset({"sip_crossed_at", "trigger_crossed_at"})
OBSERVATION_TEXT_KEYS = frozenset(
    {"trigger_feed", "trigger_source", "trigger_resolution", "status_reason", "setup_type", "capture_version"}
)
OBSERVATION_KEYS = OBSERVATION_BOOL_KEYS | OBSERVATION_NUMBER_KEYS | OBSERVATION_TIME_KEYS | OBSERVATION_TEXT_KEYS
# Once true, never false again.
STICKY_TRUE_KEYS = frozenset({"capture_late", "gap_through", "data_delayed"})


def _capture_crosses(capture: Mapping[str, Any]) -> list[datetime]:
    return [_utc(capture[key], key) for key in sorted(OBSERVATION_TIME_KEYS) if capture.get(key) is not None]


def prearmed_at(
    first_seen: datetime, setup_ready: datetime, trigger: datetime | None, capture: Mapping[str, Any]
) -> bool | None:
    """Knowable and seen at or before the earliest recorded cross (B5).

    The earliest of the resolved trigger and any captured cross time
    (``sip_crossed_at`` / ``trigger_crossed_at``) counts, so a later SIP
    reconciliation that moves the true cross before first sight un-arms it.
    """
    if trigger is None:
        return None
    earliest = min([trigger, *_capture_crosses(capture)])
    return first_seen <= earliest and setup_ready <= earliest


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
    requested_epoch: str | None = None
    epoch_reason: str = ""
    catch_revoked: bool = False
    registered_epoch: StrategyEpoch | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        # B1: structurally observation-only, whatever the constructor was given.
        if self.observation_only is not True:
            raise LifecycleError("a prospective signal is always observation_only=True")
        if self.execution_authority is not False:
            raise LifecycleError("a prospective signal never carries execution authority")
        if not isinstance(self.identity, StructureIdentity) or not isinstance(self.levels, Levels):
            raise LifecycleError("identity and levels must be StructureIdentity and Levels")
        if self.identity.structure_id != self.structure_id:
            raise LifecycleError("structure_id does not match identity")
        for name in ("setup_ready_time", "first_seen_time"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise LifecycleError(f"{name} must be a timezone-aware datetime")
        for name in ("trigger_market_time", "trigger_detection_time"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
                raise LifecycleError(f"{name} must be a timezone-aware datetime")
        if not isinstance(self.links, SignalLinks) or not isinstance(self.capture, Mapping):
            raise LifecycleError("links and capture must be SignalLinks and a mapping")
        unknown = set(self.capture) - OBSERVATION_KEYS
        if unknown:
            raise LifecycleError(f"capture may only carry capture evidence, not {sorted(unknown)}")
        for key, value in self.capture.items():
            _observation_value(key, value)  # B3: exact types on every construction
        if not isinstance(self.history, tuple) or not all(isinstance(c, StateChange) for c in self.history):
            raise LifecycleError("history must be a tuple of StateChange")
        if not isinstance(self.state, LifecycleState):
            raise LifecycleError("state must be a LifecycleState")
        for name in ("data_integrity", "signal_integrity", "execution_integrity"):
            if not isinstance(getattr(self, name), IntegrityStatus):
                raise LifecycleError(f"{name} must be an IntegrityStatus")
        if self.execution_integrity is not IntegrityStatus.NOT_APPLICABLE:
            raise LifecycleError("execution_integrity is NOT_APPLICABLE on an observation-only signal")
        if not isinstance(self.catch_revoked, bool):
            raise LifecycleError("catch_revoked must be a bool")
        for name in ("strategy", "strategy_epoch", "signal_id", "structure_id", "data_source"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise LifecycleError(f"{name} must be a non-empty string")
        # B6: a label is either a validated registry epoch or a reserved one.
        if self.registered_epoch is None:
            if self.strategy_epoch not in RESERVED_EPOCHS:
                raise LifecycleError(
                    f"strategy_epoch {self.strategy_epoch!r} is not validated against the epoch registry"
                )
        else:
            if not isinstance(self.registered_epoch, StrategyEpoch):
                raise LifecycleError("registered_epoch must be a StrategyEpoch")
            if self.registered_epoch.key != (self.strategy, self.strategy_epoch):
                raise LifecycleError("registered_epoch does not match strategy/strategy_epoch")
            problem = epoch_context_problem(self.registered_epoch, self.identity, self.first_seen_time)
            if problem is not None:
                raise LifecycleError(f"registered epoch mismatch: {problem}")
        # B5: chronology of the observation itself.
        if self.setup_ready_time < self.structure_close_time:
            raise LifecycleError("setup_ready_time cannot precede structure_close_time")
        if self.first_seen_time < self.setup_ready_time:
            raise LifecycleError("first_seen_time cannot precede setup_ready_time")
        if any(t < self.structure_close_time for t in _capture_crosses(self.capture)):
            raise LifecycleError("a captured cross time cannot precede structure_close_time")
        # Blocked observations are invalid, misses are never clean.
        if self.state in BLOCKED_STATES and (
            self.signal_integrity is not IntegrityStatus.INVALID
            or self.data_integrity is not IntegrityStatus.INVALID
        ):
            raise LifecycleError(f"{self.state.value} requires INVALID data and signal integrity")
        if self.signal_integrity is IntegrityStatus.VALID:
            problem = self.valid_integrity_problem()
            if problem is not None:
                raise LifecycleError(f"signal_integrity cannot be VALID: {problem}")

    def valid_integrity_problem(self) -> str | None:
        """Why this signal could not carry VALID signal integrity (None: it could)."""
        if self.registered_epoch is None:
            return f"epoch is not registered ({self.epoch_reason or self.strategy_epoch})"
        if self.state in BLOCKED_STATES:
            return f"state is {self.state.value}"
        resolution = self.resolution
        if resolution in MISSED_STATES:
            return f"resolution is {resolution.value}; a miss is never a clean catch"  # type: ignore[union-attr]
        if resolution is None:
            if self.state in (LifecycleState.EXPIRED, LifecycleState.INVALIDATED):
                return None  # clean non-trigger observation
            return "the structure has not resolved"
        if self.prearmed is not True:
            return "the structure was not pre-armed before the trigger"
        if self.capture.get("prospective_catch") is not True or self.catch_revoked:
            return "no prospective_catch evidence"
        if self.capture.get("capture_late") is True or self.capture.get("gap_through") is True:
            return "capture was late or gapped"
        return None

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
        return prearmed_at(self.first_seen_time, self.setup_ready_time, self.trigger_market_time, self.capture)

    @property
    def resolution(self) -> LifecycleState | None:
        """How the structure resolved (TRIGGERED / MISSED_LATE / MISSED_GAP), from history.

        Survives OUTCOME_CLOSED / DATA_BLOCKED, so a closed miss is still a miss.
        """
        for change in reversed(self.history):
            if change.state in RESOLVED_WITH_DIRECTION:
                return change.state
        return None

    @property
    def is_prospective_catch(self) -> bool:
        """A registered, pre-armed, timely TRIGGERED capture with VALID signal integrity."""
        return (
            self.resolution is LifecycleState.TRIGGERED
            and self.signal_integrity is IntegrityStatus.VALID
            and self.valid_integrity_problem() is None
        )

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
        """True only when the label was validated against the epoch registry."""
        return self.registered_epoch is not None


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

    def __post_init__(self) -> None:
        # Times are normalized at the boundary so no ISO string leaks into
        # StateChange / to_record (adapter rows carry ISO strings).
        object.__setattr__(self, "detected_at", _utc(self.detected_at, "detected_at"))
        if self.market_time is not None:
            object.__setattr__(self, "market_time", _utc(self.market_time, "market_time"))


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
    registry: EpochRegistry | None = None,
) -> SignalEvent:
    """OPENED event for a structure first seen WATCHING. Never carries authority.

    ``strategy_epoch`` is validated against ``registry`` (default: the
    committed #1150 registry). An unknown, DRAFT, out-of-window or
    context-mismatched epoch opens the signal under ``UNREGISTERED_EPOCH``; the
    requested label and the reason are kept, never silently trusted.
    """
    for name, value in (("strategy", strategy), ("strategy_epoch", strategy_epoch), ("data_source", data_source)):
        if not isinstance(value, str) or not value.strip():
            raise LifecycleError("strategy, strategy_epoch and data_source are required strings")
    if observation_only is not True:
        raise LifecycleError("a prospective signal is always observation-only")
    if not isinstance(identity, StructureIdentity) or not isinstance(levels, Levels):
        raise LifecycleError("identity and levels must be StructureIdentity and Levels")
    ready = _utc(setup_ready_time, "setup_ready_time")
    seen = _utc(first_seen_time, "first_seen_time")
    if ready is None or seen is None:
        raise LifecycleError("setup_ready_time and first_seen_time are required")
    if ready < identity.structure_close_time:
        raise LifecycleError("setup_ready_time cannot precede structure_close_time")
    if seen < identity.structure_close_time:
        raise LifecycleError("first_seen_time cannot precede structure_close_time")
    if seen < ready:
        raise LifecycleError("first_seen_time cannot precede setup_ready_time")
    epoch, reason = resolve_signal_epoch(
        registry if registry is not None else default_registry(),
        strategy=strategy,
        strategy_epoch=strategy_epoch,
        identity=identity,
        first_seen_time=seen,
    )
    stamped = strategy_epoch if epoch is not None or strategy_epoch in RESERVED_EPOCHS else UNREGISTERED_EPOCH
    return SignalEvent(
        signal_id=make_signal_id(identity.structure_id, strategy, stamped),
        seq=0,
        event_type=EventType.OPENED,
        detected_at=seen,
        state=LifecycleState.WATCHING,
        payload={
            "identity": identity,
            "strategy": strategy,
            "strategy_epoch": stamped,
            "requested_epoch": strategy_epoch,
            "epoch_reason": reason,
            "registered_epoch": epoch,
            "setup_ready_time": ready,
            "data_source": data_source,
            "observation_only": True,
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
    if not isinstance(event.reason, str) or not event.reason.strip():
        raise LifecycleError(f"{target.value} requires a reason")
    market_time = _utc(event.market_time, "market_time")
    if market_time is not None:
        if market_time < signal.structure_close_time:
            raise LifecycleError("trigger market time cannot precede structure close")
        if market_time > event.detected_at:
            raise LifecycleError("a market event cannot be recorded before it happened")
    updates: dict[str, Any] = {"state": target}
    if target in RESOLVED_WITH_DIRECTION:
        direction = event.payload.get("direction")
        if not isinstance(direction, str) or direction not in ("LONG", "SHORT"):
            raise LifecycleError(f"{target.value} requires a resolved direction")
        if market_time is None:
            raise LifecycleError(f"{target.value} requires the trigger market time")
        detected = _utc(event.payload.get("trigger_detected_at"), "trigger_detected_at") or event.detected_at
        if detected < market_time:
            raise LifecycleError("trigger detection cannot precede the market trigger")
        if detected > event.detected_at:
            raise LifecycleError("trigger detection cannot follow the event that records it")
        if target is LifecycleState.TRIGGERED and (
            signal.first_seen_time > market_time or signal.setup_ready_time > market_time
        ):
            raise LifecycleError(
                "structure was not knowable and first seen before the trigger traded; record MISSED_LATE"
            )
        if target is LifecycleState.MISSED_GAP and event.payload.get("gap_through") is not True:
            raise LifecycleError("MISSED_GAP requires gap_through evidence")
        updates.update(direction=direction, trigger_market_time=market_time, trigger_detection_time=detected)
    if target in MISSED_STATES and signal.signal_integrity is not IntegrityStatus.INVALID:
        updates["signal_integrity"] = IntegrityStatus.DEGRADED  # a miss is never a clean catch
    if target in BLOCKED_STATES:
        updates.update(data_integrity=IntegrityStatus.INVALID, signal_integrity=IntegrityStatus.INVALID)
    if target is LifecycleState.OUTCOME_CLOSED:
        outcome_ref = event.payload.get("outcome_ref")
        if not isinstance(outcome_ref, str) or not outcome_ref.strip():
            raise LifecycleError("OUTCOME_CLOSED requires outcome_ref")
    links = signal.links
    if target is LifecycleState.OUTCOME_CLOSED:
        links = links.merged(SignalLinks(outcome_ref=event.payload["outcome_ref"]))
    change = StateChange(target, market_time, event.detected_at, event.reason)
    return replace(signal, **updates, links=links, history=(*signal.history, change))


def _apply_levels(signal: ProspectiveSignal, event: SignalEvent) -> ProspectiveSignal:
    if signal.state is not LifecycleState.WATCHING:
        raise LifecycleError("levels may only be revised while WATCHING")
    levels = event.payload.get("levels")
    if not isinstance(levels, Levels):
        raise LifecycleError("LEVELS event requires Levels")
    if levels.revision <= signal.levels.revision:
        raise LifecycleError("level revision must increase")
    return replace(signal, levels=levels)


def _observation_value(key: str, value: Any) -> Any:
    if key in OBSERVATION_BOOL_KEYS:
        if not isinstance(value, bool):
            raise LifecycleError(f"{key} must be a bool, not {value!r}")
        return value
    if key in OBSERVATION_NUMBER_KEYS:
        return _finite(value, key)
    if key in OBSERVATION_TIME_KEYS:
        if not isinstance(value, str):
            raise LifecycleError(f"{key} must be an ISO-8601 string")
        return _iso(_utc(value, key))
    if not isinstance(value, str):
        raise LifecycleError(f"{key} must be a string")
    return value


def _apply_observation(signal: ProspectiveSignal, event: SignalEvent) -> ProspectiveSignal:
    unknown = set(event.payload) - OBSERVATION_KEYS
    if unknown:
        raise LifecycleError(f"OBSERVATION may only carry capture evidence, not {sorted(unknown)}")
    incoming = {k: _observation_value(k, v) for k, v in event.payload.items()}
    for key in OBSERVATION_TIME_KEYS & set(incoming):
        crossed = _utc(incoming[key], key)
        if crossed < signal.structure_close_time:
            raise LifecycleError(f"{key} cannot precede structure_close_time")
        if crossed > event.detected_at:
            raise LifecycleError(f"{key} cannot be later than the event that records it")
    current = signal.capture
    for key in STICKY_TRUE_KEYS:
        if current.get(key) is True and incoming.get(key) is False:
            raise LifecycleError(f"{key} was recorded true; provenance cannot be cleared")
    for key in OBSERVATION_TIME_KEYS:
        if current.get(key) is not None and key in incoming and incoming[key] != current[key]:
            raise LifecycleError(f"{key} is write-once; it was already recorded as {current[key]}")
    merged = {**current, **incoming}
    prearmed = prearmed_at(signal.first_seen_time, signal.setup_ready_time, signal.trigger_market_time, merged)
    # A catch is revoked when withdrawn, or when a later cross time shows the
    # structure was not knowable/seen before the true cross (B5).
    revoked = signal.catch_revoked or (
        current.get("prospective_catch") is True
        and (merged["prospective_catch"] is False or prearmed is False)
    )
    if incoming.get("prospective_catch") is True:
        if revoked:
            raise LifecycleError("a revoked prospective_catch cannot be restored")
        if signal.state is not LifecycleState.TRIGGERED or prearmed is not True:
            raise LifecycleError(f"prospective_catch requires a pre-armed TRIGGERED signal, not {signal.state.value}")
        if merged.get("capture_late") is True or merged.get("gap_through") is True:
            raise LifecycleError("a late or gapped capture cannot be a prospective catch")
    updates: dict[str, Any] = {"capture": MappingProxyType(merged), "catch_revoked": revoked}
    demoted = revoked or prearmed is False or merged.get("capture_late") is True or merged.get("gap_through") is True
    if demoted and signal.signal_integrity is IntegrityStatus.VALID:
        updates["signal_integrity"] = IntegrityStatus.DEGRADED  # evidence demotes; it never promotes
    return replace(signal, **updates)


INTEGRITY_KEYS = frozenset({"data_integrity", "signal_integrity", "execution_integrity"})


def _apply_integrity(signal: ProspectiveSignal, event: SignalEvent) -> ProspectiveSignal:
    unknown = set(event.payload) - INTEGRITY_KEYS
    if unknown:
        # B1: no authority key (execution_authority, authority_ref, ...) is accepted at all.
        raise LifecycleError(f"INTEGRITY may only carry integrity statuses, not {sorted(unknown)}")
    if not event.payload:
        raise LifecycleError("INTEGRITY event must set an integrity status")
    updates = {key: _status(value, key) for key, value in event.payload.items()}
    if updates.get("execution_integrity", IntegrityStatus.NOT_APPLICABLE) is not IntegrityStatus.NOT_APPLICABLE:
        raise LifecycleError("execution_integrity is NOT_APPLICABLE on an observation-only signal")
    if "signal_integrity" in updates:
        old, new = signal.signal_integrity, updates["signal_integrity"]
        if old is not IntegrityStatus.UNKNOWN and old is not new:
            if new not in _SIGNAL_RANK or _SIGNAL_RANK[new] < _SIGNAL_RANK.get(old, -1):
                raise LifecycleError(
                    f"signal_integrity {old.value} -> {new.value} would promote or erase provenance; "
                    "only demotion is allowed"
                )
    if "data_integrity" in updates:
        old, new = signal.data_integrity, updates["data_integrity"]
        if old is not IntegrityStatus.UNKNOWN and old is not new:
            if new not in _SIGNAL_RANK or _SIGNAL_RANK[new] < _SIGNAL_RANK.get(old, -1):
                raise LifecycleError(
                    f"data_integrity {old.value} -> {new.value} would promote or erase provenance; "
                    "only demotion is allowed (INVALID is final)"
                )
    return replace(signal, **updates)


class SignalJournal:
    """In-memory, append-only fold of signal events with structure-level dedupe."""

    def __init__(self, registry: EpochRegistry | None = None) -> None:
        self._signals: dict[str, ProspectiveSignal] = {}
        self._events: dict[str, list[SignalEvent]] = {}
        self._registry = registry

    @property
    def registry(self) -> EpochRegistry:
        return self._registry if self._registry is not None else default_registry()

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
            if not isinstance(identity, StructureIdentity):
                raise LifecycleError("OPENED requires a StructureIdentity")
            if make_signal_id(identity.structure_id, p["strategy"], p["strategy_epoch"]) != event.signal_id:
                raise LifecycleError("signal_id does not match identity/strategy/epoch")
            claimed = p.get("registered_epoch")
            if claimed is not None and self.registry.get(p["strategy"], p["strategy_epoch"]) != claimed:
                raise LifecycleError("OPENED claims an epoch that is not this journal's registry entry")
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
                requested_epoch=p.get("requested_epoch"),
                epoch_reason=p.get("epoch_reason", ""),
                registered_epoch=p.get("registered_epoch"),
            )
        else:
            current = self._signals.get(event.signal_id)
            if current is None:
                raise LifecycleError(f"{event.signal_id} has no OPENED event")
            if current.terminal and event.event_type not in (EventType.LINK, EventType.INTEGRITY):
                raise LifecycleError(f"{event.signal_id} is terminal ({current.state.value})")
            handler = {
                EventType.STATE: _apply_state,
                EventType.LEVELS: _apply_levels,
                EventType.OBSERVATION: _apply_observation,
                EventType.INTEGRITY: _apply_integrity,
                EventType.LINK: lambda s, e: replace(s, links=s.links.merged(e.payload["links"])),
            }[event.event_type]
            try:
                signal = handler(current, event)
            except (KeyError, TypeError, AttributeError) as exc:
                raise LifecycleError(f"malformed {event.event_type.value} event: {exc}") from exc

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
        "resolution_state": signal.resolution.value if signal.resolution else None,
        "prospective_catch": signal.is_prospective_catch,
        "requested_epoch": signal.requested_epoch,
        "epoch_reason": signal.epoch_reason,
        "epoch_definition_sha256": (
            signal.registered_epoch.definition_sha256 if signal.registered_epoch is not None else None
        ),
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


def _record_time(record: Mapping[str, Any], name: str, problems: list[str]) -> datetime | None:
    try:
        return _utc(record.get(name), name)
    except LifecycleError as exc:
        problems.append(str(exc))
        return None


def verify_record(record: Mapping[str, Any], registry: EpochRegistry | None = None) -> list[str]:
    """Problems with a stored snapshot; recomputes ids so edits are detected.

    With ``registry``, a VALID record's epoch label and definition hash must
    match a registered epoch.
    """
    if not isinstance(record, Mapping):
        return ["record must be a mapping"]
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
    if record["observation_only"] is not True or record["execution_authority"] is not False:
        problems.append("observation-only record claims execution authority")
    try:
        state = LifecycleState(record["lifecycle_state"])
        statuses = {key: _status(record[key], key) for key in ("data_integrity", "signal_integrity", "execution_integrity")}
        resolution = LifecycleState(record["resolution_state"]) if record.get("resolution_state") else None
        history_states = [LifecycleState(change["state"]) for change in record.get("history") or ()]
        capture = record.get("capture") or {}
        if not isinstance(capture, Mapping):
            raise TypeError("capture must be a mapping")
        crosses = _capture_crosses(capture)
    except (ValueError, TypeError, KeyError, LifecycleError) as exc:
        problems.append(str(exc))
        return problems
    # The resolution is derived from history, never trusted from the summary field.
    derived = next((st for st in reversed(history_states) if st in RESOLVED_WITH_DIRECTION), None)
    if derived is not resolution:
        problems.append("resolution_state does not match the record's history")
    resolution = derived
    if history_states and history_states[-1] is not state:
        problems.append("lifecycle_state does not match the record's history")
    if statuses["execution_integrity"] is not IntegrityStatus.NOT_APPLICABLE:
        problems.append("execution_integrity must be NOT_APPLICABLE on a signal")
    # Chronology (B5).
    close = _record_time(record, "structure_close_time", problems)
    ready = _record_time(record, "setup_ready_time", problems)
    seen = _record_time(record, "first_seen_time", problems)
    trig = _record_time(record, "trigger_market_time", problems)
    detect = _record_time(record, "trigger_detection_time", problems)
    if close and ready and ready < close:
        problems.append("setup_ready_time precedes structure_close_time")
    if ready and seen and seen < ready:
        problems.append("first_seen_time precedes setup_ready_time")
    if trig and close and trig < close:
        problems.append("trigger_market_time precedes structure_close_time")
    if trig and detect and detect < trig:
        problems.append("trigger detection precedes the market trigger")
    if resolution is LifecycleState.TRIGGERED and trig and ((seen and seen > trig) or (ready and ready > trig)):
        problems.append("TRIGGERED resolution for a structure not knowable/seen before the trigger")
    if close and any(t < close for t in crosses):
        problems.append("a captured cross time precedes structure_close_time")
    prearmed = prearmed_at(seen, ready, trig, capture) if seen and ready else None
    # Integrity provenance (B2/B4/B6).
    if statuses["signal_integrity"] is IntegrityStatus.VALID:
        if record["strategy_epoch"] in RESERVED_EPOCHS:
            problems.append("VALID signal integrity under an unregistered epoch")
        if resolution in MISSED_STATES or state in BLOCKED_STATES:
            problems.append("VALID signal integrity on a miss or blocked observation")
        if resolution is LifecycleState.TRIGGERED and record.get("prospective_catch") is not True:
            problems.append("VALID signal integrity without a prospective catch")
        if resolution is LifecycleState.TRIGGERED and (
            capture.get("prospective_catch") is not True or prearmed is not True
        ):
            problems.append("VALID signal integrity without pre-armed prospective_catch evidence")
        if resolution is None and state not in (LifecycleState.EXPIRED, LifecycleState.INVALIDATED):
            problems.append("VALID signal integrity on an unresolved structure")
        if capture.get("capture_late") is True or capture.get("gap_through") is True:
            problems.append("VALID signal integrity on a late or gapped capture")
        if registry is not None:
            epoch = registry.get(str(record["strategy"]), str(record["strategy_epoch"]))
            if epoch is None or epoch.definition_sha256 != record.get("epoch_definition_sha256"):
                problems.append("record epoch is not the registered epoch definition")
    if record.get("prospective_catch") is True and statuses["signal_integrity"] is not IntegrityStatus.VALID:
        problems.append("prospective_catch without VALID signal integrity")
    return problems


def dedupe_records(records: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get("structure_id")), []).append(record)
    return grouped
