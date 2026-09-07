from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

import execution.mes_trend_consolidation_break_evidence as evidence
from config.settings import ConfigError, _validate_config
from context.market_context import OHLCData
from execution.mes_trend_consolidation_break_evidence import (
    INITIAL_ACTIVATION_MODES,
    detect_candidate,
    evidence_path,
    lane_mode,
    process_mes_trend_consolidation_break_evidence,
)
from ops.mes_trend_consolidation_break_monitor import summarize_lane


FLAT = {
    "source": "read-only-test-snapshot",
    "checked_at": "2026-05-23T14:29:00+00:00",
    "positions": {"flat": True, "detail": "0 open position(s)"},
    "working_orders": {"flat": True, "detail": "0 working order(s)"},
    "confirmed": True,
    "reason": "TRADOVATE_DEMO_FLAT_CONFIRMED",
}


def _decision(*failed_gates: str):
    return SimpleNamespace(
        decision="NO_TRADE",
        reason="Regime gate rejected: REGIME_NOT_FULL",
        failed_gates=list(failed_gates or ["REGIME_NOT_FULL"]),
        regime="RESTRICTED",
        setup=None,
    )


def _bars():
    return [
        {"ts": "0", "open": 110, "high": 111, "low": 108, "close": 109, "volume": 1},
        {"ts": "1", "open": 109, "high": 109, "low": 101, "close": 102, "volume": 1},
        {"ts": "2", "open": 103, "high": 104, "low": 101, "close": 102, "volume": 1},
        {"ts": "3", "open": 102, "high": 103, "low": 101, "close": 102, "volume": 1},
        {"ts": "4", "open": 102, "high": 103, "low": 101, "close": 101.5, "volume": 1},
    ]


def _state(fresh_market_state, *, ts_offset=0, ohlc=None):
    state = copy.deepcopy(fresh_market_state)
    state.instrument = "MES"
    state.timestamp += timedelta(minutes=15 * ts_offset)
    state.session = "new_york"
    state.trend = replace(state.trend, direction="DOWN", strength="STRONG")
    state.market_condition = "TRENDING"
    state.ohlc = ohlc or OHLCData(open=102, high=103, low=101, close=101.5, timeframe="15")
    return state


def _paper_cfg(config):
    return replace(config, mes_trend_consolidation_break_mode="paper_sim")


def test_default_mode_is_observe_only_but_activation_request_is_documented(monkeypatch):
    monkeypatch.delenv("MES_TREND_CONSOLIDATION_BREAK_MODE", raising=False)
    assert lane_mode() == "observe_only"
    assert INITIAL_ACTIVATION_MODES == {"trend_consolidation_break": "paper_sim"}


def test_external_broker_modes_are_rejected(config):
    for value in ("tradovate_demo", "demo", "live", "tradovate"):
        with pytest.raises(ConfigError, match="MES_TREND_CONSOLIDATION_BREAK_MODE"):
            _validate_config(
                replace(
                    config,
                    mes_trend_consolidation_break_mode=value,
                    max_staleness_seconds=60,
                )
            )


def test_reuses_existing_observer_definition(fresh_market_state):
    setup, confluence, rejection = detect_candidate(_state(fresh_market_state), _bars())
    assert rejection is None
    assert confluence == []
    assert setup["source_strategy"] == "trend_consolidation_break_observed"
    assert setup["direction"] == "SHORT"
    assert setup["entry"] == pytest.approx(100.75)
    assert setup["stop"] == pytest.approx(104.25)
    assert setup["target"] == pytest.approx(93.75)


def test_observe_only_records_candidate_but_never_creates_order(
    tmp_path, fresh_market_state, config, monkeypatch
):
    class PaperBrokerMustNotExist:
        def __init__(self, *args, **kwargs):
            raise AssertionError("observe_only constructed PaperBroker")

    monkeypatch.setattr(evidence, "PaperBroker", PaperBrokerMustNotExist)
    event = process_mes_trend_consolidation_break_evidence(
        state=_state(fresh_market_state),
        cfg=config,
        log_dir=tmp_path,
        recent_bars=_bars(),
        decision=_decision(),
        flatness_snapshot=FLAT,
    )[0]
    assert event["mode"] == "observe_only"
    assert event["accepted"] is True
    assert event["paper_order_id"] is None
    assert event["fill_status"] == "NO_FILL"
    assert event["normal_runtime_gate"]["failed_gates"] == ["REGIME_NOT_FULL"]


