"""Shared stop-width sizing — behavior-preserving extraction guard.

These lock the extracted `apply_stop_multiplier` to the exact output of the
former inline blocks in webhook.runner / replay.replay_engine, so the live and
replay paths stay byte-identical.
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy.stop_sizing import apply_stop_multiplier, round_to_tick


@dataclass
class _Setup:
    direction: str
    entry: float
    stop: float
    target: float
    rr_ratio: float = 0.0


def test_long_widens_stop_and_recomputes_rr():
    s = _Setup("LONG", entry=20000.0, stop=19990.0, target=20022.0)  # risk 10
    applied = apply_stop_multiplier(s, "MNQ", {"MNQ": 2.5})
    assert applied == 2.5
    # raw = 20000 - 2.5*10 = 19975 → tick-rounded (0.25) = 19975.0
    assert s.stop == 19975.0
    # new risk 25, reward 22 → rr 0.88
    assert s.rr_ratio == 0.88


def test_short_widens_stop_upward():
    s = _Setup("SHORT", entry=7400.0, stop=7407.0, target=7386.0)  # risk 7
    applied = apply_stop_multiplier(s, "MES", {"MES": 2.0})
    assert applied == 2.0
    assert s.stop == 7414.0  # 7400 + 2.0*7
    assert s.rr_ratio == 1.0  # reward 14 / new risk 14


def test_multiplier_unset_or_one_is_noop():
    s = _Setup("LONG", entry=20000.0, stop=19990.0, target=20022.0)
    assert apply_stop_multiplier(s, "MNQ", {}) == 1.0
    assert apply_stop_multiplier(s, "MNQ", {"MNQ": 1.0}) == 1.0
    assert apply_stop_multiplier(s, "MNQ", None) == 1.0
    assert s.stop == 19990.0 and s.rr_ratio == 0.0  # untouched


def test_missing_stop_is_noop():
    s = _Setup("LONG", entry=20000.0, stop=None, target=20022.0)
    assert apply_stop_multiplier(s, "MNQ", {"MNQ": 2.5}) == 1.0


def test_zero_risk_is_noop_returns_one():
    s = _Setup("LONG", entry=20000.0, stop=20000.0, target=20022.0)  # risk 0
    assert apply_stop_multiplier(s, "MNQ", {"MNQ": 2.5}) == 1.0
    assert s.stop == 20000.0


def test_round_to_tick_matches_instrument_tick():
    assert round_to_tick(19975.13, "MNQ") == 19975.25  # 0.25 tick
    assert round_to_tick(1850.07, "MGC") == 1850.1      # 0.10 tick
    assert round_to_tick(75.013, "MCL") == 75.01        # 0.01 tick
    assert round_to_tick(19975.13, "MNQ1!") == 19975.25  # root-normalized
