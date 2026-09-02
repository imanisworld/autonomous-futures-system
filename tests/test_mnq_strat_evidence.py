from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import timedelta

import pytest

import execution.mnq_strat_evidence as evidence
from config.settings import ConfigError, _validate_config
from context.market_context import OHLCData
from execution.mnq_strat_evidence import (
    INITIAL_ACTIVATION_MODES,
    LANES,
    detect_lane,
    evidence_path,
    lane_mode,
    process_mnq_strat_evidence,
)
from ops.mnq_strat_evidence_monitor import summarize_lane
from strategy.strat_classifier import StratContext, classify_sequence


FLAT = {
    "source": "read-only-test-snapshot",
    "checked_at": "2026-05-23T14:29:00+00:00",
    "positions": {"flat": True, "detail": "0 open position(s)"},
    "working_orders": {"flat": True, "detail": "0 working order(s)"},
    "confirmed": True,
    "reason": "TRADOVATE_DEMO_FLAT_CONFIRMED",
}


def _paper_cfg(config, lane: str = "strat_22_reversal"):
    return replace(config, **{f"mnq_{lane}_mode": "paper_sim"})


def _state(fresh_market_state, sequence: str, *, close: float = 101.0):
    state = copy.deepcopy(fresh_market_state)
    state.ohlc = OHLCData(open=99.0, high=105.0, low=92.0, close=close, timeframe="15")
    state.raw = {
        "previous_bar_high": 100.0,
        "previous_bar_low": 90.0,
        "two_bars_back_high": 102.0,
        "two_bars_back_low": 88.0,
    }
    patterns = {
        "strat_22_reversal": classify_sequence(None, "two_down", "two_up"),
        "strat_22_continuation": classify_sequence(None, "two_up", "two_up"),
        "strat_32": classify_sequence(None, "outside_bar", "two_up"),
        "strat_322": classify_sequence("outside_bar", "two_down", "two_up"),
    }
    state.strat = patterns[sequence]
    return state


@pytest.mark.parametrize(
    ("requested", "classifier_sequence", "trigger"),
    [
        ("strat_22_reversal", "strat_22_reversal", "reversal"),
        ("strat_22_continuation", "strat_22_continuation", "continuation"),
        ("strat_32", "strat_outside_continuation", "outside_bar_followthrough"),
        ("strat_322", "strat_322_reversal", "reversal"),
    ],
)
def test_each_existing_pattern_is_detected_independently(
    fresh_market_state, config, requested, classifier_sequence, trigger
):
    state = _state(fresh_market_state, requested)
    spec, setup, rejection = detect_lane(state, cfg=config)
    assert spec is not None and spec.key == requested
    assert spec.sequence == classifier_sequence
    assert state.strat.strat_trigger == trigger
    assert setup is not None
    assert rejection is None


def test_reversal_and_continuation_cannot_be_mislabeled(fresh_market_state, config):
    reversal, _, _ = detect_lane(
        _state(fresh_market_state, "strat_22_reversal"), cfg=config
    )
    continuation, _, _ = detect_lane(
        _state(fresh_market_state, "strat_22_continuation"), cfg=config
    )
    assert reversal.key == "strat_22_reversal"
    assert reversal.trigger == "reversal"
    assert continuation.key == "strat_22_continuation"
    assert continuation.trigger == "continuation"
    assert reversal.key != continuation.key


def test_all_defaults_observe_only_but_requested_activation_is_explicit(monkeypatch):
    for spec in LANES.values():
        monkeypatch.delenv(spec.env_name, raising=False)
        assert lane_mode(spec.key) == "observe_only"
    assert INITIAL_ACTIVATION_MODES == {
        "strat_22_reversal": "paper_sim",
        "strat_22_continuation": "observe_only",
        "strat_32": "observe_only",
        "strat_322": "observe_only",
    }


def test_strat_lane_modes_reject_any_external_broker_value(config):
    for value in ("tradovate_demo", "demo", "live", "tradovate"):
        with pytest.raises(ConfigError, match="never valid for Strat evidence"):
            _validate_config(replace(
                config,
                mnq_strat_22_reversal_mode=value,
                max_staleness_seconds=60,
            ))


def test_duplicate_candidates_are_suppressed(tmp_path, fresh_market_state, config):
    state = _state(fresh_market_state, "strat_22_reversal")
    first = process_mnq_strat_evidence(
        state=state, cfg=config, log_dir=tmp_path, flatness_snapshot=FLAT
    )
    second = process_mnq_strat_evidence(
        state=state, cfg=config, log_dir=tmp_path, flatness_snapshot=FLAT
    )
    assert [row["event"] for row in first] == ["CANDIDATE"]
    assert second == []
    assert len(evidence_path(tmp_path, "strat_22_reversal").read_text().splitlines()) == 1


