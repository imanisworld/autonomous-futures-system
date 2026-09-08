"""PaperBroker must refuse a fill that lands beyond its own bracket, on EVERY
entry path -- not only the stop-market one.

The stop-market path has always refused this (`ENTRY_BRACKET_INVALID_AT_FILL`).
The marketable-limit and live-price paths reached the position-opening code
without it, because the equivalent rule sat behind `post_fill_validation_required`,
which the derived paper lanes set to False. The marketable tolerance bounds only
the ADVERSE side, so on the favourable side the fill is the market however far it
has run from a stale plan level. Once that distance exceeds the stop distance the
static stop sits between the fill and the target, and the position is structurally
guaranteed to "stop out" in profit with its label contradicting its P&L.

Measured consequence of the missing guard (see
docs/inverse-orb-baseline-post-fill-decomposition-2026-09-08.md): 38 of 57 fills
in the inverse ORB canonical baseline, carrying +$1,138.26 -- more than the whole
lane's reported profit.
"""
from __future__ import annotations

import pytest

from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker


def _order(direction: str, *, entry: float, stop: float, target: float, **kw) -> BracketOrder:
    return BracketOrder(
        instrument="MNQ", direction=direction, entry=entry, stop=stop, target=target,
        rr_ratio=2.0, strategy="orb_breakout", contracts=1, min_rr_ratio=2.0,
        max_stop_ticks=120.0, **kw,
    )


def _ioc_broker(tolerance_ticks: float = 8.0) -> PaperBroker:
    return PaperBroker(
        starting_balance=1500.0, entry_fill_model="ioc_limit",
        entry_tolerance_ticks_default=tolerance_ticks,
    )


@pytest.mark.parametrize(
    ("direction", "entry", "stop", "target", "market"),
    [
        # LONG filled ABOVE its own target: opens already past the profit level.
        ("LONG", 19498.5, 19486.0, 19523.5, 19540.0),
        # SHORT filled BELOW its own target: mirror image.
        ("SHORT", 19498.5, 19511.0, 19473.5, 19460.0),
    ],
)
def test_fill_beyond_own_bracket_is_refused_on_the_live_price_path(
    direction, entry, stop, target, market
) -> None:
    broker = PaperBroker(starting_balance=1500.0, entry_fill_model="market", slippage_ticks=0)
    fill = broker.execute_bracket(
        _order(direction, entry=entry, stop=stop, target=target, force_market_entry=True),
        market_price=market,
    )
    assert fill.exit_reason == "ENTRY_BRACKET_INVALID_AT_FILL"
    assert fill.result == "CANCELLED"
    assert fill.pnl_dollars == 0.0
    # The decisive property: no position was opened.
    assert broker.get_position() is None or not broker.get_position().open


def test_guard_does_not_depend_on_post_fill_validation_required() -> None:
    """The derived paper lanes set this flag False; the rule must still apply."""
    broker = PaperBroker(starting_balance=1500.0, entry_fill_model="market", slippage_ticks=0)
    order = _order(
        "LONG", entry=19498.5, stop=19486.0, target=19523.5,
        force_market_entry=True, post_fill_validation_required=False,
    )
    fill = broker.execute_bracket(order, market_price=19540.0)
    assert fill.exit_reason == "ENTRY_BRACKET_INVALID_AT_FILL"


def test_ioc_limit_path_refuses_a_favourable_side_runaway() -> None:
    """A wide tolerance lets the fill land past the target; it must be refused."""
    broker = _ioc_broker(tolerance_ticks=200.0)
    fill = broker.execute_bracket(
        _order("LONG", entry=19498.5, stop=19486.0, target=19523.5), market_price=19540.0,
    )
    assert fill.exit_reason == "ENTRY_BRACKET_INVALID_AT_FILL"
    assert broker.get_position() is None or not broker.get_position().open


def test_valid_fill_inside_the_bracket_still_opens_normally() -> None:
    broker = _ioc_broker(tolerance_ticks=8.0)
    fill = broker.execute_bracket(
        _order("LONG", entry=19498.5, stop=19486.0, target=19523.5), market_price=19500.0,
    )
    assert fill.exit_reason != "ENTRY_BRACKET_INVALID_AT_FILL"
    position = broker.get_position()
    assert position is not None and position.open
    assert position.entry_price == pytest.approx(19500.0)


def test_no_fill_still_reports_not_filled_not_invalid_bracket() -> None:
    """Beyond tolerance on the ADVERSE side is a no-fill, a different outcome."""
    broker = _ioc_broker(tolerance_ticks=4.0)
    fill = broker.execute_bracket(
        _order("LONG", entry=19498.5, stop=19486.0, target=19523.5), market_price=19510.0,
    )
    assert fill.exit_reason == "ENTRY_NOT_FILLED"


def test_stop_market_and_limit_paths_now_agree_on_the_same_geometry() -> None:
    """Parity is the point: the same invalid geometry, the same verdict."""
    geometry = dict(entry=19498.5, stop=19486.0, target=19523.5)
    limit_fill = _ioc_broker(tolerance_ticks=200.0).execute_bracket(
        _order("LONG", **geometry), market_price=19540.0,
    )
    live_fill = PaperBroker(
        starting_balance=1500.0, entry_fill_model="market", slippage_ticks=0,
    ).execute_bracket(_order("LONG", force_market_entry=True, **geometry), market_price=19540.0)
    assert limit_fill.exit_reason == live_fill.exit_reason == "ENTRY_BRACKET_INVALID_AT_FILL"
