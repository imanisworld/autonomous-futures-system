"""
tests/test_main_resolution.py

Integration coverage for optional next-bar paper fill resolution.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from journal.journal_logger import JournalLogger
from main import load_next_bar, main


def fresh_market_state() -> dict:
    with open("data/sample_market_state.json", encoding="utf-8") as handle:
        state = json.load(handle)
    # Recent 10:30 ET opening-window timestamp so this test exercises fill
    # resolution, not the live session-window or staleness gate.
    ts = datetime.now(timezone.utc).replace(hour=14, minute=30, second=0, microsecond=0)
    if ts > datetime.now(timezone.utc):
        ts -= timedelta(days=1)
    while ts.weekday() >= 5:
        ts -= timedelta(days=1)
    now = ts.isoformat()
    state["timestamp"] = now
    state["ohlc"]["bar_start"] = now
    state["volume"]["current_bar"] = 5000
    state["volume"]["relative"] = 1.3
    return state



def risk_rules_without_position_sizing(tmp_path):
    """Temp risk_rules for the MES resolution fixtures.

    Also restores the pre-isolated-lane permissive universe (MES+MNQ, all
    concepts paper-eligible). The shipped risk_rules.yaml is narrowed to the
    isolated MNQ orb_breakout lane, and data/sample_market_state.json is an
    MES state — these tests prove main()'s fill-resolution and journaling
    wiring, so they construct their own permissive config explicitly rather
    than depending on the shipped default being broad.
    """
    import yaml
    from tests.conftest import (
        _PERMISSIVE_ENABLED_CONCEPTS,
        _PERMISSIVE_STRATEGY_STATUS,
    )

    rules = yaml.safe_load(Path("risk_rules.yaml").read_text())
    rules["position_sizing"]["position_sizing_enabled"] = False
    rules["data_quality"]["max_staleness_seconds"] = 999999
    rules["instruments"]["allowed"] = ["MES", "MNQ"]
    rules["instruments"]["required"] = ["MES", "MNQ"]
    rules["strategy"]["enabled_concepts"] = list(_PERMISSIVE_ENABLED_CONCEPTS)
    rules["strategy_permission_gate"]["strategy_status"] = dict(
        _PERMISSIVE_STRATEGY_STATUS
    )
    rules["daily_limits"]["max_trades_per_day"] = 9999

    path = tmp_path / "risk_rules_no_sizing.yaml"
    path.write_text(yaml.safe_dump(rules))
    return path

def test_load_next_bar_reads_high_low(tmp_path):
    path = tmp_path / "next_bar.json"
    path.write_text(json.dumps({"high": 19600.0, "low": 19490.0}))

    next_bar = load_next_bar(str(path))

    assert next_bar.high == 19600.0
    assert next_bar.low == 19490.0


def test_main_resolves_target_and_journals_outcome(tmp_path, monkeypatch):
    market_state_path = tmp_path / "market_state.json"
    market_state_path.write_text(json.dumps(fresh_market_state()))
    next_bar_path = tmp_path / "next_bar.json"
    # orb_reclaim: entry=5898.5, stop=5888.5, target=5923.5 — low stays clear
    # of stop, high clears target for a clean WIN.
    next_bar_path.write_text(json.dumps({"high": 5925.0, "low": 5895.0}))  # MES scale — hits target

    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    risk_rules_path = risk_rules_without_position_sizing(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "main.py",
            "--market-state",
            str(market_state_path),
            "--next-bar",
            str(next_bar_path),
            "--risk-rules",
            str(risk_rules_path),
        ],
    )

    assert main() == 0

    journal = JournalLogger(log_dir=str(tmp_path / "logs"))
    summary = journal.get_summary()
    daily = journal.get_daily_state()

    assert summary["trades"] == 1
    assert summary["wins"] == 1
    assert summary["losses"] == 0
    assert daily.trade_count == 1
    assert daily.consecutive_losses == 0
    assert daily.has_open_position is False


def test_main_resolves_stop_and_daily_state_tracks_loss(tmp_path, monkeypatch):
    state = fresh_market_state()
    market_state_path = tmp_path / "market_state.json"
    market_state_path.write_text(json.dumps(state))
    next_bar_path = tmp_path / "next_bar.json"
    # orb_reclaim: entry=5898.5, stop=5888.5, target=5923.5 — high stays clear
    # of target, low clears stop for a clean LOSS.
    next_bar_path.write_text(json.dumps({"high": 5895.0, "low": 5885.0}))  # MES scale — hits stop

    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    risk_rules_path = risk_rules_without_position_sizing(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "main.py",
            "--market-state",
            str(market_state_path),
            "--next-bar",
            str(next_bar_path),
            "--risk-rules",
            str(risk_rules_path),
        ],
    )

    assert main() == 0

    journal = JournalLogger(log_dir=str(tmp_path / "logs"))
    summary = journal.get_summary()
    daily = journal.get_daily_state()

    assert summary["trades"] == 1
    assert summary["wins"] == 0
    assert summary["losses"] == 1
    assert daily.consecutive_losses == 1
    assert daily.has_open_position is False
