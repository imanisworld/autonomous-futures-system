"""IOC-faithful entry fill model (Workstream A Phase 0).

Replay's PaperBroker historically filled EVERY entry unconditionally while the
live box's Limit-IOC entry fills ~14% — the root of the assumed-fill fictions
(vwap, orb). These tests pin the new ioc_limit model to the live semantics:
fill AT the market capped at entry ± tolerance, or book CANCELLED /
ENTRY_NOT_FILLED exactly like the live box, counting nothing.
"""
from __future__ import annotations

import pytest

from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker


def _order(direction="LONG", entry=100.0, stop=95.0, target=110.0, instrument="MES"):
    return BracketOrder(
        instrument=instrument,
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        rr_ratio=2.0,
        strategy="orb_breakout",
        contracts=1,
    )


def _broker(**kw):
    defaults = dict(
        starting_balance=1500.0,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={"MES": 16.0, "MNQ": 32.0},
    )
    defaults.update(kw)
    return PaperBroker(**defaults)


# ── legacy market model: zero behavior change ────────────────────────────────

def test_market_model_fills_unconditionally_without_market_price():
    broker = PaperBroker(starting_balance=1500.0)
    fill = broker.execute_bracket(_order())
    assert fill.result == "OPEN"
    assert fill.entry_price == 100.0


def test_market_model_ignores_market_price():
    broker = PaperBroker(starting_balance=1500.0)
    fill = broker.execute_bracket(_order(), market_price=999.0)
    assert fill.result == "OPEN"
    assert fill.entry_price == 100.0


# ── ioc_limit: marketable fills ──────────────────────────────────────────────

def test_ioc_long_fills_at_market_within_tolerance():
    # MES tol 16 ticks * 0.25 = 4.0 → limit 104.0; market 102.5 is marketable
    broker = _broker()
    fill = broker.execute_bracket(_order(), market_price=102.5)
    assert fill.result == "OPEN"
    assert fill.entry_price == 102.5  # fills AT the market, not the plan price


def test_ioc_long_fill_capped_at_limit_price_with_slippage():
    broker = _broker(slippage_ticks=8.0)  # 2.0 points of slip
    fill = broker.execute_bracket(_order(), market_price=103.0)
    # market 103.0 + 2.0 slip = 105.0 but the limit caps the fill at 104.0
    assert fill.result == "OPEN"
    assert fill.entry_price == 104.0


def test_ioc_long_below_entry_fills_at_market_favorably():
    broker = _broker()
    fill = broker.execute_bracket(_order(), market_price=98.0)
    assert fill.result == "OPEN"
    assert fill.entry_price == 98.0  # marketable limit buys at the (better) market


def test_ioc_short_mirror():
    broker = _broker()
    # SHORT entry 100, tol 4.0 → limit 96.0. Market 97.0 marketable; 95.0 not.
    fill = broker.execute_bracket(
        _order(direction="SHORT", stop=105.0, target=90.0), market_price=97.0
    )
    assert fill.result == "OPEN"
    assert fill.entry_price == 97.0


# ── ioc_limit: self-cancel (the live no-fill) ────────────────────────────────

def test_ioc_long_beyond_tolerance_books_cancelled_like_live():
    broker = _broker()
    fill = broker.execute_bracket(_order(), market_price=104.25)
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ENTRY_NOT_FILLED"
    assert fill.pnl_dollars == 0.0
    assert broker.get_position() is None  # no position was opened
    # broker is immediately reusable — a no-fill must not wedge the sim
    refill = broker.execute_bracket(_order(), market_price=100.0)
    assert refill.result == "OPEN"


def test_ioc_short_beyond_tolerance_cancels():
    broker = _broker()
    fill = broker.execute_bracket(
        _order(direction="SHORT", stop=105.0, target=90.0), market_price=95.75
    )
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ENTRY_NOT_FILLED"


