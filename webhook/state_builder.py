"""
webhook/state_builder.py

Converts an AlertPayload (TradingView webhook body) into a MarketState
that the existing DecisionEngine + RiskEngine pipeline consumes.

Responsibilities:
- Parse/normalize timestamps (ISO 8601 or Unix ms)
- Normalize ticker symbols  (e.g. "MNQ1!" → "MNQ")
- Auto-detect trading session from ET timestamp when not explicitly provided
- Derive price_vs_vwap / price_vs_pdh / price_vs_pdl when absent
- Apply safe defaults for missing optional context so the broker-interface
  types are always satisfied (strategies handle None gracefully)
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from context.market_context import (
    MarketState,
    OHLCData,
    ORBData,
    PreviousDayData,
    PriceData,
    TrendData,
    VWAPData,
    VolumeData,
)
from strategy.strat_classifier import StratContext, classify_from_ohlc, classify_sequence
from webhook.payload import AlertPayload

# ─── Constants ────────────────────────────────────────────────────────────────

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

# Root ticker symbols the system accepts, sorted longest-first for prefix matching.
_KNOWN_INSTRUMENTS: tuple[str, ...] = tuple(
    sorted(("MNQ", "MES", "MGC", "MCL"), key=len, reverse=True)
)


# ─── Normalisation helpers ─────────────────────────────────────────────────────

def normalize_instrument(ticker: str) -> str:
    """
    Convert a TradingView ticker to the canonical 3-letter instrument name.

    Uses prefix matching against known instruments so that futures contract
    suffixes (month codes + year digits + "!") don't corrupt the base symbol.

    Examples:
        "MNQ1!"          → "MNQ"
        "MNQU2026"       → "MNQ"
        "CME_MINI:MNQ1!" → "MNQ"
        "MES"            → "MES"
        "MGC1!"          → "MGC"
    """
    if ":" in ticker:
        ticker = ticker.split(":")[-1]
    upper = ticker.upper()
    for root in _KNOWN_INSTRUMENTS:   # longest-first; avoids prefix ambiguity
        if upper.startswith(root):
            return root
    # Unknown instrument — return as-is; RiskEngine will reject it.
    return upper


def parse_timestamp(value: str) -> datetime:
    """
    Accept either an ISO 8601 string or a Unix timestamp (seconds or ms).

    TradingView's {{time}} placeholder emits Unix ms; Pine Script's
    str.tostring(time) also emits ms.  A formatted ISO string is accepted too.
    """
    stripped = value.strip()
    if stripped.lstrip("-").isdigit():
        ts_int = int(stripped)
        if ts_int > 1_000_000_000_000:   # milliseconds
            ts_int //= 1000
        return datetime.fromtimestamp(ts_int, tz=_UTC)
    return datetime.fromisoformat(stripped.replace("Z", "+00:00"))


def detect_session(ts: datetime) -> str:
    """
    Map a UTC datetime to a session name using ET market hours.

    London:      03:00–08:29 ET
    Session gap: 08:30–09:29 ET
    New York:    09:30–12:00 ET
    Anything outside → "off_hours" (RiskEngine will block it)
    """
    et_time = ts.astimezone(_ET).time()
    if time(3, 0) <= et_time < time(8, 30):
        return "london"
    if time(8, 30) <= et_time < time(9, 30):
        return "session_gap"
    if time(9, 30) <= et_time <= time(12, 0):
        return "new_york"
    return "off_hours"


# ─── Main builder ─────────────────────────────────────────────────────────────

def build_market_state(payload: AlertPayload) -> MarketState:
    """
    Convert an AlertPayload to a MarketState ready for the decision pipeline.
    Missing optional fields are filled with safe defaults so downstream
    dataclasses are never handed None for typed float fields.
    """
    ts = parse_timestamp(payload.timestamp)
    instrument = normalize_instrument(payload.ticker)
    session = payload.session or detect_session(ts)

    # ── Derived string flags ─────────────────────────────────────────────────
    vwap_value = payload.vwap if payload.vwap is not None else payload.close
    price_vs_vwap = (
        "above" if payload.close > vwap_value else
        "below" if payload.close < vwap_value else
        "at"
    )

    pdh = payload.previous_day_high if payload.previous_day_high is not None else payload.high
    pdl = payload.previous_day_low  if payload.previous_day_low  is not None else payload.low
    pdc = payload.previous_day_close if payload.previous_day_close is not None else payload.close

    price_vs_pdh = payload.price_vs_pdh or (
        "above" if payload.close > pdh else
        "below" if payload.close < pdh else "at"
    )
    price_vs_pdl = payload.price_vs_pdl or (
        "above" if payload.close > pdl else
        "below" if payload.close < pdl else "at"
    )

    orb_h = payload.orb_high if payload.orb_high is not None else payload.high
    orb_l = payload.orb_low  if payload.orb_low  is not None else payload.low
    strat = build_strat_context(payload)

    return MarketState(
        timestamp=ts,
        instrument=instrument,
        session=session,
        price=PriceData(last=payload.close, bid=payload.close, ask=payload.close),
        ohlc=OHLCData(
            open=payload.open,
            high=payload.high,
            low=payload.low,
            close=payload.close,
            timeframe=payload.timeframe,
            bar_start=payload.timestamp,
        ),
        vwap=VWAPData(
            value=vwap_value,
            price_vs_vwap=price_vs_vwap,
            reclaimed=price_vs_vwap == "above",
            holding=price_vs_vwap in ("above", "below"),
        ),
        orb=ORBData(
            high=orb_h,
            low=orb_l,
            timeframe_minutes=15,
            status=payload.orb_status,
        ),
        previous_day=PreviousDayData(
            high=pdh,
            low=pdl,
            close=pdc,
            price_vs_pdh=price_vs_pdh,
            price_vs_pdl=price_vs_pdl,
        ),
        volume=VolumeData(
            current_bar=payload.volume,
            avg_bar=max(payload.avg_volume, 1),
            relative=payload.volume / max(payload.avg_volume, 1),
        ),
        market_condition=payload.market_condition,
        trend=TrendData(
            direction=payload.trend_direction,
            strength=payload.trend_strength,
        ),
        strat=strat,
        raw=None,
    )


def build_strat_context(payload: AlertPayload) -> StratContext:
    """Use explicit Strat fields first, otherwise classify from optional OHLC history."""
    if any(
        value is not None
        for value in (
            payload.current_bar_type,
            payload.previous_bar_type,
            payload.two_bars_back_type,
            payload.strat_sequence,
            payload.strat_trigger,
            payload.strat_direction,
        )
    ):
        classified = classify_sequence(
            payload.two_bars_back_type,
            payload.previous_bar_type,
            payload.current_bar_type,
        )
        return StratContext(
            current_bar_type=payload.current_bar_type or classified.current_bar_type,
            previous_bar_type=payload.previous_bar_type or classified.previous_bar_type,
            two_bars_back_type=payload.two_bars_back_type or classified.two_bars_back_type,
            strat_sequence=payload.strat_sequence or classified.strat_sequence,
            strat_trigger=payload.strat_trigger or classified.strat_trigger,
            strat_direction=payload.strat_direction or classified.strat_direction,
        )

    return classify_from_ohlc(
        current_high=payload.high,
        current_low=payload.low,
        previous_high=payload.previous_bar_high,
        previous_low=payload.previous_bar_low,
        two_bars_back_high=payload.two_bars_back_high,
        two_bars_back_low=payload.two_bars_back_low,
    )
