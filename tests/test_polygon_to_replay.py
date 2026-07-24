"""Tests for the Polygon→replay candle derivation (scripts/polygon_to_replay)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scripts.polygon_to_replay import derive_candles, ema_series
from strategy.strat_classifier import (
    INSIDE_BAR,
    OUTSIDE_BAR,
    TWO_DOWN,
    TWO_UP,
    normalize_bar_type,
)

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
        # Steady +0.5 climbing bars break the prior high only → directional
        # two-up, NOT the collapsed/undirected "2" (regression: the base-
        # timeframe bar type used to be silently collapsed to "2" here,
        # which no directional consumer — strat_212/122, vwap_hold's SHORT
        # gate, the CHOPPY→RANGE_BOUND veto — could ever match).
        assert c["current_bar_type"] == "2U"
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


class TestDirectionalBarTypePreserved:
    """Regression for the polygon_to_replay.py defect: current_bar_type/
    previous_bar_type/two_bars_back_type were collapsed to an undirected
    "2" for ANY directional bar (2U or 2D alike) before being written to
    the ReplayCandle JSONL. Every consumer that reads current_bar_type
    directly for direction — strat_212/122's arm check, vwap_hold's SHORT
    gate, the CHOPPY→RANGE_BOUND veto in _score_market_condition — could
    never match a bare "2" against TWO_UP/TWO_DOWN, so all three were
    silently inert against every dataset scripts/polygon_to_replay.py ever
    produced. Fix: stop collapsing; store classify_htf_bar's own directional
    output (1/2U/2D/3) verbatim, exactly like the already-uncollapsed
    daily/four_hour/one_hour bar-type fields, and exactly like live's own
    Pine classify_bar() output (which normalize_bar_type() maps identically
    — see test_normalize_bar_type_accepts_emitted_representation below)."""

    def _candles_with_chained_pattern(self):
        # Same warmup as TestDeriveCandles._candles (EMA200 + first NY ORB),
        # then 5 bars with deliberately chosen OHLC so each bar's type
        # relative to its immediate predecessor is exactly controlled:
        #   [1] breaks high only  -> 2U
        #   [2] breaks neither    -> 1  (inside [1]'s range)
        #   [3] breaks low only   -> 2D
        #   [4] breaks both       -> 3  (outside [3]'s range)
        start = datetime(2026, 6, 9, 2, 0, tzinfo=_ET)
        bars = _mk_bars(260, start)
        next_ts = bars[-1]["ts"] + 15 * 60
        pattern_ohlc = [
            (200.0, 200.0, 190.0, 195.0),  # [0] reference bar
            (195.0, 205.0, 195.0, 202.0),  # [1] high>200, low=195 (not <190) -> 2U
            (202.0, 203.0, 197.0, 200.0),  # [2] high<205, low>195           -> 1
            (200.0, 201.0, 190.0, 193.0),  # [3] high<203, low<197           -> 2D
            (193.0, 210.0, 180.0, 205.0),  # [4] high>201, low<190           -> 3
        ]
        for i, (o, h, l, c) in enumerate(pattern_ohlc):
            bars.append({
                "ts": next_ts + i * 15 * 60, "open": o, "high": h, "low": l,
                "close": c, "volume": 100,
            })
        candles = derive_candles(bars, "MES", 15)
        return candles[-4:]  # the 4 classified pattern bars, in order

    def test_two_up_preserved_directional(self):
        c2u = self._candles_with_chained_pattern()[0]
        assert c2u["current_bar_type"] == "2U"
        assert normalize_bar_type(c2u["current_bar_type"]) == TWO_UP

    def test_two_down_preserved_directional(self):
        c2d = self._candles_with_chained_pattern()[2]
        assert c2d["current_bar_type"] == "2D"
        assert normalize_bar_type(c2d["current_bar_type"]) == TWO_DOWN

    def test_inside_bar_unchanged(self):
        c1 = self._candles_with_chained_pattern()[1]
        assert c1["current_bar_type"] == "1"
        assert normalize_bar_type(c1["current_bar_type"]) == INSIDE_BAR

    def test_outside_bar_unchanged(self):
        c3 = self._candles_with_chained_pattern()[3]
        assert c3["current_bar_type"] == "3"
        assert normalize_bar_type(c3["current_bar_type"]) == OUTSIDE_BAR

    def test_previous_bar_type_also_directional(self):
        # The 1-bar-lagged field must carry the same fix — strat_212/122
        # reads previous_bar_type directly too (arming needs BOTH bars'
        # direction, not just the current one).
        c_inside, c_2d = self._candles_with_chained_pattern()[1:3]
        assert c_inside["previous_bar_type"] == "2U"
        assert c_2d["previous_bar_type"] == "1"

    def test_normalize_bar_type_accepts_emitted_representation(self):
        # Downstream normalization (shared by live and replay) already
        # handles this exact "2U"/"2D" vocabulary — no changes were needed
        # anywhere outside this converter.
        assert normalize_bar_type("2U") == TWO_UP
        assert normalize_bar_type("2D") == TWO_DOWN
        assert normalize_bar_type("1") == INSIDE_BAR
        assert normalize_bar_type("3") == OUTSIDE_BAR