def test_ioc_uses_per_root_tolerance():
    broker = _broker()
    # MNQ tol 32 ticks * 0.25 = 8.0 → limit 108.0; market 107 fills for MNQ
    fill = broker.execute_bracket(_order(instrument="MNQ"), market_price=107.0)
    assert fill.result == "OPEN"


def test_ioc_requires_market_price():
    broker = _broker()
    with pytest.raises(ValueError):
        broker.execute_bracket(_order())


def test_unknown_model_rejected():
    with pytest.raises(ValueError):
        PaperBroker(entry_fill_model="wat")


# ── resolution still works after an ioc fill ─────────────────────────────────

def test_ioc_fill_resolves_normally():
    broker = _broker(pessimistic_both_hit=True)
    fill = broker.execute_bracket(_order(), market_price=101.0)
    assert fill.result == "OPEN"
    resolved = broker.resolve_position(NextBarOHLC(high=111.0, low=99.0))
    assert resolved is not None and resolved.result == "WIN"


# ── stop_market: causal next-bar stop entry ──────────────────────────────────

def test_stop_market_arms_without_opening_position():
    broker = PaperBroker(entry_fill_model="stop_market")
    fill = broker.execute_bracket(_order())
    assert fill.result == "PENDING"
    assert broker.get_position() is None
    assert broker.has_pending_entry()


def test_stop_market_long_gap_through_fills_at_next_open_with_slip():
    broker = PaperBroker(entry_fill_model="stop_market", slippage_ticks=1.0)
    broker.execute_bracket(_order(entry=100.0, stop=95.0, target=110.0))
    fill = broker.resolve_position(NextBarOHLC(open=102.0, high=111.0, low=101.0))
    assert fill is not None
    assert fill.result == "WIN"
    assert fill.entry_price == 102.25
    assert fill.exit_price == 110.0


def test_stop_market_long_triggers_inside_next_bar_at_stop_level():
    broker = PaperBroker(entry_fill_model="stop_market", slippage_ticks=1.0)
    broker.execute_bracket(_order(entry=100.0, stop=95.0, target=110.0))
    fill = broker.resolve_position(NextBarOHLC(open=99.0, high=111.0, low=98.5))
    assert fill is not None
    assert fill.result == "WIN"
    assert fill.entry_price == 100.25


def test_stop_market_short_mirror_gap_through():
    broker = PaperBroker(entry_fill_model="stop_market", slippage_ticks=1.0)
    broker.execute_bracket(_order(direction="SHORT", entry=100.0, stop=105.0, target=90.0))
    fill = broker.resolve_position(NextBarOHLC(open=98.0, high=99.0, low=89.0))
    assert fill is not None
    assert fill.result == "WIN"
    assert fill.entry_price == 97.75
    assert fill.exit_price == 90.0


def test_stop_market_missing_next_open_fails_closed():
    broker = PaperBroker(entry_fill_model="stop_market")
    broker.execute_bracket(_order())
    fill = broker.resolve_position(NextBarOHLC(high=111.0, low=99.0))
    assert fill is not None
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ENTRY_OPEN_UNAVAILABLE"
    assert broker.get_position() is None
    assert not broker.has_pending_entry()


def test_stop_market_not_triggered_on_next_bar_cancels():
    broker = PaperBroker(entry_fill_model="stop_market")
    broker.execute_bracket(_order(entry=100.0, stop=95.0, target=110.0))
    fill = broker.resolve_position(NextBarOHLC(open=98.0, high=99.75, low=97.0))
    assert fill is not None
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ENTRY_NOT_TRIGGERED"


def test_stop_market_gap_beyond_original_bracket_fails_closed():
    broker = PaperBroker(entry_fill_model="stop_market", slippage_ticks=1.0)
    broker.execute_bracket(_order(entry=100.0, stop=95.0, target=110.0))
    fill = broker.resolve_position(NextBarOHLC(open=112.0, high=113.0, low=111.0))
    assert fill is not None
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ENTRY_BRACKET_INVALID_AT_FILL"
    assert broker.get_position() is None
