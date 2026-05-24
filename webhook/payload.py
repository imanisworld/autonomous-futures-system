"""
webhook/payload.py

Pydantic model for inbound TradingView webhook payloads.

Required fields (available from any bar-close alert):
    ticker, timestamp, open, high, low, close

Optional context fields (provided by a full Pine Script indicator):
    vwap, orb_high/low, trend, market_condition, previous-day levels, etc.
    When absent, strategies that require them return NO_TRADE gracefully.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class AlertPayload(BaseModel):
    # ── Required — present in every TradingView bar-close alert ──────────────
    ticker: str
    timestamp: str          # ISO 8601 string OR Unix ms timestamp as string
    open: float
    high: float
    low: float
    close: float

    # ── Semi-required — usually present ──────────────────────────────────────
    volume: int = 0
    timeframe: str = "5m"

    # ── Optional session override (auto-detected from timestamp if absent) ───
    session: Optional[str] = None

    # ── Optional context from Pine Script indicator ───────────────────────────
    avg_volume: int = 1
    vwap: Optional[float] = None
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    orb_status: Optional[str] = None
    market_condition: Optional[str] = None
    trend_direction: Optional[str] = None
    trend_strength: Optional[str] = None
    previous_day_high: Optional[float] = None
    previous_day_low: Optional[float] = None
    previous_day_close: Optional[float] = None
    price_vs_pdh: Optional[str] = None
    price_vs_pdl: Optional[str] = None

    # ── Optional Strat context ───────────────────────────────────────────────
    current_bar_type: Optional[str] = None
    previous_bar_type: Optional[str] = None
    two_bars_back_type: Optional[str] = None
    strat_sequence: Optional[str] = None
    strat_trigger: Optional[str] = None
    strat_direction: Optional[str] = None
    previous_bar_high: Optional[float] = None
    previous_bar_low: Optional[float] = None
    two_bars_back_high: Optional[float] = None
    two_bars_back_low: Optional[float] = None
