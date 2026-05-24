"""
replay/candle_loader.py

Loads offline replay candles from JSONL. No live data access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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

    def load_jsonl(self, path: str | Path) -> list[ReplayCandle]:
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
        return candles

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
        )