def test_paper_sim_creates_pending_paper_order_never_tradovate(
    tmp_path, fresh_market_state, config, monkeypatch
):
    from execution.tradovate_broker import TradovateBroker

    monkeypatch.setattr(
        TradovateBroker,
        "execute_bracket",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("MES proof lane touched Tradovate")
        ),
    )
    event = process_mes_trend_consolidation_break_evidence(
        state=_state(fresh_market_state),
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=_bars(),
        decision=_decision(),
        flatness_snapshot=FLAT,
    )[0]
    assert event["mode"] == "paper_sim"
    assert event["fill_status"] == "PENDING"
    assert event["broker_route"] == "PaperBroker"
    assert event["paper_order_id"].startswith("PAPER-")


def test_memory_critical_blocks_new_pending_paper_order(
    tmp_path, fresh_market_state, config, monkeypatch
):
    monkeypatch.setattr(
        evidence,
        "read_critical_memory_block",
        lambda: {"level": "CRITICAL", "reason": "derived headroom exhausted"},
    )
    event = process_mes_trend_consolidation_break_evidence(
        state=_state(fresh_market_state),
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=_bars(),
        decision=_decision(),
        flatness_snapshot=FLAT,
    )[0]

    assert event["accepted"] is False
    assert event["paper_order_id"] is None
    assert "MEMORY_CRITICAL" in event["rejection_reason"]


def test_absent_flatness_evidence_fails_closed(
    tmp_path, fresh_market_state, config
):
    event = process_mes_trend_consolidation_break_evidence(
        state=_state(fresh_market_state),
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=_bars(),
        decision=_decision(),
    )[0]
    assert event["accepted"] is False
    assert event["paper_order_id"] is None
    assert event["structural_isolation_status"] == "UNCONFIRMED_FAIL_CLOSED"
    assert "STRUCTURAL_ISOLATION_UNCONFIRMED_FAIL_CLOSED" in event["rejection_reason"]


def test_duplicate_candidates_are_suppressed(tmp_path, fresh_market_state, config):
    state = _state(fresh_market_state)
    first = process_mes_trend_consolidation_break_evidence(
        state=state,
        cfg=config,
        log_dir=tmp_path,
        recent_bars=_bars(),
        decision=_decision(),
        flatness_snapshot=FLAT,
    )
    second = process_mes_trend_consolidation_break_evidence(
        state=state,
        cfg=config,
        log_dir=tmp_path,
        recent_bars=_bars(),
        decision=_decision(),
        flatness_snapshot=FLAT,
    )
    assert [row["event"] for row in first] == ["CANDIDATE"]
    assert second == []
    assert len(evidence_path(tmp_path).read_text().splitlines()) == 1


def test_pending_order_fills_and_resolves_with_stop_first_ambiguity(
    tmp_path, fresh_market_state, config
):
    candidate = process_mes_trend_consolidation_break_evidence(
        state=_state(fresh_market_state),
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=_bars(),
        decision=_decision(),
        flatness_snapshot=FLAT,
    )[0]
    paper_id = candidate["paper_order_id"]

    # Gap through the short stop-entry. The same causal bar touches both target
    # and stop; pessimistic PaperBroker resolution must book the stop first.
    fill_bar = _state(
        fresh_market_state,
        ts_offset=1,
        ohlc=OHLCData(open=100.0, high=105.0, low=93.0, close=94.0, timeframe="15"),
    )
    emitted = process_mes_trend_consolidation_break_evidence(
        state=fill_bar,
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    )
    assert [row["event"] for row in emitted] == ["FILL", "OUTCOME"]
    fill = emitted[0]
    outcome = emitted[1]
    assert fill["paper_order_id"] == paper_id
    assert fill["actual_entry"] == pytest.approx(100.0)
    assert outcome["paper_order_id"] == paper_id
    assert outcome["result"] == "LOSS"
    assert outcome["exit_reason"] == "STOP_HIT"
    assert outcome["broker_route"] == "PaperBroker"
    assert outcome["commission_dollars"] == pytest.approx(1.48)
    assert outcome["net_dollars"] < outcome["gross_dollars"]


