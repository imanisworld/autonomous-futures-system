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