def test_observe_only_cannot_create_an_order(
    tmp_path, fresh_market_state, config, monkeypatch
):
    class PaperBrokerMustNotExist:
        def __init__(self, *args, **kwargs):
            raise AssertionError("observe_only constructed PaperBroker")

    monkeypatch.setattr(evidence, "PaperBroker", PaperBrokerMustNotExist)
    state = _state(fresh_market_state, "strat_22_reversal")
    event = process_mnq_strat_evidence(
        state=state, cfg=config, log_dir=tmp_path, flatness_snapshot=FLAT
    )[0]
    assert event["mode"] == "observe_only"
    assert event["accepted"] is True
    assert event["paper_order_id"] is None
    assert event["fill_status"] == "NO_FILL"


def test_paper_sim_never_calls_tradovate_and_every_order_is_synthetic(
    tmp_path, fresh_market_state, config, monkeypatch
):
    from execution.tradovate_broker import TradovateBroker

    def forbidden(*args, **kwargs):
        raise AssertionError("Strat paper lane touched Tradovate")

    monkeypatch.setattr(TradovateBroker, "execute_bracket", forbidden)
    state = _state(fresh_market_state, "strat_22_reversal")
    event = process_mnq_strat_evidence(
        state=state, cfg=_paper_cfg(config), log_dir=tmp_path, flatness_snapshot=FLAT
    )[0]
    assert event["broker_route"] == "PaperBroker"
    assert event["paper_order_id"].startswith("PAPER-")
    assert event["fill_status"] == "FILLED"
    assert event["tradovate_snapshot"]["confirmed"] is True


def test_memory_critical_blocks_new_paper_position(
    tmp_path, fresh_market_state, config, monkeypatch
):
    monkeypatch.setattr(
        evidence,
        "read_critical_memory_block",
        lambda: {"level": "CRITICAL", "reason": "derived headroom exhausted"},
    )
    event = process_mnq_strat_evidence(
        state=_state(fresh_market_state, "strat_22_reversal"),
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        flatness_snapshot=FLAT,
    )[0]

    assert event["accepted"] is False
    assert event["paper_order_id"] is None
    assert "MEMORY_CRITICAL" in event["rejection_reason"]


def test_absent_flatness_evidence_fails_structural_isolation_closed(
    tmp_path, fresh_market_state, config, monkeypatch
):
    state = _state(fresh_market_state, "strat_22_reversal")
    event = process_mnq_strat_evidence(
        state=state, cfg=_paper_cfg(config), log_dir=tmp_path
    )[0]
    assert event["accepted"] is False
    assert event["paper_order_id"] is None
    assert event["structural_isolation_status"] == "UNCONFIRMED_FAIL_CLOSED"
    assert "STRUCTURAL_ISOLATION_UNCONFIRMED_FAIL_CLOSED" in event["rejection_reason"]


def test_complete_lifecycle_remains_paperbroker_owned(
    tmp_path, fresh_market_state, config, monkeypatch
):
    entry_state = _state(fresh_market_state, "strat_22_reversal")
    candidate = process_mnq_strat_evidence(
        state=entry_state, cfg=_paper_cfg(config), log_dir=tmp_path, flatness_snapshot=FLAT
    )[0]
    paper_id = candidate["paper_order_id"]

    exit_state = copy.deepcopy(entry_state)
    exit_state.timestamp += timedelta(minutes=15)
    exit_state.strat = StratContext()
    exit_state.ohlc = OHLCData(
        open=105.0, high=122.0, low=95.0, close=120.0, timeframe="15"
    )
    emitted = process_mnq_strat_evidence(
        state=exit_state, cfg=_paper_cfg(config), log_dir=tmp_path, flatness_snapshot=FLAT
    )
    outcome = next(row for row in emitted if row["event"] == "OUTCOME")
    assert outcome["result"] == "WIN"
    assert outcome["exit_reason"] == "TARGET_HIT"
    assert outcome["paper_order_id"] == paper_id
    assert outcome["broker_route"] == "PaperBroker"
    assert outcome["commission_dollars"] == pytest.approx(1.48)
    assert outcome["net_dollars"] < outcome["gross_dollars"]


def test_runner_movements_are_preserved_without_stealing_static_exit_ownership(
    tmp_path, fresh_market_state, config
):
    entry_state = _state(fresh_market_state, "strat_22_reversal")
    process_mnq_strat_evidence(
        state=entry_state, cfg=_paper_cfg(config), log_dir=tmp_path,
        flatness_snapshot=FLAT,
    )
    favorable = copy.deepcopy(entry_state)
    favorable.timestamp += timedelta(minutes=15)
    favorable.strat = StratContext()
    favorable.ohlc = OHLCData(
        open=102.0, high=115.0, low=95.0, close=114.0, timeframe="15"
    )
    assert process_mnq_strat_evidence(
        state=favorable, cfg=_paper_cfg(config), log_dir=tmp_path,
        flatness_snapshot=FLAT,
    ) == []

    next_bar = copy.deepcopy(favorable)
    next_bar.timestamp += timedelta(minutes=15)
    next_bar.ohlc = OHLCData(
        open=114.0, high=116.0, low=110.0, close=115.0, timeframe="15"
    )
    process_mnq_strat_evidence(
        state=next_bar, cfg=_paper_cfg(config), log_dir=tmp_path,
        flatness_snapshot=FLAT,
    )
    rows = [
        json.loads(line)
        for line in evidence_path(tmp_path, "strat_22_reversal").read_text().splitlines()
    ]
    runner_event = next(row for row in rows if row["event"] == "RUNNER_MOVE")
    assert runner_event["armed"] is True
    assert runner_event["to"] > runner_event["from"]


