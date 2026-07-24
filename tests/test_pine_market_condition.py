"""Tests for scripts/pine_market_condition.py -- Pine-exact market_condition
reconstruction used for offline replay evidence only (not the live/replay
decision path). See the module docstring for what's being reproduced and why.
"""
from __future__ import annotations

import itertools

import pytest

from scripts.pine_market_condition import (
    RECONSTRUCTED,
    RECONSTRUCTED_UNVALIDATED_INIT,
    UNAVAILABLE_SYNTHETIC_VOLUME,
    UNAVAILABLE_WARMUP,
    atr14_series,
    pine_trend_direction,
    reconstruct_bar,
    reconstruct_market_condition,
    sma_series,
    true_range,
)


# ---------------------------------------------------------------------------
# Requirement 1: Pine-exact 3-state trend direction (NOT classify_trend()'s
# richer MODERATE/WEAK states).
# ---------------------------------------------------------------------------

class TestPineTrendDirection:
    def test_up_on_full_bull_stack(self):
        assert pine_trend_direction(close=110, ema9=105, ema21=100, ema55=95) == "UP"

    def test_down_on_full_bear_stack(self):
        assert pine_trend_direction(close=90, ema9=95, ema21=100, ema55=105) == "DOWN"

    def test_sideways_on_partial_stack_python_would_call_moderate_up(self):
        # close > ema21 and ema9 > ema21, but NOT close > ema9 (full stack broken:
        # close < ema9). context.trend.classify_trend() would call this
        # MODERATE/UP -- a state Pine's own 3-state formula cannot produce.
        # Reconstruction must say SIDEWAYS.
        assert pine_trend_direction(close=100, ema9=102, ema21=98, ema55=97) == "SIDEWAYS"

    def test_sideways_on_mixed_stack(self):
        assert pine_trend_direction(close=100, ema9=105, ema21=98, ema55=102) == "SIDEWAYS"

    def test_none_when_any_input_missing(self):
        assert pine_trend_direction(None, 105, 100, 95) is None
        assert pine_trend_direction(110, None, 100, 95) is None
        assert pine_trend_direction(110, 105, None, 95) is None
        assert pine_trend_direction(110, 105, 100, None) is None


# ---------------------------------------------------------------------------
# Requirement 3: Wilder/RMA ATR14, not a simple moving average of true range.
# ---------------------------------------------------------------------------

def _atr_fixture(n: int = 24) -> tuple[list[float], list[float], list[float]]:
    """Deterministic, non-constant OHLC series (no randomness) so Wilder
    smoothing and a naive SMA-of-true-range genuinely diverge."""
    highs, lows, closes = [], [], []
    for i in range(n):
        base = 100 + (i % 5) * 2
        high = base + 1
        low = base - 1 - (i % 3)
        close = low + (high - low) * (0.3 + 0.1 * (i % 4))
        highs.append(high)
        lows.append(low)
        closes.append(close)
    return highs, lows, closes


def _true_range_longhand(high, low, prev_close):
    """Bar-0 true range uses high-low (Pine ta.tr(true) has no prior close
    on the very first bar of the series). Written out longhand -- not a
    call into true_range() -- so this cross-check shares no code with the
    implementation under test."""
    if prev_close is None:
        return high - low
    a = high - low
    b = high - prev_close
    if b < 0:
        b = -b
    c = low - prev_close
    if c < 0:
        c = -c
    return max(a, b, c)


def _independent_wilder_atr14(highs, lows, closes) -> list[float | None]:
    """Second, independently-written Wilder ATR14 implementation (not a call
    into atr14_series) used as a cross-check, not a restatement. Includes
    bar 0's true range (high-low, no prior close) in the RMA seed, per
    Pine's actual ta.tr(true)/ta.rma(14) behavior -- an operator review
    caught an earlier version of this same fixture that silently dropped
    bar 0 and validated the implementation's off-by-one against itself."""
    n = len(highs)
    trs = [_true_range_longhand(highs[0], lows[0], None)] + [
        _true_range_longhand(highs[i], lows[i], closes[i - 1]) for i in range(1, n)
    ]
    out: list[float | None] = [None] * n
    if n < 14:
        return out
    atr = sum(trs[0:14]) / 14.0
    out[13] = atr
    for i in range(14, n):
        atr = (atr * 13.0 + trs[i]) / 14.0
        out[i] = atr
    return out


