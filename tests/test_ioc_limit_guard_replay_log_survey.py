"""Tests for scripts/ioc_limit_guard_replay_log_survey.py.

The survey's headline is a zero, and a zero is the easy thing to produce by
accident. These pin the ways it could be wrong: unpaired outcomes counted as
clean, deviation never measured, or an invalid fill silently skipped.

Synthetic journals only — the real survey reads gitignored `logs/`.
"""
from __future__ import annotations

import json

import pytest

from scripts import ioc_limit_guard_replay_log_survey as survey


def _trade(direction: str, entry: float, stop: float, target: float) -> str:
    return json.dumps({
        "decision": "TRADE",
        "setup": {"direction": direction, "entry": entry, "stop": stop, "target": target},
    })


def _outcome(entry_price: float, pnl: float = 10.0, exit_reason: str = "STOP_HIT") -> str:
    return json.dumps({
        "type": "OUTCOME",
        "outcome": {"entry_price": entry_price, "pnl_dollars": pnl, "exit_reason": exit_reason},
    })


@pytest.fixture
def journal(tmp_path):
    def write(*lines: str) -> str:
        root = tmp_path / "run"
        (root / "MNQ").mkdir(parents=True, exist_ok=True)
        (root / "MNQ" / "journal_2026-01-01.jsonl").write_text("\n".join(lines) + "\n")
        return str(root)
    return write


def test_a_fill_inside_its_bracket_is_clean(journal):
    root = journal(_trade("SHORT", 20000.0, 20012.5, 19960.0), _outcome(19998.0))
    result = survey.survey(root)
    assert result["paired_trades"] == 1
    assert result["invalid_at_fill"] == 0


def test_a_fill_beyond_its_own_stop_is_counted_with_its_pnl(journal):
    root = journal(_trade("SHORT", 20000.0, 20012.5, 19960.0), _outcome(20050.0, pnl=84.0))
    result = survey.survey(root)
    assert result["invalid_at_fill"] == 1
    assert result["pnl_invalid_at_fill"] == 84.0
    assert result["invalid_exit_reasons"] == {"STOP_HIT": 1}


def test_an_outcome_with_no_preceding_trade_is_reported_not_ignored(journal):
    """A zero built on unattributable rows would be a false clean bill."""
    root = journal(_outcome(20050.0))
    result = survey.survey(root)
    assert result["unattributable_outcomes"] == 1
    assert result["paired_trades"] == 0


def test_each_trade_is_consumed_by_only_one_outcome(journal):
    root = journal(
        _trade("SHORT", 20000.0, 20012.5, 19960.0),
        _outcome(19998.0),
        _outcome(20050.0),  # no TRADE of its own — must not reuse the one above
    )
    result = survey.survey(root)
    assert result["paired_trades"] == 1
    assert result["unattributable_outcomes"] == 1
    assert result["invalid_at_fill"] == 0


def test_deviation_is_measured_so_a_zero_is_falsifiable(journal):
    """If fills never deviate, an invalid count of 0 means nothing."""
    root = journal(
        _trade("SHORT", 20000.0, 20012.5, 19960.0), _outcome(19998.0),   # 8 ticks
        _trade("LONG", 19000.0, 18990.0, 19040.0), _outcome(19000.0),    # 0 ticks
    )
    deviation = survey.survey(root)["detachment_ticks"]
    assert deviation["count"] == 2
    assert deviation["nonzero"] == 1
    assert deviation["max"] == 8.0


def test_outcomes_without_a_fill_price_are_skipped_entirely(journal):
    root = journal(
        _trade("SHORT", 20000.0, 20012.5, 19960.0),
        json.dumps({"type": "OUTCOME", "outcome": {"pnl_dollars": 5.0}}),
    )
    result = survey.survey(root)
    assert result["paired_trades"] == 0
    assert result["unattributable_outcomes"] == 0


def test_long_and_short_use_opposite_bracket_orderings():
    assert survey.bracket_valid_at_fill("LONG", 100.0, 90.0, 110.0)
    assert not survey.bracket_valid_at_fill("LONG", 85.0, 90.0, 110.0)
    assert survey.bracket_valid_at_fill("SHORT", 100.0, 110.0, 90.0)
    assert not survey.bracket_valid_at_fill("SHORT", 115.0, 110.0, 90.0)
