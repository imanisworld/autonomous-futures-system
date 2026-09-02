from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from alert_ranker.scorer import score_setup


NOW = datetime(2026, 9, 1, 10, 0, tzinfo=ZoneInfo("America/New_York"))


def _payload(**overrides):
    data = {
        "ticker": "AAPL",
        "pattern": "2-1-2",
        "direction": "LONG",
        "price": 101.0,
        "vwap": 100.0,
        "ema20": 99.0,
        "volume_ratio": 1.3,
        "iv_rank": 25.0,
    }
    data.update(overrides)
    return data


def test_signa_alignment_opposition_and_missing_do_not_change_actionable_score():
    missing = score_setup(_payload(), now=NOW)
    aligned = score_setup(
        _payload(signa_grade="A", signa_score=95, signa_daily_direction="UP"),
        now=NOW,
    )
    opposed = score_setup(
        _payload(signa_grade="A", signa_score=95, signa_daily_direction="DOWN"),
        now=NOW,
    )
    weak = score_setup(
        _payload(signa_grade="F", signa_score=5, signa_daily_direction="DOWN"),
        now=NOW,
    )

    scores = {missing.score, aligned.score, opposed.score, weak.score}
    assert len(scores) == 1
    assert missing.components["signa"] == 0
    assert aligned.components["signa"] == 0
    assert opposed.components["signa"] == 0
    assert weak.components["signa"] == 0


def test_signa_remains_available_in_raw_telemetry_without_score_authority():
    result = score_setup(
        _payload(signa_grade="A", signa_score=88, signa_daily_direction="UP"),
        now=NOW,
    )

    assert result.raw["signa_grade"] == "A"
    assert result.raw["signa_score"] == 88
    assert result.raw["signa_daily_direction"] == "UP"
    assert result.components["signa"] == 0
