"""
tests/test_main_resolution.py

Integration coverage for optional next-bar paper fill resolution.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from journal.journal_logger import JournalLogger
from main import load_next_bar, main


def fresh_market_state() -> dict:
    with open("data/sample_market_state.json", encoding="utf-8") as handle:
        state = json.load(handle)
    # Fixed 10:30 ET opening-window timestamp so this test exercises fill
    # resolution, not the live session-window gate.
    now = datetime(2026, 5, 29, 14, 30, tzinfo=timezone.utc).isoformat()
    state["timestamp"] = now
    state["ohlc"]["bar_start"] = now
    return state



def risk_rules_without_position_sizing(tmp_path):
    text = Path("risk_rules.yaml").read_text()
    text = re.sub(
        r"position_sizing_enabled: true",
        "position_sizing_enabled: false",
        text,
        count=1,
    )
    text = re.sub(
        r"max_staleness_seconds: 300",
        "max_staleness_seconds: 999999",
        text,
        count=1,
    )
    path = tmp_path / "risk_rules_no_sizing.yaml"
    path.write_text(text)
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
    next_bar_path.write_text(json.dumps({"high": 19600.0, "low": 19490.0}))

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
    next_bar_path.write_text(json.dumps({"high": 19510.0, "low": 19450.0}))

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
