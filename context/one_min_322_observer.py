"""Observation-only 1m trigger observer for MNQ 60M 3-2-2 First Live.

The observer maintains its own isolated state under tf1m/. It reuses the
canonical pure 3-2-2 state machine only to establish the completed 7/8/9AM
setup at the 10:00 ET boundary. It never imports or calls DecisionEngine,
RiskEngine, PaperBroker, or an external broker adapter.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from config.futures_contracts import contract_root
from context.bar_history import _parse_dt
from context.five_min_feed import is_five_min, recent_five_min
from context.one_min_feed import ONE_MIN_LANE, is_one_min, one_min_enabled
from strategy.strat_322_first_live import advance_strat_322_first_live

ET = ZoneInfo("America/New_York")
INSTRUMENT = "MNQ"
STRATEGY = "strat_322_first_live"
ENABLED_ENV = "ONE_MIN_322_OBSERVER_ENABLED"


def one_min_322_observer_enabled() -> bool:
    """Active only when the generic 1m lane and the dedicated observer are on."""
    dedicated = os.getenv(ENABLED_ENV, "").strip().lower() in {"1", "true", "yes"}
    return one_min_enabled() and dedicated


def _root(value: str) -> str:
    return contract_root(value) or str(value or "").upper().strip()


def _state_path(log_dir: str, day: date) -> Path:
    return (
        Path(log_dir)
        / ONE_MIN_LANE
        / "322_first_live"
        / f"state_{day.isoformat()}.json"
    )


def _evidence_path(log_dir: str, day: date) -> Path:
    return (
        Path(log_dir)
        / ONE_MIN_LANE
        / "322_first_live"
        / f"evidence_{day.isoformat()}.jsonl"
    )


def _claim_path(log_dir: str, arm_key: str) -> Path:
    digest = hashlib.sha256(arm_key.encode("utf-8")).hexdigest()
    return Path(log_dir) / ONE_MIN_LANE / "322_first_live" / "claims" / f"{digest}.claim"


def read_322_observer_state(log_dir: str, for_date: date) -> dict:
    path = _state_path(log_dir, for_date)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_state(log_dir: str, day: date, state: dict) -> None:
    path = _state_path(log_dir, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, separators=(",", ":"), default=str),
        encoding="utf-8",
    )
    tmp.replace(path)


def _append_evidence(log_dir: str, day: date, event: dict) -> None:
    path = _evidence_path(log_dir, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":"), default=str) + "\n")


def _claim_once(log_dir: str, arm_key: str) -> bool:
    path = _claim_path(log_dir, arm_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(arm_key)
    return True


def _payload_open(payload) -> Optional[datetime]:
    parsed = _parse_dt(str(getattr(payload, "timestamp", "") or ""))
    return parsed.astimezone(ET) if parsed is not None else None


def _reference_bars(log_dir: str, day: date, boundary: datetime) -> list[dict]:
    bars = recent_five_min(INSTRUMENT, log_dir, 1000, for_date=day, lookback_days=1)
    out = []
    for bar in bars:
        ts = _parse_dt(str(bar.get("ts") or bar.get("timestamp") or ""))
        if ts is None:
            continue
        local = ts.astimezone(ET)
        if local.date() != day:
            continue
        if ts + timedelta(minutes=5) <= boundary.astimezone(ts.tzinfo):
            out.append(bar)
    return out


def _missing_reference_slots(bars: list[dict], day: date) -> list[str]:
    have = set()
    for bar in bars:
        ts = _parse_dt(str(bar.get("ts") or bar.get("timestamp") or ""))
        if ts is not None:
            have.add(ts.astimezone(ET).replace(second=0, microsecond=0))
    expected = [
        datetime(day.year, day.month, day.day, 7, 0, tzinfo=ET)
        + timedelta(minutes=5 * i)
        for i in range(36)
    ]
    return [slot.isoformat() for slot in expected if slot not in have]


def _base_event(event: str, day: date, bar_ts: datetime) -> dict:
    return {
        "event": event,
        "mode": "observation_only",
        "trade_authorized": False,
        "paper_fill_authorized": False,
        "external_broker": False,
        "instrument": INSTRUMENT,
        "strategy": STRATEGY,
        "trading_date": day.isoformat(),
        "bar_ts": bar_ts.isoformat(),
    }


def advance_322_observer_from_five_min(
    payload, log_dir: str, for_date: Optional[date] = None
) -> Optional[dict]:
    """Maintain isolated observer state only at the 10:00/11:00 boundaries."""
    if not one_min_322_observer_enabled():
        return None
    if _root(getattr(payload, "ticker", "")) != INSTRUMENT:
        return None
    if not is_five_min(getattr(payload, "timeframe", None)):
        return None

    bar_open = _payload_open(payload)
    if bar_open is None:
        return None
    day = for_date or bar_open.date()
    if bar_open.date() != day:
        return None
    bar_close = bar_open + timedelta(minutes=5)
    hm = (bar_close.hour, bar_close.minute)
    existing = read_322_observer_state(log_dir, day)

    # The setup is knowable exactly when the 09:55 bar closes at 10:00 ET.
    if hm == (10, 0):
        boundary_key = bar_close.isoformat()
        if (
            existing.get("observer_boundary_ts") == boundary_key
            and existing.get("trading_date") == day.isoformat()
        ):
            return None

        bars = _reference_bars(log_dir, day, bar_close)
        missing = _missing_reference_slots(bars, day)
        if missing:
            state = {
                "trading_date": day.isoformat(),
                "status": "INVALIDATED",
                "invalidation": "REFERENCE_DATA_INCOMPLETE",
                "observer_boundary_ts": boundary_key,
                "missing_reference_bars": missing,
            }
            _write_state(log_dir, day, state)
            event = _base_event("ARM_BLOCKED", day, bar_close)
            event.update(
                reason="REFERENCE_DATA_INCOMPLETE",
                missing_reference_count=len(missing),
                missing_reference_bars=missing,
            )
            _append_evidence(log_dir, day, event)
            return event

        next_state, candidate = advance_strat_322_first_live(
            bars_5m=bars,
            current_bar_ts=bar_close,
            instrument=INSTRUMENT,
            persisted_state={},
        )
        # No 10:00 bar exists in the completed reference set, so candidate
        # creation at the arm boundary would indicate a causal-data violation.
        if candidate is not None:
            next_state = {
                "trading_date": day.isoformat(),
                "status": "INVALIDATED",
                "invalidation": "UNEXPECTED_TRIGGER_AT_ARM_BOUNDARY",
            }

        next_state = dict(next_state)
        next_state["observer_boundary_ts"] = boundary_key
        next_state["observer_mode"] = "observation_only"
        _write_state(log_dir, day, next_state)

        if next_state.get("status") == "ARMED":
            event = _base_event("ARMED", day, bar_close)
            event.update(
                direction=next_state.get("direction"),
                trigger=next_state.get("trigger"),
                stop=next_state.get("stop"),
                target=next_state.get("target"),
                setup_bar_ts=next_state.get("setup_bar_ts"),
                expires_at=next_state.get("expires_at"),
            )
        else:
            event = _base_event("SETUP_NOT_ARMED", day, bar_close)
            event.update(
                reason=next_state.get("invalidation") or "CANONICAL_SETUP_NOT_ARMED"
            )
        _append_evidence(log_dir, day, event)
        return event

    # Expire an untouched arm when the 10:55 bar closes at 11:00 ET.
    if hm == (11, 0):
        if (
            existing.get("trading_date") != day.isoformat()
            or existing.get("status") != "ARMED"
        ):
            return None
        next_state, _ = advance_strat_322_first_live(
            bars_5m=[],
            current_bar_ts=bar_close,
            instrument=INSTRUMENT,
            persisted_state=existing,
        )
        next_state = dict(next_state)
        next_state["observer_boundary_ts"] = existing.get("observer_boundary_ts")
        next_state["observer_mode"] = "observation_only"
        _write_state(log_dir, day, next_state)
        event = _base_event("EXPIRED", day, bar_close)
        event.update(reason=next_state.get("invalidation") or "NO_BREAK_BY_11AM")
        _append_evidence(log_dir, day, event)
        return event

    return None


def evaluate_armed_322_touch(
    payload, log_dir: str, for_date: Optional[date] = None
) -> Optional[dict]:
    """Observe the first strict 1m break of an already-armed 3-2-2 setup."""
    if not one_min_322_observer_enabled():
        return None
    if _root(getattr(payload, "ticker", "")) != INSTRUMENT:
        return None
    if not is_one_min(getattr(payload, "timeframe", None)):
        return None

    bar_open = _payload_open(payload)
    if bar_open is None:
        return None
    day = for_date or bar_open.date()
    if bar_open.date() != day:
        return None
    local_time = bar_open.timetz().replace(tzinfo=None)
    if not (time(10, 0) <= local_time < time(11, 0)):
        return None

    state = read_322_observer_state(log_dir, day)
    if state.get("trading_date") != day.isoformat() or state.get("status") != "ARMED":
        return None

    direction = str(state.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return None
    try:
        trigger = float(state["trigger"])
        stop = float(state["stop"])
        target = float(state["target"])
        open_px = float(payload.open)
        high = float(payload.high)
        low = float(payload.low)
        close = float(payload.close)
    except (KeyError, TypeError, ValueError):
        return None

    crossed = high > trigger if direction == "LONG" else low < trigger
    if not crossed:
        return None

    gap_through = open_px > trigger if direction == "LONG" else open_px < trigger
    trigger_reference = open_px if gap_through else trigger
    bracket_valid = (
        stop < trigger_reference < target
        if direction == "LONG"
        else target < trigger_reference < stop
    )
    arm_key = "|".join(
        [
            day.isoformat(),
            direction,
            f"{trigger:.8f}",
            str(state.get("setup_bar_ts") or ""),
        ]
    )

    if not bracket_valid:
        invalid = dict(state)
        invalid.update(
            status="INVALIDATED",
            invalidation="ENTRY_BRACKET_INVALID_AT_TOUCH",
            first_live_bar_ts=bar_open.isoformat(),
            trigger_reference=trigger_reference,
            gap_through=gap_through,
        )
        _write_state(log_dir, day, invalid)
        event = _base_event("TRIGGER_BLOCKED", day, bar_open)
        event.update(
            reason="ENTRY_BRACKET_INVALID_AT_TOUCH",
            direction=direction,
            trigger=trigger,
            trigger_reference=trigger_reference,
            stop=stop,
            target=target,
            gap_through=gap_through,
            one_min_ohlc={"open": open_px, "high": high, "low": low, "close": close},
        )
        _append_evidence(log_dir, day, event)
        return event

    if not _claim_once(log_dir, arm_key):
        return None

    triggered = dict(state)
    triggered.update(
        status="TRIGGERED",
        invalidation=None,
        first_live_bar_ts=bar_open.isoformat(),
        observed_at=(bar_open + timedelta(minutes=1)).isoformat(),
        trigger_reference=trigger_reference,
        gap_through=gap_through,
        arm_key=arm_key,
    )
    _write_state(log_dir, day, triggered)

    event = _base_event("TRIGGER_TOUCH", day, bar_open)
    event.update(
        decision_time=(bar_open + timedelta(minutes=1)).isoformat(),
        direction=direction,
        trigger=trigger,
        trigger_reference=trigger_reference,
        stop=stop,
        target=target,
        gap_through=gap_through,
        setup_bar_ts=state.get("setup_bar_ts"),
        arm_key=arm_key,
        one_min_ohlc={"open": open_px, "high": high, "low": low, "close": close},
    )
    _append_evidence(log_dir, day, event)
    return event
