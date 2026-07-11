"""Tests for scripts/stocks_advisory_robustness_audit.py's pure bucketing/
stats helper functions. No CSV files, no network -- constructs
BacktestTradeResult objects directly. Integration coverage for the
underlying strategy logic already lives in
tests/test_stocks_tqqq_sqqq_backtest.py; this file only covers the
audit's own read-only aggregation code."""

from __future__ import annotations

from typing import Optional

import pytest

import scripts.stocks_advisory_robustness_audit as audit
from stocks_advisory.backtest_models import Bar, BacktestTradeResult, DaySession, TradeDirection


def _trade(
    date: str, vehicle: str, direction: TradeDirection, dollar_result: float,
    entry_price: Optional[float] = None, exit_price: Optional[float] = None,
) -> BacktestTradeResult:
    return BacktestTradeResult(
        trade_date=date, vehicle_symbol=vehicle, direction=direction,
        dollar_result=dollar_result, entry_price=entry_price, exit_price=exit_price,
        skipped=False,
    )


def _bar(ts: str, o: float, h: float, l: float, c: float, v: int = 100) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


class TestQuarterAndMonth:
    def test_quarter_of(self):
        assert audit._quarter_of("2025-01-15") == "2025-Q1"
        assert audit._quarter_of("2025-04-01") == "2025-Q2"
        assert audit._quarter_of("2025-12-31") == "2025-Q4"

    def test_month_of(self):
        assert audit._month_of("2025-06-10") == "2025-06"


class TestBucketStats:
    def test_empty_bucket(self):
        stats = audit._bucket_stats([])
        assert stats["trade_count"] == 0
        assert stats["win_rate_percent"] is None
        assert stats["total_pnl_dollars"] == 0.0

    def test_mixed_wins_and_losses(self):
        trades = [
            _trade("2025-01-01", "TQQQ", TradeDirection.LONG_TQQQ, 10.0),
            _trade("2025-01-02", "TQQQ", TradeDirection.LONG_TQQQ, 20.0),
            _trade("2025-01-03", "SQQQ", TradeDirection.LONG_SQQQ, -5.0),
        ]
        stats = audit._bucket_stats(trades)
        assert stats["trade_count"] == 3
        assert stats["win_rate_percent"] == pytest.approx(66.6666, abs=0.01)
        assert stats["total_pnl_dollars"] == 25.0
        assert stats["expectancy_dollars"] == pytest.approx(8.3333, abs=0.01)
        assert stats["profit_factor"] == 6.0  # 30 gross win / 5 gross loss

    def test_no_losses_profit_factor_none(self):
        trades = [_trade("2025-01-01", "TQQQ", TradeDirection.LONG_TQQQ, 10.0)]
        stats = audit._bucket_stats(trades)
        assert stats["profit_factor"] is None


class TestByVehicle:
    def test_splits_long_and_inverse(self):
        trades = [
            _trade("2025-01-01", "TQQQ", TradeDirection.LONG_TQQQ, 10.0),
            _trade("2025-01-02", "SQQQ", TradeDirection.LONG_SQQQ, -5.0),
        ]
        result = audit.by_vehicle(trades)
        assert set(result.keys()) == {"TQQQ", "SQQQ"}
        assert result["TQQQ"]["trade_count"] == 1
        assert result["SQQQ"]["trade_count"] == 1


class TestBucketLabel:
    def test_gap_bucket_boundaries(self):
        assert audit._bucket_label(-2.0, audit._GAP_BUCKET_EDGES) == "gap < -1.0%"
        assert audit._bucket_label(-1.0, audit._GAP_BUCKET_EDGES) == "-1.0% to -0.3%"  # lower edge inclusive
        assert audit._bucket_label(0.0, audit._GAP_BUCKET_EDGES) == "-0.3% to +0.3%"
        assert audit._bucket_label(1.0, audit._GAP_BUCKET_EDGES) == "gap > +1.0%"  # upper edge exclusive on prior bucket


