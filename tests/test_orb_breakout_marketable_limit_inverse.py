from types import SimpleNamespace

from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker
from research.orb_breakout_marketable_limit_inverse import (
    _attribution,
    _force_one_contract_for_orb,
    _identity_payload,
    _mirror,
)


def _order(direction="LONG", *, entry=100.0, stop=97.0, target=106.6):
    return BracketOrder(
        instrument="MNQ",
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        rr_ratio=2.2,
        strategy="orb_breakout",
        contracts=1,
    )


def _row(
    *,
    direction="LONG",
    resolved=1,
    filled=1,
    pnl=10.0,
    bar_ts="2026-01-02T14:15:00+00:00",
):
    return {
        "date": "2026-01-02",
        "bar_ts": bar_ts,
        "instrument": "MNQ",
        "strategy": "orb_breakout",
        "original_direction": "SHORT",
        "direction": direction,
        "session": "new_york",
        "half": "H1",
        "attempted": 1,
        "filled": filled,
        "resolved": resolved,
        "open": 0,
        "cancelled_no_fill": int(not filled),
        "pnl_before_commission": pnl + 1.48 if resolved else 0.0,
        "pnl_after_commission": pnl if resolved else 0.0,
    }


def test_mirror_changes_only_side_and_bracket_orientation():
    inverse = _mirror(_order())
    assert inverse.direction == "SHORT"
    assert inverse.entry == 100.0
    assert inverse.stop == 103.0
    assert inverse.target == 93.4
    assert inverse.contracts == 1
    assert inverse.strategy == "orb_breakout"

    reinverted = _mirror(inverse)
    assert reinverted.direction == "LONG"
    assert reinverted.entry == 100.0
    assert reinverted.stop == 97.0
    assert reinverted.target == 106.6


def test_frozen_marketable_limit_is_bounded_and_fail_closed():
    broker = PaperBroker(
        slippage_ticks=1,
        pessimistic_both_hit=True,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={"MNQ": 8.0},
    )
    inverse = _mirror(_order())

    # Inverse SHORT cap is 98.0. A 99 market is tradable and fills at 98.75
    # after one adverse tick; a 97.75 market is beyond the cap and cancels.
    fill = broker.execute_bracket(inverse, market_price=99.0)
    assert fill.result == "OPEN"
    assert fill.entry_price == 98.75

    broker.cancel_all()
    cancelled = broker.execute_bracket(inverse, market_price=97.75)
    assert cancelled.result == "CANCELLED"
    assert cancelled.exit_reason == "ENTRY_NOT_FILLED"


def test_dynamic_sizing_recommendation_is_diagnostic_only():
    setup = SimpleNamespace(
        strategy="orb_breakout",
        contracts=2,
        instrument="MNQ",
        session="london",
        direction="LONG",
    )
    daily_state = SimpleNamespace(account_balance=6200.0)
    diagnostics = []

    _force_one_contract_for_orb(setup, daily_state, diagnostics)

    assert setup.contracts == 1
    assert diagnostics == [
        {
            "instrument": "MNQ",
            "session": "london",
            "direction": "LONG",
            "account_balance": 6200.0,
            "recommended_contracts": 2,
            "submitted_contracts": 1,
        }
    ]


def test_fixed_quantity_override_does_not_touch_other_strategies():
    setup = SimpleNamespace(
        strategy="orb_reclaim",
        contracts=2,
        instrument="MNQ",
        session="london",
        direction="LONG",
    )
    diagnostics = []

    _force_one_contract_for_orb(
        setup,
        SimpleNamespace(account_balance=6200.0),
        diagnostics,
    )

    assert setup.contracts == 2
    assert diagnostics == []


def test_identity_payload_is_order_independent_and_session_sensitive():
    first = {
        "date": "2026-01-02",
        "bar_ts": "2026-01-02T14:15:00+00:00",
        "instrument": "MNQ",
        "original_direction": "SHORT",
        "session": "new_york",
    }
    second = {
        "date": "2026-01-03",
        "bar_ts": "2026-01-03T08:15:00+00:00",
        "instrument": "MNQ",
        "original_direction": "LONG",
        "session": "london",
    }
    assert _identity_payload([first, second]) == _identity_payload([second, first])

    changed = dict(second, session="asian")
    assert _identity_payload([first, second]) != _identity_payload([first, changed])


def test_direction_and_fill_attribution_reconciles():
    common_original = _row(pnl=-20.0)
    common_inverse = _row(direction="SHORT", pnl=30.0)
    original_only = _row(
        pnl=15.0,
        bar_ts="2026-01-03T14:15:00+00:00",
    )
    inverse_cancel = _row(
        direction="SHORT",
        resolved=0,
        filled=0,
        pnl=0.0,
        bar_ts="2026-01-03T14:15:00+00:00",
    )
    original_cancel = _row(
        resolved=0,
        filled=0,
        pnl=0.0,
        bar_ts="2026-01-04T14:15:00+00:00",
    )
    inverse_only = _row(
        direction="SHORT",
        pnl=40.0,
        bar_ts="2026-01-04T14:15:00+00:00",
    )

    result = _attribution(
        [common_original, original_only, original_cancel],
        [common_inverse, inverse_cancel, inverse_only],
    )

    assert result["directional_effect_common_resolved_net_delta"] == 50.0
    assert result["fill_selection_net_delta"] == 25.0
    assert result["reconciled_total_fixed_net_delta"] == 75.0
    assert result["actual_total_fixed_net_delta"] == 75.0
