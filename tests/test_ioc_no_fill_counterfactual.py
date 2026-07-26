from scripts.ioc_no_fill_counterfactual import decision, settle_attempt


def _attempt(**overrides):
    row = {
        "paper_order_id": "PAPER-test",
        "instrument": "MNQ",
        "signal_timestamp": "2025-07-24T13:00:00+00:00",
        "session": "new_york",
        "strategy": "orb_reclaim",
        "direction": "LONG",
        "planned_entry": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "rr_ratio": 2.0,
        "notes": None,
        "contracts": 1,
        "original_outcome": "CANCELLED",
        "original_exit_reason": "ENTRY_NOT_FILLED",
    }
    row.update(overrides)
    return row


def test_market_counterfactual_uses_decision_close_and_adverse_slippage():
    candles = {
        "MNQ": [
            {
                "timestamp": "2025-07-24T13:00:00+00:00",
                "open": 104.0,
                "high": 105.0,
                "low": 103.0,
                "close": 104.0,
                "timeframe": "15m",
            },
            {
                "timestamp": "2025-07-24T13:15:00+00:00",
                "open": 104.0,
                "high": 111.0,
                "low": 103.0,
                "close": 110.0,
                "timeframe": "15m",
            },
        ]
    }
    row = settle_attempt(
        _attempt(),
        candles,
        {("MNQ", "2025-07-24T13:00:00+00:00"): 0},
        slippage_ticks=1.0,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
    )
    assert row["counterfactual_entry"] == 104.25
    assert row["result"] == "WIN"
    assert row["exit_price"] == 110.0
    assert row["pnl_before_commission"] == 11.5


def test_unknown_same_bar_path_resolves_stop_before_target():
    candles = {
        "MNQ": [
            {
                "timestamp": "2025-07-24T13:00:00+00:00",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "timeframe": "15m",
            },
            {
                "timestamp": "2025-07-24T13:15:00+00:00",
                "open": 100.0,
                "high": 111.0,
                "low": 94.0,
                "close": 105.0,
                "timeframe": "15m",
            },
        ]
    }
    row = settle_attempt(
        _attempt(),
        candles,
        {("MNQ", "2025-07-24T13:00:00+00:00"): 0},
        slippage_ticks=1.0,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
    )
    assert row["result"] == "LOSS"
    assert row["exit_reason"] == "STOP_HIT"
    assert row["exit_price"] == 94.75
    assert row["both_stop_and_target_hit_on_exit_bar"] is True


def test_short_market_counterfactual_slips_lower_and_resolves_conservatively():
    candles = {
        "MNQ": [
            {
                "timestamp": "2025-07-24T13:00:00+00:00",
                "open": 96.0,
                "high": 97.0,
                "low": 95.0,
                "close": 96.0,
                "timeframe": "15m",
            },
            {
                "timestamp": "2025-07-24T13:15:00+00:00",
                "open": 96.0,
                "high": 106.0,
                "low": 89.0,
                "close": 100.0,
                "timeframe": "15m",
            },
        ]
    }
    row = settle_attempt(
        _attempt(direction="SHORT", stop=105.0, target=90.0),
        candles,
        {("MNQ", "2025-07-24T13:00:00+00:00"): 0},
        slippage_ticks=1.0,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
    )
    assert row["counterfactual_entry"] == 95.75
    assert row["result"] == "LOSS"
    assert row["exit_price"] == 105.25
    assert row["both_stop_and_target_hit_on_exit_bar"] is True


def test_decision_rule_requires_ci_to_clear_zero():
    profitable = {
        "net_after_commission": 100.0,
        "profit_factor_after_commission": 2.0,
        "expectancy_95pct_bootstrap_ci": [1.0, 4.0],
    }
    mixed = {
        "net_after_commission": 100.0,
        "profit_factor_after_commission": 2.0,
        "expectancy_95pct_bootstrap_ci": [-1.0, 4.0],
    }
    negative = {
        "net_after_commission": -100.0,
        "profit_factor_after_commission": 0.5,
        "expectancy_95pct_bootstrap_ci": [-4.0, -1.0],
    }
    assert decision(profitable)[0].endswith("MATERIALLY PROFITABLE")
    assert decision(mixed)[0].startswith("MIXED")
    assert decision(negative)[0].endswith("MATERIALLY NEGATIVE")
