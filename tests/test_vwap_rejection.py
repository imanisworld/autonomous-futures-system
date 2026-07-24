"""Causal one-bar-lookback VWAP failed-reclaim (vwap_rejection) detector.

Fixes the proven contradiction documented in
docs/vwap-hold-vs-vwap-rejection-overlap-audit-2026-07-23.md (PR #308):
the prior entry condition required state.vwap.reclaimed == True AND
state.vwap.price_vs_vwap == "below" on the SAME bar — structurally
impossible, since reclaimed can only be True on a bar where price closed
above VWAP. vwap_rejection could never fire.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from context.market_context import MarketState, TrendData, VWAPData
from strategy.signal_engine import DecisionEngine
from strategy.vwap_rejection import advance_vwap_reclaim_state, vwap_trading_day


# ─── Pure state machine: strategy/vwap_rejection.py ─────────────────────────


def test_reclaim_bar_alone_is_not_a_failed_reclaim():
    """The reclaim bar itself: vwap_reclaimed=True, price above VWAP. Not a
    failed reclaim on this same bar (nothing preceded it to fail)."""
    state, is_failed = advance_vwap_reclaim_state(
        price_vs_vwap="above", vwap_reclaimed=True, trading_date="2026-07-24",
        persisted_state=None,
    )
    assert is_failed is False
    assert state == {"trading_date": "2026-07-24", "previous_bar_reclaimed": True}


def test_bar_immediately_after_reclaim_closing_below_is_failed_reclaim():
    """The core fix: prior bar was a genuine reclaim, this bar closes back
    below VWAP -> failed reclaim (rejection)."""
    armed, _ = advance_vwap_reclaim_state(
        price_vs_vwap="above", vwap_reclaimed=True, trading_date="2026-07-24",
        persisted_state=None,
    )
    _, is_failed = advance_vwap_reclaim_state(
        price_vs_vwap="below", vwap_reclaimed=False, trading_date="2026-07-24",
        persisted_state=armed,
    )
    assert is_failed is True


def test_price_above_vwap_for_several_bars_then_below_is_not_a_failed_reclaim():
    """Operator's required case: price sits above VWAP for multiple bars
    (only the FIRST of those was the genuine crossover) before eventually
    closing below. That is a plain trend change, not a failed reclaim —
    only the bar immediately following the reclaim bar itself qualifies."""
    # Bar 1: genuine reclaim.
    s1, _ = advance_vwap_reclaim_state(
        price_vs_vwap="above", vwap_reclaimed=True, trading_date="2026-07-24",
        persisted_state=None,
    )
    # Bar 2: still above, but NOT itself a new crossover (already above).
    s2, is_failed_bar2 = advance_vwap_reclaim_state(
        price_vs_vwap="above", vwap_reclaimed=False, trading_date="2026-07-24",
        persisted_state=s1,
    )
    assert is_failed_bar2 is False
    # Bar 3: closes below VWAP. The immediately preceding bar (bar 2) was NOT
    # a reclaim bar, so this must NOT be flagged as a failed reclaim.
    _, is_failed_bar3 = advance_vwap_reclaim_state(
        price_vs_vwap="below", vwap_reclaimed=False, trading_date="2026-07-24",
        persisted_state=s2,
    )
    assert is_failed_bar3 is False


def test_trading_date_boundary_resets_the_memory():
    """A reclaim on one VWAP trading day must not carry into the next day's
    session — VWAP itself resets, so 'previous bar was reclaimed' from the
    old session has no meaning against the freshly-reset anchor."""
    armed, _ = advance_vwap_reclaim_state(
        price_vs_vwap="above", vwap_reclaimed=True, trading_date="2026-07-24",
        persisted_state=None,
    )
    _, is_failed = advance_vwap_reclaim_state(
        price_vs_vwap="below", vwap_reclaimed=False, trading_date="2026-07-25",
        persisted_state=armed,
    )
    assert is_failed is False


def test_vwap_trading_day_resets_at_1800_et_not_midnight_utc():
    # 17:59 ET on 07-24 -> still the 07-23 CME trading day.
    before = datetime(2026, 7, 24, 21, 59, tzinfo=timezone.utc)  # 17:59 ET (EDT, UTC-4)
    after = before + timedelta(minutes=1)  # 18:00 ET -> new trading day
    assert vwap_trading_day(before) == "2026-07-23"
    assert vwap_trading_day(after) == "2026-07-24"


def test_reclaim_and_failed_reclaim_cannot_both_be_true_same_bar():
    """By construction: a bar with vwap_reclaimed=True must have price above
    VWAP (system convention — see VWAPData/state_builder), so it can never
    also satisfy the failed-reclaim condition (price below VWAP) on that
    same bar, regardless of what the prior bar was."""
    for prior_reclaimed in (True, False):
        prior_state = {"trading_date": "2026-07-24", "previous_bar_reclaimed": prior_reclaimed}
        _, is_failed = advance_vwap_reclaim_state(
            price_vs_vwap="above", vwap_reclaimed=True, trading_date="2026-07-24",
            persisted_state=prior_state,
        )
        assert is_failed is False


# ─── DecisionEngine integration: same evaluate() path live and replay share ─


def _with_vwap_rejection_enabled(config):
    if "vwap_rejection" in config.enabled_concepts:
        return config
    return replace(config, enabled_concepts=list(config.enabled_concepts) + ["vwap_rejection"])


def _vwap_state(fresh_market_state, *, price_vs_vwap: str, reclaimed: bool, direction: str = "DOWN") -> MarketState:
    return replace(
        fresh_market_state,
        vwap=VWAPData(value=19495.0, price_vs_vwap=price_vs_vwap, reclaimed=reclaimed, holding=True),
        trend=TrendData(direction=direction, strength="MODERATE", ema_fast_above_slow=(direction == "UP")),
    )


def test_evaluate_reclaim_bar_does_not_set_failed_reclaim_flag(config, fresh_market_state, clean_daily_state):
    engine = DecisionEngine(_with_vwap_rejection_enabled(config))
    state = _vwap_state(fresh_market_state, price_vs_vwap="above", reclaimed=True, direction="UP")
    engine.evaluate(state, clean_daily_state)
    assert state.vwap_failed_reclaim is False


def test_evaluate_bar_after_reclaim_closing_below_sets_failed_reclaim_and_fires(
    config, fresh_market_state, clean_daily_state
):
    """The full live/replay-shared path: DecisionEngine.evaluate() called
    across two consecutive bars, threading the SAME daily_state (exactly
    how webhook/runner.py and replay/replay_engine.py both drive it) —
    proves state_builder-independent parity: whichever pipeline populates
    MarketState.vwap.reclaimed/price_vs_vwap, this produces the identical
    vwap_rejection candidate."""
    engine = DecisionEngine(_with_vwap_rejection_enabled(config))

    bar1_ts = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)
    bar1 = replace(
        _vwap_state(fresh_market_state, price_vs_vwap="above", reclaimed=True, direction="DOWN"),
        timestamp=bar1_ts,
    )
    engine.evaluate(bar1, clean_daily_state)
    assert bar1.vwap_failed_reclaim is False

    bar2 = replace(
        _vwap_state(fresh_market_state, price_vs_vwap="below", reclaimed=False, direction="DOWN"),
        timestamp=bar1_ts + timedelta(minutes=15),
    )
    decision = engine.evaluate(bar2, clean_daily_state)
    assert bar2.vwap_failed_reclaim is True
    assert decision.setup is not None
    assert decision.setup.strategy == "vwap_rejection"
    assert decision.setup.direction == "SHORT"


def test_evaluate_bar_after_non_reclaim_above_bar_does_not_fire(
    config, fresh_market_state, clean_daily_state
):
    """Operator's required case at the DecisionEngine level: several bars
    above VWAP (only the first a genuine reclaim) before closing below must
    NOT produce a vwap_rejection candidate."""
    engine = DecisionEngine(_with_vwap_rejection_enabled(config))
    base_ts = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)

    bar1 = replace(_vwap_state(fresh_market_state, price_vs_vwap="above", reclaimed=True, direction="DOWN"), timestamp=base_ts)
    engine.evaluate(bar1, clean_daily_state)

    bar2 = replace(_vwap_state(fresh_market_state, price_vs_vwap="above", reclaimed=False, direction="DOWN"), timestamp=base_ts + timedelta(minutes=15))
    engine.evaluate(bar2, clean_daily_state)

    bar3 = replace(_vwap_state(fresh_market_state, price_vs_vwap="below", reclaimed=False, direction="DOWN"), timestamp=base_ts + timedelta(minutes=30))
    decision = engine.evaluate(bar3, clean_daily_state)
    assert bar3.vwap_failed_reclaim is False
    assert decision.setup is None or decision.setup.strategy != "vwap_rejection"


def test_disabled_concept_never_sets_the_flag(config, fresh_market_state, clean_daily_state):
    disabled_cfg = replace(config, enabled_concepts=[c for c in config.enabled_concepts if c != "vwap_rejection"])
    engine = DecisionEngine(disabled_cfg)
    state = _vwap_state(fresh_market_state, price_vs_vwap="below", reclaimed=False, direction="DOWN")
    state.vwap_failed_reclaim = True  # pre-set to prove evaluate() clears it
    engine.evaluate(state, clean_daily_state)
    assert state.vwap_failed_reclaim is False


def test_undefined_price_vs_vwap_does_not_crash_or_arm(config, fresh_market_state, clean_daily_state):
    engine = DecisionEngine(_with_vwap_rejection_enabled(config))
    state = _vwap_state(fresh_market_state, price_vs_vwap="undefined", reclaimed=False, direction="DOWN")
    engine.evaluate(state, clean_daily_state)
    assert state.vwap_failed_reclaim is False
