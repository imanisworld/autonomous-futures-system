from __future__ import annotations

from execution.trailing import compute_trailed_stop


def test_before_activation_returns_original_stop():
    # LONG entry 100 stop 90 (R=10). Favourable only +0.5R -> not yet trailing.
    stop, trailing = compute_trailed_stop(
        is_long=True, entry=100, original_stop=90, max_favorable=105, activation_r=1.0, trail_r=0.5)
    assert trailing is False
    assert stop == 90


def test_long_trails_half_R_behind_favorable():
    # +1.5R favourable (high 115) -> trail 0.5R(5) behind -> 110.
    stop, trailing = compute_trailed_stop(
        is_long=True, entry=100, original_stop=90, max_favorable=115, activation_r=1.0, trail_r=0.5)
    assert trailing is True
    assert stop == 110


def test_short_trails_above_favorable():
    # SHORT entry 100 stop 110 (R=10). Favourable low 85 (+1.5R) -> trail to 85+5=90.
    stop, trailing = compute_trailed_stop(
        is_long=False, entry=100, original_stop=110, max_favorable=85, activation_r=1.0, trail_r=0.5)
    assert trailing is True
    assert stop == 90


def test_never_loosens_past_original_stop():
    # Just barely activated; trailed level would sit below the original stop ->
    # clamp to the original stop (never loosen).
    stop, trailing = compute_trailed_stop(
        is_long=True, entry=100, original_stop=90, max_favorable=110, activation_r=1.0, trail_r=2.0)
    assert trailing is True
    assert stop == 90  # 110 - 2R(20) = 90, not below


def test_zero_risk_returns_original_no_trailing():
    stop, trailing = compute_trailed_stop(
        is_long=True, entry=100, original_stop=100, max_favorable=120)
    assert trailing is False
    assert stop == 100