def test_same_bar_stop_target_ambiguity_resolves_stop_first(
    tmp_path, fresh_market_state, config, monkeypatch
):
    entry_state = _state(fresh_market_state, "strat_22_reversal")
    process_mnq_strat_evidence(
        state=entry_state, cfg=_paper_cfg(config), log_dir=tmp_path, flatness_snapshot=FLAT
    )
    exit_state = copy.deepcopy(entry_state)
    exit_state.timestamp += timedelta(minutes=15)
    exit_state.strat = StratContext()
    exit_state.ohlc = OHLCData(
        open=101.0, high=122.0, low=89.0, close=100.0, timeframe="15"
    )
    outcome = next(
        row for row in process_mnq_strat_evidence(
            state=exit_state, cfg=_paper_cfg(config), log_dir=tmp_path, flatness_snapshot=FLAT
        )
        if row["event"] == "OUTCOME"
    )
    assert outcome["result"] == "LOSS"
    assert outcome["exit_reason"] == "STOP_HIT"


def test_existing_orb_vwap_configuration_and_non_strat_behavior_unchanged(
    tmp_path, fresh_market_state, config, monkeypatch
):
    monkeypatch.setenv("MNQ_ORB_RECLAIM_PROOF_MODE", "paper_sim")
    monkeypatch.setenv("MNQ_ORB_BREAKOUT_PROOF_MODE", "observe_only")
    monkeypatch.setenv("MNQ_VWAP_HOLD_PROOF_MODE", "observe_only")
    before = (
        config.enabled_concepts.copy(),
        config.live_trading_enabled,
        config.paper_mode,
        config.min_rr_ratio,
    )
    state = copy.deepcopy(fresh_market_state)
    state.strat = StratContext()
    assert process_mnq_strat_evidence(
        state=state, cfg=config, log_dir=tmp_path, flatness_snapshot=FLAT
    ) == []
    assert before == (
        config.enabled_concepts,
        config.live_trading_enabled,
        config.paper_mode,
        config.min_rr_ratio,
    )
    assert evidence.os.getenv("MNQ_ORB_RECLAIM_PROOF_MODE") == "paper_sim"
    assert evidence.os.getenv("MNQ_ORB_BREAKOUT_PROOF_MODE") == "observe_only"
    assert evidence.os.getenv("MNQ_VWAP_HOLD_PROOF_MODE") == "observe_only"


def test_lane_generic_monitor_reports_required_robustness_metrics(tmp_path):
    lane = "strat_22_reversal"
    rows = [
        {"event": "CANDIDATE", "accepted": True, "fill_status": "FILLED"},
        {"event": "CANDIDATE", "accepted": False, "fill_status": "NO_FILL"},
        {
            "event": "OUTCOME", "result": "WIN", "net_dollars": 10.0,
            "net_ticks": 20.0, "direction": "LONG", "session": "new_york",
            "entry_ts": "2026-05-01T14:30:00+00:00",
        },
        {
            "event": "OUTCOME", "result": "LOSS", "net_dollars": -5.0,
            "net_ticks": -10.0, "direction": "SHORT", "session": "london",
            "entry_ts": "2026-06-01T08:30:00+00:00",
        },
    ]
    path = evidence_path(tmp_path, lane)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    report = summarize_lane(tmp_path, lane)
    assert report["candidate_count"] == 2
    assert report["fill_count"] == 1
    assert report["no_fill_count"] == 1
    assert report["wins"] == 1 and report["losses"] == 1
    assert report["profit_factor"] == 2.0
    assert report["expectancy_dollars"] == 2.5
    assert report["maximum_drawdown_dollars"] == 5.0
    assert set(report["long_vs_short"]) == {"LONG", "SHORT"}
    assert set(report["ny_vs_london"]) == {"new_york", "london"}
    assert report["excluding_largest_winner"]["net_dollars"] == -5.0
    assert report["excluding_largest_winner"]["net_ticks"] == -10.0
    assert report["excluding_top_five_winners"]["net_dollars"] == -5.0
    assert report["excluding_top_five_winners"]["net_ticks"] == -10.0
