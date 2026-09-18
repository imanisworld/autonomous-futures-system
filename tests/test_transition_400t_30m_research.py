from __future__ import annotations

import copy
import dataclasses

from config.settings import load_config
from context.transition_400t_30m_research import (
    DAILY_LOSS_LIMIT,
    MAX_DRAWDOWN_PERCENT,
    STARTING_BALANCE,
    canonical_candidate,
    isolated_config,
    open_research_position,
    resolve_six_available_5m_bars,
)
from risk.risk_engine import DailyState
from strategy.shadow_setups import evaluate_shadow_setups
from strategy.transition_failed_breakdown_reclaim import (
    RESEARCH_DUMMY_TARGET_TICKS,
    RESEARCH_STRATEGY,
    RESEARCH_STOP_TICKS,
    detect_transition_failed_breakdown_reclaim,
)


def _transition_state_and_bars(fresh_market_state):
    state = copy.deepcopy(fresh_market_state)
    state.timestamp = state.timestamp.replace(hour=14, minute=15)
    state.session = "new_york"
    state.market_condition = "RANGE_BOUND"
    state.trend.strength = "MODERATE"
    bars = [
        {
            "ts": f"2026-05-23T{h:02d}:{m:02d}:00+00:00",
            "open": 19500.0,
            "high": 19510.0,
            "low": 19490.0 if i == 3 else 19495.0,
            "close": 19502.0,
            "volume": 1000,
        }
        for i, (h, m) in enumerate(
            [(12, 30), (12, 45), (13, 0), (13, 15), (13, 30), (13, 45)]
        )
    ]
    bars.append(
        {
            "ts": "2026-05-23T14:00:00+00:00",
            "open": 19494.0,
            "high": 19500.0,
            "low": 19486.0,
            "close": 19492.0,
            "volume": 1400,
        }
    )
    state.ohlc.open = 19492.0
    state.ohlc.high = 19498.0
    state.ohlc.low = 19489.0
    state.ohlc.close = 19496.0
    state.ohlc.timeframe = "15m"
    state.transition_bar_history_5m = list(bars)
    return state, bars


def _research_test_config():
    cfg = load_config()
    return dataclasses.replace(
        cfg,
        allowed_instruments=["MNQ"],
        min_confluence_grade="",
        require_strong_trend={"MNQ": False},
        live_trading_enabled=False,
    )


def test_shared_detector_preserves_shadow_candidate_geometry(fresh_market_state):
    state, bars = _transition_state_and_bars(fresh_market_state)
    signal = detect_transition_failed_breakdown_reclaim(state, bars)
    assert signal is not None

    candidates = {
        c.strategy: c for c in evaluate_shadow_setups(state, recent_bars=bars)
    }
    shadow = candidates["transition_failed_breakdown_reclaim"]

    assert shadow.entry == signal.entry == 19496.0
    assert shadow.stop == signal.reference_stop == 19485.5
    assert shadow.target == signal.reference_target == 19500.0


def test_decision_engine_research_candidate_uses_frozen_400t_geometry(fresh_market_state):
    state, _ = _transition_state_and_bars(fresh_market_state)
    candidate = canonical_candidate(state, _research_test_config(), DailyState())
    assert candidate is not None
    setup = candidate.setup

    assert setup.strategy == RESEARCH_STRATEGY
    assert setup.entry == 19496.0
    assert setup.stop == 19396.0
    assert setup.target == 19496.0 + RESEARCH_DUMMY_TARGET_TICKS * 0.25
    assert (setup.entry - setup.stop) / 0.25 == RESEARCH_STOP_TICKS


def test_research_strategy_is_default_off():
    cfg = load_config()
    assert RESEARCH_STRATEGY not in cfg.enabled_concepts


