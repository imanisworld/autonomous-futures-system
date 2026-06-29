"""5-minute entry feed — increment 1: ingest + store, OFF the 15M decision path.

The live decision engine is 15M-only, but STRAT doctrine wants a higher-timeframe
setup + a 5M entry trigger (see memory `project_timeframe_gap`). Today a 5M
TradingView alert is rejected as a TIMEFRAME_MISMATCH config error. This module
is the foundation of the fix:

  * accepts 5M bars on a SEPARATE lane (a `tf5m/` subdir under the journal dir)
    so they never mix with the 15M bar history that trend/window reads depend on;
  * stores them for the entry-trigger phase (increment 2) to consume;
  * NEVER produces a trade by itself — a stored 5M bar only records context.

Flag-gated by FIVE_MIN_FEED_ENABLED, default OFF. When off, nothing changes: 5M
alerts continue to hit the existing 15M timeframe guard and are rejected. This
keeps the 15M-only live behaviour byte-for-byte identical until the feed is
deliberately enabled.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from context.bar_history import BarHistory

# Subdirectory under the journal dir that isolates the 5M lane from 15M bars.
FIVE_MIN_LANE = "tf5m"
FIVE_MIN_MINUTES = 5


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
    """Contract root (e.g. 'MES1!' → 'MES'), matching the 15M lane's instrument
    key so increment 2 can join 5M context to the 15M decision by instrument."""
    return (instrument or "").upper().rstrip("!1234567890HMUZ")


def _history(log_dir: str) -> BarHistory:
    """The 5M BarHistory, isolated in its own subdir so recent()/window reads
    over 15M bars never see 5M bars and vice-versa."""
    return BarHistory(log_dir=str(Path(log_dir) / FIVE_MIN_LANE))


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
    instrument: str, log_dir: str, n: int = 60, for_date=None
) -> List[dict]:
    """Most recent ``n`` stored 5M bars for an instrument (oldest→newest)."""
    return _history(log_dir).recent(_root(instrument), n, for_date=for_date)
