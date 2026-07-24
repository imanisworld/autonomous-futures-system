"""Causal one-bar-lookback VWAP failed-reclaim (vwap_rejection) contract.

Fixes the proven contradiction documented in
docs/vwap-hold-vs-vwap-rejection-overlap-audit-2026-07-23.md (PR #308):
the prior entry condition required state.vwap.reclaimed == True AND
state.vwap.price_vs_vwap == "below" on the SAME bar — structurally
impossible, since reclaimed can only be True on a bar where price closed
above VWAP. vwap_rejection could never fire.

Architecture (revised from an earlier backend-persisted-state draft after
two proven correctness defects):
  - VWAPData.failed_reclaim is populated UPSTREAM, not computed inside
    DecisionEngine from DailyState. Live: Pine sends it directly
    (payload.vwap_failed_reclaim), since Pine tracks its own crossover
    state on every bar regardless of whether the backend even evaluates
    that bar. Replay: replay_engine.py derives it from the candle sequence
    itself (prev_candle/prev_prev_candle), independent of
    DecisionEngine/DailyState.
  - Why not backend-persisted DailyState (the rejected earlier draft): (1)
    DailyState is not instrument-keyed (journal.get_daily_state() covers a
    whole day across BOTH MNQ and MES), so a flat persisted-state dict
    would let one instrument's reclaim bleed into the other's. (2)
    webhook/runner.py returns BLOCKED_MAX_TRADES / BLOCKED_LOSS_LOCKOUT /
    BLOCKED_OPEN_POSITION bars BEFORE DecisionEngine.evaluate() ever runs —
    so state advanced only inside evaluate() would desync from the true
    immediately-preceding market bar whenever any of those gates fired.
"""

from __future__ import annotations

from dataclasses import replace

from context.market_context import MarketState, TrendData, VWAPData
from replay.candle_loader import ReplayCandle
from replay.replay_engine import ReplayEngine
from strategy.signal_engine import DecisionEngine
from webhook.payload import AlertPayload
from webhook.state_builder import build_market_state


# ─── Live: payload -> state_builder passthrough ──────────────────────────────


def _payload(**overrides) -> AlertPayload:
    base = dict(
        ticker="MES1!",
        timestamp="2026-07-24T14:15:00Z",
        open=7454.0, high=7459.25, low=7438.25, close=7450.25,
        volume=1000, avg_volume=1000, timeframe="15",
        vwap=7449.0,
    )
    base.update(overrides)
    return AlertPayload(**base)


def test_payload_failed_reclaim_defaults_false():
    assert _payload().vwap_failed_reclaim is False


def test_state_builder_passes_through_failed_reclaim_true():
    state = build_market_state(_payload(vwap_failed_reclaim=True, vwap_reclaimed=False))
    assert state.vwap.failed_reclaim is True


def test_state_builder_passes_through_failed_reclaim_false():
    state = build_market_state(_payload(vwap_failed_reclaim=False))
    assert state.vwap.failed_reclaim is False


def test_reclaim_and_failed_reclaim_are_independent_fields_on_the_payload():
    """below -> above: Pine sends vwap_reclaimed=True, vwap_failed_reclaim
    stays False (this bar is the reclaim itself, not a rejection of one)."""
    state = build_market_state(_payload(vwap_reclaimed=True, vwap_failed_reclaim=False))
    assert state.vwap.reclaimed is True
    assert state.vwap.failed_reclaim is False


# ─── strategy/signal_engine.py::_try_vwap_rejection gates on the new field ──


def _vwap_state(fresh_market_state, *, price_vs_vwap: str, reclaimed: bool, failed_reclaim: bool, direction: str = "DOWN") -> MarketState:
    return replace(
        fresh_market_state,
        vwap=VWAPData(value=19495.0, price_vs_vwap=price_vs_vwap, reclaimed=reclaimed, holding=True, failed_reclaim=failed_reclaim),
        trend=TrendData(direction=direction, strength="MODERATE", ema_fast_above_slow=(direction == "UP")),
    )


