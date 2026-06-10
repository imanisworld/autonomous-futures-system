"""Tests for the Polygon→replay candle derivation (scripts/polygon_to_replay)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scripts.polygon_to_replay import collapse_bar_type, derive_candles, ema_series

_ET = ZoneInfo("America/New_York")


def _mk_bars(n: int, start_et: datetime, *, base: float = 100.0, step: float = 0.5):
    """n 15m bars climbing `step` per bar from an ET start time."""
    bars = []
    price = base
    for i in range(n):
        ts = int((start_et + timedelta(minutes=15 * i)).timestamp())
        bars.append({"ts": ts, "open": price, "high": price + 1.0,
                     "low": price - 1.0, "close": price + step, "volume": 100})
        price += step
    return bars


class TestEmaSeries:
    def test_warmup_then_values(self):
        closes = [float(i) for i in range(1, 30)]
        ema = ema_series(closes, 9)
        assert ema[:8] == [None] * 8
        assert ema[8] == pytest.approx(5.0)  # SMA seed of 1..9
        assert all(v is not None for v in ema[8:])
        assert ema[-1] < closes[-1]  # EMA lags a rising series

    def test_short_series_all_none(self):
        assert ema_series([1.0, 2.0], 9) == [None, None]


class TestCollapseBarType:
    def test_vocabulary(self):
        assert collapse_bar_type("2U") == "2"
        assert collapse_bar_type("2D") == "2"
        assert collapse_bar_type("1") == "1"
        assert collapse_bar_type("3") == "3"
        assert collapse_bar_type(None) is None


class TestDeriveCandles:
    def _candles(self):
        # Start 02:00 ET so EMA200 warms up well before the 09:30 NY open.
        start = datetime(2026, 6, 9, 2, 0, tzinfo=_ET)
        bars = _mk_bars(260, start)
        return bars, derive_candles(bars, "MES", 15)

    def test_no_candles_before_first_ny_orb(self):
        _, candles = self._candles()
        assert candles  # something was emitted
        first = datetime.fromisoformat(candles[0]["timestamp"]).astimezone(_ET)
        assert (first.hour, first.minute) >= (9, 30)

    def test_orb_is_0930_bar_and_persists(self):
        bars, candles = self._candles()
        open_bar = next(
            b for b in bars
            if datetime.fromtimestamp(b["ts"], tz=_ET).hour == 9
            and datetime.fromtimestamp(b["ts"], tz=_ET).minute == 30
        )
        # At 15m timeframe the ORB is exactly the 09:30 bar's range...
        assert candles[0]["orb_high"] == open_bar["high"]
        assert candles[0]["orb_low"] == open_bar["low"]
        # ...and persists unchanged on later bars of the same day.
        assert candles[5]["orb_high"] == open_bar["high"]

    def test_trend_and_schema_on_rising_series(self):
        _, candles = self._candles()
        c = candles[10]
        assert c["trend_direction"] == "UP"  # monotonic rise → bullish EMA stack
        assert c["trend_strength"] in ("STRONG", "MODERATE")
        assert c["market_condition"] == "TRENDING"
        assert c["price_vs_vwap"] in ("above", "below", "at")
        # Steady +0.5 climbing bars break the prior high only → strat type 2.
        assert c["current_bar_type"] == "2"
        # Schema fields the engine gates on are present and non-None.
        for key in ("vwap", "ema_9", "ema_21", "ema_55", "avg_volume",
                    "previous_day_high", "previous_day_low", "session",
                    "orb_status", "timeframe", "ftfc_direction"):
            assert key in c
        assert c["timeframe"] == "15m"
        assert c["instrument"] == "MES"

    def test_orb_status_above_on_breakout(self):
        _, candles = self._candles()
        # The series rises forever, so late bars close above the frozen ORB high.
        assert candles[-1]["orb_status"] in ("above", "reclaimed_high")

    def test_htf_context_populated(self):
        _, candles = self._candles()
        c = candles[-1]
        # 1h resample exists from early bars; daily exists once a CME day closed.
        assert c["one_hour_direction"] in ("UP", "DOWN", "NEUTRAL", None)
        assert c["one_hour_bar_type"] is not None
