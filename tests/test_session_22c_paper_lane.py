"""Session-scoped 2-2 continuation forward paper lane (prereg H6/H7, 2026-09-21).

Proves: default OFF with no I/O; exact-token + epoch activation; settings
validation; Asia sub-lane = EMA-aligned only, label ignored, 1.5R re-anchor;
Sunday sub-lane = window only, no filter, 1.0R re-anchor; Sunday precedence
over Asia inside the window; one open position per sub-lane; day-roll expiry;
per-day/per-lane geometry dedupe; runner hook inert by default; no broker route.
"""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime

import pytest

import context.session_22c_paper_lane as lane
from config.settings import ConfigError, _validate_config
from context.market_context import OHLCData, TrendData

EPOCH = "2026-09-01T00:00:00+00:00"
# 2026-09-15 is a Tuesday: 23:00Z is the Asia session, not the Sunday window.
ASIA_TS = "2026-09-15T23:00:00+00:00"
# 2026-09-20 is a Sunday: 22:15Z is inside the reopen window.
SUNDAY_TS = "2026-09-20T22:15:00+00:00"
CAND = {"strategy": "strat_22_continuation_observed", "direction": "LONG", "entry": 100.0, "stop": 90.0, "target": 120.0}


def _cfg(config, mode="paper_sim", epoch=EPOCH):
    return replace(config, session_22c_paper_mode=mode, session_22c_paper_epoch_start=epoch)


def _state(fresh_market_state, ts: str, *, session="asian", close=100.0, high=101.0, low=99.0,
           trend="UP", market_condition="RANGE_BOUND", instrument="MNQ", timeframe="15"):
    s = copy.deepcopy(fresh_market_state)
    s.instrument = instrument
    s.timestamp = datetime.fromisoformat(ts)
    s.session = session
    s.ohlc = OHLCData(open=close, high=high, low=low, close=close, timeframe=timeframe)
    s.market_condition = market_condition
    s.trend = TrendData(direction=trend, strength=1)
    s.structural_regime = {"structural_market_condition": None, "structural_direction": None}
    return s


def _events(log_dir):
    p = lane.evidence_path(log_dir)
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


# ── activation ──────────────────────────────────────────────────────────────


def test_default_off_and_unset_env_is_off(config, monkeypatch):
    monkeypatch.delenv(lane.MODE_ENV, raising=False)
    assert lane.mode(config) == "off"
    assert lane.mode(None) == "off"
    assert not lane.is_active(config)


@pytest.mark.parametrize("value", ["on", "paper", "papersim", "true", "1", "demo", "observe_only"])
def test_only_exact_paper_sim_token_activates(config, value):
    assert lane.mode(_cfg(config, mode=value)) == "off"
    assert not lane.is_active(_cfg(config, mode=value))


def test_paper_sim_without_valid_epoch_is_inactive(config):
    assert not lane.is_active(_cfg(config, epoch=None))
    assert not lane.is_active(_cfg(config, epoch="2026-09-01T00:00:00"))  # naive
    assert not lane.is_active(_cfg(config, epoch="garbage"))
    assert lane.is_active(_cfg(config))


def test_default_off_creates_no_files(config, fresh_market_state, tmp_path):
    out = lane.process_bar(state=_state(fresh_market_state, ASIA_TS), cfg=config, log_dir=tmp_path, shadow_candidates=[CAND])
    assert out is None
    assert not lane.lane_dir(tmp_path).exists()


def test_settings_reject_unknown_mode_and_bad_epoch(config):
    base = replace(config, max_staleness_seconds=900)  # the shared fixture's 0 is itself invalid
    with pytest.raises(ConfigError, match="SESSION_22C_PAPER_MODE"):
        _validate_config(_cfg(base, mode="demo"))
    with pytest.raises(ConfigError, match="SESSION_22C_PAPER_EPOCH_START is required"):
        _validate_config(_cfg(base, epoch=None))
    with pytest.raises(ConfigError, match="UTC offset"):
        _validate_config(_cfg(base, epoch="2026-09-01T00:00:00"))
    with pytest.raises(ConfigError, match="ISO-8601"):
        _validate_config(_cfg(base, epoch="nope"))
    _validate_config(_cfg(base))  # valid
    _validate_config(_cfg(base, mode="off", epoch=None))  # default


def test_proof_pins_registered():
    from ops.live_box_guard import PROOF_CRITICAL_RUNTIME_OVERRIDES
    assert "SESSION_22C_PAPER_MODE" in PROOF_CRITICAL_RUNTIME_OVERRIDES
    assert "SESSION_22C_PAPER_EPOCH_START" in PROOF_CRITICAL_RUNTIME_OVERRIDES


def test_module_has_no_broker_route():
    """Paper-only by construction: the module never imports a live broker path."""
    import ast
    from pathlib import Path

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


# ── definition ──────────────────────────────────────────────────────────────