def test_pending_stop_order_survives_restart_once_without_duplicate(
    tmp_path, fresh_market_state, config
):
    first = process_mes_trend_consolidation_break_evidence(
        state=_state(fresh_market_state),
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=_bars(),
        decision=_decision(),
        flatness_snapshot=FLAT,
    )[0]
    paper_id = first["paper_order_id"]

    # Simulate restart by relying only on persisted JSON state on the next call.
    fill_bar = _state(
        fresh_market_state,
        ts_offset=1,
        ohlc=OHLCData(open=102.0, high=102.5, low=100.5, close=101.0, timeframe="15"),
    )
    emitted = process_mes_trend_consolidation_break_evidence(
        state=fill_bar,
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    )
    assert [row["event"] for row in emitted] == ["FILL"]
    assert emitted[0]["paper_order_id"] == paper_id

    # Replaying the same bar after the fill must not create a second FILL or a
    # duplicate candidate/order.
    replay = process_mes_trend_consolidation_break_evidence(
        state=fill_bar,
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    )
    assert replay == []
    rows = [json.loads(line) for line in evidence_path(tmp_path).read_text().splitlines()]
    assert [row["event"] for row in rows].count("FILL") == 1
    assert len({row.get("paper_order_id") for row in rows if row.get("paper_order_id")}) == 1


def test_cancelled_pending_order_does_not_resurrect(
    tmp_path, fresh_market_state, config
):
    first = process_mes_trend_consolidation_break_evidence(
        state=_state(fresh_market_state),
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=_bars(),
        decision=_decision(),
        flatness_snapshot=FLAT,
    )[0]
    no_trigger_bar = _state(
        fresh_market_state,
        ts_offset=1,
        ohlc=OHLCData(open=102.0, high=103.0, low=101.5, close=102.5, timeframe="15"),
    )
    no_fill = process_mes_trend_consolidation_break_evidence(
        state=no_trigger_bar,
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    )[0]
    assert no_fill["event"] == "NO_FILL"
    assert no_fill["paper_order_id"] == first["paper_order_id"]

    later_trigger = _state(
        fresh_market_state,
        ts_offset=2,
        ohlc=OHLCData(open=100.0, high=101.0, low=99.0, close=99.5, timeframe="15"),
    )
    assert process_mes_trend_consolidation_break_evidence(
        state=later_trigger,
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    ) == []
    rows = [json.loads(line) for line in evidence_path(tmp_path).read_text().splitlines()]
    assert [row["event"] for row in rows] == ["CANDIDATE", "NO_FILL"]


def test_filled_position_restores_and_resolves_under_same_owner(
    tmp_path, fresh_market_state, config
):
    candidate = process_mes_trend_consolidation_break_evidence(
        state=_state(fresh_market_state),
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=_bars(),
        decision=_decision(),
        flatness_snapshot=FLAT,
    )[0]
    paper_id = candidate["paper_order_id"]
    fill_bar = _state(
        fresh_market_state,
        ts_offset=1,
        ohlc=OHLCData(open=102.0, high=102.5, low=100.5, close=101.0, timeframe="15"),
    )
    assert [row["event"] for row in process_mes_trend_consolidation_break_evidence(
        state=fill_bar,
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    )] == ["FILL"]

    exit_bar = _state(
        fresh_market_state,
        ts_offset=2,
        ohlc=OHLCData(open=100.0, high=100.5, low=93.5, close=94.0, timeframe="15"),
    )
    outcome = process_mes_trend_consolidation_break_evidence(
        state=exit_bar,
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    )[0]
    assert outcome["event"] == "OUTCOME"
    assert outcome["result"] == "WIN"
    assert outcome["paper_order_id"] == paper_id
    assert outcome["lane"] == "trend_consolidation_break"
    assert outcome["broker_route"] == "PaperBroker"


def test_corrupt_state_fails_closed_by_forgetting_pending_without_order(
    tmp_path, fresh_market_state, config
):
    evidence.state_path(tmp_path).write_text("{not json")
    emitted = process_mes_trend_consolidation_break_evidence(
        state=_state(fresh_market_state, ts_offset=1),
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    )
    assert emitted == []
    assert not evidence_path(tmp_path).exists()


