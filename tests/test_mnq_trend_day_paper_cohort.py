"""Safety and contract tests for the MNQ existing-family trend-day paper cohort."""
from __future__ import annotations

import ast
import copy
import json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import context.mnq_trend_day_paper_cohort as lane
from config.settings import ConfigError, _validate_config
from context.market_context import OHLCData, TrendData

EPOCH = "2026-09-01T00:00:00+00:00"
BASE_TS = "2026-09-21T14:30:00+00:00"


def _cfg(config, mode="paper_sim", epoch=EPOCH):
    return replace(
        config,
        mnq_trend_day_paper_mode=mode,
        mnq_trend_day_paper_epoch_start=epoch,
    )


def _state(
    fresh_market_state,
    ts: str,
    *,
    close=100.0,
    high=101.0,
    low=99.0,
    instrument="MNQ",
    timeframe="15",
    market_condition="TRENDING",
):
    state = copy.deepcopy(fresh_market_state)
    state.instrument = instrument
    state.timestamp = datetime.fromisoformat(ts)
    state.session = "new_york"
    state.ohlc = OHLCData(
        open=close,
        high=high,
        low=low,
        close=close,
        timeframe=timeframe,
    )
    state.market_condition = market_condition
    state.trend = TrendData(direction="UP", strength=1)
    state.structural_regime = {
        "structural_market_condition": "STRUCTURAL_TREND_UP",
        "structural_direction": "LONG",
    }
    return state


def _cand(strategy, *, entry=100.0, stop=90.0, target=120.0, direction="LONG"):
    return {
        "strategy": strategy,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
    }


def _events(log_dir):
    path = lane.evidence_path(log_dir)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _ts(minutes: int) -> str:
    return (datetime.fromisoformat(BASE_TS) + timedelta(minutes=minutes)).isoformat()


# activation / fail-closed


def test_default_off_creates_no_files(config, fresh_market_state, tmp_path):
    out = lane.process_bar(
        state=_state(fresh_market_state, BASE_TS),
        cfg=config,
        log_dir=tmp_path,
        shadow_candidates=[_cand("ema_pullback_trend")],
    )
    assert out is None
    assert not lane.lane_dir(tmp_path).exists()


@pytest.mark.parametrize("value", ["on", "paper", "true", "1", "demo", "observe_only"])
def test_only_exact_paper_sim_token_activates(config, value):
    assert lane.mode(_cfg(config, mode=value)) == "off"
    assert lane.is_active(_cfg(config, mode=value)) is False


def test_paper_sim_requires_offset_aware_epoch(config):
    assert not lane.is_active(_cfg(config, epoch=None))
    assert not lane.is_active(_cfg(config, epoch="2026-09-01T00:00:00"))
    assert not lane.is_active(_cfg(config, epoch="garbage"))
    assert lane.is_active(_cfg(config))


def test_settings_validate_mode_and_epoch(config):
    base = replace(config, max_staleness_seconds=900)
    with pytest.raises(ConfigError, match="MNQ_TREND_DAY_PAPER_MODE"):
        _validate_config(_cfg(base, mode="demo"))
    with pytest.raises(ConfigError, match="MNQ_TREND_DAY_PAPER_EPOCH_START is required"):
        _validate_config(_cfg(base, epoch=None))
    with pytest.raises(ConfigError, match="UTC offset"):
        _validate_config(_cfg(base, epoch="2026-09-01T00:00:00"))
    with pytest.raises(ConfigError, match="ISO-8601"):
        _validate_config(_cfg(base, epoch="nope"))
    _validate_config(_cfg(base))
    _validate_config(_cfg(base, mode="off", epoch=None))


def test_proof_pins_registered():
    from ops.live_box_guard import PROOF_CRITICAL_RUNTIME_OVERRIDES

    assert "MNQ_TREND_DAY_PAPER_MODE" in PROOF_CRITICAL_RUNTIME_OVERRIDES
    assert "MNQ_TREND_DAY_PAPER_EPOCH_START" in PROOF_CRITICAL_RUNTIME_OVERRIDES


