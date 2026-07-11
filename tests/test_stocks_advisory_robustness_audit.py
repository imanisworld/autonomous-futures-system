"""Tests for scripts/stocks_advisory_robustness_audit.py's pure bucketing/
stats helper functions. No CSV files, no network -- constructs
BacktestTradeResult objects directly. Integration coverage for the
underlying strategy logic already lives in
tests/test_stocks_tqqq_sqqq_backtest.py; this file only covers the
audit's own read-only aggregation code."""

from __future__ import annotations

import pytest

import scripts.stocks_advisory_robustness_audit as audit
from stocks_advisory.backtest_models import BacktestTradeResult, TradeDirection


def _trade(date: str, vehicle: str, direction: TradeDirection, dollar_result: float) -> BacktestTradeResult:
    return BacktestTradeResult(
        trade_date=date, vehicle_symbol=vehicle, direction=direction,
        dollar_result=dollar_result, skipped=False,
    )


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
