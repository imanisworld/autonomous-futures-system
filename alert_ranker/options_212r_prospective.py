"""Pure prospective 2-1-2 reversal observation logic.

This module has no provider, storage, Discord, risk, broker, or order imports.
It converts already-completed 30m/5m bars into stable ARMED/resolution evidence
for the 212 reversal research lane.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Sequence

from .causal_bars import Bar, MINUTE_5, MINUTE_30
from .session_calendar import Session, nyse_session_for
from .trigger_geometry import geometry_for_trigger
from .trigger_time import arm_trigger_setup, resolve_trigger

PROSPECTIVE_212R_ID = "OPTIONS_212R_PROSPECTIVE"
PROSPECTIVE_212R_VERSION = "212r-prospective-v0.1"


@dataclass(frozen=True)
class Prospective212Observation:
    setup_id: str
    setup_fingerprint: str
    ticker: str
    session_date: str
    watch_start: str
    watch_until: str
    status: str
    family: str | None
    subtype: str | None
    direction: str | None
    trigger_bar_start: str | None
    trigger_detectable_at: str | None
    trigger_level: float | None
    invalidation_level: float | None
    source_target: float | None
    source_target_r: float | None
    source_target_consumed: bool
    final_scenario: str
    opposite_side_broken_later: bool
    reason_code: str
    boundary_high: float
    boundary_low: float
    reference_direction: str | None
    source_timeframe: str = "30Min"
    trigger_timeframe: str = "5Min"
    observation_source: str = "public_chart"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CaptureGate:
    eligible: bool
    reason_code: str | None
    lag_seconds: float | None


def evaluate_capture_gate(
    observation: Prospective212Observation,
    *,
    prearmed_at: datetime | None,
    decision_ts: datetime,
    max_capture_lag_seconds: float,
    trigger_crossed_at: datetime | None = None,
) -> CaptureGate:
    """Decide whether current option data can count as prospective trigger evidence.

    The gate is deliberately stricter than simply reconstructing a trigger after
    the fact: the setup must have been observed before the causal break and the
    source target must still exist. When an exact SIP crossing timestamp is
    supplied, both the no-hindsight arm deadline and capture lag use that
    crossing. Otherwise the legacy mechanics-only fallback requires an arm
    before the 5m bucket and measures lag from bar detectability. Production
    evidence must supply the exact crossing clock.
    """
    if decision_ts.tzinfo is None or decision_ts.utcoffset() is None:
        raise ValueError("decision_ts must be timezone-aware")
    if max_capture_lag_seconds <= 0:
        raise ValueError("max_capture_lag_seconds must be positive")
    if observation.status != "TRIGGERED" or observation.family != "STRAT_212_REVERSAL":
        return CaptureGate(False, "not_212r_trigger", None)
    trigger_start = _parse_observation_ts(observation.trigger_bar_start)
    detectable = _parse_observation_ts(observation.trigger_detectable_at)
    if prearmed_at is None or trigger_start is None:
        return CaptureGate(False, "no_proven_pretrigger_arm", None)
    if observation.source_target_consumed:
        return CaptureGate(False, "source_target_consumed_at_trigger", None)
    if detectable is None:
        return CaptureGate(False, "trigger_detectable_time_missing", None)

    lag_anchor = detectable
    prearm_deadline = trigger_start
    negative_reason = "trigger_bar_not_yet_observable"
    if trigger_crossed_at is not None:
        if trigger_crossed_at.tzinfo is None or trigger_crossed_at.utcoffset() is None:
            raise ValueError("trigger_crossed_at must be timezone-aware")
        crossed = trigger_crossed_at.astimezone(timezone.utc)
        trigger_end = trigger_start + MINUTE_5.delta
        if crossed < trigger_start or crossed >= trigger_end:
            return CaptureGate(False, "trigger_cross_outside_proven_bucket", None)
        lag_anchor = crossed
        prearm_deadline = crossed
        negative_reason = "trigger_cross_not_yet_observable"

    if prearmed_at.astimezone(timezone.utc) >= prearm_deadline:
        return CaptureGate(False, "no_proven_pretrigger_arm", None)

    lag = (decision_ts.astimezone(timezone.utc) - lag_anchor).total_seconds()
    if lag < 0:
        return CaptureGate(False, negative_reason, lag)
    if lag > max_capture_lag_seconds:
        return CaptureGate(False, "decision_time_capture_late", lag)
    return CaptureGate(True, None, lag)


def _parse_observation_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observation timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _setup_id(ticker: str, watch_start: datetime) -> str:
    raw = {
        "ticker": ticker.upper(),
        "watch_start": watch_start.astimezone(timezone.utc).isoformat(),
        "pattern": "212",
    }
    payload = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _setup_fingerprint(*, high: float, low: float, reference: str | None) -> str:
    raw = {
        "boundary_high": float(high),
        "boundary_low": float(low),
        "reference_direction": reference,
    }
    payload = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _previous_session(session: Session) -> Session | None:
    cursor = session.date - timedelta(days=1)
    for _ in range(10):
        previous = nyse_session_for(cursor)
        if previous is not None:
            return previous
        cursor -= timedelta(days=1)
    return None


def _source_r(direction: str, trigger: float, stop: float, target: float) -> tuple[float | None, bool]:
    risk = trigger - stop if direction == "LONG" else stop - trigger
    reward = target - trigger if direction == "LONG" else trigger - target
    if risk <= 0:
        return None, False
    consumed = reward <= 0
    return round(reward / risk, 6), consumed


def observe_212_setups(
    *,
    ticker: str,
    history_30m: Sequence[Bar],
    session_5m: Sequence[Bar],
    session: Session,
    decision_ts: datetime,
) -> tuple[Prospective212Observation, ...]:
    """Reconstruct every 212 arm in the current session through ``decision_ts``.

    Only completed caller-supplied 5m bars can prove a trigger. A setup still
    inside its watch window with no break is WATCHING; after the window closes
    it is EXPIRED.  Continuations and ambiguous breaks are retained so the 212R
    population is not outcome-selected.
    """

    if decision_ts.tzinfo is None or decision_ts.utcoffset() is None:
        raise ValueError("decision_ts must be timezone-aware")
    point = decision_ts.astimezone(timezone.utc)
    open_utc = session.open.astimezone(timezone.utc)
    close_utc = session.close.astimezone(timezone.utc)
    history = sorted(history_30m, key=lambda bar: bar.start_utc)
    lower = sorted(session_5m, key=lambda bar: bar.start_utc)
    out: list[Prospective212Observation] = []

    watch_start = open_utc
    while watch_start < min(point, close_utc):
        watch_until = min(watch_start + MINUTE_30.delta, close_utc)
        completed = [
            bar for bar in history
            if bar.start_utc + MINUTE_30.delta <= watch_start
        ]
        if not completed:
            watch_start += MINUTE_30.delta
            continue
        last_close = completed[-1].start_utc + MINUTE_30.delta
        contiguous = last_close == watch_start
        if not contiguous and watch_start == open_utc:
            previous = _previous_session(session)
            contiguous = previous is not None and last_close == previous.close.astimezone(timezone.utc)
        if not contiguous:
            watch_start += MINUTE_30.delta
            continue
        armed = arm_trigger_setup(completed)
        if armed is None or armed.pattern != "212":
            watch_start += MINUTE_30.delta
            continue

        known_through = min(point, watch_until)
        expected: list[datetime] = []
        cursor = watch_start
        while cursor + MINUTE_5.delta <= known_through:
            expected.append(cursor)
            cursor += MINUTE_5.delta
        have = {bar.start_utc for bar in lower}
        missing = [start for start in expected if start not in have]
        if missing:
            out.append(
                Prospective212Observation(
                    setup_id=_setup_id(ticker, watch_start),
                    setup_fingerprint=_setup_fingerprint(
                        high=armed.boundary_high, low=armed.boundary_low, reference=armed.reference_direction
                    ),
                    ticker=ticker.upper(), session_date=session.date.isoformat(),
                    watch_start=watch_start.isoformat(), watch_until=watch_until.isoformat(),
                    status="DATA_BLOCKED", family=None, subtype=None, direction=None,
                    trigger_bar_start=None, trigger_detectable_at=None, trigger_level=None,
                    invalidation_level=None, source_target=None, source_target_r=None,
                    source_target_consumed=False, final_scenario="unknown",
                    opposite_side_broken_later=False,
                    reason_code="missing_5m_bars:" + ",".join(item.isoformat() for item in missing),
                    boundary_high=armed.boundary_high, boundary_low=armed.boundary_low,
                    reference_direction=armed.reference_direction,
                )
            )
            watch_start += MINUTE_30.delta
            continue

        result = resolve_trigger(
            armed,
            lower,
            lower_timeframe=MINUTE_5,
            watch_start=watch_start,
            watch_until=watch_until,
        )
        status = result.status
        if status == "NO_TRIGGER":
            status = "WATCHING" if point < watch_until else "EXPIRED"

        target = target_r = None
        consumed = False
        if result.status == "TRIGGERED" and result.family == "STRAT_212_REVERSAL":
            parent_bar = completed[-2]
            geometry = geometry_for_trigger(armed=armed, result=result, parent_bar=parent_bar)
            target = geometry.target
            if (
                target is not None
                and result.trigger_level is not None
                and result.invalidation_level is not None
                and result.direction is not None
            ):
                target_r, consumed = _source_r(
                    result.direction,
                    float(result.trigger_level),
                    float(result.invalidation_level),
                    float(target),
                )

        trigger_start = result.trigger_bar_start
        detectable = trigger_start + MINUTE_5.delta if trigger_start is not None else None
        out.append(
            Prospective212Observation(
                setup_id=_setup_id(ticker, watch_start),
                setup_fingerprint=_setup_fingerprint(
                    high=armed.boundary_high, low=armed.boundary_low, reference=armed.reference_direction
                ),
                ticker=ticker.upper(),
                session_date=session.date.isoformat(),
                watch_start=watch_start.isoformat(),
                watch_until=watch_until.isoformat(),
                status=status,
                family=result.family,
                subtype=result.subtype,
                direction=result.direction,
                trigger_bar_start=trigger_start.isoformat() if trigger_start else None,
                trigger_detectable_at=detectable.isoformat() if detectable else None,
                trigger_level=result.trigger_level,
                invalidation_level=result.invalidation_level,
                source_target=target,
                source_target_r=target_r,
                source_target_consumed=consumed,
                final_scenario=result.final_scenario,
                opposite_side_broken_later=result.opposite_side_broken_later,
                reason_code=result.reason_code,
                boundary_high=armed.boundary_high,
                boundary_low=armed.boundary_low,
                reference_direction=armed.reference_direction,
            )
        )
        watch_start += MINUTE_30.delta

    return tuple(out)