def test_isolated_config_does_not_mutate_global_config():
    cfg = dataclasses.replace(_research_test_config(), min_confluence_grade="B")
    original_stop = dict(cfg.max_stop_ticks)
    lane = isolated_config(cfg)

    assert lane is not cfg
    assert cfg.max_stop_ticks == original_stop
    assert lane.max_stop_ticks["MNQ"] == 400.0
    assert lane.min_confluence_grade == ""
    assert cfg.min_confluence_grade != ""
    assert lane.max_daily_loss == DAILY_LOSS_LIMIT == 400.0
    assert lane.max_drawdown_percent == MAX_DRAWDOWN_PERCENT == 0.20
    assert lane.enabled_concepts == [RESEARCH_STRATEGY]
    assert lane.live_trading_enabled is False


def test_isolated_executor_opens_paper_only_and_times_out_after_six_bars(fresh_market_state):
    state, _ = _transition_state_and_bars(fresh_market_state)
    daily = DailyState(
        trade_count=0,
        consecutive_losses=0,
        has_open_position=False,
        account_balance=STARTING_BALANCE,
        account_peak_balance=STARTING_BALANCE,
    )
    opened = open_research_position(
        state=state,
        cfg=_research_test_config(),
        daily_state=daily,
        market_price=state.ohlc.close,
    )
    assert opened.status == "OPEN"
    assert opened.broker is not None
    assert opened.broker.is_live is False
    assert opened.fill["result"] == "OPEN"

    bars = [
        {"open": 19496.0, "high": 19505.0, "low": 19490.0, "close": 19500.0}
        for _ in range(6)
    ]
    result = resolve_six_available_5m_bars(opened.broker, bars)
    assert result.status == "RESOLVED"
    assert result.exit_reason == "TIME_30M"
    assert result.bars_consumed == 6
    assert result.net_pnl_dollars is not None


def test_stop_resolves_before_time_exit(fresh_market_state):
    state, _ = _transition_state_and_bars(fresh_market_state)
    daily = DailyState(
        account_balance=STARTING_BALANCE,
        account_peak_balance=STARTING_BALANCE,
    )
    opened = open_research_position(
        state=state,
        cfg=_research_test_config(),
        daily_state=daily,
        market_price=state.ohlc.close,
    )
    assert opened.status == "OPEN"

    bars = [
        {"open": 19496.0, "high": 19500.0, "low": 19490.0, "close": 19495.0},
        {"open": 19495.0, "high": 19498.0, "low": 19390.0, "close": 19400.0},
    ] + [
        {"open": 19400.0, "high": 19410.0, "low": 19395.0, "close": 19405.0}
        for _ in range(4)
    ]
    result = resolve_six_available_5m_bars(opened.broker, bars)
    assert result.status == "RESOLVED"
    assert result.exit_reason in {"STOP_HIT", "STOP_GAP"}
    assert result.bars_consumed == 2
    assert result.net_pnl_dollars < 0


def test_full_decision_engine_keeps_existing_range_trending_gate(fresh_market_state):
    """Canonical full DecisionEngine path is deliberately still blocked.

    The research executor uses collect_strategy_candidates so we can exercise
    RiskEngine/PaperBroker without silently adding a range/trend exemption.
    Any exemption is a separate policy decision.
    """
    from strategy.signal_engine import DecisionEngine

    state, _ = _transition_state_and_bars(fresh_market_state)
    decision = DecisionEngine(config=isolated_config(_research_test_config())).evaluate(
        state,
        DailyState(
            account_balance=STARTING_BALANCE,
            account_peak_balance=STARTING_BALANCE,
        ),
    )
    assert decision.decision == "NO_TRADE"
    assert "MARKET_CONDITION_NOT_TRENDING" in decision.failed_gates


def test_research_geometry_is_label_independent_but_legacy_shadow_is_not(
    fresh_market_state,
):
    from strategy.transition_failed_breakdown_reclaim import (
        detect_transition_geometry,
        detect_transition_failed_breakdown_reclaim,
    )

    state, bars = _transition_state_and_bars(fresh_market_state)
    state.market_condition = "TRENDING"

    assert detect_transition_failed_breakdown_reclaim(state, bars) is None
    assert detect_transition_geometry(state, bars) is not None

    candidate = canonical_candidate(state, _research_test_config(), DailyState())
    assert candidate is not None
    assert candidate.setup.strategy == RESEARCH_STRATEGY
