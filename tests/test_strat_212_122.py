"""Canonical Strat 2-1-2 / 1-2-2: pure state machine + identity/causality
integration coverage.

Covers the repair that removed the Phase-1 ORB/trend/VWAP proxy from
strat_212/strat_122 (identity contamination) and replaced the entry/stop
anchor with the prior reference bar's boundary instead of the bar being
evaluated (causality). See strategy/strat_212_122.py for the design.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from context.market_context import (
    MarketState,
    OHLCData,
    ORBData,
    PreviousDayData,
    PriceData,
    StratContext,
    TrendData,
    VolumeData,
    VWAPData,
)
from journal.journal_logger import JournalLogger
from risk.risk_engine import DailyState
from strategy.signal_engine import DecisionEngine
from strategy.stop_sizing import apply_stop_multiplier
from strategy.strat_212_122 import STRAT_122, STRAT_212, advance_strat_212_122


# ─── Pure state machine ─────────────────────────────────────────────────────


def test_212_long_clean_trigger_anchors_to_inside_bar_not_current_bar():
    state, cand = advance_strat_212_122(
        previous_bar_type="inside_bar",
        two_bars_back_type="two_up",
        previous_bar_high=100.0,
        previous_bar_low=95.0,
        current_high=200.0,  # current bar's own range must NOT be the anchor
        current_low=99.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert state["status"] == "TRIGGERED"
    assert cand == {
        "kind": "OPEN",
        "pattern": STRAT_212,
        "direction": "LONG",
        "entry": 100.25,
        "stop": 94.0,
        "target": 112.75,
    }


def test_212_short_clean_trigger():
    state, cand = advance_strat_212_122(
        previous_bar_type="inside_bar",
        two_bars_back_type="two_down",
        previous_bar_high=100.0,
        previous_bar_low=95.0,
        current_high=96.0,
        current_low=94.75,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert state["status"] == "TRIGGERED"
    assert cand["direction"] == "SHORT"
    assert cand["entry"] == 94.75
    assert cand["stop"] == 101.0
    assert cand["target"] == 82.25


def test_122_long_clean_trigger_anchors_to_prior_2d_bar():
    # 1 -> 2D -> 2U: bullish reversal, entry = prior 2D bar's high + tick.
    state, cand = advance_strat_212_122(
        previous_bar_type="two_down",
        two_bars_back_type="inside_bar",
        previous_bar_high=100.0,
        previous_bar_low=95.0,
        current_high=100.5,
        current_low=99.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert state["status"] == "TRIGGERED"
    assert cand["pattern"] == STRAT_122
    assert cand["direction"] == "LONG"
    assert cand["entry"] == 100.25
    assert cand["stop"] == 94.0


def test_122_short_clean_trigger_anchors_to_prior_2u_bar():
    state, cand = advance_strat_212_122(
        previous_bar_type="two_up",
        two_bars_back_type="inside_bar",
        previous_bar_high=100.0,
        previous_bar_low=95.0,
        current_high=95.5,
        current_low=94.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert state["status"] == "TRIGGERED"
    assert cand["pattern"] == STRAT_122
    assert cand["direction"] == "SHORT"
    assert cand["entry"] == 94.75
    assert cand["stop"] == 101.0


@pytest.mark.parametrize(
    "previous_bar_type,two_bars_back_type",
    [
        ("two_up", "two_up"),      # no inside bar anywhere — no precursor
        ("two_down", "two_down"),
        ("outside_bar", "inside_bar"),
        (None, None),
    ],
)
def test_no_precursor_no_candidate(previous_bar_type, two_bars_back_type):
    state, cand = advance_strat_212_122(
        previous_bar_type=previous_bar_type,
        two_bars_back_type=two_bars_back_type,
        previous_bar_high=100.0,
        previous_bar_low=95.0,
        current_high=101.0,
        current_low=99.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert state["status"] == "NO_PRECURSOR"
    assert cand is None


def test_missing_boundary_data_fails_closed():
    state, cand = advance_strat_212_122(
        previous_bar_type="inside_bar",
        two_bars_back_type="two_up",
        previous_bar_high=None,
        previous_bar_low=None,
        current_high=101.0,
        current_low=99.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert state["status"] == "NO_CANDIDATE_MISSING_BOUNDARY_DATA"
    assert cand is None


def test_expires_when_neither_boundary_reached():
    state, cand = advance_strat_212_122(
        previous_bar_type="inside_bar",
        two_bars_back_type="two_up",
        previous_bar_high=100.0,
        previous_bar_low=95.0,
        current_high=99.0,
        current_low=96.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert state["status"] == "EXPIRED_NOT_TRIGGERED"
    assert cand is None


def test_invalidated_when_only_stop_side_reached():
    state, cand = advance_strat_212_122(
        previous_bar_type="inside_bar",
        two_bars_back_type="two_up",
        previous_bar_high=100.0,
        previous_bar_low=95.0,
        current_high=99.0,
        current_low=93.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert state["status"] == "INVALIDATED_BEFORE_TRIGGER"
    assert cand is None


def test_outside_after_trigger_resolves_pessimistically_as_loss():
    state, cand = advance_strat_212_122(
        previous_bar_type="inside_bar",
        two_bars_back_type="two_up",
        previous_bar_high=100.0,
        previous_bar_low=95.0,
        current_high=101.0,
        current_low=93.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert state["status"] == "TRIGGERED_OUTSIDE_AFTER_TRIGGER"
    assert cand == {
        "kind": "RESOLVED",
        "pattern": STRAT_212,
        "direction": "LONG",
        "entry": 100.25,
        "exit": 94.0,
        "target": 112.75,
        "result": "LOSS",
        "exit_reason": "OUTSIDE_AFTER_TRIGGER",
    }


def test_outside_after_trigger_short_side_also_resolves_as_loss():
    state, cand = advance_strat_212_122(
        previous_bar_type="two_up",
        two_bars_back_type="inside_bar",
        previous_bar_high=100.0,
        previous_bar_low=95.0,
        current_high=101.0,
        current_low=93.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert state["status"] == "TRIGGERED_OUTSIDE_AFTER_TRIGGER"
    assert cand["pattern"] == STRAT_122
    assert cand["direction"] == "SHORT"
    assert cand["result"] == "LOSS"
    assert cand["exit_reason"] == "OUTSIDE_AFTER_TRIGGER"


def test_target_is_fixed_2r_both_patterns():
    _, cand_212 = advance_strat_212_122(
        previous_bar_type="inside_bar", two_bars_back_type="two_up",
        previous_bar_high=100.0, previous_bar_low=95.0,
        current_high=100.5, current_low=99.0,
        tick_size=0.25, trading_date="2026-07-24",
    )
    _, cand_122 = advance_strat_212_122(
        previous_bar_type="two_down", two_bars_back_type="inside_bar",
        previous_bar_high=100.0, previous_bar_low=95.0,
        current_high=100.5, current_low=99.0,
        tick_size=0.25, trading_date="2026-07-24",
    )
    risk_212 = cand_212["entry"] - cand_212["stop"]
    risk_122 = cand_122["entry"] - cand_122["stop"]
    assert cand_212["target"] == pytest.approx(cand_212["entry"] + 2.0 * risk_212)
    assert cand_122["target"] == pytest.approx(cand_122["entry"] + 2.0 * risk_122)


# ─── DecisionEngine integration ─────────────────────────────────────────────


def _state(*, strat: StratContext | None, raw: dict, high: float, low: float) -> MarketState:
    return MarketState(
        timestamp=datetime(2026, 7, 24, 14, 30, tzinfo=timezone.utc),
        instrument="MNQ",
        session="new_york",
        price=PriceData(last=100.0, bid=99.75, ask=100.25),
        ohlc=OHLCData(open=99.0, high=high, low=low, close=100.0, timeframe="5m"),
        vwap=VWAPData(value=99.0, price_vs_vwap="above"),
        orb=ORBData(high=105.0, low=95.0, timeframe_minutes=15, status="inside"),
        previous_day=PreviousDayData(high=110.0, low=90.0, close=100.0),
        volume=VolumeData(current_bar=1000, avg_bar=900, relative=1.1),
        market_condition="TRENDING",
        trend=TrendData(direction="UP", strength="STRONG"),
        strat=strat,
        raw=raw,
    )


def _daily_state() -> DailyState:
    return DailyState(trade_count=0, consecutive_losses=0, has_open_position=False)


def test_try_strat_212_returns_none_when_disabled(config):
    assert "strat_212" not in config.enabled_concepts
    engine = DecisionEngine(config=config)
    state = _state(
        strat=StratContext(
            current_bar_type="two_up", previous_bar_type="inside_bar",
            two_bars_back_type="two_up",
        ),
        raw={"previous_bar_high": 100.0, "previous_bar_low": 95.0},
        high=100.5, low=99.0,
    )
    daily = _daily_state()
    engine._advance_strat_212_122(state, daily)
    assert state.strat_212_122_candidate is None
    assert engine._try_strat_212(state) is None


def test_try_strat_212_open_candidate_when_enabled(config):
    cfg = replace(config, enabled_concepts=[*config.enabled_concepts, "strat_212"])
    engine = DecisionEngine(config=cfg)
    state = _state(
        strat=StratContext(
            current_bar_type="two_up", previous_bar_type="inside_bar",
            two_bars_back_type="two_up",
        ),
        raw={"previous_bar_high": 100.0, "previous_bar_low": 95.0},
        high=100.5, low=99.0,
    )
    daily = _daily_state()
    engine._advance_strat_212_122(state, daily)
    setup = engine._try_strat_212(state)
    assert setup is not None
    assert setup.strategy == "strat_212"
    assert setup.entry == 100.25
    assert setup.stop == 94.0
    assert setup.pre_resolved is None


def test_try_strat_212_resolved_candidate_carries_pre_resolved(config):
    cfg = replace(config, enabled_concepts=[*config.enabled_concepts, "strat_212"])
    engine = DecisionEngine(config=cfg)
    state = _state(
        strat=StratContext(
            current_bar_type="outside_bar", previous_bar_type="inside_bar",
            two_bars_back_type="two_up",
        ),
        raw={"previous_bar_high": 100.0, "previous_bar_low": 95.0},
        high=101.0, low=93.0,
    )
    daily = _daily_state()
    engine._advance_strat_212_122(state, daily)
    setup = engine._try_strat_212(state)
    assert setup is not None
    assert setup.pre_resolved == {
        "result": "LOSS",
        "exit_price": 94.0,
        "exit_reason": "OUTSIDE_AFTER_TRIGGER",
    }
    assert setup.entry == 100.25
    assert setup.stop == 94.0  # matches pre_resolved exit_price exactly


def test_old_proxy_conditions_no_longer_produce_a_setup(config):
    """The removed Phase-1 proxy fired on ORB status=='inside' + trend + VWAP
    with NO real Strat sequence at all. That combination must now be inert."""
    cfg = replace(
        config, enabled_concepts=[*config.enabled_concepts, "strat_212", "strat_122"]
    )
    engine = DecisionEngine(config=cfg)
    state = _state(
        strat=None,  # no classified sequence available at all
        raw={},
        high=100.5, low=99.0,
    )
    daily = _daily_state()
    engine._advance_strat_212_122(state, daily)
    assert engine._try_strat_212(state) is None
    assert engine._try_strat_122(state) is None


def test_generic_stop_multiplier_cannot_rewrite_causal_stop():
    from strategy.signal_engine import SetupDetail

    setup = SetupDetail(
        direction="LONG", entry=100.25, stop=94.0, target=112.75,
        rr_ratio=2.0, strategy="strat_212",
    )
    mult = apply_stop_multiplier(setup, "MNQ", {"MNQ": 2.5})
    assert mult == 1.0
    assert setup.stop == 94.0

    setup_122 = SetupDetail(
        direction="SHORT", entry=94.75, stop=101.0, target=82.25,
        rr_ratio=2.0, strategy="strat_122",
    )
    mult = apply_stop_multiplier(setup_122, "MNQ", {"MNQ": 2.5})
    assert mult == 1.0
    assert setup_122.stop == 101.0


# ─── Journal persistence / reconstruction ───────────────────────────────────


def test_strat_212_122_state_round_trips_through_journal(tmp_path):
    journal = JournalLogger(str(tmp_path))
    journal._append(
        {
            "ts": "2026-01-06T14:30:00Z",
            "instrument": "MNQ",
            "session": "new_york",
            "decision": "NO_TRADE",
            "reason": "test",
            "strategy_state": {
                "strat_212_122": {
                    "trading_date": "2026-07-24",
                    "status": "EXPIRED_NOT_TRIGGERED",
                    "pattern": "strat_212",
                }
            },
        },
        for_date=date(2026, 7, 24),
    )
    reconstructed = journal.get_daily_state(date(2026, 7, 24))
    assert reconstructed.strat_212_122_state["status"] == "EXPIRED_NOT_TRIGGERED"
    assert reconstructed.strat_212_122_state["pattern"] == "strat_212"


# ─── Replay end-to-end: outside-after-trigger journals a LOSS, no order ────


def _replay_row(timestamp: str, **overrides) -> dict:
    row = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    row.update({"timestamp": timestamp, "timeframe": "5m", "instrument": "MNQ"})
    row.update(overrides)
    return row


def test_replay_outside_after_trigger_journals_loss_with_no_open_position(
    monkeypatch, config, tmp_path
):
    from replay.replay_engine import ReplayEngine

    cfg = replace(config, enabled_concepts=[*config.enabled_concepts, "strat_212"])
    path = tmp_path / "day.jsonl"
    # Bar 1: two_up (two_bars_back). Bar 2: inside (previous). Bar 3: outside
    # relative to bar 2's [95, 100] range — both boundaries crossed same bar.
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                _replay_row(
                    "2026-01-06T14:20:00Z",
                    open=90.0, high=95.0, low=88.0, close=94.0,
                    current_bar_type="two_up", previous_bar_type=None,
                    two_bars_back_type=None,
                ),
                _replay_row(
                    "2026-01-06T14:25:00Z",
                    open=97.0, high=100.0, low=95.0, close=97.0,
                    current_bar_type="inside_bar", previous_bar_type="two_up",
                    two_bars_back_type=None,
                    previous_bar_high=95.0, previous_bar_low=88.0,
                ),
                _replay_row(
                    "2026-01-06T14:30:00Z",
                    open=97.0, high=101.0, low=93.0, close=94.5,
                    current_bar_type="outside_bar", previous_bar_type="inside_bar",
                    two_bars_back_type="two_up",
                    previous_bar_high=100.0, previous_bar_low=95.0,
                ),
            )
        )
        + "\n"
    )
    log_dir = tmp_path / "logs"
    report = ReplayEngine(config=cfg, log_dir=str(log_dir)).run(path)

    rows = [
        json.loads(line)
        for line in Path(report.journal_path).read_text().splitlines()
    ]
    outcome = next(
        (row for row in rows if row.get("type") == "OUTCOME"), None
    )
    assert outcome is not None
    assert outcome["outcome"]["result"] == "LOSS"
    assert outcome["outcome"]["exit_reason"] == "OUTSIDE_AFTER_TRIGGER"
    assert report.open_trades == 0