def test_sunday_window_boundaries():
    assert lane.in_sunday_window("2026-09-20T22:00:00+00:00")
    assert lane.in_sunday_window("2026-09-21T00:45:00+00:00")
    assert not lane.in_sunday_window("2026-09-21T01:00:00+00:00")
    assert not lane.in_sunday_window("2026-09-20T21:45:00+00:00")
    assert not lane.in_sunday_window("2026-09-19T23:00:00+00:00")  # Saturday


def test_lane_for_bar_precedence():
    assert lane.lane_for_bar(SUNDAY_TS, "asian") == "sunday"
    assert lane.lane_for_bar(ASIA_TS, "asian") == "asia"
    assert lane.lane_for_bar(ASIA_TS, "london") is None
    assert lane.lane_for_bar("2026-09-15T15:00:00+00:00", "new_york") is None


def test_reanchor_targets():
    assert lane.reanchor_target(CAND, 1.5) == 115.0
    assert lane.reanchor_target(CAND, 1.0) == 110.0
    short = {**CAND, "direction": "SHORT", "stop": 110.0, "target": 80.0}
    assert lane.reanchor_target(short, 1.5) == 85.0


def test_asia_requires_ema_alignment_and_ignores_label():
    ctx_up = {"trend": {"direction": "UP"}, "market_condition": "DEAD"}
    ctx_down = {"trend": {"direction": "DOWN"}, "market_condition": "TRENDING"}
    ctx_side = {"trend": {"direction": "SIDEWAYS"}, "market_condition": "TRENDING"}
    assert [c["target"] for c in lane.lane_candidates("asia", ctx_up, [CAND], "d", set())] == [115.0]
    assert lane.lane_candidates("asia", ctx_down, [CAND], "d", set()) == []
    assert lane.lane_candidates("asia", ctx_side, [CAND], "d", set()) == []


def test_sunday_has_no_filter_and_uses_1r():
    ctx = {"trend": {"direction": "DOWN"}, "market_condition": "DEAD"}
    picks = lane.lane_candidates("sunday", ctx, [CAND], "d", set())
    assert len(picks) == 1 and picks[0]["target"] == 110.0 and picks[0]["target_r"] == 1.0


def test_only_22_continuation_family():
    other = {**CAND, "strategy": "ema_pullback_trend"}
    ctx = {"trend": {"direction": "UP"}}
    assert lane.lane_candidates("asia", ctx, [other], "d", set()) == []
    assert lane.lane_candidates("sunday", ctx, [other], "d", set()) == []


def test_dedupe_is_per_day_per_lane_and_ignores_target():
    ctx = {"trend": {"direction": "UP"}}
    seen: set = set()
    assert len(lane.lane_candidates("asia", ctx, [CAND], "d1", seen)) == 1
    assert lane.lane_candidates("asia", ctx, [{**CAND, "target": 150.0}], "d1", seen) == []  # same geometry
    assert len(lane.lane_candidates("sunday", ctx, [CAND], "d1", seen)) == 1  # other lane
    assert len(lane.lane_candidates("asia", ctx, [CAND], "d2", seen)) == 1  # other day


# ── runtime ─────────────────────────────────────────────────────────────────


def test_asia_bar_fills_at_1_5r_and_resolves_on_target(config, fresh_market_state, tmp_path):
    cfg = _cfg(config)
    lane.process_bar(state=_state(fresh_market_state, ASIA_TS), cfg=cfg, log_dir=tmp_path, shadow_candidates=[CAND])
    st = lane.load_state(tmp_path)
    assert st["positions"]["asia"] is not None and st["positions"]["sunday"] is None
    assert st["positions"]["asia"]["target"] == 115.0
    # next bar tags 115 but not 90 → WIN at the re-anchored 1.5R target
    nxt = _state(fresh_market_state, "2026-09-15T23:15:00+00:00", close=114.0, high=116.0, low=99.5)
    lane.process_bar(state=nxt, cfg=cfg, log_dir=tmp_path, shadow_candidates=[])
    ev = _events(tmp_path)
    assert [e["event"] for e in ev] == ["CANDIDATE_FILLED", "OUTCOME"]
    assert ev[-1]["lane"] == "asia" and ev[-1]["result"] == "WIN" and ev[-1]["target_r"] == 1.5
    assert lane.load_state(tmp_path)["positions"]["asia"] is None


def test_asia_bar_with_wrong_ema_side_does_nothing(config, fresh_market_state, tmp_path):
    cfg = _cfg(config)
    out = lane.process_bar(state=_state(fresh_market_state, ASIA_TS, trend="DOWN"), cfg=cfg, log_dir=tmp_path, shadow_candidates=[CAND])
    assert out["events"] == [] and lane.load_state(tmp_path)["positions"]["asia"] is None


