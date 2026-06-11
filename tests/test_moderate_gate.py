"""
tests/test_moderate_gate.py

Tests for the MODERATE-trend admission experiment (off by default).

Two redundant gates normally require a full EMA stack (STRONG): the
trend-strength gate and the pre-setup EMA-stack-alignment gate. Per-instrument
config flags can admit MODERATE bars past both so the setups become the deciders:

  - allow_moderate_pullback → PULLBACK bars (stack intact, dip to ema9)
  - allow_moderate_early    → EARLY bars (trend forming, ema55 not flipped)

This isolates the feature's behavior so the work stays cleanly separable from
other in-flight changes on the branch.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from context.trend import classify_trend, moderate_subtype
from context.market_context import TrendData
from strategy.signal_engine import DecisionEngine


# ── Pure classifier: PULLBACK vs EARLY vs None ───────────────────────────────

class TestModerateSubtype:
    def test_strong_stack_is_not_moderate(self):
        # close > ema9 > ema21 > ema55 → STRONG, not a MODERATE subtype
        assert classify_trend(105, 104, 103, 102)[1] == "STRONG"
        assert moderate_subtype(105, 104, 103, 102) is None

    def test_pullback_up_stack_intact_close_below_ema9(self):
        # stack intact (104>103>102) but close dipped below ema9 → PULLBACK
        assert classify_trend(103.5, 104, 103, 102)[1] == "MODERATE"
        assert moderate_subtype(103.5, 104, 103, 102) == "PULLBACK"

    def test_early_up_slow_ema_not_flipped(self):
        # close>ema9>ema21 but ema55 still above ema21 (not flipped) → EARLY
        assert classify_trend(106, 105, 104, 104.5)[1] == "MODERATE"
        assert moderate_subtype(106, 105, 104, 104.5) == "EARLY"

    def test_pullback_down(self):
        assert moderate_subtype(102.5, 102, 103, 104) == "PULLBACK"

    def test_early_down(self):
        # close<ema9<ema21 but ema55 still below ema21 → EARLY
        assert moderate_subtype(101, 102, 103, 102.5) == "EARLY"

    def test_missing_ema_returns_none(self):
        assert moderate_subtype(100, None, 99, 98) is None


# ── Engine gate: _admit_moderate honors the per-instrument flags ─────────────

def _state(fresh, *, strength="MODERATE", kind="PULLBACK", direction="UP",
           vwap_side="above"):
    """Clone fresh_market_state with a specific trend subtype + VWAP side."""
    st = replace(
        fresh,
        trend=TrendData(direction=direction, strength=strength,
                        ema_fast_above_slow=True, moderate_kind=kind),
    )
    st = replace(st, vwap=replace(fresh.vwap, price_vs_vwap=vwap_side))
    return st


class TestAdmitModerate:
    def test_default_off_admits_nothing(self, config, fresh_market_state):
        engine = DecisionEngine(config=config)  # no flags set
        assert engine._admit_moderate(_state(fresh_market_state, kind="PULLBACK")) is False
        assert engine._admit_moderate(_state(fresh_market_state, kind="EARLY")) is False

    def test_pullback_flag_admits_pullback_only(self, config, fresh_market_state):
        cfg = replace(config, allow_moderate_pullback={"MNQ": True})
        engine = DecisionEngine(config=cfg)
        assert engine._admit_moderate(_state(fresh_market_state, kind="PULLBACK")) is True
        assert engine._admit_moderate(_state(fresh_market_state, kind="EARLY")) is False

    def test_early_flag_admits_early(self, config, fresh_market_state):
        cfg = replace(config, allow_moderate_early={"MNQ": True})
        engine = DecisionEngine(config=cfg)
        assert engine._admit_moderate(_state(fresh_market_state, kind="EARLY")) is True
        # pullback flag not set → pullback still blocked
        assert engine._admit_moderate(_state(fresh_market_state, kind="PULLBACK")) is False

    def test_both_flags_admit_all_moderate(self, config, fresh_market_state):
        cfg = replace(config,
                      allow_moderate_pullback={"MNQ": True},
                      allow_moderate_early={"MNQ": True})
        engine = DecisionEngine(config=cfg)
        assert engine._admit_moderate(_state(fresh_market_state, kind="PULLBACK")) is True
        assert engine._admit_moderate(_state(fresh_market_state, kind="EARLY")) is True

    def test_strong_is_never_a_moderate_admission(self, config, fresh_market_state):
        cfg = replace(config, allow_moderate_pullback={"MNQ": True})
        engine = DecisionEngine(config=cfg)
        # STRONG bars take the normal STRONG path, never the moderate admission
        assert engine._admit_moderate(
            _state(fresh_market_state, strength="STRONG", kind=None)) is False

    def test_per_instrument_isolation(self, config, fresh_market_state):
        # Flag on for MES only — MNQ state must not be admitted.
        cfg = replace(config, allow_moderate_pullback={"MES": True})
        engine = DecisionEngine(config=cfg)
        assert engine._admit_moderate(_state(fresh_market_state, kind="PULLBACK")) is False

    def test_vwap_alignment_requirement(self, config, fresh_market_state):
        cfg = replace(config,
                      allow_moderate_pullback={"MNQ": True},
                      moderate_pullback_require_vwap_align={"MNQ": True})
        engine = DecisionEngine(config=cfg)
        # UP trend + price above VWAP → admitted
        assert engine._admit_moderate(
            _state(fresh_market_state, direction="UP", vwap_side="above")) is True
        # UP trend + price below VWAP → blocked by the alignment requirement
        assert engine._admit_moderate(
            _state(fresh_market_state, direction="UP", vwap_side="below")) is False
