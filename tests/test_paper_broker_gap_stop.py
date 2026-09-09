from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker


def _long_order():
    return BracketOrder(
        instrument="MES",
        direction="LONG",
        entry=100.0,
        stop=95.0,
        target=110.0,
        rr_ratio=2.0,
        strategy="strat_122",
        contracts=1,
    )


def _short_order():
    return BracketOrder(
        instrument="MES",
        direction="SHORT",
        entry=100.0,
        stop=105.0,
        target=90.0,
        rr_ratio=2.0,
        strategy="strat_122",
        contracts=1,
    )


def test_static_long_stop_gap_prices_from_open_not_stale_stop():
    broker = PaperBroker(
        starting_balance=1500.0,
        slippage_ticks=1.0,
        pessimistic_both_hit=True,
        entry_fill_model="market",
    )
    broker.execute_bracket(_long_order())
    fill = broker.resolve_position(
        NextBarOHLC(open=92.0, high=96.0, low=91.0)
    )
    assert fill is not None
    assert fill.result == "LOSS"
    assert fill.exit_reason == "STOP_GAP"
    assert fill.exit_price == 91.75
    assert fill.pnl_dollars == -42.50


def test_static_short_stop_gap_prices_from_open_not_stale_stop():
    broker = PaperBroker(
        starting_balance=1500.0,
        slippage_ticks=1.0,
        pessimistic_both_hit=True,
        entry_fill_model="market",
    )
    broker.execute_bracket(_short_order())
    fill = broker.resolve_position(
        NextBarOHLC(open=108.0, high=109.0, low=104.0)
    )
    assert fill is not None
    assert fill.result == "LOSS"
    assert fill.exit_reason == "STOP_GAP"
    assert fill.exit_price == 108.25
    assert fill.pnl_dollars == -42.50


def test_static_stop_without_open_keeps_legacy_intrabar_pricing():
    broker = PaperBroker(
        starting_balance=1500.0,
        slippage_ticks=1.0,
        pessimistic_both_hit=True,
        entry_fill_model="market",
    )
    broker.execute_bracket(_long_order())
    fill = broker.resolve_position(
        NextBarOHLC(high=100.0, low=94.0)
    )
    assert fill is not None
    assert fill.result == "LOSS"
    assert fill.exit_reason == "STOP_HIT"
    assert fill.exit_price == 94.75