def _naive_sma_of_true_range(highs, lows, closes, period: int = 14) -> list[float | None]:
    """Deliberately the WRONG approach the task explicitly forbids -- a plain
    simple moving average of true range -- used only to prove atr14_series
    does NOT match it."""
    n = len(highs)
    trs = [_true_range_longhand(highs[0], lows[0], None)] + [
        _true_range_longhand(highs[i], lows[i], closes[i - 1]) for i in range(1, n)
    ]
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = trs[max(0, i - period + 1) : i + 1]
        out[i] = sum(window) / len(window)
    return out


class TestAtr14Series:
    def test_true_range_uses_gap_when_larger_than_bar_range(self):
        # high-low=1, but the gap from prev_close dominates.
        assert true_range(high=101, low=100, prev_close=90) == 11

    def test_true_range_falls_back_to_high_low_with_no_prev_close(self):
        assert true_range(high=101, low=100, prev_close=None) == 1

    def test_none_before_warmup_complete(self):
        highs, lows, closes = _atr_fixture(20)
        out = atr14_series(highs, lows, closes)
        # First ATR is available at index 13 (the 14th bar): Pine's TR series
        # starts at bar 0 (high-low, no prior close), so RMA(14) seeds from
        # TR[0..13], not TR[1..14].
        assert out[:13] == [None] * 13
        assert out[13] is not None

    def test_first_atr_seed_includes_bar_zero_true_range(self):
        # Regression for the off-by-one: bar 0's true range (high-low, since
        # there is no prior close) must be part of the seed average.
        highs, lows, closes = _atr_fixture(20)
        out = atr14_series(highs, lows, closes)
        bar0_tr = highs[0] - lows[0]
        trs_1_to_13 = [
            _true_range_longhand(highs[i], lows[i], closes[i - 1]) for i in range(1, 14)
        ]
        expected_seed = (bar0_tr + sum(trs_1_to_13)) / 14.0
        assert out[13] == pytest.approx(expected_seed)

    def test_matches_independent_wilder_implementation(self):
        highs, lows, closes = _atr_fixture(24)
        expected = _independent_wilder_atr14(highs, lows, closes)
        actual = atr14_series(highs, lows, closes)
        assert len(actual) == len(expected)
        for i in range(13, 24):
            assert actual[i] == pytest.approx(expected[i]), f"index {i}"

    def test_diverges_from_naive_simple_average_of_true_range(self):
        highs, lows, closes = _atr_fixture(24)
        wilder = atr14_series(highs, lows, closes)
        naive = _naive_sma_of_true_range(highs, lows, closes)
        diverges = any(
            wilder[i] is not None and naive[i] is not None
            and abs(wilder[i] - naive[i]) > 1e-9
            for i in range(14, 24)
        )
        assert diverges, "fixture must exercise a case where Wilder != simple-average-of-TR"

    def test_short_series_returns_all_none(self):
        highs, lows, closes = _atr_fixture(10)
        out = atr14_series(highs, lows, closes)
        assert out == [None] * 10


# ---------------------------------------------------------------------------
# Requirement 2: exact current-inclusive 20-bar volume SMA (not the existing
# 21-bar-when-warm avg_volume window).
# ---------------------------------------------------------------------------

