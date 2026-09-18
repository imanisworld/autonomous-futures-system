"""Generic isolated 1-minute context feed.

Storage only: no strategy state, risk engine, decision engine, or broker access.
"""
from __future__ import annotations

import os
from pathlib import Path

from config.futures_contracts import contract_root
from context.bar_history import BarHistory
from context.five_min_feed import normalize_minutes

ONE_MIN_LANE = "tf1m"
ONE_MIN_MINUTES = 1
ENABLED_ENV = "ONE_MIN_TRIGGER_ENABLED"


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
