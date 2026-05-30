"""
replay/candle_loader.py

Loads offline replay candles from JSONL. No live data access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ReplayCandle:
    timestamp: str
    instrument: str
    session: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float
    orb_high: float
    orb_low: float
    orb_status: Optional[str]
    market_condition: Optional[str]
    trend_direction: Optional[str]
    trend_strength: Optional[str]
    previous_day_high: float
    previous_day_low: float
    previous_day_close: float
    price_vs_pdh: Optional[str] = None
    price_vs_pdl: Optional[str] = None
    timeframe: str = "5m"
    avg_volume: int = 1
    # London ORB (optional — populated when session=london and Pine provides it)
    london_orb_high: Optional[float] = None
    london_orb_low: Optional[float] = None
    london_orb_status: Optional[str] = None
    # Strat classification (optional — Phase 2; computed from bar history when present)
    current_bar_type: Optional[str] = None
    previous_bar_type: Optional[str] = None
    two_bars_back_type: Optional[str] = None
    strat_sequence: Optional[str] = None
    strat_trigger: Optional[str] = None
    strat_direction: Optional[str] = None
    # Bar history highs/lows for auto-classification when explicit types not provided
    previous_bar_high: Optional[float] = None
    previous_bar_low: Optional[float] = None
    two_bars_back_high: Optional[float] = None
    two_bars_back_low: Optional[float] = None
    # GEX / Signa / ICC / Supply-Demand enrichment (optional)
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
    signa_grade: Optional[str] = None
    signa_score: Optional[float] = None
    signa_daily_direction: Optional[str] = None
    signa_weekly_direction: Optional[str] = None
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
    supply_top: Optional[float] = None
    supply_bottom: Optional[float] = None
    supply_wavg: Optional[float] = None
    demand_top: Optional[float] = None
    demand_bottom: Optional[float] = None
    demand_wavg: Optional[float] = None

    @property
    def price_vs_vwap(self) -> str:
        if self.close > self.vwap:
            return "above"
        if self.close < self.vwap:
            return "below"
        return "at"


class ReplayCandleLoader:
    """Reads replay candles from local JSONL files."""

    REQUIRED_FIELDS = {
        "timestamp",
        "instrument",
        "session",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "vwap",
        "orb_high",
        "orb_low",
        "previous_day_high",
        "previous_day_low",
        "previous_day_close",
    }

    def load_jsonl(
        self,
        path: str | Path,
        *,
        allow_mixed_instruments: bool = False,
    ) -> list[ReplayCandle]:
        replay_path = Path(path)
        candles: list[ReplayCandle] = []
        with replay_path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                missing = self.REQUIRED_FIELDS.difference(raw)
                if missing:
                    raise ValueError(f"{replay_path}:{line_no} missing fields: {sorted(missing)}")
                candles.append(self._parse(raw))
        self._validate_candles(
            candles,
            replay_path,
            allow_mixed_instruments=allow_mixed_instruments,
        )
        return candles

    def _validate_candles(
        self,
        candles: list[ReplayCandle],
        replay_path: Path,
        *,
        allow_mixed_instruments: bool,
    ) -> None:
        timestamps: list[datetime] = []
        seen_raw_timestamps: set[str] = set()
        instruments: set[str] = set()

        for idx, candle in enumerate(candles, start=1):
            if candle.timestamp in seen_raw_timestamps:
                raise ValueError(f"{replay_path}:{idx} duplicate timestamp: {candle.timestamp}")
            seen_raw_timestamps.add(candle.timestamp)
            timestamps.append(_parse_timestamp(candle.timestamp))
            instruments.add(candle.instrument)

            if candle.high < candle.low:
                raise ValueError(f"{replay_path}:{idx} malformed candle: high < low")
            if not (candle.low <= candle.open <= candle.high):
                raise ValueError(f"{replay_path}:{idx} malformed candle: open outside high/low")
            if not (candle.low <= candle.close <= candle.high):
                raise ValueError(f"{replay_path}:{idx} malformed candle: close outside high/low")
            if candle.orb_high < candle.orb_low:
                raise ValueError(f"{replay_path}:{idx} malformed ORB: orb_high < orb_low")
            if candle.volume < 0:
                raise ValueError(f"{replay_path}:{idx} malformed candle: volume < 0")
            if candle.avg_volume < 1:
                raise ValueError(f"{replay_path}:{idx} malformed candle: avg_volume < 1")

        if timestamps != sorted(timestamps):
            raise ValueError(f"{replay_path} candles are not sorted by timestamp")
        if not allow_mixed_instruments and len(instruments) > 1:
            raise ValueError(f"{replay_path} contains mixed instruments: {sorted(instruments)}")

    @staticmethod
    def _parse(raw: dict) -> ReplayCandle:
        return ReplayCandle(
            timestamp=raw["timestamp"],
            instrument=raw["instrument"],
            session=raw["session"],
            open=float(raw["open"]),
            high=float(raw["high"]),
            low=float(raw["low"]),
            close=float(raw["close"]),
            volume=int(raw["volume"]),
            vwap=float(raw["vwap"]),
            orb_high=float(raw["orb_high"]),
            orb_low=float(raw["orb_low"]),
            orb_status=raw.get("orb_status"),
            market_condition=raw.get("market_condition"),
            trend_direction=raw.get("trend_direction"),
            trend_strength=raw.get("trend_strength"),
            previous_day_high=float(raw["previous_day_high"]),
            previous_day_low=float(raw["previous_day_low"]),
            previous_day_close=float(raw["previous_day_close"]),
            price_vs_pdh=raw.get("price_vs_pdh"),
            price_vs_pdl=raw.get("price_vs_pdl"),
            timeframe=raw.get("timeframe", "5m"),
            avg_volume=int(raw.get("avg_volume", 1)),
            london_orb_high=float(raw["london_orb_high"]) if raw.get("london_orb_high") is not None else None,
            london_orb_low=float(raw["london_orb_low"]) if raw.get("london_orb_low") is not None else None,
            london_orb_status=raw.get("london_orb_status"),
            current_bar_type=raw.get("current_bar_type"),
            previous_bar_type=raw.get("previous_bar_type"),
            two_bars_back_type=raw.get("two_bars_back_type"),
            strat_sequence=raw.get("strat_sequence"),
            strat_trigger=raw.get("strat_trigger"),
            strat_direction=raw.get("strat_direction"),
            previous_bar_high=float(raw["previous_bar_high"]) if raw.get("previous_bar_high") is not None else None,
            previous_bar_low=float(raw["previous_bar_low"]) if raw.get("previous_bar_low") is not None else None,
            two_bars_back_high=float(raw["two_bars_back_high"]) if raw.get("two_bars_back_high") is not None else None,
            two_bars_back_low=float(raw["two_bars_back_low"]) if raw.get("two_bars_back_low") is not None else None,
            gex_flip=_float_or_none(raw.get("gex_flip")),
            call_wall=_float_or_none(raw.get("call_wall")),
            put_wall=_float_or_none(raw.get("put_wall")),
            hvl=_float_or_none(raw.get("hvl")),
            max_pain=_float_or_none(raw.get("max_pain")),
            ghost=_float_or_none(raw.get("ghost")),
            mid_upper=_float_or_none(raw.get("mid_upper")),
            mid_lower=_float_or_none(raw.get("mid_lower")),
            vol_trigger_up=_float_or_none(raw.get("vol_trigger_up")),
            vol_trigger_down=_float_or_none(raw.get("vol_trigger_down")),
            gex_regime=raw.get("gex_regime"),
            delta_bias=raw.get("delta_bias"),
            signa_grade=raw.get("signa_grade"),
            signa_score=_float_or_none(raw.get("signa_score")),
            signa_daily_direction=raw.get("signa_daily_direction"),
            signa_weekly_direction=raw.get("signa_weekly_direction"),
            icc_phase=raw.get("icc_phase"),
            icc_entry_signal=raw.get("icc_entry_signal"),
            icc_indication_type=raw.get("icc_indication_type"),
            icc_indication_level=_float_or_none(raw.get("icc_indication_level")),
            icc_last_swing_high=_float_or_none(raw.get("icc_last_swing_high")),
            icc_last_swing_low=_float_or_none(raw.get("icc_last_swing_low")),
            icc_correction_high=_float_or_none(raw.get("icc_correction_high")),
            icc_correction_low=_float_or_none(raw.get("icc_correction_low")),
            icc_stop_loss=_float_or_none(raw.get("icc_stop_loss")),
            icc_tp1=_float_or_none(raw.get("icc_tp1")),
            icc_tp2=_float_or_none(raw.get("icc_tp2")),
            icc_htf_phase=raw.get("icc_htf_phase"),
            supply_top=_float_or_none(raw.get("supply_top")),
            supply_bottom=_float_or_none(raw.get("supply_bottom")),
            supply_wavg=_float_or_none(raw.get("supply_wavg")),
            demand_top=_float_or_none(raw.get("demand_top")),
            demand_bottom=_float_or_none(raw.get("demand_bottom")),
            demand_wavg=_float_or_none(raw.get("demand_wavg")),
        )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _float_or_none(value):
    if value is None or value == "":
        return None
    return float(value)
