"""
tests/test_tradovate_resolve.py

Locks in the stateless-safe, instrument-aware resolve_position() fix.

Before the fix, get_position() fell back to the stale cached _last_position
(open=True) whenever Tradovate reported flat, so a position closed server-side
by its OSO bracket never resolved in the journal — it stuck open and the
one-position rule blocked all further trades. resolve_position now judges
closure on OUR contract via /position/list and only books an outcome when a
real fill for that contract is visible.
"""

from __future__ import annotations

import pytest

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
    b._last_position = Position(
        instrument="MNQ", direction="LONG", entry_price=30000.0,
        stop=29994.0, target=30015.0, quantity=1, open=True,
    )
    return b


def _wire_get(monkeypatch, broker, positions, fills):
    def fake_get(path):
        if path.startswith("/position/list"):
            return positions
        if path.startswith("/fill/list"):
            return fills
        return []
    monkeypatch.setattr(broker, "_get", fake_get)


def test_still_open_when_our_contract_has_net_position(monkeypatch):
    b = _broker(monkeypatch)
    _wire_get(monkeypatch, b, [{"contractId": OUR_CID, "netPos": 1}], [])
    assert b.resolve_position() is None
    assert b._last_position is not None and b._last_position.open  # untouched


def test_resolves_win_when_flat_and_target_fill_present(monkeypatch):
    b = _broker(monkeypatch)
    _wire_get(monkeypatch, b, [], [{"contractId": OUR_CID, "price": 30015.0}])
    fill = b.resolve_position()
    assert fill is not None
    assert fill.result == "WIN" and fill.exit_reason == "TARGET_HIT"
    assert fill.exit_price == 30015.0
    assert b._last_position is None  # cleared so journal can mark it closed


def test_resolves_loss_when_flat_line_item_and_stop_fill(monkeypatch):
    b = _broker(monkeypatch)
    # netPos==0 line item (flat but present) must count as closed, not open.
    _wire_get(monkeypatch, b, [{"contractId": OUR_CID, "netPos": 0}], [{"contractId": OUR_CID, "price": 29994.0}])
    fill = b.resolve_position()
    assert fill is not None and fill.result == "LOSS" and fill.exit_reason == "STOP_HIT"


def test_other_instrument_open_does_not_block_our_resolution(monkeypatch):
    b = _broker(monkeypatch)
    # A different contract is open; OURS is flat → we must still resolve.
    _wire_get(monkeypatch, b, [{"contractId": OTHER_CID, "netPos": 1}], [{"contractId": OUR_CID, "price": 30015.0}])
    fill = b.resolve_position()
    assert fill is not None and fill.result == "WIN"


def test_flat_but_no_fill_yet_retries_instead_of_guessing(monkeypatch):
    b = _broker(monkeypatch)
    _wire_get(monkeypatch, b, [], [])  # flat, but fills not visible yet
    assert b.resolve_position() is None
    assert b._last_position is not None  # NOT cleared — retry next bar
    assert b._resolve_fail_count == 1


def test_auth_failure_leaves_position_open(monkeypatch):
    b = _broker(monkeypatch)
    monkeypatch.setattr(b, "_authenticate", lambda: False)
    _wire_get(monkeypatch, b, [], [])
    assert b.resolve_position() is None
    assert b._last_position is not None  # can't tell → never book a guess
