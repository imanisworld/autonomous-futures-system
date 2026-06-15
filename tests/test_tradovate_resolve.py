"""
tests/test_tradovate_resolve.py

Locks in the stateless-safe, instrument-aware resolve_position() that prices the
exit by matching our journaled target/stop against the FILLED bracket child
order — NOT "the last account fill", which with overlapping 6-contract orders
grabbed an unrelated fill (30208.75) and fabricated +$537/+$266 wins while the
real demo account was −$66.96.

resolve now:
  • judges closure on /position/list for OUR contract only,
  • finds the filled Limit(target)/Stop(stop) child for OUR contract,
  • books WIN@target / LOSS@stop from that, retrying (not guessing) if no child
    matches yet, and never fabricating from an unrelated fill.
"""

from __future__ import annotations

from execution.broker_interface import Position
from execution.tradovate_broker import TradovateBroker, TradovateConfig


OUR_CID = 12345
OTHER_CID = 99999


def _broker(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("TRADOVATE_USERNAME", "x")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "x")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "1")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "x")
    b = TradovateBroker(config=TradovateConfig.from_env())
    b._account_id = 1
    b._resolve_fail_count = 0
    monkeypatch.setattr(b, "_authenticate", lambda: True)
    monkeypatch.setattr(b, "_find_contract_id", lambda inst: OUR_CID)
    # LONG MNQ: target 30015 (above entry → WIN), stop 29994 (below → LOSS).
    b._last_position = Position(
        instrument="MNQ", direction="LONG", entry_price=30000.0,
        stop=29994.0, target=30015.0, quantity=1, open=True,
    )
    return b


def _wire(monkeypatch, broker, positions, fills):
    def fake_get(path):
        if path.startswith("/position/list"):
            return positions
        if path.startswith("/fill/list"):
            return fills
        return []
    monkeypatch.setattr(broker, "_get", fake_get)


def test_still_open_when_our_contract_has_net_position(monkeypatch):
    b = _broker(monkeypatch)
    _wire(monkeypatch, b, [{"contractId": OUR_CID, "netPos": 1}], [])
    assert b.resolve_position() is None
    assert b._last_position is not None and b._last_position.open


def test_win_when_target_fill_matches(monkeypatch):
    b = _broker(monkeypatch)
    # entry fill 30002 (≠ intended 30000), exit fill at target 30015.
    _wire(monkeypatch, b, [],
          [{"contractId": OUR_CID, "price": 30002.0}, {"contractId": OUR_CID, "price": 30015.0}])
    fill = b.resolve_position()
    assert fill is not None
    assert fill.result == "WIN" and fill.exit_reason == "TARGET_HIT"
    assert fill.exit_price == 30015.0
    assert fill.entry_price == 30002.0  # real entry fill used, not the planned 30000
    assert b._last_position is None


def test_loss_when_stop_fill_matches(monkeypatch):
    b = _broker(monkeypatch)
    _wire(monkeypatch, b, [{"contractId": OUR_CID, "netPos": 0}],
          [{"contractId": OUR_CID, "price": 30001.0}, {"contractId": OUR_CID, "price": 29994.0}])
    fill = b.resolve_position()
    assert fill is not None
    assert fill.result == "LOSS" and fill.exit_reason == "STOP_HIT"
    assert fill.exit_price == 29994.0 and (fill.pnl_dollars or 0) < 0


def test_other_instrument_open_does_not_block_resolution(monkeypatch):
    b = _broker(monkeypatch)
    _wire(monkeypatch, b, [{"contractId": OTHER_CID, "netPos": 1}],
          [{"contractId": OUR_CID, "price": 30000.0}, {"contractId": OUR_CID, "price": 30015.0}])
    fill = b.resolve_position()
    assert fill is not None and fill.result == "WIN"


def test_unrelated_fill_does_NOT_fabricate_a_win(monkeypatch):
    # THE REGRESSION: flat, but the only fill is far from target/stop (the 30208.75
    # bug). Must NOT book a win — no bracket price matched → retry, position kept.
    b = _broker(monkeypatch)
    _wire(monkeypatch, b, [], [{"contractId": OUR_CID, "price": 30208.75}])
    assert b.resolve_position() is None
    assert b._last_position is not None  # not cleared, not booked
    assert b._resolve_fail_count == 1


def test_flat_no_match_books_breakeven_after_retries(monkeypatch):
    b = _broker(monkeypatch)
    _wire(monkeypatch, b, [], [])  # flat, nothing matches
    assert b.resolve_position() is None  # attempt 1
    assert b.resolve_position() is None  # attempt 2
    fill = b.resolve_position()          # attempt 3 → conservative breakeven at entry
    assert fill is not None and fill.exit_reason == "FORCE_CLOSE_UNMATCHED"
    assert fill.exit_price == 30000.0


def test_auth_failure_leaves_position_open(monkeypatch):
    b = _broker(monkeypatch)
    monkeypatch.setattr(b, "_authenticate", lambda: False)
    _wire(monkeypatch, b, [], [])
    assert b.resolve_position() is None
    assert b._last_position is not None


