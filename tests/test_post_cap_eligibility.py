from __future__ import annotations

import copy
import dataclasses

import pytest

from adaptive.post_cap_eligibility import evaluate_post_cap_eligibility
from risk.risk_engine import DailyState
from strategy.signal_engine import SetupDetail


def _setup() -> SetupDetail:
    return SetupDetail(
        direction="LONG",
        entry=19500.0,
        stop=19480.0,
        target=19540.0,
        rr_ratio=2.0,
        strategy="orb_reclaim",
    )


def test_cap_only_observer_can_mark_locally_eligible(config, fresh_market_state):
    state = copy.deepcopy(fresh_market_state)
    daily = DailyState(
        trade_count=config.max_trades_per_day,
        consecutive_losses=0,
        has_open_position=False,
        realized_pnl_dollars=0.0,
        account_balance=1500.0,
        account_peak_balance=1500.0,
    )
    setup = _setup()
    setup_before = copy.deepcopy(setup)
    daily_before = copy.deepcopy(daily)

    result = evaluate_post_cap_eligibility(
        state=state,
        setup=setup,
        cfg=config,
        daily_state=daily,
        account_balance=1500.0,
        account_peak_balance=1500.0,
    )

    assert result["observation_only"] is True
    assert result["execution_reachable"] is False
    assert result["daily_trade_limit_bypassed"] is True
    assert result["original_trade_count"] == config.max_trades_per_day
    assert result["risk_without_daily_cap"]["failed_rule"] != "daily_trade_limit"
    assert result["broker_balance_parity_claimed"] is False
    assert result["account_balance_source"] == "journal_ledger"
    assert setup == setup_before
    assert daily == daily_before


def test_other_risk_gate_still_rejects_after_cap_is_removed(config, fresh_market_state):
    cfg = dataclasses.replace(
        config,
        max_daily_loss=150.0,
        max_consecutive_losses=9999,
    )
    daily = DailyState(
        trade_count=cfg.max_trades_per_day,
        consecutive_losses=0,
        has_open_position=False,
        realized_pnl_dollars=-1000.0,
        account_balance=1500.0,
        account_peak_balance=1500.0,
    )

    result = evaluate_post_cap_eligibility(
        state=copy.deepcopy(fresh_market_state),
        setup=_setup(),
        cfg=cfg,
        daily_state=daily,
        account_balance=1500.0,
        account_peak_balance=1500.0,
    )

    assert result["eligible_except_daily_cap"] is False
    assert result["risk_without_daily_cap"]["failed_rule"] == "max_daily_loss"


def test_observer_refuses_when_capacity_was_not_reached(config, fresh_market_state):
    daily = DailyState(
        trade_count=config.max_trades_per_day - 1,
        consecutive_losses=0,
        has_open_position=False,
    )

    with pytest.raises(ValueError, match="before capacity"):
        evaluate_post_cap_eligibility(
            state=copy.deepcopy(fresh_market_state),
            setup=_setup(),
            cfg=config,
            daily_state=daily,
            account_balance=1500.0,
            account_peak_balance=1500.0,
        )


def test_observer_records_transformed_bracket_without_mutating_input(
    config, fresh_market_state
):
    cfg = dataclasses.replace(
        config,
        stop_multiplier_per_instrument={"MNQ": 1.5},
    )
    daily = DailyState(
        trade_count=cfg.max_trades_per_day,
        consecutive_losses=0,
        has_open_position=False,
        realized_pnl_dollars=0.0,
    )
    setup = _setup()

    result = evaluate_post_cap_eligibility(
        state=copy.deepcopy(fresh_market_state),
        setup=setup,
        cfg=cfg,
        daily_state=daily,
        account_balance=1500.0,
        account_peak_balance=1500.0,
    )

    assert result["stop_multiplier_applied"] == 1.5
    assert result["transformed_setup"]["stop"] < setup.stop
    assert setup.stop == 19480.0
