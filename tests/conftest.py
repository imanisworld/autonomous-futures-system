"""
tests/conftest.py

Shared fixtures for all test modules.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

# Unit tests must never inherit deployment values from a developer's .env.
# Individual tests still control environment variables through monkeypatch.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import SystemConfig
from context.market_context import (
    MarketState, PriceData, OHLCData, VWAPData, ORBData,
    PreviousDayData, VolumeData, TrendData,
)
from risk.risk_engine import DailyState, TradeSetup


@pytest.fixture(autouse=True)
def isolate_live_broker_env(monkeypatch):
    """Keep local live/demo broker settings from leaking into unit tests."""
    monkeypatch.setenv("BROKER", "paper")
    monkeypatch.setenv("SITE_ACCESS_CODE", "")
    monkeypatch.delenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", raising=False)
    monkeypatch.delenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", raising=False)
    monkeypatch.delenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", raising=False)
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "0")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "")
    monkeypatch.setenv("TRADOVATE_USERNAME", "")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "")


@pytest.fixture(autouse=True)
def clear_webhook_dedupe_cache():
    """The webhook app keeps a module-level duplicate-alert cache; tests reuse
    the same bar timestamps, so clear it between tests to keep them independent."""
    app_module = sys.modules.get("webhook.app")
    if app_module is not None and hasattr(app_module, "_DEDUPE"):
        app_module._DEDUPE.clear()
    yield


@pytest.fixture(autouse=True)
def reset_tradovate_shared_auth():
    """Clear the process-shared Tradovate auth state between tests so token /
    circuit-breaker state never leaks across broker auth tests."""
    from execution.tradovate_broker import _reset_shared_auth
    from execution.tradovate_supervisor import reset_reliability_snapshot
    _reset_shared_auth()
    reset_reliability_snapshot()
    yield
    _reset_shared_auth()
    reset_reliability_snapshot()


# ─── Permissive runtime config (pre-isolated-lane universe) ───────────────────
# The shipped risk_rules.yaml is narrowed to the isolated MNQ orb_breakout
# inverse forward-paper lane (risk_rules 1.2.0): MNQ only, orb_breakout the
# only enabled concept and the only PAPER_ELIGIBLE strategy, 3 trades/day.
#
# Runtime tests that exercise GENERAL engine behavior (execution safety,
# order-recheck, replay, resolution, observers) need MES and/or non-ORB
# strategies as fixtures. They must NOT depend on the shipped production
# config happening to be permissive — that coupling is exactly what this
# helper removes. Such tests call load_permissive_config() to construct the
# broad universe explicitly and locally, so narrowing the shipped config can
# never silently change what they assert.
#
# This is a TEST FIXTURE ONLY. It never affects the shipped configuration.

_PERMISSIVE_ENABLED_CONCEPTS = [
    "orb_breakout", "orb_reclaim", "orb_rejection",
    "vwap_reclaim", "vwap_rejection", "vwap_hold",
    "pdh_reclaim", "pdl_reclaim",
    "strat_4hr_retrigger", "strat_322_first_live", "strat_122",
]

# Mirrors the pre-1.2.0 shipped permission map: everything PAPER_ELIGIBLE
# except the two evidence-based demotions, which are preserved.
_PERMISSIVE_STRATEGY_STATUS = {
    name: ("SHADOW_ONLY" if name in ("vwap_hold", "pdh_reclaim") else "PAPER_ELIGIBLE")
    for name in _PERMISSIVE_ENABLED_CONCEPTS
}


def load_permissive_config(**overrides):
    """Config with the pre-isolated-lane universe (MES+MNQ, all concepts).

    For runtime tests that need instruments/strategies the isolated shipped
    config disables. Extra keyword arguments are applied as dataclass
    overrides, so callers can layer their own per-test settings on top.
    """
    import dataclasses
    from config.settings import load_config

    return dataclasses.replace(
        load_config(),
        allowed_instruments=["MES", "MNQ"],
        required_instruments=["MES", "MNQ"],
        enabled_concepts=list(_PERMISSIVE_ENABLED_CONCEPTS),
        strategy_status=dict(_PERMISSIVE_STRATEGY_STATUS),
        max_trades_per_day=9999,
        **overrides,
    )


# ─── Config Fixture ────────────────────────────────────────────────────────────

@pytest.fixture
def config() -> SystemConfig:
    """Standard test config — paper mode, LIVE_TRADING_ENABLED=false."""
    return SystemConfig(
        live_trading_enabled=False,
        paper_mode=True,
        allowed_instruments=["MNQ", "MES", "MGC", "MCL"],
        allowed_sessions=["london", "new_york"],
        disabled_sessions=["asian"],
        session_hours={},
        max_trades_per_day=3,
        max_consecutive_losses=2,
        max_daily_loss=0.0,
        max_drawdown_percent=0.0,
        circuit_breaker_losses=0,
        circuit_breaker_pause_minutes=30,
        conservative_mode=False,
        max_open_positions=1,
        averaging_down_allowed=False,
        max_contracts_per_instrument={"MNQ": 2, "MES": 1, "MGC": 0, "MCL": 0},
        require_entry=True,
        require_stop=True,
        require_target=True,
        min_rr_ratio=2.0,
        max_staleness_seconds=0,   # disabled in tests (historical timestamps)
        reject_null_required_fields=True,
        reject_contradictory_data=True,
        # Most existing fixtures/tests build a TradeSetup without entry_time
        # (same reason max_staleness_seconds is disabled above: historical/
        # synthetic timestamps aren't the point of those tests). Tests that
        # specifically exercise the alert-freshness gate override this back to
        # True (the real production default) via dataclasses.replace(config, ...).
        reject_on_missing_alert_timestamp=False,
        tradable_states=["TRENDING", "RANGE_BOUND"],
        non_tradable_states=["CHOPPY", "DEAD"],
        enabled_concepts=[
            "orb_reclaim", "orb_rejection", "vwap_reclaim",
            "vwap_hold", "pdh_reclaim", "pdl_reclaim", "continuation_pullback"
        ],
        broker_priority=["paper", "tradovate"],
        starting_capital_default=1000.0,
        minimum_starting_capital=500.0,
        max_account_risk_per_trade_percent=1.0,
        max_daily_loss_percent=3.0,
        require_margin_check=True,
        log_dir="logs_test",
        log_level="WARNING",
        risk_rules_path="risk_rules.yaml",
        discord_notifications_enabled=False,
        discord_webhook_url="",
        discord_notify_decisions=[
            "TRADE", "RISK_REJECTED", "BLOCKED_MAX_TRADES", "BLOCKED_LOSS_LOCKOUT"
        ],
        signa_api_enabled=False,
        signa_api_key_configured=False,
        # Mechanics tests use the simple/legacy fill model for deterministic
        # outcomes (clean target = WIN). Fill-realism (slippage + pessimistic
        # both-hit) has its own coverage in test_paper_broker.py, and the
        # production default is locked by test_production_fill_defaults_are_honest.
        fill_slippage_ticks=0.0,
        fill_pessimistic_both_hit=False,
    )


@pytest.fixture
def clean_daily_state() -> DailyState:
    """Fresh daily state — no trades, no losses, no open position."""
    return DailyState(trade_count=0, consecutive_losses=0, has_open_position=False)


@pytest.fixture
def fresh_market_state() -> MarketState:
    """A valid, fresh MNQ ORB reclaim market state."""
    now = datetime(2026, 5, 23, 14, 30, tzinfo=timezone.utc)  # 10:30 ET opening window
    return MarketState(
        timestamp=now,
        instrument="MNQ",
        session="new_york",
        price=PriceData(last=19505.25, bid=19505.00, ask=19505.50),
        ohlc=OHLCData(
            open=19480.0, high=19510.0, low=19475.0, close=19505.25, timeframe="5m"
        ),
        vwap=VWAPData(value=19495.0, price_vs_vwap="above", reclaimed=True, holding=True),
        orb=ORBData(high=19498.0, low=19462.0, timeframe_minutes=15, status="reclaimed_high"),
        previous_day=PreviousDayData(high=19520.0, low=19440.0, close=19475.0),
        volume=VolumeData(current_bar=4200, avg_bar=3800, relative=1.10),
        market_condition="TRENDING",
        trend=TrendData(direction="UP", strength="MODERATE", ema_fast_above_slow=True),
        raw={},
    )


@pytest.fixture
def valid_trade_setup() -> TradeSetup:
    return TradeSetup(
        direction="LONG",
        entry=19500.0,
        stop=19480.0,
        target=19540.0,
        rr_ratio=2.0,
        strategy="orb_reclaim",
        instrument="MNQ",
        session="new_york",
    )