class TestByGapAndRange:
    def test_unmatched_days_counted_not_silently_dropped(self):
        trades = [_trade("2099-01-01", "TQQQ", TradeDirection.LONG_TQQQ, 10.0)]  # no day_context entry
        result = audit.by_gap_and_range(trades, day_context={})
        assert result["trades_with_no_day_context_matched"] == 1
        assert result["by_gap_bucket"] == {}

    def test_matched_day_buckets_correctly(self):
        trades = [_trade("2025-01-01", "TQQQ", TradeDirection.LONG_TQQQ, 10.0)]
        result = audit.by_gap_and_range(trades, day_context={"2025-01-01": (0.5, 0.4)})
        assert result["trades_with_no_day_context_matched"] == 0
        assert "+0.3% to +1.0%" in result["by_gap_bucket"]
        assert "0.3% to 0.6%" in result["by_opening_range_bucket_as_volatility_proxy"]


class TestConcentration:
    def test_top_and_bottom_five(self):
        trades = [
            _trade(f"2025-01-{i:02d}", "TQQQ", TradeDirection.LONG_TQQQ, float(i) - 5)
            for i in range(1, 11)
        ]  # results: -4..5
        result = audit.concentration(trades)
        assert len(result["top_5_winners"]) == 5
        assert result["top_5_winners"][0]["dollar_result"] == 5.0  # highest first
        assert len(result["top_5_losers"]) == 5
        assert result["top_5_losers"][0]["dollar_result"] == -4.0  # lowest first
        assert result["total_pnl_dollars"] == sum(float(i) - 5 for i in range(1, 11))

    def test_share_of_total_pnl_percent_computed(self):
        trades = [_trade(f"2025-01-{i:02d}", "TQQQ", TradeDirection.LONG_TQQQ, 10.0) for i in range(1, 6)]
        result = audit.concentration(trades)
        assert result["top_5_winners_share_of_total_pnl_percent"] == 100.0


class TestExcludingBestN:
    def test_excludes_exactly_n_highest(self):
        trades = [_trade(f"2025-01-{i:02d}", "TQQQ", TradeDirection.LONG_TQQQ, float(i)) for i in range(1, 11)]
        result = audit.excluding_best_n(trades, n=5)
        assert result["excluded_count"] == 5
        assert result["with_best_excluded"]["trade_count"] == 5  # 10 - 5 excluded
        # remaining should be the 5 SMALLEST results: 1,2,3,4,5 -> sum 15
        assert result["with_best_excluded"]["total_pnl_dollars"] == 15.0


class TestRobinhoodRegulatoryFee:
    def test_buy_only_leg_has_no_fee_semantics(self):
        # Fee function only ever receives the sell-leg shares/proceeds --
        # there is no separate "buy fee" concept to test since Robinhood
        # charges $0 commission and SEC/FINRA fees apply to sells only.
        fee = audit.robinhood_regulatory_fee_dollars(shares_sold=0, sell_proceeds_dollars=0)
        assert fee == 0.0

    def test_typical_small_trade_fee_is_a_few_cents_at_most(self):
        # $1000 position, ~$50/share vehicle -> ~20 shares sold.
        fee = audit.robinhood_regulatory_fee_dollars(shares_sold=20, sell_proceeds_dollars=1000.0)
        assert 0.0 < fee < 0.10  # nowhere near the old $1.00 flat placeholder

    def test_sec_fee_component_scales_with_proceeds(self):
        small = audit.robinhood_regulatory_fee_dollars(shares_sold=20, sell_proceeds_dollars=1000.0)
        large = audit.robinhood_regulatory_fee_dollars(shares_sold=20, sell_proceeds_dollars=100_000.0)
        assert large > small

    def test_finra_taf_capped_at_max(self):
        # An enormous share count should still cap at FINRA_TAF_MAX_PER_TRADE_DOLLARS
        # (plus a correspondingly larger SEC fee, which is uncapped).
        fee = audit.robinhood_regulatory_fee_dollars(shares_sold=10_000_000, sell_proceeds_dollars=1.0)
        assert fee == pytest.approx(audit.FINRA_TAF_MAX_PER_TRADE_DOLLARS, abs=0.01)

    def test_negative_inputs_never_produce_negative_fee(self):
        fee = audit.robinhood_regulatory_fee_dollars(shares_sold=-5, sell_proceeds_dollars=-100.0)
        assert fee == 0.0


