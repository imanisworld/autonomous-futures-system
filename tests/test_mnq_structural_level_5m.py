"""
tests/test_mnq_structural_level_5m.py

Pure-logic coverage for context/mnq_structural_level_5m.py. This module is
NOT wired into webhook/runner.py and has no config/env fields, drift-guard
pins, or execution/shadow tracker -- see docs/mnq-structural-level-5m-study-
2026-07-13.md. The replay study found the fixed-target exit robustly
negative and the runner-exit variant fails a 2-tick slippage stress test, so
per the operator's own gate ("only proceed to live integration if replay...
survives both halves with realistic fills") this lane was not built out
beyond the pure detector + replay study. These tests exist so the module
that DOES live in the repo (used only by scripts/structural_level_5m_study.py)
is not merely research-script-tested.
"""
from __future__ import annotations

import os

import pytest

from context.mnq_structural_level_5m import (
    classify_context,
    detect_candidates,
    is_structural_level_5m_candidate,
    mapped_levels,
    session_bucket,
    structural_level_5m_directions,
    structural_level_5m_min_rr,
    structural_level_5m_mode,
    structural_level_5m_sessions,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in [
        "STRUCTURAL_LEVEL_5M_MODE", "STRUCTURAL_LEVEL_5M_INSTRUMENTS",
        "STRUCTURAL_LEVEL_5M_DIRECTIONS", "STRUCTURAL_LEVEL_5M_ENTRY_MODES",
        "STRUCTURAL_LEVEL_5M_SESSIONS", "STRUCTURAL_LEVEL_5M_MIN_RR",
        "STRUCTURAL_LEVEL_5M_MAX_STOP_POINTS", "STRUCTURAL_LEVEL_5M_LEVELS",
        "FIVE_MIN_FEED_ENABLED",
    ]:
        monkeypatch.delenv(key, raising=False)


def test_default_mode_is_off():
    assert structural_level_5m_mode() == "off"


def test_default_min_rr_and_sessions():
    assert structural_level_5m_min_rr() == 1.5
    assert structural_level_5m_sessions() == frozenset({"overnight", "premarket", "rth"})
    assert structural_level_5m_directions() == frozenset({"long", "short"})


def test_candidate_gate_requires_mode_on(monkeypatch):
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    assert is_structural_level_5m_candidate("MNQ", "5", cfg=None) is False
    monkeypatch.setenv("STRUCTURAL_LEVEL_5M_MODE", "shadow")
    assert is_structural_level_5m_candidate("MNQ", "5", cfg=None) is True


def test_candidate_gate_rejects_mes_and_15m(monkeypatch):
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    monkeypatch.setenv("STRUCTURAL_LEVEL_5M_MODE", "shadow")
    assert is_structural_level_5m_candidate("MES", "5", cfg=None) is False
    assert is_structural_level_5m_candidate("MNQ", "15", cfg=None) is False


def test_session_bucket_mapping():
    assert session_bucket("asian") == "overnight"
    assert session_bucket("london") == "premarket"
    assert session_bucket("new_york") == "rth"
    assert session_bucket("off_hours") is None
    assert session_bucket("garbage") is None


def test_mapped_levels_only_reads_present_non_null_fields():
    bar = {
        "previous_day_high": 30076.75, "previous_day_low": 29677.5,
        "orb_high": 29675.0, "orb_low": None, "gex_flip": None,
    }
    levels = mapped_levels(bar)
    assert levels == {
        "previous_day_high": 30076.75, "previous_day_low": 29677.5,
        "orb_high": 29675.0,
    }


def test_mapped_levels_never_invents_mid_hvi_mp_or_overnight_fields():
    bar = {"mid_upper": 100.0, "hvi": 5.0, "mp": 200.0, "overnight_high": 1.0}
    assert mapped_levels(bar) == {}


@pytest.mark.parametrize("direction,trend,strength,cond,expected", [
    ("long", "UP", "STRONG", "TRENDING", "aligned"),
    ("long", "DOWN", "STRONG", "TRENDING", "opposed"),
    ("long", "DOWN", "WEAK", "TRENDING", "neutral"),
    ("long", None, None, "RANGE_BOUND", "unclear"),
    ("short", "DOWN", "STRONG", "TRENDING", "aligned"),
    ("short", "UP", "STRONG", "TRENDING", "opposed"),
])
def test_classify_context(direction, trend, strength, cond, expected):
    assert classify_context(
        direction, trend_direction=trend, trend_strength=strength, market_condition=cond,
    ) == expected


def _bar(ts, o, h, l, c, **levels):
    row = {"timestamp": ts, "open": o, "high": h, "low": l, "close": c}
    row.update(levels)
    return row


def test_reclaim_accepts_with_correct_geometry_when_context_neutral():
    levels = {"previous_day_high": 30076.75, "previous_day_low": 29677.5,
              "orb_high": 29675.0, "orb_low": 29607.0}
    window = [_bar("t0", 29685, 29699.5, 29682.75, 29685.5, **levels)]
    current = _bar("t1", 29685, 29685, 29657.75, 29673.0, **levels)  # closes below PDL
    reclaim_bar = _bar("t2", 29674.25, 29694.75, 29666.5, 29688.75, **levels)  # closes back above

    cands = detect_candidates(
        window=window + [current], current_bar=reclaim_bar, session="london",
        trend_direction="UP", trend_strength="WEAK", market_condition="TRENDING",
    )
    accepted = [
        c for c in cands if c["decision"] == "ACCEPTED" and c["setup_type"] == "reclaim"
        and c["source_level_name"] == "previous_day_low"
    ]
    assert len(accepted) == 1
    c = accepted[0]
    assert c["direction"] == "long"
    assert c["entry"] == pytest.approx(29688.75)
    assert c["stop"] < c["entry"]
    assert c["next_mapped_level"] == "previous_day_high"
    assert c["rr"] >= structural_level_5m_min_rr()


def test_reclaim_rejected_when_context_opposed():
    levels = {"previous_day_high": 30076.75, "previous_day_low": 29677.5,
              "orb_high": 29675.0, "orb_low": 29607.0}
    window = [_bar("t0", 29685, 29699.5, 29682.75, 29685.5, **levels)]
    current = _bar("t1", 29685, 29685, 29657.75, 29673.0, **levels)
    reclaim_bar = _bar("t2", 29674.25, 29694.75, 29666.5, 29688.75, **levels)

    cands = detect_candidates(
        window=window + [current], current_bar=reclaim_bar, session="london",
        trend_direction="DOWN", trend_strength="STRONG", market_condition="TRENDING",
    )
    reclaim_rows = [c for c in cands if c["setup_type"] == "reclaim" and c["direction"] == "long"
                    and c["source_level_name"] == "previous_day_low"]
    assert reclaim_rows and reclaim_rows[0]["decision"] == "REJECTED"
    assert reclaim_rows[0]["rejection_reason"] == "CONTEXT_OPPOSED"


def test_break_and_retest_short_matches_real_2026_07_13_sequence():
    """Reconstructed from real box data (logs/tf5m/bars_MNQ_2026-07-13.jsonl +
    the 12:15 UTC journal context) -- see the manual validation section of
    docs/mnq-structural-level-5m-study-2026-07-13.md."""
    levels = {"previous_day_high": 30076.75, "previous_day_low": 29677.5,
              "orb_high": 29675.0, "orb_low": 29607.0}
    bars = [
        _bar("2026-07-13T12:20:00+00:00", 29718.75, 29718.75, 29694.0, 29697.5, **levels),
        _bar("2026-07-13T12:25:00+00:00", 29697.0, 29699.5, 29682.75, 29685.5, **levels),
        _bar("2026-07-13T12:30:00+00:00", 29685.0, 29685.0, 29657.75, 29673.0, **levels),
    ]
    current = _bar("2026-07-13T12:35:00+00:00", 29672.75, 29676.5, 29655.25, 29675.0, **levels)

    cands = detect_candidates(
        window=bars, current_bar=current, session="london",
        trend_direction="DOWN", trend_strength="STRONG", market_condition="TRENDING",
    )
    accepted = [c for c in cands if c["decision"] == "ACCEPTED"
                and c["setup_type"] == "break_and_retest" and c["direction"] == "short"]
    assert len(accepted) == 1
    c = accepted[0]
    assert c["source_level_name"] == "previous_day_low"
    assert c["entry"] == pytest.approx(29675.0)
    assert c["stop"] == pytest.approx(29679.5)
    assert c["next_mapped_level"] == "orb_low"


def test_no_mapped_level_rejects_cleanly():
    current = _bar("t0", 100, 101, 99, 100.5)
    cands = detect_candidates(window=[], current_bar=current, session="new_york")
    assert len(cands) == 1
    assert cands[0]["decision"] == "REJECTED"
    assert cands[0]["rejection_reason"] == "NO_MAPPED_LEVEL"


def test_off_hours_session_always_rejected_regardless_of_config():
    levels = {"previous_day_high": 30076.75}
    current = _bar("t0", 100, 101, 99, 100.5, **levels)
    cands = detect_candidates(window=[], current_bar=current, session="off_hours")
    assert len(cands) == 1
    assert cands[0]["rejection_reason"] == "SESSION_DISABLED"


def test_stop_too_wide_rejects(monkeypatch):
    monkeypatch.setenv("STRUCTURAL_LEVEL_5M_MAX_STOP_POINTS", "1.0")
    levels = {"previous_day_high": 30076.75, "previous_day_low": 29677.5,
              "orb_high": 29675.0, "orb_low": 29607.0}
    window = [_bar("t0", 29685, 29699.5, 29682.75, 29685.5, **levels)]
    current = _bar("t1", 29685, 29685, 29657.75, 29673.0, **levels)
    reclaim_bar = _bar("t2", 29674.25, 29694.75, 29566.5, 29688.75, **levels)  # wide swing low

    cands = detect_candidates(
        window=window + [current], current_bar=reclaim_bar, session="london",
        trend_direction="UP", trend_strength="WEAK", market_condition="TRENDING",
    )
    reclaim_rows = [c for c in cands if c["setup_type"] == "reclaim" and c["direction"] == "long"
                    and c["source_level_name"] == "previous_day_low"]
    assert reclaim_rows and reclaim_rows[0]["rejection_reason"] == "STOP_TOO_WIDE"