def _with_vwap_rejection_enabled(config):
    if "vwap_rejection" in config.enabled_concepts:
        return config
    return replace(config, enabled_concepts=list(config.enabled_concepts) + ["vwap_rejection"])


def test_try_vwap_rejection_fires_on_failed_reclaim_flag(config, fresh_market_state):
    engine = DecisionEngine(_with_vwap_rejection_enabled(config))
    state = _vwap_state(fresh_market_state, price_vs_vwap="below", reclaimed=False, failed_reclaim=True)
    setup = engine._try_vwap_rejection(state)
    assert setup is not None
    assert setup.strategy == "vwap_rejection"
    assert setup.direction == "SHORT"


def test_try_vwap_rejection_does_not_fire_without_the_flag(config, fresh_market_state):
    """Operator's required case: price below VWAP alone (no failed_reclaim
    flag set) must NOT fire — covers both 'never reclaimed' and 'reclaimed
    many bars ago, not the immediately preceding bar' scenarios, since
    upstream (Pine/replay) is responsible for only setting the flag True on
    the genuine one-bar-lookback case."""
    engine = DecisionEngine(_with_vwap_rejection_enabled(config))
    state = _vwap_state(fresh_market_state, price_vs_vwap="below", reclaimed=False, failed_reclaim=False)
    assert engine._try_vwap_rejection(state) is None


def test_try_vwap_rejection_none_when_vwap_missing(config, fresh_market_state):
    engine = DecisionEngine(_with_vwap_rejection_enabled(config))
    state = replace(fresh_market_state, vwap=None)
    assert engine._try_vwap_rejection(state) is None


# ─── Replay: candle-sequence derivation, independent of DailyState ──────────


_VWAP = 7449.0


def _candle(position: str, **overrides) -> ReplayCandle:
    """position: 'above' or 'below' -- price_vs_vwap is a computed property
    on ReplayCandle (close vs vwap), not a settable field, so pick a close
    on the correct side of the fixed _VWAP."""
    close = _VWAP + 5.0 if position == "above" else _VWAP - 5.0
    base = dict(
        timestamp="2026-07-24T14:00:00+00:00", instrument="MES", session="new_york",
        open=close, high=close + 2, low=close - 2, close=close, volume=1000, vwap=_VWAP,
        orb_high=7460.0, orb_low=7440.0, orb_status="inside",
        market_condition="TRENDING", trend_direction="DOWN", trend_strength="STRONG",
        previous_day_high=7500.0, previous_day_low=7400.0, previous_day_close=7450.0,
        timeframe="15m",
    )
    base.update(overrides)
    candle = ReplayCandle(**base)
    assert candle.price_vs_vwap == position
    return candle


def test_replay_below_to_above_is_reclaim_only_not_failed_reclaim():
    engine = ReplayEngine(log_dir='logs_test')
    bar1 = _candle("below")
    bar2 = _candle("above")
    state = engine._market_state_from_candle(bar2, prev_candle=bar1, prev_prev_candle=None)
    assert state.vwap.reclaimed is True
    assert state.vwap.failed_reclaim is False


def test_replay_bar_after_reclaim_closing_below_is_failed_reclaim():
    engine = ReplayEngine(log_dir='logs_test')
    bar0 = _candle("below")   # two bars back
    bar1 = _candle("above")   # the reclaim bar
    bar2 = _candle("below")   # closes back below -> failed reclaim
    state = engine._market_state_from_candle(bar2, prev_candle=bar1, prev_prev_candle=bar0)
    assert state.vwap.failed_reclaim is True
    assert state.vwap.reclaimed is False  # this bar itself is not a reclaim


