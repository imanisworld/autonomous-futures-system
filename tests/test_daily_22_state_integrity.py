"""Fail-closed persisted-state proofs for the multi-day Daily 2-2 paper lane."""
from __future__ import annotations

import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from config.settings import load_config
from context import daily_22_state_integrity as guard
from context import daily_22_swing_collector as lane

ET = ZoneInfo("America/New_York")


def _cfg(epoch="2026-09-09T00:00:00+00:00"):
    cfg = copy.copy(load_config())
    cfg.wide_stop_ledger_mode = "paper_sim"
    cfg.wide_stop_ledger_epoch_start = epoch
    return cfg


def _valid_state(cfg):
    epoch = lane._epoch(cfg)
    return lane._empty_state(epoch)


def _write_state(tmp_path, cfg, state):
    path = lane._state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def test_missing_state_is_allowed_only_for_fresh_empty_campaign(tmp_path):
    guard.assert_state_integrity(tmp_path, _cfg())

    audit = lane._audit_path(tmp_path)
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text('{"collector_event":"CANDIDATE"}\n', encoding="utf-8")
    with pytest.raises(
        guard.DailySwingStateIntegrityError,
        match="state_missing_with_existing_evidence",
    ):
        guard.assert_state_integrity(tmp_path, _cfg())


def test_corrupt_state_blocks_instead_of_resetting_to_fresh_5k(tmp_path):
    path = lane._state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(
        guard.DailySwingStateIntegrityError,
        match="unreadable_or_corrupt",
    ):
        guard.assert_state_integrity(tmp_path, _cfg())


def test_wrong_epoch_state_blocks_instead_of_becoming_flat(tmp_path):
    old = _cfg("2026-09-08T00:00:00+00:00")
    _write_state(tmp_path, old, _valid_state(old))
    with pytest.raises(
        guard.DailySwingStateIntegrityError,
        match="epoch_mismatch",
    ):
        guard.assert_state_integrity(tmp_path, _cfg("2026-09-09T00:00:00+00:00"))


def test_valid_flat_state_passes(tmp_path):
    cfg = _cfg()
    _write_state(tmp_path, cfg, _valid_state(cfg))
    guard.assert_state_integrity(tmp_path, cfg)


def test_valid_open_multiday_position_passes(tmp_path):
    cfg = _cfg()
    state = _valid_state(cfg)
    state["position"] = {
        "candidate_key": "2026-09-09|DAILY_22_CONTINUATION_FIRST_BREAK|LONG",
        "direction": "LONG",
        "planned_entry": 25000.0,
        "entry": 25000.25,
        "stop": 24600.0,
        "target": 25800.0,
        "actual_rr": 2.0,
        "planned_risk_dollars": 800.5,
        "entry_time": datetime(2026, 9, 9, 9, 35, tzinfo=ET).isoformat(),
        "paper_order_id": "PAPER-proof",
        "mae_points": 0.0,
        "mfe_points": 0.0,
    }
    _write_state(tmp_path, cfg, state)
    guard.assert_state_integrity(tmp_path, cfg)


def test_impossible_open_bracket_blocks(tmp_path):
    cfg = _cfg()
    state = _valid_state(cfg)
    state["position"] = {
        "direction": "LONG",
        "entry": 25000.0,
        "stop": 25100.0,
        "target": 25800.0,
        "entry_time": datetime(2026, 9, 9, 9, 35, tzinfo=ET).isoformat(),
    }
    _write_state(tmp_path, cfg, state)
    with pytest.raises(
        guard.DailySwingStateIntegrityError,
        match="invalid_bracket",
    ):
        guard.assert_state_integrity(tmp_path, cfg)
