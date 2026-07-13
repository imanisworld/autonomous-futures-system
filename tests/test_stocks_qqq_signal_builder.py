"""
tests/test_stocks_qqq_signal_builder.py

stocks_advisory/qqq_signal_builder.py tests. Proves it derives a correct
QQQSignalInput from a full first hour of completed bars, fails closed
(never raises) on every malformed/partial/stale input case, and has no
Robinhood/broker/execution/futures/options_manager coupling.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import stocks_advisory.qqq_signal_builder as qqq_signal_builder_module
from stocks_advisory.backtest_models import Bar
from stocks_advisory.qqq_signal_builder import SignalBuildResult, build_qqq_signal
from stocks_advisory.tqqq_sqqq_models import QQQSignalInput


def _bar(minute_offset: int, o: float, h: float, l: float, c: float, v: int) -> Bar:
    hour = 9 + (30 + minute_offset) // 60
    minute = (30 + minute_offset) % 60
    return Bar(
        timestamp=f"2026-07-06T{hour:02d}:{minute:02d}:00-04:00",
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
    )


def _full_valid_day() -> list[Bar]:
    bars = []
    # 12 five-minute bars covering the first hour: 09:30 .. 10:25
    for i in range(12):
        m = i * 5
        bars.append(_bar(m, 100.0 + i * 0.1, 100.5 + i * 0.1, 99.8 + i * 0.1, 100.2 + i * 0.1, 1000 + i * 10))
    # one confirming bar after the first hour closes: 10:30
    bars.append(_bar(60, 101.5, 101.8, 101.3, 101.6, 1200))
    return bars


VALID_KWARGS = dict(
    date="2026-07-06",
    qqq_previous_day_close=99.0,
    qqq_previous_day_high=100.0,
    qqq_previous_day_low=98.0,
    qqq_relative_volume=1.2,
    allowed_max_gap_percent=2.0,
    allowed_min_first_hour_range=0.1,
    allowed_max_first_hour_range=5.0,
)


def test_builds_signal_from_full_first_hour_plus_confirmation_bar():
    bars = _full_valid_day()
    result = build_qqq_signal(qqq_bars_today=bars, **VALID_KWARGS)
    assert result.ok is True
    assert result.reject_reason == ""
    assert isinstance(result.signal, QQQSignalInput)
    assert result.signal.date == "2026-07-06"
    assert result.signal.qqq_open == bars[0].open
    assert result.signal.qqq_current_price == bars[-1].close
    assert result.signal.qqq_first_hour_high == max(b.high for b in bars[:12])
    assert result.signal.qqq_first_hour_low == min(b.low for b in bars[:12])
    assert result.signal.qqq_first_hour_close == bars[11].close
    assert result.signal.relative_volume == 1.2
    assert result.signal.market_regime_label is None


def test_gap_percent_is_signed_and_matches_backtest_formula():
    bars = _full_valid_day()
    kwargs = dict(VALID_KWARGS)
    kwargs["qqq_previous_day_close"] = 90.0
    result = build_qqq_signal(qqq_bars_today=bars, **kwargs)
    assert result.ok is True
    expected_gap = (bars[0].open - 90.0) / 90.0 * 100.0
    assert abs(result.signal.qqq_gap_percent - expected_gap) < 1e-9
    assert result.signal.qqq_gap_percent > 0  # confirms sign is preserved, not abs()'d


def test_vwap_is_cumulative_causal_through_last_supplied_bar():
    bars = _full_valid_day()
    result = build_qqq_signal(qqq_bars_today=bars, **VALID_KWARGS)
    assert result.ok is True
    cum_pv = 0.0
    cum_vol = 0.0
    for b in bars:
        typical = (b.high + b.low + b.close) / 3.0
        cum_pv += typical * b.volume
        cum_vol += b.volume
    expected_vwap = cum_pv / cum_vol
    assert abs(result.signal.qqq_vwap - expected_vwap) < 1e-9


def test_market_regime_label_passthrough():
    bars = _full_valid_day()
    result = build_qqq_signal(qqq_bars_today=bars, market_regime_label="trend_up", **VALID_KWARGS)
    assert result.ok is True
    assert result.signal.market_regime_label == "trend_up"


def test_rejects_empty_bars():
    result = build_qqq_signal(qqq_bars_today=[], **VALID_KWARGS)
    assert result.ok is False
    assert result.signal is None
    assert "no QQQ bars" in result.reject_reason


def test_rejects_before_first_hour_closes():
    bars = _full_valid_day()[:12]  # exactly the opening range, no confirmation bar yet
    result = build_qqq_signal(qqq_bars_today=bars, **VALID_KWARGS)
    assert result.ok is False
    assert "has not closed yet" in result.reject_reason


def test_rejects_partial_first_hour():
    bars = _full_valid_day()[:5]  # not even a full first hour
    result = build_qqq_signal(qqq_bars_today=bars, **VALID_KWARGS)
    assert result.ok is False
    assert result.signal is None


def test_rejects_malformed_timestamp():
    bars = _full_valid_day()
    bad = dataclasses.replace(bars[0], timestamp="not-a-timestamp")
    bars[0] = bad
    result = build_qqq_signal(qqq_bars_today=bars, **VALID_KWARGS)
    assert result.ok is False
    assert "malformed bar timestamp" in result.reject_reason


def test_rejects_out_of_order_bars():
    bars = _full_valid_day()
    bars[3], bars[4] = bars[4], bars[3]
    result = build_qqq_signal(qqq_bars_today=bars, **VALID_KWARGS)
    assert result.ok is False
    assert "not strictly ascending" in result.reject_reason


def test_rejects_duplicate_timestamp_bar():
    bars = _full_valid_day()
    duplicate = dataclasses.replace(bars[3], timestamp=bars[2].timestamp)
    bars[3] = duplicate
    result = build_qqq_signal(qqq_bars_today=bars, **VALID_KWARGS)
    assert result.ok is False
    assert "not strictly ascending" in result.reject_reason


def test_rejects_non_positive_ohlc():
    bars = _full_valid_day()
    bars[2] = dataclasses.replace(bars[2], low=0.0)
    result = build_qqq_signal(qqq_bars_today=bars, **VALID_KWARGS)
    assert result.ok is False
    assert "non-positive OHLC" in result.reject_reason


def test_rejects_negative_volume():
    bars = _full_valid_day()
    bars[2] = dataclasses.replace(bars[2], volume=-5)
    result = build_qqq_signal(qqq_bars_today=bars, **VALID_KWARGS)
    assert result.ok is False
    assert "negative volume" in result.reject_reason


def test_rejects_non_positive_previous_close():
    bars = _full_valid_day()
    kwargs = dict(VALID_KWARGS)
    kwargs["qqq_previous_day_close"] = 0.0
    result = build_qqq_signal(qqq_bars_today=bars, **kwargs)
    assert result.ok is False
    assert "previous-day close" in result.reject_reason


def test_rejects_non_positive_previous_high_low():
    bars = _full_valid_day()
    kwargs = dict(VALID_KWARGS)
    kwargs["qqq_previous_day_high"] = -1.0
    result = build_qqq_signal(qqq_bars_today=bars, **kwargs)
    assert result.ok is False
    assert "previous-day high/low" in result.reject_reason


def test_never_raises_on_malformed_input():
    # A grab-bag of malformed input; must always return a SignalBuildResult, never raise.
    for bars in ([], _full_valid_day()[:12], _full_valid_day()[:1]):
        result = build_qqq_signal(qqq_bars_today=bars, **VALID_KWARGS)
        assert isinstance(result, SignalBuildResult)


def test_no_broker_execution_futures_options_manager_import():
    source = Path(qqq_signal_builder_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_prefixes = ("broker", "execution", "futures", "options_manager", "robinhood")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.lower().startswith(forbidden_prefixes), alias.name
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            assert not module.startswith(forbidden_prefixes), module