def test_next_bar_no_trigger_records_no_fill(tmp_path, fresh_market_state, config):
    process_mes_trend_consolidation_break_evidence(
        state=_state(fresh_market_state),
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=_bars(),
        decision=_decision(),
        flatness_snapshot=FLAT,
    )
    no_trigger_bar = _state(
        fresh_market_state,
        ts_offset=1,
        ohlc=OHLCData(open=102.0, high=103.0, low=101.5, close=102.5, timeframe="15"),
    )
    emitted = process_mes_trend_consolidation_break_evidence(
        state=no_trigger_bar,
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    )
    assert [row["event"] for row in emitted] == ["NO_FILL"]
    assert emitted[0]["paper_order_id"].startswith("PAPER-")
    assert emitted[0]["reason"] == "ENTRY_NOT_TRIGGERED"


def test_monitor_reports_required_metrics(tmp_path):
    rows = [
        {"event": "CANDIDATE", "accepted": True, "fill_status": "PENDING"},
        {"event": "FILL", "fill_status": "FILLED"},
        {
            "event": "OUTCOME",
            "result": "WIN",
            "net_dollars": 10.0,
            "net_ticks": 8.0,
            "direction": "SHORT",
            "session": "new_york",
            "entry_ts": "2026-07-16T18:00:00+00:00",
        },
        {
            "event": "OUTCOME",
            "result": "LOSS",
            "net_dollars": -5.0,
            "net_ticks": -4.0,
            "direction": "LONG",
            "session": "london",
            "entry_ts": "2026-08-01T18:00:00+00:00",
        },
    ]
    evidence_path(tmp_path).write_text("".join(json.dumps(row) + "\n" for row in rows))
    report = summarize_lane(tmp_path)
    assert report["candidate_count"] == 1
    assert report["fill_count"] == 1
    assert report["wins"] == 1
    assert report["losses"] == 1
    assert report["profit_factor"] == 2.0
    assert report["expectancy_dollars"] == 2.5
    assert report["excluding_largest_winner"]["net_dollars"] == -5.0


def test_monitor_separates_observe_only_from_terminal_no_fill(tmp_path):
    rows = [
        {
            "event": "CANDIDATE",
            "accepted": True,
            "mode": "observe_only",
            "fill_status": "NO_FILL",
        },
        {
            "event": "CANDIDATE",
            "accepted": False,
            "mode": "paper_sim",
            "fill_status": "NO_FILL",
            "rejection_reason": (
                "LANE_ORDER_OR_POSITION_ALREADY_OPEN; "
                "STRUCTURAL_ISOLATION_UNCONFIRMED_FAIL_CLOSED"
            ),
        },
        {
            "event": "NO_FILL",
            "fill_status": "NO_FILL",
            "reason": "ENTRY_NOT_TRIGGERED",
        },
    ]
    evidence_path(tmp_path).write_text("".join(json.dumps(row) + "\n" for row in rows))

    report = summarize_lane(tmp_path)

    assert report["evidence_file_exists"] is True
    assert report["mode_counts"] == {"observe_only": 1, "paper_sim": 1}
    assert report["candidate_fill_statuses"] == {"NO_FILL": 2}
    assert report["observe_only_no_order_count"] == 1
    assert report["terminal_no_fill_count"] == 1
    assert report["terminal_no_fill_reasons"] == {"ENTRY_NOT_TRIGGERED": 1}
    assert report["rejection_reasons"] == {
        "LANE_ORDER_OR_POSITION_ALREADY_OPEN": 1,
        "STRUCTURAL_ISOLATION_UNCONFIRMED_FAIL_CLOSED": 1,
    }
    assert report["no_fill_count"] == 3


