"""Pure prospective 1-2-2 reversal observation logic.

No provider, storage, notification, risk, broker, or order imports live here.
Completed 30m bars arm fixed 1-2-2 boundaries; completed 5m bars provide a
fallback structural view. The production research collector uses exact IEX
trade ordering while the setup is WATCHING and keeps delayed SIP reconciliation
separate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Sequence

from .causal_bars import Bar, MINUTE_5, MINUTE_30
from .session_calendar import Session, nyse_session_for
from .trigger_time import arm_trigger_setup, resolve_trigger

PROSPECTIVE_122_ID = "OPTIONS_122_PROSPECTIVE"
PROSPECTIVE_122_VERSION = "122-prospective-v0.1"


@dataclass(frozen=True)
class Prospective122Observation:
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
    structural_opposite_boundary: float | None
    strategy_stop: float | None
    strategy_target: float | None
    strategy_geometry_status: str
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


def _parse_observation_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observation timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def evaluate_capture_gate(
    observation: Prospective122Observation,
    *,
    prearmed_at: datetime | None,
    decision_ts: datetime,
    max_capture_lag_seconds: float,
    trigger_crossed_at: datetime,
) -> CaptureGate:
    """Gate option evidence on a genuinely pre-armed exact IEX reversal clock."""
    if decision_ts.tzinfo is None or decision_ts.utcoffset() is None:
        raise ValueError("decision_ts must be timezone-aware")
    if trigger_crossed_at.tzinfo is None or trigger_crossed_at.utcoffset() is None:
        raise ValueError("trigger_crossed_at must be timezone-aware")
    if max_capture_lag_seconds <= 0:
        raise ValueError("max_capture_lag_seconds must be positive")
    if observation.status != "TRIGGERED" or observation.family != "OTHER:strat_122":
        return CaptureGate(False, "not_122_reversal_trigger", None)
    trigger_start = _parse_observation_ts(observation.trigger_bar_start)
    if prearmed_at is None or trigger_start is None:
        return CaptureGate(False, "no_proven_pretrigger_arm", None)

    crossed = trigger_crossed_at.astimezone(timezone.utc)
    trigger_end = trigger_start + MINUTE_5.delta
    if crossed < trigger_start or crossed >= trigger_end:
        return CaptureGate(False, "trigger_cross_outside_proven_bucket", None)
    if prearmed_at.astimezone(timezone.utc) >= crossed:
        return CaptureGate(False, "no_proven_pretrigger_arm", None)

    lag = (decision_ts.astimezone(timezone.utc) - crossed).total_seconds()
    if lag < 0:
        return CaptureGate(False, "trigger_cross_not_yet_observable", lag)
    if lag > max_capture_lag_seconds:
        return CaptureGate(False, "decision_time_capture_late", lag)
    return CaptureGate(True, None, lag)


def _setup_id(ticker: str, watch_start: datetime) -> str:
    raw = {
        "ticker": ticker.upper(),
        "watch_start": watch_start.astimezone(timezone.utc).isoformat(),
        "pattern": "122",
    }
    return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _setup_fingerprint(*, high: float, low: float, reference: str | None) -> str:
    raw = {
        "boundary_high": float(high),
        "boundary_low": float(low),
        "reference_direction": reference,
    }
    return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _previous_session(session: Session) -> Session | None:
    cursor = session.date - timedelta(days=1)
    for _ in range(10):
        previous = nyse_session_for(cursor)
        if previous is not None:
            return previous
        cursor -= timedelta(days=1)
    return None


def observe_122_setups(
    *,
    ticker: str,
    history_30m: Sequence[Bar],
    session_5m: Sequence[Bar],
    session: Session,
    decision_ts: datetime,
) -> tuple[Prospective122Observation, ...]:
    """Reconstruct every structurally armed 1-2-2 watch window through now.

    The strategy stop/target are deliberately unresolved. The opposite side of
    the armed directional 2 is retained only as a structural boundary; it must
    not be relabeled as a proven options stop.
    """
    if decision_ts.tzinfo is None or decision_ts.utcoffset() is None:
        raise ValueError("decision_ts must be timezone-aware")
    point = decision_ts.astimezone(timezone.utc)
    open_utc = session.open.astimezone(timezone.utc)
    close_utc = session.close.astimezone(timezone.utc)
    history = sorted(history_30m, key=lambda bar: bar.start_utc)
    lower = sorted(session_5m, key=lambda bar: bar.start_utc)
    out: list[Prospective122Observation] = []

    watch_start = open_utc
    while watch_start < min(point, close_utc):
        watch_until = min(watch_start + MINUTE_30.delta, close_utc)
        completed = [bar for bar in history if bar.start_utc + MINUTE_30.delta <= watch_start]
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
        if armed is None or armed.pattern != "122":
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
            out.append(Prospective122Observation(
                setup_id=_setup_id(ticker, watch_start),
                setup_fingerprint=_setup_fingerprint(high=armed.boundary_high, low=armed.boundary_low, reference=armed.reference_direction),
                ticker=ticker.upper(), session_date=session.date.isoformat(),
                watch_start=watch_start.isoformat(), watch_until=watch_until.isoformat(),
                status="DATA_BLOCKED", family=None, subtype=None, direction=None,
                trigger_bar_start=None, trigger_detectable_at=None, trigger_level=None,
                structural_opposite_boundary=None, strategy_stop=None, strategy_target=None,
                strategy_geometry_status="UNRESOLVED",
                final_scenario="unknown", opposite_side_broken_later=False,
                reason_code="missing_5m_bars:" + ",".join(item.isoformat() for item in missing),
                boundary_high=armed.boundary_high, boundary_low=armed.boundary_low,
                reference_direction=armed.reference_direction,
            ))
            watch_start += MINUTE_30.delta
            continue

        result = resolve_trigger(
            armed, lower, lower_timeframe=MINUTE_5,
            watch_start=watch_start, watch_until=watch_until,
        )
        status = result.status
        if status == "NO_TRIGGER":
            status = "WATCHING" if point < watch_until else "EXPIRED"

        trigger_start = result.trigger_bar_start
        detectable = trigger_start + MINUTE_5.delta if trigger_start is not None else None
        out.append(Prospective122Observation(
            setup_id=_setup_id(ticker, watch_start),
            setup_fingerprint=_setup_fingerprint(high=armed.boundary_high, low=armed.boundary_low, reference=armed.reference_direction),
            ticker=ticker.upper(), session_date=session.date.isoformat(),
            watch_start=watch_start.isoformat(), watch_until=watch_until.isoformat(),
            status=status, family=result.family, subtype=result.subtype, direction=result.direction,
            trigger_bar_start=trigger_start.isoformat() if trigger_start else None,
            trigger_detectable_at=detectable.isoformat() if detectable else None,
            trigger_level=result.trigger_level,
            structural_opposite_boundary=result.invalidation_level,
            strategy_stop=None, strategy_target=None, strategy_geometry_status="UNRESOLVED",
            final_scenario=result.final_scenario,
            opposite_side_broken_later=result.opposite_side_broken_later,
            reason_code=result.reason_code,
            boundary_high=armed.boundary_high, boundary_low=armed.boundary_low,
            reference_direction=armed.reference_direction,
        ))
        watch_start += MINUTE_30.delta

    return tuple(out)
