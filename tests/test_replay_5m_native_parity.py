from __future__ import annotations

import json
from dataclasses import replace

import pytest

from replay.replay_engine import ReplayEngine
from strategy.signal_engine import DecisionEngine, DecisionOutput
from tests.test_e2e_scenarios import _base_config


def _write_candle(path, *, timeframe: str) -> None:
    row = {
        "timestamp": "2026-01-06T14:30:00+00:00",
        "instrument": "MNQ",
        "session": "new_york",
        "open": 19094.0,
        "high": 19096.0,
        "low": 19093.0,
        "close": 19095.5,
        "volume": 1000,
        "avg_volume": 1000,
        "vwap": 19094.5,
        "orb_high": 19096.0,
        "orb_low": 19090.0,
        "orb_status": "inside",
        "market_condition": "TRENDING",
        "trend_direction": "UP",
        "trend_strength": "STRONG",
        "previous_day_high": 19100.0,
        "previous_day_low": 19000.0,
        "previous_day_close": 19050.0,
        "timeframe": timeframe,
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def _capture_replay_state(monkeypatch, cfg, tmp_path, *, timeframe: str):
    candle_path = tmp_path / f"one-{timeframe}.jsonl"
    _write_candle(candle_path, timeframe=timeframe)
    seen = {}

    def _evaluate(_engine, state, daily_state):
        seen["canonical_4hr_only"] = state.canonical_4hr_only
        seen["bar_history_5m"] = list(state.bar_history_5m)
        return DecisionOutput(
            timestamp=state.timestamp,
            instrument=state.instrument,
            session=state.session,
            decision="NO_TRADE",
            reason="routing parity capture",
        )

    monkeypatch.setattr(DecisionEngine, "evaluate", _evaluate)
    ReplayEngine(cfg, log_dir=str(tmp_path / "replay")).run(candle_path)
    return seen


@pytest.mark.parametrize("strategy", ["strat_4hr_retrigger", "strat_322_first_live"])
def test_replay_marks_five_minute_native_strategy_as_canonical(strategy, monkeypatch, tmp_path):
    """Replay must present the same canonical 5m state that live runner does."""
    cfg = replace(
        _base_config(tmp_path),
        enabled_concepts=[strategy],
        expected_timeframe_minutes=5,
        strategy_permission_gate_enabled=False,
    )

    seen = _capture_replay_state(monkeypatch, cfg, tmp_path, timeframe="5m")

    assert seen["canonical_4hr_only"] is True
    assert len(seen["bar_history_5m"]) == 1


def test_replay_does_not_leak_canonical_flag_into_normal_15m_lane(monkeypatch, tmp_path):
    """Enabling a 5m-native strategy must not convert normal 15m replay bars."""
    cfg = replace(
        _base_config(tmp_path),
        enabled_concepts=["strat_4hr_retrigger"],
        expected_timeframe_minutes=15,
        strategy_permission_gate_enabled=False,
    )

    seen = _capture_replay_state(monkeypatch, cfg, tmp_path, timeframe="15m")

    assert seen["canonical_4hr_only"] is False
    assert seen["bar_history_5m"] == []


def test_incidental_5m_tag_is_not_canonical_when_replay_contract_is_15m(monkeypatch, tmp_path):
    """Both the candle and replay's declared authoritative timeframe must be 5m."""
    cfg = replace(
        _base_config(tmp_path),
        enabled_concepts=["strat_322_first_live"],
        expected_timeframe_minutes=15,
        strategy_permission_gate_enabled=False,
    )

    seen = _capture_replay_state(monkeypatch, cfg, tmp_path, timeframe="5m")

    assert seen["canonical_4hr_only"] is False
    assert len(seen["bar_history_5m"]) == 1