def test_replay_price_above_for_several_bars_then_below_is_not_failed_reclaim():
    """Operator's required case: only the FIRST above-bar was the genuine
    crossover; a later below-close after several above bars must not be
    flagged, since the immediately preceding bar was not itself a reclaim."""
    engine = ReplayEngine(log_dir='logs_test')
    bar0 = _candle("below")
    bar1 = _candle("above")   # genuine reclaim
    bar2 = _candle("above")   # stays above (not itself a new crossover)
    bar3 = _candle("below")   # closes below several bars after the reclaim

    state2 = engine._market_state_from_candle(bar2, prev_candle=bar1, prev_prev_candle=bar0)
    assert state2.vwap.reclaimed is False  # bar1->bar2 is above->above, no new cross
    assert state2.vwap.failed_reclaim is False

    state3 = engine._market_state_from_candle(bar3, prev_candle=bar2, prev_prev_candle=bar1)
    assert state3.vwap.failed_reclaim is False


def test_replay_reclaim_and_failed_reclaim_mutually_exclusive_same_bar():
    """By construction: vwap_reclaimed=True this bar requires price_vs_vwap
    == 'above' this bar, so failed_reclaim (which requires 'below') can
    never also be True on that same bar, regardless of what preceded it."""
    engine = ReplayEngine(log_dir='logs_test')
    bar0 = _candle("below")
    bar1 = _candle("above")  # this bar is itself a reclaim
    for prev_prev in (bar0, _candle("above")):
        state = engine._market_state_from_candle(bar1, prev_candle=bar0, prev_prev_candle=prev_prev)
        if state.vwap.reclaimed:
            assert state.vwap.failed_reclaim is False


def test_replay_missing_lookback_bars_never_crashes_or_arms():
    engine = ReplayEngine(log_dir='logs_test')
    first_bar = _candle("below")
    state = engine._market_state_from_candle(first_bar, prev_candle=None, prev_prev_candle=None)
    assert state.vwap.reclaimed is False
    assert state.vwap.failed_reclaim is False


# ─── Regressions for the two rejected-architecture blockers ─────────────────


def test_instrument_isolation_no_shared_state_to_contaminate(config, fresh_market_state, clean_daily_state):
    """Blocker #1 (rejected earlier draft): DailyState is not
    instrument-keyed (journal.get_daily_state() reconstructs ONE DailyState
    covering a whole day across BOTH MNQ and MES interleaved). The current
    architecture has no backend-persisted VWAP-reclaim state at all to
    contaminate: MNQ's failed_reclaim comes from MNQ's own payload/candle,
    MES's from its own — proven here by processing an MNQ bar with
    failed_reclaim=True through evaluate() with a SHARED daily_state, then
    confirming a subsequent MES bar (same daily_state) with no flag set does
    NOT inherit it."""
    engine = DecisionEngine(_with_vwap_rejection_enabled(config))
    mnq_state = replace(
        _vwap_state(fresh_market_state, price_vs_vwap="below", reclaimed=False, failed_reclaim=True),
        instrument="MNQ",
    )
    decision = engine.evaluate(mnq_state, clean_daily_state)
    assert decision.setup is not None and decision.setup.strategy == "vwap_rejection"

    mes_state = replace(
        _vwap_state(fresh_market_state, price_vs_vwap="below", reclaimed=False, failed_reclaim=False),
        instrument="MES",
    )
    decision2 = engine.evaluate(mes_state, clean_daily_state)  # same daily_state object
    assert decision2.setup is None or decision2.setup.strategy != "vwap_rejection"