def test_paperbroker_pending_restore_preserves_id_without_changing_market_behavior():
    from execution.broker_interface import BracketOrder
    from execution.paper_broker import NextBarOHLC, PaperBroker

    order = BracketOrder(
        instrument="MES",
        direction="SHORT",
        entry=100.0,
        stop=104.0,
        target=92.0,
        rr_ratio=2.0,
        strategy="trend_consolidation_break",
        contracts=1,
    )
    restored = PaperBroker(entry_fill_model="stop_market", pessimistic_both_hit=True)
    restored.restore_pending_stop_entry(order, paper_order_id="PAPER-test")
    assert restored.has_pending_entry() is True
    cancelled = restored.resolve_position(NextBarOHLC(open=102.0, high=103.0, low=101.0))
    assert cancelled.result == "CANCELLED"
    assert cancelled.paper_order_id == "PAPER-test"
    assert restored.has_pending_entry() is False

    normal = PaperBroker(slippage_ticks=0.0, pessimistic_both_hit=True)
    fill = normal.execute_bracket(order, market_price=100.0)
    assert fill.result == "OPEN"
    assert fill.paper_order_id.startswith("PAPER-")
    assert normal.has_pending_entry() is False


def test_bars_held_counts_each_processed_bar_exactly_once(
    tmp_path, fresh_market_state, config
):
    process_mes_trend_consolidation_break_evidence(
        state=_state(fresh_market_state),
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=_bars(),
        decision=_decision(),
        flatness_snapshot=FLAT,
    )
    fill_bar = _state(
        fresh_market_state,
        ts_offset=1,
        ohlc=OHLCData(open=102.0, high=102.5, low=100.5, close=101.0, timeframe="15"),
    )
    assert [row["event"] for row in process_mes_trend_consolidation_break_evidence(
        state=fill_bar,
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    )] == ["FILL"]
    hold_bar = _state(
        fresh_market_state,
        ts_offset=2,
        ohlc=OHLCData(open=101.0, high=102.0, low=100.0, close=101.5, timeframe="15"),
    )
    assert process_mes_trend_consolidation_break_evidence(
        state=hold_bar,
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    ) == []
    exit_bar = _state(
        fresh_market_state,
        ts_offset=3,
        ohlc=OHLCData(open=100.0, high=100.5, low=93.5, close=94.0, timeframe="15"),
    )
    outcome = process_mes_trend_consolidation_break_evidence(
        state=exit_bar,
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    )[0]
    assert outcome["event"] == "OUTCOME"
    # fill bar + hold bar + exit bar — the fill bar must be counted once, not
    # twice (the pre-fix double _resolve_position inflated this to 4).
    assert outcome["bars_held"] == 3


def test_runner_track_never_arms_off_the_fill_bars_own_extreme(
    tmp_path, fresh_market_state, config
):
    process_mes_trend_consolidation_break_evidence(
        state=_state(fresh_market_state),
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=_bars(),
        decision=_decision(),
        flatness_snapshot=FLAT,
    )
    # SHORT entry 100.75 (fills 100.5 with 1-tick slippage), R = 3.5. The fill
    # bar itself runs 1.6R favourable without touching the 93.75 target — the
    # runner track must NOT arm from this bar's own extreme (intra-bar
    # look-ahead); it may only arm on the NEXT bar, from prior-bar extremes.
    fill_bar = _state(
        fresh_market_state,
        ts_offset=1,
        ohlc=OHLCData(open=102.0, high=102.5, low=95.0, close=96.0, timeframe="15"),
    )
    assert [row["event"] for row in process_mes_trend_consolidation_break_evidence(
        state=fill_bar,
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    )] == ["FILL"]
    rows = [json.loads(line) for line in evidence_path(tmp_path).read_text().splitlines()]
    assert [row["event"] for row in rows].count("RUNNER_MOVE") == 0

    hold_bar = _state(
        fresh_market_state,
        ts_offset=2,
        ohlc=OHLCData(open=96.0, high=97.0, low=95.5, close=96.5, timeframe="15"),
    )
    assert process_mes_trend_consolidation_break_evidence(
        state=hold_bar,
        cfg=_paper_cfg(config),
        log_dir=tmp_path,
        recent_bars=[],
        decision=_decision(),
        flatness_snapshot=FLAT,
    ) == []
    rows = [json.loads(line) for line in evidence_path(tmp_path).read_text().splitlines()]
    moves = [row for row in rows if row["event"] == "RUNNER_MOVE"]
    assert len(moves) == 1
    assert moves[0]["to"] == pytest.approx(96.75)  # 95.0 max-fav + 0.5R trail
