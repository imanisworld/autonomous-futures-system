"""5-minute entry feed — ingest, store, and trigger an armed 15M setup.

The live decision engine is 15M-only, but STRAT doctrine wants a higher-timeframe
setup + a 5M entry trigger (see memory `project_timeframe_gap`). Today a 5M
TradingView alert is rejected as a TIMEFRAME_MISMATCH config error. This module
is the foundation of the fix:

  * accepts 5M bars on a SEPARATE lane (a `tf5m/` subdir under the journal dir)
    so they never mix with the 15M bar history that trend/window reads depend on;
  * stores them for the entry-trigger phase;
  * can trigger only an exact setup already validated and armed by the 15M
    decision engine. The 5M lane never discovers or modifies a setup.

Flag-gated by FIVE_MIN_FEED_ENABLED, default OFF. When off, nothing changes: 5M
alerts continue to hit the existing 15M timeframe guard and are rejected. This
keeps the 15M-only live behaviour byte-for-byte identical until the feed is
deliberately enabled.
"""
from __future__ import annotations

import os
import re
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from context.bar_history import BarHistory, _parse_dt

# Subdirectory under the journal dir that isolates the 5M lane from 15M bars.
FIVE_MIN_LANE = "tf5m"
FIVE_MIN_MINUTES = 5
ARM_TTL_MINUTES = 20
MAX_TRIGGER_DISTANCE_TICKS = 1
_TICK_SIZE = {"MES": 0.25, "MNQ": 0.25, "MGC": 0.1, "MCL": 0.01}


def five_min_enabled() -> bool:
    """True only when FIVE_MIN_FEED_ENABLED is explicitly truthy. Default OFF."""
    return os.getenv("FIVE_MIN_FEED_ENABLED", "").strip().lower() in ("1", "true", "yes")


def normalize_minutes(timeframe: object) -> Optional[int]:
    """Best-effort minutes from a timeframe token: '5', '5m', '5min', '1h'.

    Self-contained (no import from webhook.runner) to avoid a circular import,
    since runner imports this module.
    """
    if timeframe is None:
        return None
    s = str(timeframe).strip().lower()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    for suffix, mult in (("min", 1), ("m", 1), ("h", 60), ("hr", 60)):
        if s.endswith(suffix):
            head = s[: -len(suffix)].strip()
            if head.isdigit():
                return int(head) * mult
    return None


def is_five_min(timeframe: object) -> bool:
    return normalize_minutes(timeframe) == FIVE_MIN_MINUTES


def _root(instrument: str) -> str:
    """Contract root from a TradingView continuous symbol, e.g. 'MES1!' → 'MES',
    'MYM1!' → 'MYM'. Matches the 15M lane's instrument key so increment 2 can join
    5M context to the 15M decision by instrument.

    A plain ``rstrip("!1234567890HMUZ")`` over-strips roots that END in a month-
    code letter (MYM → 'MY'). This parses the structured suffix instead: the
    leading alphabetic run, then an optional contract index and '!'. Falls back to
    the upper-cased input when the shape is unexpected (kept internally consistent
    because record/recent both apply _root)."""
    s = (instrument or "").upper().strip()
    m = re.match(r"^([A-Z]+?)\d*!?$", s)
    return m.group(1) if m else s


def _history(log_dir: str) -> BarHistory:
    """The 5M BarHistory, isolated in its own subdir so recent()/window reads
    over 15M bars never see 5M bars and vice-versa."""
    return BarHistory(log_dir=str(Path(log_dir) / FIVE_MIN_LANE))


def _arm_path(instrument: str, log_dir: str, for_date=None) -> Path:
    d = for_date or date.today()
    return Path(log_dir) / FIVE_MIN_LANE / f"armed_{_root(instrument)}_{d.isoformat()}.json"


def arm_fifteen_min_setup(
    instrument: str,
    log_dir: str,
    *,
    setup: dict,
    payload: dict,
    for_date=None,
) -> dict:
    """Persist the exact setup approved by the 15M engine before detachment.

    This is deliberately a single replaceable arm per instrument/day. A later
    15M decision clears it before evaluation, so stale authority cannot stack.
    """
    source_ts = _parse_dt(str(payload.get("timestamp") or ""))
    source_minutes = normalize_minutes(payload.get("timeframe")) or 15
    authorized_at = (
        source_ts + timedelta(minutes=source_minutes) if source_ts else None
    )
    record = {
        "instrument": _root(instrument),
        "armed_from_ts": str(payload.get("timestamp") or ""),
        # TradingView bar timestamps identify the bar OPEN. The setup is not
        # knowable until that bar closes, so TTL and causal triggering begin at
        # open + timeframe, not at the source timestamp.
        "authorized_at": authorized_at.isoformat() if authorized_at else None,
        "setup": setup,
        "payload": payload,
    }
    path = _arm_path(instrument, log_dir, for_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, separators=(",", ":"), default=str))
    tmp.replace(path)
    return record