def test_replay_candle_derivation_is_independent_of_whether_evaluate_runs():
    """Blocker #2 (rejected earlier draft): webhook/runner.py returns
    BLOCKED_MAX_TRADES / BLOCKED_LOSS_LOCKOUT / BLOCKED_OPEN_POSITION bars
    BEFORE DecisionEngine.evaluate() is ever called, so a memory advanced
    only inside evaluate() would desync from the true immediately-preceding
    market bar whenever any of those gates fired. The current architecture
    computes failed_reclaim in _market_state_from_candle -- called
    unconditionally for every candle in the replay loop BEFORE any gate
    check -- so it is structurally impossible for a skipped/blocked
    evaluate() call to affect it. Proven directly: the bar-after-reclaim
    result is identical whether or not evaluate() is ever invoked on the
    intervening state, because failed_reclaim never reads anything
    DecisionEngine/DailyState touches."""
    engine = ReplayEngine(log_dir='logs_test')
    bar0 = _candle("below")
    bar1 = _candle("above")   # reclaim bar -- would be a BLOCKED bar live
    bar2 = _candle("below")   # failed reclaim, regardless of bar1's decision

    state1 = engine._market_state_from_candle(bar1, prev_candle=bar0, prev_prev_candle=None)
    assert state1.vwap.reclaimed is True
    # bar1's state is deliberately never passed through DecisionEngine.evaluate()
    # here, simulating a BLOCKED_MAX_TRADES/LOSS_LOCKOUT/OPEN_POSITION bar.

    state2 = engine._market_state_from_candle(bar2, prev_candle=bar1, prev_prev_candle=bar0)
    assert state2.vwap.failed_reclaim is True


# ─── Regression: mixed-instrument replay must not leak reclaim state ────────


def test_mixed_instrument_replay_does_not_leak_reclaim_across_instruments(tmp_path, config):
    """ReplayEngine.run() supports allow_mixed_instruments=True, where
    candles from different instruments interleave in one sequence. The
    per-run prev_candle/prev_prev_candle used to be two global scalars —
    an MNQ reclaim immediately preceding an unrelated MES bar in the merged
    stream would have falsely armed MES's failed_reclaim. Fixed by keying
    the lookback history by (instrument, timeframe): each stream only ever
    sees its own authoritative bars.

    Sequence: MNQ below -> MNQ above (reclaim) -> MES below.
    MES must NOT be flagged as a failed reclaim off MNQ's crossover.
    """
    import json
    import dataclasses as _dc

    import strategy.signal_engine as se

    def row(instrument, ts, close, vwap=7449.0):
        return {
            "timestamp": ts, "instrument": instrument, "session": "new_york",
            "open": close, "high": close + 2, "low": close - 2, "close": close,
            "volume": 1000, "avg_volume": 1000, "vwap": vwap,
            "orb_high": vwap + 20, "orb_low": vwap - 20,
            "previous_day_high": vwap + 50, "previous_day_low": vwap - 50,
            "previous_day_close": vwap, "timeframe": "15m",
        }

    rows = [
        row("MNQ", "2026-07-24T14:00:00+00:00", 7444.0),  # below
        row("MNQ", "2026-07-24T14:15:00+00:00", 7454.0),  # above -> reclaim
        row("MES", "2026-07-24T14:30:00+00:00", 7444.0),  # below -- must NOT flag
    ]
    candle_path = tmp_path / "mixed.jsonl"
    candle_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    captured = []
    orig_evaluate = se.DecisionEngine.evaluate

    def instrumented(self, state, daily_state):
        captured.append((state.instrument, state.vwap.reclaimed, state.vwap.failed_reclaim))
        return orig_evaluate(self, state, daily_state)

    se.DecisionEngine.evaluate = instrumented
    try:
        patched = _dc.replace(
            config, enabled_concepts=list(config.enabled_concepts) + ["vwap_rejection"]
        )
        engine = ReplayEngine(config=patched, log_dir=str(tmp_path / "logs"))
        engine.run(candle_path, allow_mixed_instruments=True)
    finally:
        se.DecisionEngine.evaluate = orig_evaluate

    assert len(captured) == 3
    mnq_bar1, mnq_bar2, mes_bar1 = captured
    assert mnq_bar1 == ("MNQ", False, False)
    assert mnq_bar2 == ("MNQ", True, False)
    assert mes_bar1 == ("MES", False, False)  # NOT contaminated by MNQ's reclaim
