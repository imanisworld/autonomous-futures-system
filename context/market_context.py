"""
context/market_context.py

Loads and validates market state JSON files.
Any data quality issue → raises DataQualityError (caller logs NO_TRADE).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import jsonschema

from config.settings import SystemConfig, load_config
from strategy.strat_classifier import StratContext


# ─── Exceptions ──────────────────────────────────────────────────────────────

class DataQualityError(ValueError):
    """Raised when market state data fails any quality check."""


class StaleDataError(DataQualityError):
    """Raised when market state timestamp is too old."""


class SchemaValidationError(DataQualityError):
    """Raised when market state JSON fails schema validation."""


# ─── Market State Dataclass ──────────────────────────────────────────────────

@dataclass
class OHLCData:
    open: float
    high: float
    low: float
    close: float
    timeframe: str
    bar_start: Optional[str] = None


@dataclass
class VWAPData:
    value: float
    price_vs_vwap: str          # above | below | at
    reclaimed: Optional[bool] = None
    holding: Optional[bool] = None


@dataclass
class ORBData:
    high: float
    low: float
    timeframe_minutes: int
    status: Optional[str] = None  # above | below | inside | reclaimed_high | etc.


@dataclass
class PreviousDayData:
    high: float
    low: float
    close: float
    price_vs_pdh: Optional[str] = None
    price_vs_pdl: Optional[str] = None


@dataclass
class VolumeData:
    current_bar: int
    avg_bar: int
    relative: Optional[float] = None


@dataclass
class PriceData:
    last: float
    bid: float
    ask: float


@dataclass
class TrendData:
    direction: Optional[str] = None        # UP | DOWN | SIDEWAYS
    strength: Optional[str] = None         # STRONG | MODERATE | WEAK
    ema_fast_above_slow: Optional[bool] = None


@dataclass
class GEXContext:
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


@dataclass
class SignaContext:
    grade: Optional[str] = None
    score: Optional[float] = None
    daily_direction: Optional[str] = None
    weekly_direction: Optional[str] = None


@dataclass
class ICCContext:
    phase: Optional[str] = None
    entry_signal: Optional[str] = None
    indication_type: Optional[str] = None
    indication_level: Optional[float] = None
    last_swing_high: Optional[float] = None
    last_swing_low: Optional[float] = None
    correction_high: Optional[float] = None
    correction_low: Optional[float] = None
    stop_loss: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    htf_phase: Optional[str] = None


@dataclass
class HTFContext:
    daily_bar_type: Optional[str] = None
    daily_direction: Optional[str] = None
    four_hour_bar_type: Optional[str] = None
    four_hour_direction: Optional[str] = None
    one_hour_bar_type: Optional[str] = None
    one_hour_direction: Optional[str] = None
    ftfc_direction: Optional[str] = None
    ftfc_aligned: Optional[bool] = None


@dataclass
class SupplyDemandData:
    supply_top: Optional[float] = None
    supply_bottom: Optional[float] = None
    supply_wavg: Optional[float] = None
    demand_top: Optional[float] = None
    demand_bottom: Optional[float] = None
    demand_wavg: Optional[float] = None

    def price_in_supply(self, price: float) -> bool:
        if self.supply_bottom is None or self.supply_top is None:
            return False
        return self.supply_bottom <= price <= self.supply_top

    def price_in_demand(self, price: float) -> bool:
        if self.demand_bottom is None or self.demand_top is None:
            return False
        return self.demand_bottom <= price <= self.demand_top

    def price_at_demand(self, price: float) -> bool:
        """True if price is inside or within 2 ticks of the demand zone."""
        if self.demand_bottom is None or self.demand_top is None:
            return False
        margin = (self.demand_top - self.demand_bottom) * 0.2
        return (self.demand_bottom - margin) <= price <= (self.demand_top + margin)

    def price_at_supply(self, price: float) -> bool:
        """True if price is inside or within 2 ticks of the supply zone."""
        if self.supply_bottom is None or self.supply_top is None:
            return False
        margin = (self.supply_top - self.supply_bottom) * 0.2
        return (self.supply_bottom - margin) <= price <= (self.supply_top + margin)


@dataclass
class KeyLevels:
    """Intraday and multi-day price levels used for entries, targets, and bias."""
    # Intraday (running, resets each session)
    hod: Optional[float] = None          # High of Day — resistance / short target
    lod: Optional[float] = None          # Low of Day  — support  / long target
    # Previous week (static, from Pine weekly security call)
    prev_week_high: Optional[float] = None
    prev_week_low: Optional[float] = None
    # EMAs (values from Pine, calculated on chart timeframe)
    ema_9: Optional[float] = None
    ema_21: Optional[float] = None
    ema_55: Optional[float] = None
    ema_200: Optional[float] = None
    # Derived flags (set by state_builder when values present)
    ema_9_above_21: Optional[bool] = None   # 9/21 crossover — momentum direction
    price_above_ema_55: Optional[bool] = None  # trend bias
    price_above_ema_200: Optional[bool] = None  # macro bias

    def near_level(self, price: float, level: float, ticks: int = 8, tick_size: float = 0.25) -> bool:
        """True if price is within N ticks of a key level."""
        return abs(price - level) <= ticks * tick_size


@dataclass
class MarketState:
    timestamp: datetime
    instrument: str
    session: str
    price: PriceData
    ohlc: OHLCData
    vwap: VWAPData
    orb: ORBData
    previous_day: PreviousDayData
    volume: VolumeData
    market_condition: Optional[str] = None
    trend: Optional[TrendData] = None
    strat: Optional[StratContext] = None
    gex: Optional[GEXContext] = None
    signa: Optional[SignaContext] = None
    icc: Optional[ICCContext] = None
    htf: Optional[HTFContext] = None
    sd: Optional[SupplyDemandData] = None
    key_levels: Optional[KeyLevels] = None
    # Direction of recent close-to-close price action over a window of prior bars
    # (UP/DOWN/None), from context.bar_history. Populated on the LIVE ingest path
    # only (the replay/test paths leave it None → no behavior change there). Lets
    # regime see CONTINUOUS price action, not just this one bar's Pine label.
    window_direction: Optional[str] = None
    notes: Optional[str] = None
    raw: dict = None  # Original dict for reference


# ─── Loader ──────────────────────────────────────────────────────────────────

class MarketStateLoader:
    """Loads and validates market state JSON files."""

    SCHEMA_PATH = Path(__file__).parent.parent / "market_state.schema.json"

    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or load_config()
        self._schema = self._load_schema()

    def _load_schema(self) -> dict:
        if not self.SCHEMA_PATH.exists():
            raise FileNotFoundError(f"Schema not found: {self.SCHEMA_PATH}")
        with open(self.SCHEMA_PATH) as f:
            return json.load(f)

    def load(self, path: str) -> MarketState:
        """
        Load market state from JSON file.

        Raises:
            FileNotFoundError: File doesn't exist.
            SchemaValidationError: JSON doesn't match schema.
            StaleDataError: Data is older than max_staleness_seconds.
            DataQualityError: Any other data quality issue.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Market state file not found: {p}")

        with open(p) as f:
            raw = json.load(f)

        self._validate_schema(raw)
        self._validate_ohlc_logic(raw)
        state = self._parse(raw)
        self._validate_freshness(state)
        return state

    def load_from_dict(self, raw: dict) -> MarketState:
        """Load market state from a dict (for testing)."""
        self._validate_schema(raw)
        self._validate_ohlc_logic(raw)
        state = self._parse(raw)
        self._validate_freshness(state)
        return state

    def _validate_schema(self, raw: dict) -> None:
        try:
            jsonschema.validate(raw, self._schema)
        except jsonschema.ValidationError as e:
            raise SchemaValidationError(f"Schema validation failed: {e.message}") from e

    def _validate_ohlc_logic(self, raw: dict) -> None:
        """Check for contradictory OHLC values."""
        if not self.config.reject_contradictory_data:
            return
        ohlc = raw.get("ohlc", {})
        high = ohlc.get("high", 0)
        low = ohlc.get("low", 0)
        open_ = ohlc.get("open", 0)
        close = ohlc.get("close", 0)

        if high < low:
            raise DataQualityError(f"Contradictory OHLC: high ({high}) < low ({low})")
        if open_ > high or open_ < low:
            raise DataQualityError(f"Contradictory OHLC: open ({open_}) outside high/low range")
        if close > high or close < low:
            raise DataQualityError(f"Contradictory OHLC: close ({close}) outside high/low range")

        pd = raw.get("previous_day", {})
        pd_high = pd.get("high", 0)
        pd_low = pd.get("low", 0)
        if pd_high < pd_low:
            raise DataQualityError(f"Contradictory previous_day: high ({pd_high}) < low ({pd_low})")

    def _validate_freshness(self, state: MarketState) -> None:
        now = datetime.now(timezone.utc)
        age = (now - state.timestamp).total_seconds()
        if age > self.config.max_staleness_seconds:
            raise StaleDataError(
                f"Market state is stale: {age:.0f}s old "
                f"(max {self.config.max_staleness_seconds}s). "
                f"Timestamp: {state.timestamp.isoformat()}"
            )

    def _parse(self, raw: dict) -> MarketState:
        ts_str = raw["timestamp"]
        # Parse ISO 8601 — handle Z suffix
        ts_str = ts_str.replace("Z", "+00:00")
        ts = datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        price_raw = raw["price"]
        price = PriceData(
            last=price_raw["last"],
            bid=price_raw["bid"],
            ask=price_raw["ask"],
        )

        ohlc_raw = raw["ohlc"]
        ohlc = OHLCData(
            open=ohlc_raw["open"],
            high=ohlc_raw["high"],
            low=ohlc_raw["low"],
            close=ohlc_raw["close"],
            timeframe=ohlc_raw["timeframe"],
            bar_start=ohlc_raw.get("bar_start"),
        )

        vwap_raw = raw["vwap"]
        vwap = VWAPData(
            value=vwap_raw["value"],
            price_vs_vwap=vwap_raw["price_vs_vwap"],
            reclaimed=vwap_raw.get("reclaimed"),
            holding=vwap_raw.get("holding"),
        )

        orb_raw = raw["orb"]
        orb = ORBData(
            high=orb_raw["high"],
            low=orb_raw["low"],
            timeframe_minutes=orb_raw["timeframe_minutes"],
            status=orb_raw.get("status"),
        )

        pd_raw = raw["previous_day"]
        previous_day = PreviousDayData(
            high=pd_raw["high"],
            low=pd_raw["low"],
            close=pd_raw["close"],
            price_vs_pdh=pd_raw.get("price_vs_pdh"),
            price_vs_pdl=pd_raw.get("price_vs_pdl"),
        )

        vol_raw = raw["volume"]
        volume = VolumeData(
            current_bar=vol_raw["current_bar"],
            avg_bar=vol_raw["avg_bar"],
            relative=vol_raw.get("relative"),
        )

        trend = None
        if raw.get("trend"):
            t = raw["trend"]
            trend = TrendData(
                direction=t.get("direction"),
                strength=t.get("strength"),
                ema_fast_above_slow=t.get("ema_fast_above_slow"),
            )

        return MarketState(
            timestamp=ts,
            instrument=raw["instrument"],
            session=raw["session"],
            price=price,
            ohlc=ohlc,
            vwap=vwap,
            orb=orb,
            previous_day=previous_day,
            volume=volume,
            market_condition=raw.get("market_condition"),
            trend=trend,
            strat=StratContext(**raw["strat"]) if raw.get("strat") else None,
            notes=raw.get("notes"),
            raw=raw,
        )
