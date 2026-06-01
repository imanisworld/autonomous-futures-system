"""Tests for the isolated Alpaca options risk engine."""

from __future__ import annotations

from datetime import datetime, timezone

from risk.options_risk_engine import (
    OptionTradePlan,
    OptionsDailyState,
    OptionsRiskConfig,
    OptionsRiskEngine,
)


def _config(**kwargs):
    base = dict(
        enabled=True,
        paper_only=True,
        allowed_underlyings=["SPY", "QQQ"],
        allowed_contract_types=["CALL", "PUT"],
        allowed_sessions=["new_york"],
        session_windows={
            "new_york": [
                {"start": "09:35", "end": "11:30", "allow": True},
                {"default": False},
            ]
        },
        max_contracts=1,
        max_premium_per_contract=250,
        max_total_premium=250,
        max_daily_trades=3,
        max_daily_loss=150,
        max_consecutive_losses=2,
        max_open_positions=1,
        require_entry=True,
        require_stop=True,
        require_target=True,
        min_rr_ratio=2.0,
        allow_market_orders=False,
        require_confluence_grade="B",
    )
    base.update(kwargs)
    return OptionsRiskConfig(**base)


def _plan(**kwargs):
    base = dict(
        underlying="SPY",
        symbol="SPY260620C00600000",
        contract_type="CALL",
        side="BUY",
        quantity=1,
        entry_premium=1.00,
        stop_premium=0.50,
        target_premium=2.00,
        strategy="orb_reclaim",
        session="new_york",
        timestamp=datetime(2026, 5, 29, 14, 0, tzinfo=timezone.utc),  # 10:00 ET
        order_type="limit",
        confluence_grade="B",
    )
    base.update(kwargs)
    return OptionTradePlan(**base)


def test_valid_options_plan_is_approved():
    result = OptionsRiskEngine(_config()).validate(_plan(), OptionsDailyState())

    assert result.approved


def test_disabled_options_lane_rejects():
    result = OptionsRiskEngine(_config(enabled=False)).validate(_plan(), OptionsDailyState())

    assert result.rejected
    assert result.failed_rule == "options_disabled"


def test_live_broker_rejected_when_paper_only():
    result = OptionsRiskEngine(_config()).validate(
        _plan(),
        OptionsDailyState(),
        broker_is_live=True,
    )

    assert result.rejected
    assert result.failed_rule == "live_options_blocked"


def test_only_allowed_underlyings_pass():
    result = OptionsRiskEngine(_config()).validate(_plan(underlying="TSLA"), OptionsDailyState())

    assert result.rejected
    assert result.failed_rule == "underlying_not_allowed"


def test_short_options_are_blocked():
    result = OptionsRiskEngine(_config()).validate(_plan(side="SELL"), OptionsDailyState())

    assert result.rejected
    assert result.failed_rule == "short_options_blocked"


def test_outside_session_window_rejected():
    result = OptionsRiskEngine(_config()).validate(
        _plan(timestamp=datetime(2026, 5, 29, 16, 0, tzinfo=timezone.utc)),  # 12:00 ET
        OptionsDailyState(),
    )

    assert result.rejected
    assert result.failed_rule == "session_window"


def test_market_orders_rejected_by_default():
    result = OptionsRiskEngine(_config()).validate(_plan(order_type="market"), OptionsDailyState())

    assert result.rejected
    assert result.failed_rule == "market_order_blocked"


def test_missing_stop_rejected():
    result = OptionsRiskEngine(_config()).validate(_plan(stop_premium=None), OptionsDailyState())

    assert result.rejected
    assert result.failed_rule == "stop_required"


def test_rr_must_match_futures_style_minimum():
    result = OptionsRiskEngine(_config()).validate(
        _plan(entry_premium=1.00, stop_premium=0.50, target_premium=1.50),
        OptionsDailyState(),
    )

    assert result.rejected
    assert result.failed_rule == "rr_too_low"


def test_premium_risk_caps_total_debit():
    result = OptionsRiskEngine(_config(max_total_premium=150)).validate(
        _plan(entry_premium=2.00, stop_premium=1.50, target_premium=3.00),
        OptionsDailyState(),
    )

    assert result.rejected
    assert result.failed_rule == "total_premium"


def test_daily_loss_limit_rejects():
    result = OptionsRiskEngine(_config()).validate(
        _plan(),
        OptionsDailyState(realized_pnl_dollars=-150),
    )

    assert result.rejected
    assert result.failed_rule == "daily_loss_limit"


def test_confluence_grade_required():
    result = OptionsRiskEngine(_config(require_confluence_grade="A")).validate(
        _plan(confluence_grade="B"),
        OptionsDailyState(),
    )

    assert result.rejected
    assert result.failed_rule == "confluence_grade"