def test_module_has_no_external_broker_route():
    tree = ast.parse(Path(lane.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    for mod in imported:
        assert "tradovate" not in mod.lower(), mod
        assert "broker_interface" not in mod, mod
        assert not mod.startswith("execution.broker"), mod


# frozen population


def test_population_is_existing_four_families_long_only():
    expected = {
        "ema_pullback_trend",
        "impulse_first_pullback_observed",
        "strat_22_continuation_observed",
        "trend_consolidation_break_observed",
    }
    assert set(lane.STRATEGIES) == expected

    seen = set()
    rows = [
        _cand(name, entry=100.0 + i)
        for i, name in enumerate(sorted(expected))
    ]
    rows += [
        _cand("strat_22_reversal_observed", entry=110.0),
        _cand("ema_pullback_trend", entry=111.0, stop=121.0, target=90.0, direction="SHORT"),
    ]
    picks = lane.candidates_for_bar(rows, "2026-09-21", seen)
    assert {row["strategy"] for row in picks} == expected
    assert {row["direction"] for row in picks} == {"LONG"}


def test_geometry_and_target_are_not_rewritten():
    original = _cand(
        "ema_pullback_trend",
        entry=101.25,
        stop=96.0,
        target=111.75,
    )
    pick = lane.candidates_for_bar([original], "2026-09-21", set())[0]
    assert pick["entry"] == original["entry"]
    assert pick["stop"] == original["stop"]
    assert pick["target"] == original["target"]


def test_same_geometry_dedupes_within_day_but_not_next_day():
    candidate = _cand("ema_pullback_trend")
    seen = set()
    assert len(lane.candidates_for_bar([candidate], "2026-09-21", seen)) == 1
    assert lane.candidates_for_bar([candidate], "2026-09-21", seen) == []
    assert len(lane.candidates_for_bar([candidate], "2026-09-22", seen)) == 1


# runtime behavior


def test_two_families_are_independent_hypothetical_lanes(
    config, fresh_market_state, tmp_path
):
    cfg = _cfg(config)
    candidates = [
        _cand("ema_pullback_trend"),
        _cand("impulse_first_pullback_observed", entry=100.5, stop=90.5, target=120.5),
    ]
    lane.process_bar(
        state=_state(fresh_market_state, BASE_TS, close=100.25),
        cfg=cfg,
        log_dir=tmp_path,
        shadow_candidates=candidates,
    )
    state = lane.load_state(tmp_path)
    assert state["positions"]["ema_pullback_trend"] is not None
    assert state["positions"]["impulse_first_pullback_observed"] is not None
    assert state["positions"]["strat_22_continuation_observed"] is None


def test_ioc_no_fill_is_recorded_and_does_not_consume_daily_cap(
    config, fresh_market_state, tmp_path
):
    cfg = _cfg(config)
    candidate = _cand("ema_pullback_trend", entry=100.0)
    lane.process_bar(
        state=_state(fresh_market_state, BASE_TS, close=109.0, high=110.0, low=108.0),
        cfg=cfg,
        log_dir=tmp_path,
        shadow_candidates=[candidate],
    )
    event = _events(tmp_path)[-1]
    assert event["event"] == "NO_FILL"
    assert lane.load_state(tmp_path)["trade_counts"]["ema_pullback_trend"]["fills"] == 0


def test_open_position_resolves_on_strictly_later_bar(
    config, fresh_market_state, tmp_path
):
    cfg = _cfg(config)
    candidate = _cand("ema_pullback_trend")
    lane.process_bar(
        state=_state(fresh_market_state, BASE_TS, close=100.0),
        cfg=cfg,
        log_dir=tmp_path,
        shadow_candidates=[candidate],
    )
    # Target is reached only on the later bar.
    lane.process_bar(
        state=_state(fresh_market_state, _ts(15), close=119.0, high=121.0, low=99.0),
        cfg=cfg,
        log_dir=tmp_path,
        shadow_candidates=[],
    )
    events = _events(tmp_path)
    assert [event["event"] for event in events] == ["CANDIDATE_FILLED", "OUTCOME"]
    assert events[-1]["result"] == "WIN"
    assert lane.load_state(tmp_path)["positions"]["ema_pullback_trend"] is None


def test_daily_cap_is_three_filled_trades_per_independent_lane(
    config, fresh_market_state, tmp_path
):
    cfg = _cfg(config)
    strategy = "ema_pullback_trend"

    for i in range(3):
        entry = 100.0 + i
        candidate = _cand(
            strategy,
            entry=entry,
            stop=entry - 10.0,
            target=entry + 10.0,
        )
        lane.process_bar(
            state=_state(fresh_market_state, _ts(i * 30), close=entry),
            cfg=cfg,
            log_dir=tmp_path,
            shadow_candidates=[candidate],
        )
        lane.process_bar(
            state=_state(
                fresh_market_state,
                _ts(i * 30 + 15),
                close=entry + 9.0,
                high=entry + 11.0,
                low=entry,
            ),
            cfg=cfg,
            log_dir=tmp_path,
            shadow_candidates=[],
        )

    fourth = _cand(strategy, entry=104.0, stop=94.0, target=114.0)
    lane.process_bar(
        state=_state(fresh_market_state, _ts(90), close=104.0),
        cfg=cfg,
        log_dir=tmp_path,
        shadow_candidates=[fourth],
    )
    last = _events(tmp_path)[-1]
    assert last["event"] == "CANDIDATE_SKIPPED_DAILY_CAP"
    assert last["filled_trades_today"] == 3
    assert lane.load_state(tmp_path)["trade_counts"][strategy]["fills"] == 3


def test_busy_lane_does_not_average_down(config, fresh_market_state, tmp_path):
    cfg = _cfg(config)
    strategy = "ema_pullback_trend"
    lane.process_bar(
        state=_state(fresh_market_state, BASE_TS),
        cfg=cfg,
        log_dir=tmp_path,
        shadow_candidates=[_cand(strategy)],
    )
    lane.process_bar(
        state=_state(fresh_market_state, _ts(15)),
        cfg=cfg,
        log_dir=tmp_path,
        shadow_candidates=[_cand(strategy, entry=100.5, stop=90.5, target=120.5)],
    )
    assert _events(tmp_path)[-1]["event"] == "CANDIDATE_SKIPPED_BUSY"
    assert lane.load_state(tmp_path)["trade_counts"][strategy]["fills"] == 1


def test_wrong_instrument_or_timeframe_is_ignored(
    config, fresh_market_state, tmp_path
):
    cfg = _cfg(config)
    candidate = _cand("ema_pullback_trend")
    assert lane.process_bar(
        state=_state(fresh_market_state, BASE_TS, instrument="MES"),
        cfg=cfg,
        log_dir=tmp_path,
        shadow_candidates=[candidate],
    ) is None
    assert lane.process_bar(
        state=_state(fresh_market_state, BASE_TS, timeframe="5"),
        cfg=cfg,
        log_dir=tmp_path,
        shadow_candidates=[candidate],
    ) is None


def test_pre_epoch_bar_does_not_create_evidence(config, fresh_market_state, tmp_path):
    cfg = _cfg(config, epoch="2026-10-01T00:00:00+00:00")
    out = lane.process_bar(
        state=_state(fresh_market_state, BASE_TS),
        cfg=cfg,
        log_dir=tmp_path,
        shadow_candidates=[_cand("ema_pullback_trend")],
    )
    assert out["lane_result"] == "PRE_EPOCH"
    assert not lane.lane_dir(tmp_path).exists()