class TestSmaSeries:
    def test_none_before_period_bars_available(self):
        values = list(range(1, 20))  # 19 values, period 20
        out = sma_series(values, 20)
        assert out == [None] * 19

    def test_exact_20_bar_window_not_21(self):
        # 21 values: values[0]=100 is an outlier that must roll OUT of the
        # window by index 20. The pre-existing (untouched, unrelated)
        # avg_volume bug uses range(max(0, i-20), i+1) -- 21 bars once warm --
        # so at index 20 it would still include the outlier. The correct
        # Pine ta.sma(volume, 20) average at index 20 must be values[1:21]
        # only (the last 20), excluding values[0].
        values = [100] + [10] * 20
        out = sma_series(values, 20)
        first_full_window = sum([100] + [10] * 19) / 20  # values[0:20], outlier still in range
        correct_20_bar_at_20 = sum([10] * 20) / 20  # values[1:21] -- outlier rolled out -- = 10.0
        buggy_21_bar_at_20 = sum(values) / 21  # range(0, 21) -- what the old off-by-one would give
        assert out[19] == pytest.approx(first_full_window)
        assert out[20] == pytest.approx(correct_20_bar_at_20)
        assert out[20] != pytest.approx(buggy_21_bar_at_20)

    def test_rolls_forward_correctly(self):
        values = [1] * 20 + [41]  # replacing one 1 with a jump
        out = sma_series(values, 20)
        # window at index 20 = values[1:21] = nineteen 1s + one 41
        assert out[20] == pytest.approx((19 * 1 + 41) / 20)


# ---------------------------------------------------------------------------
# Requirement 4: exact 4-bucket Pine market_condition cascade.
# ---------------------------------------------------------------------------

class TestReconstructMarketCondition:
    def test_dead_below_rel_vol_threshold(self):
        assert reconstruct_market_condition(pine_trend="UP", rel_vol=0.39, range_ratio=2.0) == "DEAD"

    def test_choppy_on_low_range_ratio(self):
        assert reconstruct_market_condition(pine_trend="UP", rel_vol=0.90, range_ratio=0.39) == "CHOPPY"

    def test_choppy_on_low_rel_vol_even_with_high_range_ratio(self):
        assert reconstruct_market_condition(pine_trend="UP", rel_vol=0.59, range_ratio=2.0) == "CHOPPY"

    def test_trending_requires_directional_trend_and_high_rel_vol(self):
        assert reconstruct_market_condition(pine_trend="DOWN", rel_vol=0.80, range_ratio=1.0) == "TRENDING"

    def test_not_trending_when_sideways_even_with_high_rel_vol(self):
        # SIDEWAYS + rel_vol>=0.80 must fall through to RANGE_BOUND, not TRENDING.
        assert reconstruct_market_condition(pine_trend="SIDEWAYS", rel_vol=0.95, range_ratio=1.0) == "RANGE_BOUND"

    def test_range_bound_fallback(self):
        assert reconstruct_market_condition(pine_trend="UP", rel_vol=0.65, range_ratio=1.0) == "RANGE_BOUND"

    def test_bucket_boundary_at_exactly_040_rel_vol_is_not_dead(self):
        # rel_vol < 0.40 is DEAD; rel_vol == 0.40 must NOT be DEAD.
        result = reconstruct_market_condition(pine_trend="SIDEWAYS", rel_vol=0.40, range_ratio=1.0)
        assert result != "DEAD"

    def test_never_produces_consolidating_or_unknown(self):
        valid_buckets = {"DEAD", "CHOPPY", "TRENDING", "RANGE_BOUND"}
        trends = ["UP", "DOWN", "SIDEWAYS", None]
        rel_vols = [0.0, 0.1, 0.39, 0.40, 0.5, 0.59, 0.60, 0.79, 0.80, 1.0, 3.0]
        range_ratios = [0.0, 0.1, 0.39, 0.40, 0.5, 1.0, 3.0]
        for trend, rv, rr in itertools.product(trends, rel_vols, range_ratios):
            result = reconstruct_market_condition(pine_trend=trend, rel_vol=rv, range_ratio=rr)
            assert result in valid_buckets, (trend, rv, rr, result)


# ---------------------------------------------------------------------------
# Requirements 5 & 6: synthetic-volume exclusion + RECONSTRUCTED/
# RECONSTRUCTED_UNVALIDATED_INIT/UNAVAILABLE labeling. Trend and
# market_condition carry INDEPENDENT statuses (operator review: ATR is
# always self-computed with no proven pre-roll/convergence window, so
# market_condition can never claim the plain RECONSTRUCTED tier even when
# trend is Pine-exact) -- each output's own status governs whether THAT
# output is None, not a single shared status.
# ---------------------------------------------------------------------------