def test_asia_ignores_market_condition_label(config, fresh_market_state, tmp_path):
    cfg = _cfg(config)
    for label in ("DEAD", "CHOPPY", "RANGE_BOUND", "TRENDING"):
        d = tmp_path / label
        lane.process_bar(state=_state(fresh_market_state, ASIA_TS, market_condition=label), cfg=cfg, log_dir=d, shadow_candidates=[CAND])
        assert lane.load_state(d)["positions"]["asia"] is not None, label


def test_sunday_bar_fills_at_1r_regardless_of_trend(config, fresh_market_state, tmp_path):
    cfg = _cfg(config)
    lane.process_bar(state=_state(fresh_market_state, SUNDAY_TS, trend="DOWN", market_condition="DEAD"), cfg=cfg, log_dir=tmp_path, shadow_candidates=[CAND])
    st = lane.load_state(tmp_path)
    assert st["positions"]["sunday"] is not None and st["positions"]["asia"] is None
    assert st["positions"]["sunday"]["target"] == 110.0


def test_sunday_and_asia_positions_are_independent(config, fresh_market_state, tmp_path):
    cfg = _cfg(config)
    # Sunday lane fills at 22:15Z Sunday …
    lane.process_bar(state=_state(fresh_market_state, SUNDAY_TS), cfg=cfg, log_dir=tmp_path, shadow_candidates=[CAND])
    # … and at 01:15Z Monday (outside the window, asian session, same observation day) the Asia lane can fill too.
    mon = _state(fresh_market_state, "2026-09-21T01:15:00+00:00")
    lane.process_bar(state=mon, cfg=cfg, log_dir=tmp_path, shadow_candidates=[{**CAND, "entry": 100.5}])
    st = lane.load_state(tmp_path)
    assert st["positions"]["sunday"] is not None and st["positions"]["asia"] is not None


def test_busy_lane_skips_second_candidate(config, fresh_market_state, tmp_path):
    cfg = _cfg(config)
    lane.process_bar(state=_state(fresh_market_state, ASIA_TS), cfg=cfg, log_dir=tmp_path, shadow_candidates=[CAND])
    nxt = _state(fresh_market_state, "2026-09-15T23:15:00+00:00")
    lane.process_bar(state=nxt, cfg=cfg, log_dir=tmp_path, shadow_candidates=[{**CAND, "entry": 100.25}])
    assert _events(tmp_path)[-1]["event"] == "CANDIDATE_SKIPPED_BUSY"


def test_open_position_expires_at_day_roll(config, fresh_market_state, tmp_path):
    cfg = _cfg(config)
    lane.process_bar(state=_state(fresh_market_state, ASIA_TS), cfg=cfg, log_dir=tmp_path, shadow_candidates=[CAND])
    roll = _state(fresh_market_state, "2026-09-16T22:15:00+00:00", close=125.0, high=130.0, low=99.5)
    lane.process_bar(state=roll, cfg=cfg, log_dir=tmp_path, shadow_candidates=[])
    ev = _events(tmp_path)[-1]
    assert ev["event"] == "OUTCOME" and ev["result"] == "EXPIRED" and ev["exit_reason"] == "OBSERVATION_DATE_ROLLED"
    assert lane.load_state(tmp_path)["positions"]["asia"] is None


def test_pre_epoch_candidates_recorded_not_traded(config, fresh_market_state, tmp_path):
    cfg = _cfg(config, epoch="2026-12-01T00:00:00+00:00")
    lane.process_bar(state=_state(fresh_market_state, ASIA_TS), cfg=cfg, log_dir=tmp_path, shadow_candidates=[CAND])
    assert _events(tmp_path)[-1]["event"] == "CANDIDATE_PRE_EPOCH"
    assert lane.load_state(tmp_path)["positions"]["asia"] is None


def test_wrong_instrument_or_timeframe_ignored(config, fresh_market_state, tmp_path):
    cfg = _cfg(config)
    assert lane.process_bar(state=_state(fresh_market_state, ASIA_TS, instrument="MES"), cfg=cfg, log_dir=tmp_path, shadow_candidates=[CAND]) is None
    assert lane.process_bar(state=_state(fresh_market_state, ASIA_TS, timeframe="60"), cfg=cfg, log_dir=tmp_path, shadow_candidates=[CAND]) is None
    assert not lane.lane_dir(tmp_path).exists()


def test_invalid_state_fails_closed(config, fresh_market_state, tmp_path):
    lane.lane_dir(tmp_path).mkdir(parents=True)
    lane.state_path(tmp_path).write_text("{not json")
    out = lane.process_bar(state=_state(fresh_market_state, ASIA_TS), cfg=_cfg(config), log_dir=tmp_path, shadow_candidates=[CAND])
    assert out["lane_result"] == "STATE_INVALID"
    assert not lane.evidence_path(tmp_path).exists()


def test_runner_hook_inert_by_default(config):
    from webhook import runner as _r
    src = open(_r.__file__).read()
    assert "session_22c_paper_lane" in src
    assert lane.mode(config) == "off"