def clear_armed_setup(instrument: str, log_dir: str, for_date=None) -> None:
    """Invalidate prior 15M authority. Optional-lane storage is fail-soft."""
    path = _arm_path(instrument, log_dir, for_date)
    try:
        path.unlink()
    except OSError:
        pass


def read_armed_setup(instrument: str, log_dir: str, for_date=None) -> Optional[dict]:
    path = _arm_path(instrument, log_dir, for_date)
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def triggered_armed_setup(
    payload,
    log_dir: str,
    for_date=None,
    *,
    now: Optional[datetime] = None,
    ttl_minutes: int = ARM_TTL_MINUTES,
    max_distance_ticks: int = MAX_TRIGGER_DISTANCE_TICKS,
) -> Optional[dict]:
    """Return an armed 15M setup only on a close-near-entry retest.

    LONG requires the 5M bar to trade at/below the original entry and close
    back no more than one tick above it; SHORT is the mirror image. Keeping the
    close near entry matters because the downstream broker enters at bar close
    while the original bracket remains unchanged. The caller consumes authority
    only after an order actually opens. Missing/malformed/stale state fails closed.
    """
    armed = read_armed_setup(payload.ticker, log_dir, for_date)
    if not armed:
        return None
    setup = armed.get("setup")
    if not isinstance(setup, dict):
        clear_armed_setup(payload.ticker, log_dir, for_date)
        return None
    armed_ts = _parse_dt(
        str(armed.get("authorized_at") or armed.get("armed_from_ts") or "")
    )
    trigger_ts = _parse_dt(str(payload.timestamp))
    current = now or trigger_ts or datetime.now(timezone.utc)
    if (
        armed_ts is None
        or trigger_ts is None
        or trigger_ts < armed_ts
        or current - armed_ts > timedelta(minutes=ttl_minutes)
    ):
        clear_armed_setup(payload.ticker, log_dir, for_date)
        return None
    try:
        entry = float(setup["entry"])
        direction = str(setup["direction"]).upper()
        tick = _TICK_SIZE.get(_root(payload.ticker), 0.25)
        triggered = retest_triggered(
            direction=direction,
            entry=entry,
            bar_high=float(payload.high),
            bar_low=float(payload.low),
            bar_close=float(payload.close),
            tick_size=tick,
            max_distance_ticks=max_distance_ticks,
        )
    except (KeyError, TypeError, ValueError):
        triggered = False
    if not triggered:
        return None
    return armed


def retest_triggered(
    *,
    direction: str,
    entry: float,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    tick_size: float,
    max_distance_ticks: int = MAX_TRIGGER_DISTANCE_TICKS,
) -> bool:
    """Pure close-confirmed retest predicate shared by live and replay.

    The caller must supply only a completed 5-minute bar.  Keeping time/arm
    lifecycle outside this predicate makes causal replay straightforward and
    prevents a research implementation from drifting away from live behavior.
    """
    max_distance = max(0, int(max_distance_ticks)) * float(tick_size)
    direction = str(direction).upper()
    if direction == "LONG":
        return bar_low <= entry <= bar_close <= entry + max_distance
    if direction == "SHORT":
        return entry - max_distance <= bar_close <= entry <= bar_high
    return False


def record_five_min(payload, log_dir: str, for_date=None) -> dict:
    """Append one 5M bar-close to the dedicated lane. Returns the stored record.

    Idempotent on the last timestamp (BarHistory.record dedupes resends).
    """
    return _history(log_dir).record(
        _root(payload.ticker),
        ts=payload.timestamp,
        open=payload.open,
        high=payload.high,
        low=payload.low,
        close=payload.close,
        volume=getattr(payload, "volume", None),
        timeframe="5m",
        for_date=for_date,
    )


def recent_five_min(
    instrument: str,
    log_dir: str,
    n: int = 60,
    for_date=None,
    *,
    lookback_days: int = 3,
) -> List[dict]:
    """Most recent ``n`` stored 5M bars for an instrument (oldest→newest)."""
    return _history(log_dir).recent(
        _root(instrument),
        n,
        for_date=for_date,
        lookback_days=lookback_days,
    )


def five_min_status(log_dir: str, instruments=None, for_date=None) -> dict:
    """Observe-only summary of the 5M lane for /status: whether the feed is on,
    and per-instrument bar count + last timestamp for the day. Read-only."""
    insts = instruments or ["MES", "MNQ"]
    hist = _history(log_dir)
    per: dict = {}
    for inst in insts:
        root = _root(inst)
        bars = hist.recent(root, 500, for_date=for_date)
        per[root] = {"bars": len(bars), "last_ts": bars[-1]["ts"] if bars else None}
    return {"enabled": five_min_enabled(), "instruments": per}
