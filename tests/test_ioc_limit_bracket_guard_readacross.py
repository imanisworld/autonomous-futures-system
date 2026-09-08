"""Tests for scripts/ioc_limit_bracket_guard_readacross.py.

The load-bearing claim is about PaperBroker itself: its stop_market path
rejects a fill that lands beyond its own bracket and its ioc_limit path does
not. That asymmetry is asserted directly here, so if the guard is ever carried
across, these tests fail loudly rather than the read-across quietly going stale.
"""
from __future__ import annotations

import pytest

from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from scripts import ioc_limit_bracket_guard_readacross as readacross


def _short(entry: float = 20000.0, stop: float = 20012.5, target: float = 19960.0) -> BracketOrder:
    """A short whose stop is 50 ticks above entry and target 160 ticks below."""
    return BracketOrder(instrument="MNQ", strategy="orb_breakout", direction="SHORT",
                        entry=entry, stop=stop, target=target, rr_ratio=3.2, contracts=1)


def _broker(model: str) -> PaperBroker:
    return PaperBroker(starting_balance=100_000.0, slippage_ticks=1.0,
                       pessimistic_both_hit=True, entry_fill_model=model,
                       entry_tolerance_ticks_by_root={"MNQ": 8.0})


# ── the rule ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("direction,fill,ok", [
    ("SHORT", 19990.0, True),    # between target and stop
    ("SHORT", 20050.0, False),   # above its own stop
    ("SHORT", 19950.0, False),   # beyond its own target
    ("LONG", 20005.0, True),
    ("LONG", 19950.0, False),
])
def test_bracket_valid_at_fill_matches_the_broker_rule(direction, fill, ok):
    if direction == "SHORT":
        assert readacross.bracket_valid_at_fill("SHORT", fill, 20012.5, 19960.0) is ok
    else:
        assert readacross.bracket_valid_at_fill("LONG", fill, 19960.0, 20012.5) is ok


# ── the asymmetry this document exists to report ─────────────────────────────


def test_ioc_limit_fills_a_short_far_above_its_own_stop_without_complaint():
    """The defect. Market 60 points above entry — well past the 50-tick stop."""
    order = _short()
    fill = _broker("ioc_limit").execute_bracket(order, market_price=20060.0)
    assert fill.result == "OPEN"
    assert fill.entry_price > order.stop, "fill should land above the stop"
    assert not readacross.bracket_valid_at_fill("SHORT", float(fill.entry_price),
                                                order.stop, order.target)


def test_that_position_would_book_a_gain_on_its_own_stop():
    """Why it matters: the 'stop' is in profit, so STOP_HIT will not mean a loss."""
    order = _short()
    fill = _broker("ioc_limit").execute_bracket(order, market_price=20060.0)
    phantom = readacross._phantom_pnl("SHORT", float(fill.entry_price), order.stop)
    assert phantom > 0


def test_stop_market_path_rejects_a_fill_outside_its_own_bracket():
    """The guard exists — on the other path only.

    A resting short fills at the bar's open when the bar opens at or below the
    entry, so a gap straight through the target produces a fill outside the
    bracket. That is the resting-path analogue of the ioc_limit case above, and
    it is refused rather than opened.
    """
    order = _short()
    broker = _broker("stop_market")
    assert broker.execute_bracket(order, market_price=20060.0).result == "PENDING"
    # Gaps below the 19960 target, so the fill cannot sit inside the bracket.
    resolved = broker.resolve_position(NextBarOHLC(high=19960.0, low=19940.0, open=19950.0))
    assert resolved is not None
    assert resolved.exit_reason == "ENTRY_BRACKET_INVALID_AT_FILL"


def test_stop_market_path_accepts_an_ordinary_triggered_fill():
    """Control for the test above: the guard is not rejecting every resting fill."""
    order = _short()
    broker = _broker("stop_market")
    broker.execute_bracket(order, market_price=20060.0)
    # Trades down through the entry without gapping past the target.
    assert broker.resolve_position(NextBarOHLC(high=20005.0, low=19995.0, open=20002.0)) is None
    position = broker.get_position()
    assert position is not None and position.open


def test_a_normally_detached_fill_inside_the_stop_is_still_accepted():
    """The guard must not reject ordinary favourable fills — only invalid ones."""
    order = _short()
    fill = _broker("ioc_limit").execute_bracket(order, market_price=20008.0)
    assert fill.result == "OPEN"
    assert readacross.bracket_valid_at_fill("SHORT", float(fill.entry_price),
                                            order.stop, order.target)


def test_tolerance_does_not_bound_the_favourable_side():
    """Central claim: a tighter tolerance cannot prevent this."""
    order = _short()
    for tolerance in (4.0, 8.0, 32.0):
        broker = PaperBroker(starting_balance=100_000.0, slippage_ticks=1.0,
                             pessimistic_both_hit=True, entry_fill_model="ioc_limit",
                             entry_tolerance_ticks_by_root={"MNQ": tolerance})
        fill = broker.execute_bracket(_short(), market_price=20060.0)
        assert fill.result == "OPEN"
        assert float(fill.entry_price) > order.stop


# ── the report ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def report() -> dict:
    return readacross.build_report(tolerance_ticks=8.0)


def test_wide_stop_family_is_exposed_only_through_4hr(report):
    lanes = {lane["lane"]: lane for lane in report["lanes"]}
    assert lanes["4hr_mnq"]["invalid_at_fill"] == 5
    assert lanes["322_mnq"]["invalid_at_fill"] == 0
    assert lanes["miyagi_mnq"]["invalid_at_fill"] == 0


def test_the_inverse_lane_is_an_order_of_magnitude_worse(report):
    lanes = {lane["lane"]: lane for lane in report["lanes"]}
    assert lanes["orb_breakout_inverse_mnq"]["invalid_share"] > 0.6
    assert lanes["4hr_mnq"]["invalid_share"] < 0.15


def test_phantom_pnl_is_positive_wherever_the_defect_occurs(report):
    """Every invalid fill books a gain — that is the signature, not a coincidence."""
    for lane in report["lanes"]:
        rows = lane["invalid_rows"]
        assert all(row["phantom_pnl_if_stop_hit"] > 0 for row in rows), lane["lane"]
        assert lane["phantom_pnl_total"] == pytest.approx(
            sum(row["phantom_pnl_if_stop_hit"] for row in rows), abs=0.01
        )


def test_every_invalid_fill_is_detached_further_than_its_stop(report):
    """The mechanism, pinned: detachment exceeding stop distance is what causes it."""
    for lane in report["lanes"]:
        for row in lane["invalid_rows"]:
            assert row["detachment_ticks"] > row["stop_distance_ticks"], (lane["lane"], row["date"])
