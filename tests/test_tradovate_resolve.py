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