class TestReconstructBar:
    _FULL_DATA = dict(close=110, ema9=105, ema21=100, ema55=95, high=111, low=108, atr14=3.0, rel_vol=0.9)

    def test_pine_native_ema_source_yields_full_reconstructed_trend(self):
        trend, trend_status, cond, cond_status = reconstruct_bar(
            **self._FULL_DATA, ema_source="pine_native", volume_is_synthetic=False
        )
        assert trend_status == RECONSTRUCTED
        assert trend == "UP"
        # market_condition is NEVER plain RECONSTRUCTED -- ATR is always
        # self-computed in both pipelines, regardless of ema_source.
        assert cond_status == RECONSTRUCTED_UNVALIDATED_INIT
        assert cond in {"DEAD", "CHOPPY", "TRENDING", "RANGE_BOUND"}

    def test_self_computed_ema_source_yields_unvalidated_init_trend(self):
        trend, trend_status, cond, cond_status = reconstruct_bar(
            **self._FULL_DATA, ema_source="self_computed", volume_is_synthetic=False
        )
        assert trend_status == RECONSTRUCTED_UNVALIDATED_INIT
        assert trend == "UP"
        assert cond_status == RECONSTRUCTED_UNVALIDATED_INIT

    def test_synthetic_volume_marks_condition_unavailable_but_not_trend(self):
        # Synthetic volume taints market_condition (it needs rel_vol) but has
        # no bearing on trend, which depends only on close/EMA.
        trend, trend_status, cond, cond_status = reconstruct_bar(
            **self._FULL_DATA, ema_source="pine_native", volume_is_synthetic=True
        )
        assert trend_status == RECONSTRUCTED
        assert trend == "UP"
        assert cond_status == UNAVAILABLE_SYNTHETIC_VOLUME
        assert cond is None

    def test_missing_atr_marks_only_condition_warmup_unavailable(self):
        data = dict(self._FULL_DATA)
        data["atr14"] = None
        trend, trend_status, cond, cond_status = reconstruct_bar(
            **data, ema_source="pine_native", volume_is_synthetic=False
        )
        assert trend_status == RECONSTRUCTED and trend == "UP"
        assert cond_status == UNAVAILABLE_WARMUP and cond is None

    def test_missing_rel_vol_marks_only_condition_warmup_unavailable(self):
        data = dict(self._FULL_DATA)
        data["rel_vol"] = None
        trend, trend_status, cond, cond_status = reconstruct_bar(
            **data, ema_source="pine_native", volume_is_synthetic=False
        )
        assert trend_status == RECONSTRUCTED and trend == "UP"
        assert cond_status == UNAVAILABLE_WARMUP and cond is None

    def test_missing_ema_marks_both_warmup_unavailable(self):
        # Trend itself is unavailable (no EMA), so condition -- which needs
        # trend as an input -- cascades to unavailable too.
        data = dict(self._FULL_DATA)
        data["ema55"] = None
        trend, trend_status, cond, cond_status = reconstruct_bar(
            **data, ema_source="pine_native", volume_is_synthetic=False
        )
        assert trend_status == UNAVAILABLE_WARMUP and trend is None
        assert cond_status == UNAVAILABLE_WARMUP and cond is None

    def test_each_output_null_iff_its_own_status_is_unavailable(self):
        # Property check: for every combination, trend is None iff
        # trend_status is UNAVAILABLE_WARMUP, and cond is None iff
        # cond_status is one of the UNAVAILABLE_* statuses -- independently.
        for ema_source in ("pine_native", "self_computed"):
            for synthetic in (True, False):
                for atr in (None, 3.0):
                    for rv in (None, 0.9):
                        for ema in (None, 95):
                            data = dict(self._FULL_DATA)
                            data["atr14"] = atr
                            data["rel_vol"] = rv
                            data["ema55"] = ema
                            trend, trend_status, cond, cond_status = reconstruct_bar(
                                **data, ema_source=ema_source, volume_is_synthetic=synthetic
                            )
                            case = (ema_source, synthetic, atr, rv, ema)
                            assert (trend is None) == (trend_status == UNAVAILABLE_WARMUP), case
                            assert (cond is None) == (
                                cond_status in (UNAVAILABLE_WARMUP, UNAVAILABLE_SYNTHETIC_VOLUME)
                            ), case
