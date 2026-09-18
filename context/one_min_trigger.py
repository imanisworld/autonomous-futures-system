"""Isolated 1-minute trigger evidence lane for armed MNQ 4HR setups.

This module cannot discover setups, run DecisionEngine, or route a broker order.
It only records 1m bars and observes whether an already-persisted ARMED 4HR
trigger was touched. Default OFF via ONE_MIN_TRIGGER_ENABLED.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from config.futures_contracts import contract_root, optional_tick_size
from context.bar_history import BarHistory, _parse_dt
from context.five_min_feed import normalize_minutes, recent_five_min
from journal.journal_logger import JournalLogger
from strategy.four_hr_retrigger import aggregate_et_bars

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
ONE_MIN_LANE = "tf1m"
ONE_MIN_MINUTES = 1
ENABLED_ENV = "ONE_MIN_TRIGGER_ENABLED"
INSTRUMENT = "MNQ"
STRATEGY = "strat_4hr_retrigger"
def one_min_enabled() -> bool:
    return os.getenv(ENABLED_ENV, "").strip().lower() in {"1", "true", "yes"}


def is_one_min(timeframe: object) -> bool:
    return normalize_minutes(timeframe) == ONE_MIN_MINUTES


def _root(value: str) -> str:
    return contract_root(value) or str(value or "").upper().strip()


def _history(log_dir: str) -> BarHistory:
    return BarHistory(log_dir=str(Path(log_dir) / ONE_MIN_LANE))


def record_one_min(payload, log_dir: str, for_date=None) -> dict:
    """Store one completed 1m TradingView bar in a lane isolated from 5m/15m."""
    return _history(log_dir).record(
        _root(payload.ticker),
        ts=payload.timestamp,
        open=payload.open,
        high=payload.high,
        low=payload.low,
        close=payload.close,
        volume=getattr(payload, "volume", None),
        timeframe="1m",
        for_date=for_date,
    )
def recent_one_min(
    instrument: str, log_dir: str, n: int = 120, for_date=None, *, lookback_days: int = 1
) -> list[dict]:
    return _history(log_dir).recent(
        _root(instrument), n, for_date=for_date, lookback_days=lookback_days
    )


def _evidence_path(log_dir: str, day: date) -> Path:
    path = Path(log_dir) / ONE_MIN_LANE / f"4hr_trigger_evidence_{day.isoformat()}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _claim_path(log_dir: str, arm_key: str) -> Path:
    digest = hashlib.sha256(arm_key.encode("utf-8")).hexdigest()
    path = Path(log_dir) / ONE_MIN_LANE / "claims" / f"{digest}.claim"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _claim_once(log_dir: str, arm_key: str) -> bool:
    path = _claim_path(log_dir, arm_key)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(arm_key)
    return True


def _append_evidence(log_dir: str, day: date, event: dict) -> None:
    with open(_evidence_path(log_dir, day), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":"), default=str) + "\n")


def _fully_completed_one_hour_stop(
    *, log_dir: str, direction: str, bar_open: datetime, for_date=None
) -> tuple[Optional[float], Optional[datetime]]:
    bars = recent_five_min(
        INSTRUMENT, log_dir, 3000, for_date=for_date, lookback_days=10
    )
    available = []
    for bar in bars:
        ts = _parse_dt(str(bar.get("ts") or bar.get("timestamp") or ""))
        if ts is None:
            continue
        if ts + timedelta(minutes=5) <= bar_open:
            available.append(bar)
    one_hour = aggregate_et_bars(available, 60)
    completed = [
        bar for bar in one_hour
        if bar.get("count", 0) >= 12 and bar["ts"] + timedelta(hours=1) <= bar_open
    ]
    if not completed:
        return None, None
    ref = completed[-1]
    stop = ref["low"] if direction == "LONG" else ref["high"]
    return float(stop), ref["ts"]


def evaluate_armed_4hr_touch(payload, log_dir: str, for_date=None) -> Optional[dict]:
    """Observe a 1m touch only when an authoritative ARMED 4HR state already exists."""
    if _root(payload.ticker) != INSTRUMENT:
        return None
    bar_open = _parse_dt(str(payload.timestamp))
    if bar_open is None:
        return None
    bar_open = bar_open.astimezone(ET)
    day = for_date or bar_open.date()
    if not (time(9, 30) <= bar_open.timetz().replace(tzinfo=None) < time(11, 0)):
        return None

    daily = JournalLogger(log_dir=log_dir).get_daily_state(day)
    state = dict(daily.four_hr_retrigger_state.get(INSTRUMENT, {}) or {})
    if state.get("status") != "ARMED":
        return None
    if state.get("trading_date") != day.isoformat():
        return None
    direction = str(state.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return None
    try:
        trigger = float(state["trigger"])
        target = float(state["target"])
        bar_open_px = float(payload.open)
        bar_high = float(payload.high)
        bar_low = float(payload.low)
    except (KeyError, TypeError, ValueError):
        return None
    touched = bar_high >= trigger if direction == "LONG" else bar_low <= trigger
    if not touched:
        return None

    stop, stop_bar_ts = _fully_completed_one_hour_stop(
        log_dir=log_dir, direction=direction, bar_open=bar_open, for_date=day
    )
    if stop is None or stop_bar_ts is None:
        event = {
            "event": "TRIGGER_BLOCKED",
            "reason": "COMPLETED_1H_STOP_MISSING",
            "instrument": INSTRUMENT,
            "strategy": STRATEGY,
            "bar_ts": bar_open.isoformat(),
            "direction": direction,
            "trigger": trigger,
        }
        _append_evidence(log_dir, day, event)
        return event
    fill_reference = (
        max(trigger, bar_open_px) if direction == "LONG"
        else min(trigger, bar_open_px)
    )
    bracket_valid = (
        stop < fill_reference < target if direction == "LONG"
        else target < fill_reference < stop
    )
    arm_key = "|".join([
        day.isoformat(), direction, f"{trigger:.8f}",
        str(state.get("setup_bar_ts") or ""), str(state.get("four_am_bar_ts") or ""),
    ])
    if not bracket_valid:
        event = {
            "event": "TRIGGER_BLOCKED",
            "reason": "ENTRY_BRACKET_INVALID_AT_TOUCH",
            "instrument": INSTRUMENT,
            "strategy": STRATEGY,
            "bar_ts": bar_open.isoformat(),
            "direction": direction,
            "trigger": trigger,
            "fill_reference": fill_reference,
            "stop": stop,
            "target": target,
        }
        _append_evidence(log_dir, day, event)
        return event
    if not _claim_once(log_dir, arm_key):
        return {
            "event": "TRIGGER_DUPLICATE",
            "instrument": INSTRUMENT,
            "strategy": STRATEGY,
            "bar_ts": bar_open.isoformat(),
            "arm_key": arm_key,
        }

    tick = optional_tick_size(INSTRUMENT)
    slip = float(tick or 0.0)
    paper_entry = (
        fill_reference + slip if direction == "LONG"
        else fill_reference - slip
    )
    event = {
        "event": "TRIGGER_TOUCH",
        "mode": "paper_evidence_only",
        "external_broker": False,
        "trade_authorized": False,
        "instrument": INSTRUMENT,
        "strategy": STRATEGY,
        "bar_ts": bar_open.isoformat(),
        "decision_time": (bar_open + timedelta(minutes=1)).isoformat(),
        "direction": direction,
        "trigger": trigger,
        "fill_reference": fill_reference,
        "paper_entry_1tick": paper_entry,
        "stop": stop,
        "stop_bar_ts": stop_bar_ts.isoformat(),
        "target": target,
        "gap_through": (
            bar_open_px >= trigger if direction == "LONG"
            else bar_open_px <= trigger
        ),
        "arm_key": arm_key,
        "source_state": state,
    }
    _append_evidence(log_dir, day, event)
    return event


def one_min_status(log_dir: str, for_date=None) -> dict:
    bars = recent_one_min(INSTRUMENT, log_dir, 500, for_date=for_date)
    return {
        "enabled": one_min_enabled(),
        "instrument": INSTRUMENT,
        "bars": len(bars),
        "last_ts": bars[-1]["ts"] if bars else None,
    }