class TestSummaryFromAdjustedPnl:
    def test_empty_series(self):
        result = audit._summary_from_adjusted_pnl([])
        assert result["total_trades"] == 0
        assert result["win_rate_percent"] is None

    def test_matches_bucket_stats_style_math(self):
        result = audit._summary_from_adjusted_pnl([10.0, -5.0, 20.0])
        assert result["total_trades"] == 3
        assert result["win_rate_percent"] == pytest.approx(66.6666, abs=0.01)
        assert result["expectancy_dollars"] == pytest.approx(8.3333, abs=0.01)
        assert result["profit_factor"] == 6.0

    def test_max_drawdown_computed_via_shared_helper(self):
        # 100 -> -50 -> 100: peak 100, trough after the loss is 50, so
        # drawdown = 50 (using the same _max_drawdown() the real engine uses).
        result = audit._summary_from_adjusted_pnl([100.0, -50.0])
        assert result["max_drawdown_dollars"] == 50.0


class TestOhlcvFillRangeCheck:
    """Covers ohlcv_fill_range_check() -- a coarse OHLCV bar-range
    consistency check ONLY. Not a test of execution realism, spread
    awareness, or fillability; see the function's own docstring."""

    def _session(self, date: str, tqqq_bars: list) -> DaySession:
        return DaySession(
            date=date, qqq_previous_close=100.0, qqq_previous_high=101.0, qqq_previous_low=99.0,
            qqq_bars=(), tqqq_bars=tuple(tqqq_bars), sqqq_bars=(),
        )

    def test_fill_within_day_range_is_consistent(self):
        bars = [_bar("2025-01-01T09:30:00-05:00", 50.0, 51.0, 49.0, 50.5)]
        session = self._session("2025-01-01", bars)
        trades = [_trade("2025-01-01", "TQQQ", TradeDirection.LONG_TQQQ, 10.0, entry_price=50.2, exit_price=50.4)]
        result = audit.ohlcv_fill_range_check(trades, [session])
        assert result["trades_checked"] == 1
        assert result["fills_outside_day_ohlc_range"] == 0

    def test_fill_outside_day_range_is_flagged(self):
        bars = [_bar("2025-01-01T09:30:00-05:00", 50.0, 51.0, 49.0, 50.5)]
        session = self._session("2025-01-01", bars)
        trades = [_trade("2025-01-01", "TQQQ", TradeDirection.LONG_TQQQ, 10.0, entry_price=999.0, exit_price=50.4)]
        result = audit.ohlcv_fill_range_check(trades, [session])
        assert result["fills_outside_day_ohlc_range"] == 1
        assert result["out_of_range_fills"][0]["field"] == "entry_price"

    def test_missing_session_skipped_not_crashed(self):
        trades = [_trade("2099-01-01", "TQQQ", TradeDirection.LONG_TQQQ, 10.0, entry_price=50.0, exit_price=51.0)]
        result = audit.ohlcv_fill_range_check(trades, [])
        assert result["trades_checked"] == 0
        assert result["fills_outside_day_ohlc_range"] == 0

    def test_reports_quote_level_validation_not_performed(self):
        result = audit.ohlcv_fill_range_check([], [])
        assert result["quote_level_validation_performed"] is False
        assert "403" in result["polygon_quote_data_entitlement"]
