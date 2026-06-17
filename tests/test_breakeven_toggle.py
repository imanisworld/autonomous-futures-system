"""Locks the 1R→breakeven stop-trail toggle (`breakeven_at_1r`).

Default (True) reproduces the legacy behavior: once a 1-contract trade reaches
1R, the stop trails to entry, so a pullback to entry books a BREAKEVEN scratch.
With the flag False the trail is disabled — the trade runs to the original stop
(LOSS) or target (WIN), never scratched. Backtests showed the trail forgoes
real edge at 1 contract, so the flag exists to A/B and ship the removal without
touching the executable strategy stack.
"""

from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker


def _order() -> BracketOrder:
    # LONG 100, stop 98, target 110 -> initial risk 2pt, 1R = 102.
    return BracketOrder(
        instrument="MES", direction="LONG", entry=100.0, stop=98.0,
        target=110.0, rr_ratio=5.0, strategy="test", contracts=1,
    )


def _broker(breakeven_at_1r: bool) -> PaperBroker:
    # No slippage / no both-hit so the only variable under test is the trail.
    return PaperBroker(
        starting_balance=10000.0, slippage_ticks=0.0,
        pessimistic_both_hit=False, breakeven_at_1r=breakeven_at_1r,
    )


def test_breakeven_trail_on_scratches_at_entry():
    """Default: reach 1R, then pull back to entry -> BREAKEVEN, ~$0."""
    broker = _broker(breakeven_at_1r=True)
    broker.execute_bracket(_order())

    # Bar 1: tags 1R (high 103 >= 102) without hitting target -> stop trails to entry.
    assert broker.resolve_position(NextBarOHLC(high=103.0, low=101.0)) is None
    # Bar 2: pulls back to entry (low 99 <= trailed stop 100) -> scratch.
    fill = broker.resolve_position(NextBarOHLC(high=101.0, low=99.0))

    assert fill is not None
    assert fill.result == "BREAKEVEN"
    assert fill.exit_reason == "BREAKEVEN_STOP"
    assert abs(fill.pnl_dollars) < 1e-6


def test_breakeven_trail_off_runs_to_original_stop():
    """Flag off: the same path is NOT scratched — it runs to the original stop."""
    broker = _broker(breakeven_at_1r=False)
    broker.execute_bracket(_order())

    # Bar 1: tags 1R — but with the trail off the stop stays at 98.
    assert broker.resolve_position(NextBarOHLC(high=103.0, low=101.0)) is None
    # Bar 2: same pullback to 99 — would have scratched with the trail; here it
    # stays open because the real stop is still 98.
    assert broker.resolve_position(NextBarOHLC(high=101.0, low=99.0)) is None
    # Bar 3: hits the original stop -> a real LOSS, not a breakeven.
    fill = broker.resolve_position(NextBarOHLC(high=99.0, low=97.0))

    assert fill is not None
    assert fill.result == "LOSS"
    assert fill.exit_reason == "STOP_HIT"
    assert fill.pnl_dollars < 0


def test_breakeven_trail_defaults_off_to_match_live_box():
    """Default is OFF: the sim must match the live box (static Tradovate
    brackets, no breakeven trail). Construction without the kwarg = no trail."""
    assert PaperBroker(starting_balance=1.0)._breakeven_at_1r is False
    # The flag still works when explicitly enabled (e.g. to model a future trail).
    assert _broker(breakeven_at_1r=True)._breakeven_at_1r is True


def test_config_default_breakeven_is_off():
    """The production config default must be BE-off (honest backtests)."""
    from config.settings import load_config

    assert load_config().breakeven_at_1r is False
