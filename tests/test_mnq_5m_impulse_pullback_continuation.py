"""
tests/test_mnq_5m_impulse_pullback_continuation.py

Pure-logic coverage for research/mnq_5m_impulse_pullback_continuation.py --
STATUS: PROMISING BUT UNPROVEN, short-only, R=1.5, at a 2-tick slippage
baseline (see docs/mnq-5m-impulse-pullback-continuation-study-2026-07-13.md).
The long side and every other R-multiple tested are REJECTED. No shadow/
live integration has been built from this module -- it lives under
research/, not context/, and has no config/env fields, drift-guard pins,
execution tracker, or webhook/runner.py wiring.
"""
from __future__ import annotations

import pytest

from research.mnq_5m_impulse_pullback_continuation import (
    detect_candidates,
    session_bucket,
)


def _bar(ts, o, h, l, c, **fields):
    row = {"timestamp": ts, "open": o, "high": h, "low": l, "close": c}
    row.update(fields)
    return row


def test_session_bucket_mapping():
    assert session_bucket("asian") == "overnight"
    assert session_bucket("london") == "premarket"
    assert session_bucket("new_york") == "rth"
    assert session_bucket("off_hours") is None


def test_off_hours_always_rejected():
    cands = detect_candidates(window=[], current_bar=_bar("t0", 1, 2, 0, 1), session="off_hours")
    assert len(cands) == 1
    assert cands[0]["rejection_reason"] == "SESSION_DISABLED"


def test_long_continuation_accepts_with_correct_geometry():
    impulse = _bar("t0", 100, 102, 99, 101,
                    trend_direction="UP", trend_strength="STRONG", market_condition="TRENDING")
    pullback1 = _bar("t1", 101, 101.5, 99.5, 100.0)
    pullback2 = _bar("t2", 100.0, 100.2, 98.5, 99.0)
    continuation = _bar("t3", 99.0, 102.5, 98.8, 102.0)  # closes above pullback high (101.5)

    cands = detect_candidates(
        window=[impulse, pullback1, pullback2], current_bar=continuation, session="new_york",
        r_multiple=2.0,
    )
    accepted = [c for c in cands if c["decision"] == "ACCEPTED" and c["direction"] == "long"]
    assert len(accepted) == 1
    c = accepted[0]
    assert c["entry"] == pytest.approx(102.0)
    assert c["stop"] == pytest.approx(98.5 - 2.0)  # pullback low - buffer
    risk = c["entry"] - c["stop"]
    assert c["target"] == pytest.approx(c["entry"] + 2.0 * risk)
    assert c["pullback_bars"] == 2


def test_short_continuation_accepts_with_correct_geometry():
    impulse = _bar("t0", 100, 101, 98, 99,
                    trend_direction="DOWN", trend_strength="STRONG", market_condition="TRENDING")
    pullback = _bar("t1", 99, 100.5, 98.8, 100.2)  # rallies against the downtrend
    continuation = _bar("t2", 100.2, 100.4, 97.0, 97.5)  # closes below pullback low (98.8)

    cands = detect_candidates(
        window=[impulse, pullback], current_bar=continuation, session="london", r_multiple=1.5,
    )
    accepted = [c for c in cands if c["decision"] == "ACCEPTED" and c["direction"] == "short"]
    assert len(accepted) == 1
    c = accepted[0]
    assert c["entry"] == pytest.approx(97.5)
    assert c["stop"] == pytest.approx(100.5 + 2.0)  # pullback high + buffer


def test_no_pullback_rejects():
    impulse = _bar("t0", 100, 101, 99, 101,
                    trend_direction="UP", trend_strength="STRONG", market_condition="TRENDING")
    still_trending = _bar("t1", 101, 103, 100.5, 102.5)  # no pullback -- just continues up
    cands = detect_candidates(window=[impulse], current_bar=still_trending, session="new_york")
    long_rows = [c for c in cands if c["direction"] == "long"]
    assert long_rows[0]["decision"] == "REJECTED"
    assert long_rows[0]["rejection_reason"] == "NO_PULLBACK"


def test_pullback_without_established_impulse_rejects():
    weak = _bar("t0", 100, 101, 99, 100,
                trend_direction="UP", trend_strength="WEAK", market_condition="RANGE_BOUND")
    pullback = _bar("t1", 100, 100.2, 98.5, 99.0)
    continuation = _bar("t2", 99.0, 101.5, 98.8, 101.2)
    cands = detect_candidates(window=[weak, pullback], current_bar=continuation, session="new_york")
    long_rows = [c for c in cands if c["direction"] == "long"]
    assert long_rows[0]["decision"] == "REJECTED"
    assert long_rows[0]["rejection_reason"] == "NO_PULLBACK"


def test_pullback_too_long_rejects():
    impulse = _bar("t0", 100, 101, 99, 101,
                    trend_direction="UP", trend_strength="STRONG", market_condition="TRENDING")
    long_pullback = [
        _bar(f"p{i}", 100 - i * 0.1, 100.2 - i * 0.1, 99.5 - i * 0.1, 100 - i * 0.1 - 0.05)
        for i in range(10)  # 10 bars > DEFAULT_MAX_PULLBACK_BARS (8)
    ]
    continuation = _bar("t_end", 99, 101.5, 98.5, 101.4)
    cands = detect_candidates(window=[impulse] + long_pullback, current_bar=continuation, session="new_york")
    long_rows = [c for c in cands if c["direction"] == "long"]
    assert long_rows[0]["decision"] == "REJECTED"
    assert long_rows[0]["rejection_reason"] == "NO_PULLBACK"


def test_cooldown_blocks_immediate_retrigger():
    impulse = _bar("t0", 100, 102, 99, 101,
                    trend_direction="UP", trend_strength="STRONG", market_condition="TRENDING")
    pullback = _bar("t1", 101, 101.2, 99.5, 100.0)
    continuation = _bar("t2", 100.0, 102.5, 99.8, 102.0)

    since = {"long": 0, "short": None}  # just triggered long this cycle
    cands = detect_candidates(
        window=[impulse, pullback], current_bar=continuation, session="new_york",
        bars_since_last_trigger=since,
    )
    long_rows = [c for c in cands if c["direction"] == "long"]
    assert long_rows[0]["decision"] == "REJECTED"
    assert long_rows[0]["rejection_reason"] == "COOLDOWN_ACTIVE"