# ── order-id-scoped resolution (partial fills + overlap-proof) ────────────────
#
# When the live fills carry orderId, resolve scopes them to OUR exact OSO
# orders: partial entry/exit legs average (quantity-weighted), and a fill from
# an overlapping trade can never be mistaken for ours. Falls back to the
# price-matcher when orderId is absent (every test above exercises that path).

ENTRY_OID, TARGET_OID, STOP_OID = 1001, 1002, 1003


def _broker_with_oso(monkeypatch, qty=2):
    b = _broker(monkeypatch)
    b._last_position.quantity = qty
    b._last_order_ids = {
        "instrument": "MNQ", "entry": ENTRY_OID, "target": TARGET_OID, "stop": STOP_OID,
    }
    return b


def test_partial_entry_fills_are_quantity_weighted(monkeypatch):
    b = _broker_with_oso(monkeypatch, qty=2)
    # 2-lot entry filled in two prints (30000, 30002) → avg 30001; target qty2 @30015.
    _wire(monkeypatch, b, [], [
        {"contractId": OUR_CID, "orderId": ENTRY_OID,  "qty": 1, "price": 30000.0},
        {"contractId": OUR_CID, "orderId": ENTRY_OID,  "qty": 1, "price": 30002.0},
        {"contractId": OUR_CID, "orderId": TARGET_OID, "qty": 2, "price": 30015.0},
    ])
    fill = b.resolve_position()
    assert fill.result == "WIN" and fill.exit_reason == "TARGET_HIT"
    assert fill.entry_price == 30001.0          # weighted mean of the two entry legs
    assert fill.exit_price == 30015.0
    assert fill.contracts == 2
    # (30015 − 30001)/0.25 = 56 ticks · $0.50 · 2 = $56.00
    assert fill.pnl_dollars == 56.0


def test_split_target_exit_is_quantity_weighted(monkeypatch):
    b = _broker_with_oso(monkeypatch, qty=2)
    # exit target fills at two prices (30015, 30017) → weighted 30016; single entry.
    _wire(monkeypatch, b, [], [
        {"contractId": OUR_CID, "orderId": ENTRY_OID,  "qty": 2, "price": 30000.0},
        {"contractId": OUR_CID, "orderId": TARGET_OID, "qty": 1, "price": 30015.0},
        {"contractId": OUR_CID, "orderId": TARGET_OID, "qty": 1, "price": 30017.0},
    ])
    fill = b.resolve_position()
    assert fill.exit_reason == "TARGET_HIT" and fill.exit_price == 30016.0
    assert fill.entry_price == 30000.0


def test_order_id_scoping_ignores_an_overlapping_trades_fill(monkeypatch):
    b = _broker_with_oso(monkeypatch, qty=2)
    # A DIFFERENT order (7777) left a fill at a wild price; it must not pollute
    # our entry average — even though it shares our contract.
    _wire(monkeypatch, b, [], [
        {"contractId": OUR_CID, "orderId": 7777,       "qty": 10, "price": 29000.0},
        {"contractId": OUR_CID, "orderId": ENTRY_OID,  "qty": 2,  "price": 30000.0},
        {"contractId": OUR_CID, "orderId": TARGET_OID, "qty": 2,  "price": 30015.0},
    ])
    fill = b.resolve_position()
    assert fill.entry_price == 30000.0 and fill.exit_price == 30015.0
    assert fill.result == "WIN"


def test_falls_back_to_price_match_when_fills_lack_order_id(monkeypatch):
    # ids are set, but the broker returned fills WITHOUT orderId → price path.
    b = _broker_with_oso(monkeypatch, qty=1)
    _wire(monkeypatch, b, [],
          [{"contractId": OUR_CID, "price": 30002.0}, {"contractId": OUR_CID, "price": 30015.0}])
    fill = b.resolve_position()
    assert fill.result == "WIN" and fill.entry_price == 30002.0


def test_instrument_mismatch_falls_back_to_price_match(monkeypatch):
    # order ids belong to a different instrument (stale) → do not scope by id.
    b = _broker_with_oso(monkeypatch, qty=1)
    b._last_order_ids["instrument"] = "MES"
    _wire(monkeypatch, b, [], [
        {"contractId": OUR_CID, "orderId": ENTRY_OID,  "qty": 1, "price": 30002.0},
        {"contractId": OUR_CID, "orderId": TARGET_OID, "qty": 1, "price": 30015.0},
    ])
    fill = b.resolve_position()
    # price-matcher still books the win from the target-priced fill
    assert fill.result == "WIN" and fill.exit_price == 30015.0


def test_order_ids_cleared_after_resolution(monkeypatch):
    b = _broker_with_oso(monkeypatch, qty=2)
    _wire(monkeypatch, b, [], [
        {"contractId": OUR_CID, "orderId": ENTRY_OID,  "qty": 2, "price": 30000.0},
        {"contractId": OUR_CID, "orderId": TARGET_OID, "qty": 2, "price": 30015.0},
    ])
    assert b.resolve_position().result == "WIN"
    assert b._last_position is None
    assert b._last_order_ids is None
