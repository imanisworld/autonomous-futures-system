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

from pydantic import BaseModel, field_validator, model_validator

# ── Price sanity ranges per instrument root ───────────────────────────────────
# Reject payloads where close price is outside the plausible range.
# Prevents MNQ prices being accepted for MES (or vice versa).
_PRICE_RANGES: dict[str, tuple[float, float]] = {
    "MES": (2_000,  12_000),   # S&P 500 micro — never been above ~6k, floor at ~1k
    "ES":  (2_000,  12_000),   # S&P 500 full
    "MNQ": (5_000,  40_000),   # Nasdaq micro — headroom above current ~30k
    "NQ":  (5_000,  40_000),   # Nasdaq full
    "MGC": (1_000,   5_000),   # Micro Gold
    "MCL": (   10,     500),   # Micro Crude Oil
}


class AlertPayload(BaseModel):
    # ── Required — present in every TradingView bar-close alert ──────────────
    ticker: str
    timestamp: str          # ISO 8601 string OR Unix ms timestamp as string
    open: float
    high: float
    low: float
    close: float

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_numeric_timestamp(cls, v: object) -> object:
        """Pine's `str.tostring(time)` emits a BARE Unix-ms number (no quotes),
        so the JSON arrives as an int. Stringify it — parse_timestamp already
        understands a Unix-ms string. Keeps ISO strings untouched."""
        if isinstance(v, bool):
            return v  # let the str validator reject it
        if isinstance(v, (int, float)):
            return str(int(v))
        return v

    # ── Semi-required — usually present ──────────────────────────────────────
    volume: int = 0
    timeframe: str = "5m"

    # ── Optional session override (auto-detected from timestamp if absent) ───
    session: Optional[str] = None

    # ── VWAP cross signal — must be sent explicitly by Pine (ta.crossover) ───
    # False by default: vwap_reclaim strategy only fires on the actual cross bar.
    vwap_reclaimed: bool = False

    # ── Optional context from Pine Script indicator ───────────────────────────
    avg_volume: int = 1
    vwap: Optional[float] = None
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    orb_status: Optional[str] = None
    london_orb_high: Optional[float] = None    # set by Pine during London session
    london_orb_low: Optional[float] = None
    london_orb_status: Optional[str] = None
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


    # ── Optional Pine-generated advisory bracket ────────────────────────────
    # Backend still validates direction/strategy/structure and risk rules.
    signal_strategy: Optional[str] = None
    signal_direction: Optional[str] = None
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    rr_ratio: Optional[float] = None
    has_signal: Optional[bool] = None
    event_type: Optional[str] = None

    # ── Optional correlation id (preserved if valid, else generated) ─────────
    event_id: Optional[str] = None
    alert_name: Optional[str] = None

    # ── Optional GEX / gamma context ────────────────────────────────────────
    gex_flip: Optional[float] = None
    call_wall: Optional[float] = None
    put_wall: Optional[float] = None
    hvl: Optional[float] = None
    max_pain: Optional[float] = None
    ghost: Optional[float] = None
    mid_upper: Optional[float] = None
    mid_lower: Optional[float] = None
    vol_trigger_up: Optional[float] = None
    vol_trigger_down: Optional[float] = None
    gex_regime: Optional[str] = None
    delta_bias: Optional[str] = None

    # ── Optional Signa context ──────────────────────────────────────────────
    signa_grade: Optional[str] = None
    signa_score: Optional[float] = None
    signa_daily_direction: Optional[str] = None
    signa_weekly_direction: Optional[str] = None

    # ── Optional ICC context ────────────────────────────────────────────────
    icc_phase: Optional[str] = None
    icc_entry_signal: Optional[str] = None
    icc_indication_type: Optional[str] = None
    icc_indication_level: Optional[float] = None
    icc_last_swing_high: Optional[float] = None
    icc_last_swing_low: Optional[float] = None
    icc_correction_high: Optional[float] = None
    icc_correction_low: Optional[float] = None
    icc_stop_loss: Optional[float] = None
    icc_tp1: Optional[float] = None
    icc_tp2: Optional[float] = None
    icc_htf_phase: Optional[str] = None

    # ── Optional HTF / FTFC context ─────────────────────────────────────────
    daily_bar_type: Optional[str] = None
    daily_direction: Optional[str] = None
    four_hour_bar_type: Optional[str] = None
    four_hour_direction: Optional[str] = None
    one_hour_bar_type: Optional[str] = None
    one_hour_direction: Optional[str] = None
    ftfc_direction: Optional[str] = None
    ftfc_aligned: Optional[bool] = None

    # ── Key levels: intraday + weekly + EMAs ────────────────────────────────
    hod: Optional[float] = None              # High of Day (running, resets daily)
    lod: Optional[float] = None              # Low of Day  (running, resets daily)
    prev_week_high: Optional[float] = None   # Previous week high — swing target
    prev_week_low: Optional[float] = None    # Previous week low  — swing target
    ema_9: Optional[float] = None
    ema_21: Optional[float] = None
    ema_55: Optional[float] = None
    ema_200: Optional[float] = None
    ema_9_above_21: Optional[bool] = None    # Pine can send true/false directly

    # ── Price sanity validator ───────────────────────────────────────────────
    @model_validator(mode="after")
    def _check_price_range(self) -> "AlertPayload":
        """
        Reject payloads where close price is outside the known range for the
        instrument.  Catches MNQ prices accidentally sent for MES ticker, etc.
        """
        root = self.ticker.upper().lstrip("1234567890")  # strip contract suffix
        # Strip trailing digits + common exchange suffixes (MES1!, MNQU2026…)
        for suffix in ("!", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
                       "H", "M", "U", "Z"):
            root = root.rstrip(suffix)
        lo, hi = _PRICE_RANGES.get(root, (0, float("inf")))
        if not (lo <= self.close <= hi):
            raise ValueError(
                f"Price {self.close} is outside the expected range "
                f"[{lo}, {hi}] for instrument '{self.ticker}' (root '{root}'). "
                f"Check that the TradingView alert is on the correct chart."
            )
        return self

    # ── Supply & Demand zones (from LuxAlgo or equivalent indicator) ─────────
    # Primary names
    supply_top: Optional[float] = None
    supply_bottom: Optional[float] = None
    supply_wavg: Optional[float] = None
    demand_top: Optional[float] = None
    demand_bottom: Optional[float] = None
    demand_wavg: Optional[float] = None
    # TradingView-friendly aliases (supply_zone_high etc.)
    supply_zone_high: Optional[float] = None
    supply_zone_low: Optional[float] = None
    demand_zone_high: Optional[float] = None
    demand_zone_low: Optional[float] = None
    # Zone metadata
    zone_type: Optional[str] = None   # "supply" | "demand" | "both"
    zone_state: Optional[str] = None  # "fresh" | "used" | "stale"

    # ── BOS / MSS (Break of Structure / Market Structure Shift) ──────────────
    # Sent by Pine on the bar where structure breaks.
    # bos_direction: "bullish" (broke above swing high) | "bearish" (broke below swing low) | null
    # mss_direction: "bullish" (bearish-to-bullish shift) | "bearish" (bullish-to-bearish) | null
    # market_structure: combined label — "bullish_bos" | "bearish_bos" | "bullish_mss" | "bearish_mss" | "none"
    bos_direction: Optional[str] = None
    mss_direction: Optional[str] = None
    market_structure: Optional[str] = None
