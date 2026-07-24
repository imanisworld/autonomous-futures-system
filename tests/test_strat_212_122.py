"""Canonical Strat 2-1-2 / 1-2-2: pure state machine + identity/causality
integration coverage.

Covers the repair that removed the Phase-1 ORB/trend/VWAP proxy from
strat_212/strat_122 (identity contamination) and replaced the entry/stop/
target resolution with a genuine two-phase armed state machine: phase 1
arms from the bar that completes the precursor (using ONLY that bar's own
type/OHLC); phase 2 resolves the already-fixed boundary against the very
next bar's OHLC (checking entry, target, AND stop together — not just
entry-vs-stop). See strategy/strat_212_122.py for the design and the
audit trail this replaced (an earlier draft recomputed everything from a
single bar and never checked whether the watched bar also reached target,
which silently misclassified same-bar wins as ordinary open positions).
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
from webhook.payload import AlertPayload


# ─── Pure state machine: phase 1 (ARM) ──────────────────────────────────────


def test_212_long_arms_on_the_inside_bar_using_its_own_ohlc():
    state, cand = advance_strat_212_122(
        current_bar_type="inside_bar",
        previous_bar_type="two_up",
        current_open=97.0,
        current_high=100.0,
        current_low=95.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert cand is None  # arming never produces a candidate on the arm bar
    assert state["status"] == "ARMED"
    assert state["pattern"] == STRAT_212
    assert state["direction"] == "LONG"
    assert state["boundary_high"] == 100.0
    assert state["boundary_low"] == 95.0
    assert state["entry_price"] == 100.25
    assert state["stop_price"] == 95.0  # exact boundary — no tick buffer
    assert state["target_price"] == pytest.approx(100.25 + 2.0 * (100.25 - 95.0))


def test_212_short_arms_on_the_inside_bar():
    state, cand = advance_strat_212_122(
        current_bar_type="inside_bar",
        previous_bar_type="two_down",
        current_open=97.0,
        current_high=100.0,
        current_low=95.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert cand is None
    assert state["status"] == "ARMED"
    assert state["direction"] == "SHORT"
    assert state["entry_price"] == 94.75
    assert state["stop_price"] == 100.0  # exact boundary


def test_122_long_arms_on_the_prior_2d_bar_reversal():
    # 1 -> 2D: this bar (2D) is what the NEXT bar must reverse through.
    state, cand = advance_strat_212_122(
        current_bar_type="two_down",
        previous_bar_type="inside_bar",
        current_open=97.0,
        current_high=100.0,
        current_low=95.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert cand is None
    assert state["status"] == "ARMED"
    assert state["pattern"] == STRAT_122
    assert state["direction"] == "LONG"
    assert state["entry_price"] == 100.25
    assert state["stop_price"] == 95.0


def test_122_short_arms_on_the_prior_2u_bar_reversal():
    state, cand = advance_strat_212_122(
        current_bar_type="two_up",
        previous_bar_type="inside_bar",
        current_open=97.0,
        current_high=100.0,
        current_low=95.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert cand is None
    assert state["status"] == "ARMED"
    assert state["direction"] == "SHORT"
    assert state["entry_price"] == 94.75
    assert state["stop_price"] == 100.0


@pytest.mark.parametrize(
    "current_bar_type,previous_bar_type",
    [
        ("two_up", "two_up"),
        ("two_down", "two_down"),
        ("outside_bar", "inside_bar"),
        ("inside_bar", "inside_bar"),
        (None, None),
    ],
)
def test_no_precursor_stays_idle(current_bar_type, previous_bar_type):
    state, cand = advance_strat_212_122(
        current_bar_type=current_bar_type,
        previous_bar_type=previous_bar_type,
        current_open=97.0,
        current_high=100.0,
        current_low=95.0,
        tick_size=0.25,
        trading_date="2026-07-24",
    )
    assert cand is None
    assert state["status"] == "IDLE"


# ─── Pure state machine: phase 2 (RESOLVE against the very next bar) ───────


def _arm_212_long(high=100.0, low=95.0):
    state, cand = advance_strat_212_122(
        current_bar_type="inside_bar", previous_bar_type="two_up",
        current_open=(high + low) / 2, current_high=high, current_low=low,
        tick_size=0.25, trading_date="2026-07-24",
    )
    assert cand is None
    return state


def _resolve(armed_state, high, low, open=99.0):
    return advance_strat_212_122(
        current_bar_type=None, previous_bar_type=None,  # irrelevant while resolving
        current_open=open, current_high=high, current_low=low,
        tick_size=0.25, trading_date="2026-07-24",
        persisted_state=armed_state,
    )


def test_resolve_neither_boundary_reached_is_a_no_trade():
    armed = _arm_212_long()
    next_state, cand = _resolve(armed, high=99.0, low=96.0)
    assert cand is None
    assert next_state["status"] == "IDLE"


def test_resolve_only_invalidation_side_reached_is_a_no_trade():
    armed = _arm_212_long()
    next_state, cand = _resolve(armed, high=99.0, low=93.0)
    assert cand is None
    assert next_state["status"] == "IDLE"


def test_resolve_entry_only_is_open_with_intrabar_causal_fill():
    """No gap: open stays below the entry boundary, so the causal fill is
    the exact structural entry level (touched intrabar), not the open."""
    armed = _arm_212_long()
    next_state, cand = _resolve(armed, high=100.5, low=99.0, open=99.0)
    assert cand == {
        "kind": "OPEN",
        "pattern": STRAT_212,
        "direction": "LONG",
        "entry": 100.25,
        "stop": 95.0,
        "target": armed["target_price"],
    }
    assert next_state["status"] == "IDLE"


def test_resolve_entry_gap_through_at_open_fills_at_the_open_not_the_boundary():
    """The fix this pass adds: when the watched bar's OWN open already
    gapped through the armed entry boundary, a resting order would have
    filled at the open, not at the (better, unreachable) structural level —
    mirrors execution/paper_broker.py::_activate_pending_stop_entry exactly.
    Stop/target stay at their original structural levels regardless."""
    armed = _arm_212_long()  # entry=100.25, stop=95.0, target=110.75
    next_state, cand = _resolve(armed, high=103.5, low=102.0, open=103.0)
    assert cand["kind"] == "OPEN"
    assert cand["entry"] == 103.0  # the gapped-through open, not 100.25
    assert cand["stop"] == 95.0
    assert cand["target"] == armed["target_price"]


def test_resolve_entry_and_target_same_bar_is_a_causal_win():
    """The scenario the prior pass's repair fixed: a watched bar whose
    range reaches all the way past target must resolve as a same-bar WIN,
    not be deferred as an ordinary OPEN position waiting on a future bar."""
    armed = _arm_212_long()
    next_state, cand = _resolve(armed, high=200.0, low=99.0, open=99.0)
    assert cand["kind"] == "RESOLVED"
    assert cand["result"] == "WIN"
    assert cand["exit"] == cand["target"] == armed["target_price"]
    assert cand["entry"] == 100.25
    assert cand["stop"] == 95.0
    assert cand["exit_reason"] == "TARGET_HIT_SAME_BAR"
    assert next_state["status"] == "IDLE"


def test_resolve_entry_and_stop_same_bar_is_a_loss():
    armed = _arm_212_long()
    next_state, cand = _resolve(armed, high=101.0, low=93.0, open=97.0)
    assert cand["kind"] == "RESOLVED"
    assert cand["result"] == "LOSS"
    assert cand["exit"] == cand["stop"] == 95.0
    assert cand["exit_reason"] == "OUTSIDE_AFTER_TRIGGER"


def test_resolve_entry_stop_and_target_all_reached_is_pessimistic_loss():
    armed = _arm_212_long()
    next_state, cand = _resolve(armed, high=200.0, low=93.0, open=97.0)
    assert cand["kind"] == "RESOLVED"
    assert cand["result"] == "LOSS"
    assert cand["exit_reason"] == "OUTSIDE_AFTER_TRIGGER"


def test_122_resolve_win_and_loss():
    state, cand = advance_strat_212_122(
        current_bar_type="two_down", previous_bar_type="inside_bar",
        current_open=97.0, current_high=100.0, current_low=95.0,
        tick_size=0.25, trading_date="2026-07-24",
    )
    _, win_cand = _resolve(state, high=200.0, low=99.0, open=99.0)
    assert win_cand["result"] == "WIN"

    _, loss_cand = _resolve(state, high=101.0, low=93.0, open=97.0)
    assert loss_cand["result"] == "LOSS"


def test_one_bar_watch_window_only_armed_state_never_persists_past_resolution():
    armed = _arm_212_long()
    next_state, _ = _resolve(armed, high=99.0, low=96.0)  # expires
    # A THIRD bar must not still be treated as watching the old arm.
    third_state, third_cand = _resolve(next_state, high=200.0, low=93.0)
    assert third_cand is None
    assert third_state["status"] == "IDLE"


# ─── DecisionEngine integration (two calendar-adjacent bars) ───────────────


def _state(*, strat, high, low, ts=datetime(2026, 7, 24, 14, 30, tzinfo=timezone.utc)) -> MarketState:
    return MarketState(
        timestamp=ts,
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
        raw={},
    )


def test_engine_stays_idle_and_returns_none_when_disabled(config):
    assert "strat_212" not in config.enabled_concepts
    engine = DecisionEngine(config=config)
    arm_bar = _state(
        strat=StratContext(current_bar_type="inside_bar", previous_bar_type="two_up"),
        high=100.0, low=95.0,
    )
    daily = DailyState(trade_count=0, consecutive_losses=0, has_open_position=False)
    engine._advance_strat_212_122(arm_bar, daily)
    assert arm_bar.strat_212_122_candidate is None
    assert engine._try_strat_212(arm_bar) is None
    assert daily.strat_212_122_state == {}


def test_engine_arms_then_resolves_open_across_two_bars(config):
    cfg = replace(config, enabled_concepts=[*config.enabled_concepts, "strat_212"])
    engine = DecisionEngine(config=cfg)
    daily = DailyState(trade_count=0, consecutive_losses=0, has_open_position=False)

    arm_bar = _state(
        strat=StratContext(current_bar_type="inside_bar", previous_bar_type="two_up"),
        high=100.0, low=95.0,
    )
    engine._advance_strat_212_122(arm_bar, daily)
    assert engine._try_strat_212(arm_bar) is None  # never a candidate on the arm bar
    assert daily.strat_212_122_state["status"] == "ARMED"

    watch_bar = _state(strat=None, high=100.5, low=99.0)
    engine._advance_strat_212_122(watch_bar, daily)
    setup = engine._try_strat_212(watch_bar)
    assert setup is not None
    assert setup.strategy == "strat_212"
    assert setup.entry == 100.25
    assert setup.stop == 95.0
    assert setup.pre_resolved is None
    assert daily.strat_212_122_state["status"] == "IDLE"


def test_engine_arms_then_resolves_win_across_two_bars(config):
    cfg = replace(config, enabled_concepts=[*config.enabled_concepts, "strat_212"])
    engine = DecisionEngine(config=cfg)
    daily = DailyState(trade_count=0, consecutive_losses=0, has_open_position=False)

    arm_bar = _state(
        strat=StratContext(current_bar_type="inside_bar", previous_bar_type="two_up"),
        high=100.0, low=95.0,
    )
    engine._advance_strat_212_122(arm_bar, daily)

    watch_bar = _state(strat=None, high=200.0, low=99.0)
    engine._advance_strat_212_122(watch_bar, daily)
    setup = engine._try_strat_212(watch_bar)
    assert setup is not None
    assert setup.pre_resolved == {
        "result": "WIN",
        "exit_price": setup.target,
        "exit_reason": "TARGET_HIT_SAME_BAR",
    }
    assert setup.stop == 95.0  # the real stop level, not the exit price


def test_engine_arms_then_resolves_loss_across_two_bars(config):
    cfg = replace(config, enabled_concepts=[*config.enabled_concepts, "strat_212"])
    engine = DecisionEngine(config=cfg)
    daily = DailyState(trade_count=0, consecutive_losses=0, has_open_position=False)

    arm_bar = _state(
        strat=StratContext(current_bar_type="inside_bar", previous_bar_type="two_up"),
        high=100.0, low=95.0,
    )
    engine._advance_strat_212_122(arm_bar, daily)

    watch_bar = _state(strat=None, high=101.0, low=93.0)
    engine._advance_strat_212_122(watch_bar, daily)
    setup = engine._try_strat_212(watch_bar)
    assert setup.pre_resolved == {
        "result": "LOSS",
        "exit_price": 95.0,
        "exit_reason": "OUTSIDE_AFTER_TRIGGER",
    }
    assert setup.stop == 95.0


def test_old_proxy_conditions_no_longer_produce_a_setup(config):
    """The removed Phase-1 proxy fired on ORB status=='inside' + trend + VWAP
    with NO real Strat sequence at all. That combination must now be inert."""
    cfg = replace(
        config, enabled_concepts=[*config.enabled_concepts, "strat_212", "strat_122"]
    )
    engine = DecisionEngine(config=cfg)
    state = _state(strat=None, high=100.5, low=99.0)
    daily = DailyState(trade_count=0, consecutive_losses=0, has_open_position=False)
    engine._advance_strat_212_122(state, daily)
    assert engine._try_strat_212(state) is None
    assert engine._try_strat_122(state) is None


def test_generic_stop_multiplier_cannot_rewrite_causal_stop():
    from strategy.signal_engine import SetupDetail

    setup = SetupDetail(
        direction="LONG", entry=100.25, stop=95.0, target=110.75,
        rr_ratio=2.0, strategy="strat_212",
    )
    mult = apply_stop_multiplier(setup, "MNQ", {"MNQ": 2.5})
    assert mult == 1.0
    assert setup.stop == 95.0

    setup_122 = SetupDetail(
        direction="SHORT", entry=94.75, stop=100.0, target=84.25,
        rr_ratio=2.0, strategy="strat_122",
    )
    mult = apply_stop_multiplier(setup_122, "MNQ", {"MNQ": 2.5})
    assert mult == 1.0
    assert setup_122.stop == 100.0


# ─── Journal persistence / reconstruction ───────────────────────────────────


def test_strat_212_122_armed_state_round_trips_through_journal(tmp_path):
    journal = JournalLogger(str(tmp_path))
    journal._append(
        {
            "ts": "2026-07-24T14:30:00Z",
            "instrument": "MNQ",
            "session": "new_york",
            "decision": "NO_TRADE",
            "reason": "test",
            "strategy_state": {
                "strat_212_122": {
                    "trading_date": "2026-07-24",
                    "status": "ARMED",
                    "pattern": "strat_212",
                    "direction": "LONG",
                    "boundary_high": 100.0,
                    "boundary_low": 95.0,
                    "entry_price": 100.25,
                    "stop_price": 95.0,
                    "target_price": 110.75,
                }
            },
        },
        for_date=date(2026, 7, 24),
    )
    reconstructed = journal.get_daily_state(date(2026, 7, 24))
    assert reconstructed.strat_212_122_state["status"] == "ARMED"
    assert reconstructed.strat_212_122_state["entry_price"] == 100.25


# ─── Replay end-to-end: same-bar resolution journals directly, no order ────


def _replay_row(timestamp: str, **overrides) -> dict:
    row = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    row.update({"timestamp": timestamp, "timeframe": "5m", "instrument": "MNQ"})
    row.update(overrides)
    return row


def test_replay_resolves_loss_with_no_open_position_left_behind(config, tmp_path):
    from replay.replay_engine import ReplayEngine

    cfg = replace(config, enabled_concepts=[*config.enabled_concepts, "strat_212"])
    path = tmp_path / "day.jsonl"
    # Bar 1 arms (inside bar following two_up, boundary = ITS OWN [95,100]).
    # Bar 2 is the watched bar: both entry and stop reached -> pessimistic LOSS.
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                _replay_row(
                    "2026-01-06T14:25:00Z",
                    open=97.0, high=100.0, low=95.0, close=97.0,
                    current_bar_type="inside_bar", previous_bar_type="two_up",
                ),
                _replay_row(
                    "2026-01-06T14:30:00Z",
                    open=97.0, high=101.0, low=93.0, close=94.5,
                    current_bar_type="outside_bar", previous_bar_type="inside_bar",
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
    outcome = next((row for row in rows if row.get("type") == "OUTCOME"), None)
    assert outcome is not None
    assert outcome["outcome"]["result"] == "LOSS"
    assert outcome["outcome"]["exit_reason"] == "OUTSIDE_AFTER_TRIGGER"
    assert outcome["outcome"]["exit_price"] == 95.0
    assert report.open_trades == 0


def test_replay_open_position_gap_fills_and_carries_forward_to_a_later_bar(
    config, tmp_path
):
    """The fix this pass adds: an OPEN candidate is restored directly at its
    causal (gap-aware) fill price and carried forward through the normal
    future-bar resolution loop — never resubmitted as a fresh order at
    whatever price the market is at once the watched bar has closed."""
    from replay.replay_engine import ReplayEngine

    # A gap-through fill worsens R:R against the structural (arm-time) stop
    # and target by construction (the target is fixed at arm time, not
    # recomputed from the actual fill) — that's real, and the normal R:R
    # gate correctly rejects a bad enough gap on its own. Lower the floor
    # here to isolate what THIS test actually checks: the causal fill price
    # carries forward correctly, not the (separately correct) R:R gate.
    cfg = replace(
        config,
        enabled_concepts=[*config.enabled_concepts, "strat_212"],
        min_rr_ratio=0.5,
    )
    path = tmp_path / "day.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                _replay_row(  # bar 1: arms — inside bar, boundary [95, 100]
                    "2026-01-06T14:25:00Z",
                    open=97.0, high=100.0, low=95.0, close=97.0,
                    current_bar_type="inside_bar", previous_bar_type="two_up",
                ),
                _replay_row(  # bar 2: watched — gaps up through entry at open
                    "2026-01-06T14:30:00Z",
                    open=103.0, high=104.0, low=102.0, close=103.5,
                    current_bar_type="two_up", previous_bar_type="inside_bar",
                ),
                _replay_row(  # bar 3: a later bar resolves it via target
                    "2026-01-06T14:35:00Z",
                    open=104.0, high=111.0, low=103.5, close=110.0,
                    current_bar_type="two_up", previous_bar_type="two_up",
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
    trade_rows = [r for r in rows if r.get("decision") == "TRADE"]
    assert len(trade_rows) == 1
    assert trade_rows[0]["setup"]["entry"] == 103.0  # the gapped open, not 100.25

    outcome = next((row for row in rows if row.get("type") == "OUTCOME"), None)
    assert outcome is not None
    assert outcome["outcome"]["result"] == "WIN"
    assert outcome["outcome"]["entry_price"] == 103.0
    assert report.open_trades == 0


def test_replay_resolves_win_with_no_open_position_left_behind(config, tmp_path):
    from replay.replay_engine import ReplayEngine

    cfg = replace(config, enabled_concepts=[*config.enabled_concepts, "strat_212"])
    path = tmp_path / "day.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                _replay_row(
                    "2026-01-06T14:25:00Z",
                    open=97.0, high=100.0, low=95.0, close=97.0,
                    current_bar_type="inside_bar", previous_bar_type="two_up",
                ),
                _replay_row(
                    "2026-01-06T14:30:00Z",
                    open=100.0, high=115.0, low=99.0, close=112.0,
                    current_bar_type="two_up", previous_bar_type="inside_bar",
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
    outcome = next((row for row in rows if row.get("type") == "OUTCOME"), None)
    assert outcome is not None
    assert outcome["outcome"]["result"] == "WIN"
    assert outcome["outcome"]["exit_reason"] == "TARGET_HIT_SAME_BAR"
    assert report.open_trades == 0


# ─── Live path: a resolved same-bar outcome must never reach a real broker ──


class _NeverCalledBroker:
    """A stand-in for a real (non-Paper) broker connection. Recording a call
    to execute_bracket at all is the failure — a strat_212/122 RESOLVED
    candidate has no real order that was ever armed ahead of the watched
    bar, so nothing should ever be submitted for it."""

    is_live = False

    def __init__(self):
        import types as _types
        self.config = _types.SimpleNamespace(env="demo")
        self.execute_bracket_calls = 0

    def get_account_balance(self):
        return 50000.0

    def execute_bracket(self, order, **kwargs):
        self.execute_bracket_calls += 1
        raise AssertionError(
            "execute_bracket must never be called for a strat_212/122 "
            "pre_resolved candidate on a non-Paper broker"
        )


def _strat_alert_payload(**overrides) -> AlertPayload:
    data = {
        "ticker": "MNQ1!",
        "timestamp": "2026-01-06T14:25:00+00:00",
        "timeframe": "15",
        "open": 19497.0, "high": 19500.0, "low": 19495.0, "close": 19497.0,
        "volume": 4200, "avg_volume": 3800, "vwap": 19495.0,
        "orb_high": 19505.0, "orb_low": 19490.0, "orb_status": "inside",
        "market_condition": "TRENDING",
        "trend_direction": "UP", "trend_strength": "STRONG",
        "previous_day_high": 19510.0, "previous_day_low": 19490.0, "previous_day_close": 19500.0,
    }
    data.update(overrides)
    return AlertPayload(**data)


def _strat_engine_cfg(config):
    return replace(
        config,
        enabled_concepts=[*config.enabled_concepts, "strat_212"],
        paper_mode=False,
        working_order_recheck_enabled=False,
        schedule_mode="current",
        live_trading_enabled=False,
    )


def test_live_broker_refuses_to_submit_a_resolved_same_bar_outcome(config, tmp_path, monkeypatch):
    from webhook import runner

    fake = _NeverCalledBroker()
    monkeypatch.setattr(runner, "_make_broker", lambda **kw: fake)
    log_dir = str(tmp_path / "logs")
    cfg = _strat_engine_cfg(config)

    arm_result = runner.process_alert(
        _strat_alert_payload(
            current_bar_type="inside_bar", previous_bar_type="two_up",
        ),
        config=cfg, log_dir=log_dir,
    )
    assert arm_result["decision"] in ("NO_TRADE", "WAIT", "DONE_FOR_DAY") or arm_result.get("fill") is None

    watch_result = runner.process_alert(
        _strat_alert_payload(
            timestamp="2026-01-06T14:30:00+00:00",
            open=19497.0, high=19501.0, low=19493.0, close=19494.5,
            current_bar_type="outside_bar", previous_bar_type="inside_bar",
        ),
        config=cfg, log_dir=log_dir,
    )
    assert watch_result["decision"] == "NO_TRADE"
    assert fake.execute_bracket_calls == 0

    rows = [
        json.loads(line)
        for line in next((tmp_path / "logs").glob("journal_*.jsonl")).read_text().splitlines()
    ]
    assert not any(row.get("type") == "OUTCOME" for row in rows)


def test_live_broker_refuses_to_submit_a_late_order_for_an_already_open_position(
    config, tmp_path, monkeypatch
):
    """The fix this pass adds: an OPEN (already-triggered, not same-bar-
    resolved) candidate must ALSO be refused on a live broker — no order was
    armed ahead of the watched bar for it either, so submitting one after
    the fact would be a completely different, unrelated trade."""
    from webhook import runner

    fake = _NeverCalledBroker()
    monkeypatch.setattr(runner, "_make_broker", lambda **kw: fake)
    log_dir = str(tmp_path / "logs")
    cfg = _strat_engine_cfg(config)

    arm_result = runner.process_alert(
        _strat_alert_payload(
            current_bar_type="inside_bar", previous_bar_type="two_up",
        ),
        config=cfg, log_dir=log_dir,
    )
    assert arm_result["decision"] in ("NO_TRADE", "WAIT", "DONE_FOR_DAY") or arm_result.get("fill") is None

    # Watched bar: entry (19500.25) touched, neither stop (19495.0) nor
    # target (~19510.75) reached — an ordinary OPEN, not a same-bar outcome.
    watch_result = runner.process_alert(
        _strat_alert_payload(
            timestamp="2026-01-06T14:30:00+00:00",
            open=19498.0, high=19501.0, low=19498.0, close=19500.5,
            current_bar_type="two_up", previous_bar_type="inside_bar",
        ),
        config=cfg, log_dir=log_dir,
    )
    assert watch_result["decision"] == "NO_TRADE"
    assert fake.execute_bracket_calls == 0

    rows = [
        json.loads(line)
        for line in next((tmp_path / "logs").glob("journal_*.jsonl")).read_text().splitlines()
    ]
    assert not any(row.get("decision") == "TRADE" for row in rows)
