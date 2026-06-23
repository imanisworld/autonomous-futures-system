"""
tests/test_order_id_persistence.py

Persist + restore Tradovate OSO order ids across a restart, so resolve_position()
keeps order-id exit attribution instead of degrading to price-matching (which can
misbook a slipped-stop LOSS as BREAKEVEN). Attribution-only — no change to order
placement, risk, strategy, paper behaviour, or live permissions.
"""
from __future__ import annotations

import pytest

from execution.broker_interface import Position
from execution.paper_broker import PaperBroker
from execution.tradovate_broker import TradovateBroker, TradovateConfig
from journal.journal_logger import JournalLogger

OUR_CID = 12345


def _trade_record(instrument="MNQ", direction="LONG"):
    return {
        "ts": "2026-06-23T15:00:00+00:00",
        "decision": "TRADE",
        "instrument": instrument,
        "setup": {"direction": direction, "entry": 30000.0, "stop": 29994.0,
                  "target": 30015.0, "contracts": 1},
    }


# ─── 1. order ids written with the open position record ───────────────────────

def test_order_ids_persist_with_open_position(tmp_path):
    j = JournalLogger(log_dir=str(tmp_path))
    j.log_decision(_trade_record(), {"result": "APPROVED", "failed_rule": None, "reason": None})
    j.log_order_ids(instrument="MNQ", session="new_york",
                    order_ids={"instrument": "MNQ", "entry": "E1", "target": "T1", "stop": "S1"})

    pos = j.get_open_position()
    assert pos is not None
    assert pos["order_ids"] == {"instrument": "MNQ", "entry": "E1", "target": "T1", "stop": "S1"}


# ─── 5 (data side) + 4: a position with NO order-ids record is still valid ─────

def test_open_position_without_order_ids_has_none(tmp_path):
    j = JournalLogger(log_dir=str(tmp_path))
    j.log_decision(_trade_record(), {"result": "APPROVED", "failed_rule": None, "reason": None})

    pos = j.get_open_position()
    assert pos is not None
    assert pos.get("order_ids") is None  # absent → safe fallback downstream


def test_order_ids_cleared_after_outcome(tmp_path):
    j = JournalLogger(log_dir=str(tmp_path))
    j.log_decision(_trade_record(), {"result": "APPROVED", "failed_rule": None, "reason": None})
    j.log_order_ids(instrument="MNQ", session="new_york", order_ids={"instrument": "MNQ", "stop": "S1"})
    j.log_outcome(instrument="MNQ", session="new_york", result="WIN", entry_price=30000.0,
                  exit_price=30015.0, exit_reason="TARGET_HIT", pnl_ticks=60.0, pnl_dollars=30.0)
    assert j.get_open_position() is None  # closed → no leaked open/order-ids


# ─── resolve helpers (mirror tests/test_tradovate_resolve.py) ─────────────────

def _broker(monkeypatch):
    for k, v in {"TRADOVATE_ENV": "demo", "TRADOVATE_USERNAME": "x", "TRADOVATE_PASSWORD": "x",
                 "TRADOVATE_API_KEY_ID": "1", "TRADOVATE_API_KEY_SECRET": "x"}.items():
        monkeypatch.setenv(k, v)
    b = TradovateBroker(config=TradovateConfig.from_env())
    b._account_id = 1
    b._resolve_fail_count = 0
    monkeypatch.setattr(b, "_authenticate", lambda: True)
    monkeypatch.setattr(b, "_find_contract_id", lambda inst: OUR_CID)
    b._last_position = Position(instrument="MNQ", direction="LONG", entry_price=30000.0,
                                stop=29994.0, target=30015.0, quantity=1, open=True)
    return b


def _wire(monkeypatch, broker, fills):
    def fake_get(path):
        if path.startswith("/position/list"):
            return []  # our contract is flat → bracket closed it
        if path.startswith("/fill/list"):
            return fills
        return []
    monkeypatch.setattr(broker, "_get", fake_get)


# ─── 2 + 3: restored order ids drive correct attribution on a SLIPPED stop ─────

def test_restored_order_ids_book_correct_loss_on_slipped_stop(tmp_path, monkeypatch):
    # Persist → reconstruct → restore into a fresh broker (the restart path).
    j = JournalLogger(log_dir=str(tmp_path))
    j.log_decision(_trade_record(), {"result": "APPROVED", "failed_rule": None, "reason": None})
    j.log_order_ids(instrument="MNQ", session="new_york",
                    order_ids={"instrument": "MNQ", "entry": "E1", "target": "T1", "stop": "S1"})
    restored = j.get_open_position()["order_ids"]

    b = _broker(monkeypatch)
    b._last_order_ids = restored  # what webhook/runner does on restart
    # Stop slipped to 29985 — 9pts past the 29994 stop, well outside the 2-tick
    # price-match tolerance. Only order-id matching can attribute it correctly.
    _wire(monkeypatch, b, [
        {"contractId": OUR_CID, "price": 30002.0, "orderId": "E1"},
        {"contractId": OUR_CID, "price": 29985.0, "orderId": "S1"},
    ])
    fill = b.resolve_position()
    assert fill is not None
    assert fill.result == "LOSS"          # correctly booked, NOT a breakeven scratch
    assert fill.exit_reason == "STOP_HIT"
    assert fill.exit_price == 29985.0


# ─── illustrates the bug the fix prevents: no ids + slipped stop → misbook ─────

def test_missing_order_ids_slipped_stop_misbooks_breakeven(monkeypatch):
    b = _broker(monkeypatch)
    b._last_order_ids = None  # fresh broker after restart, ids not restored
    _wire(monkeypatch, b, [
        {"contractId": OUR_CID, "price": 30002.0},  # no orderId
        {"contractId": OUR_CID, "price": 29985.0},  # slipped stop, no orderId
    ])
    # Price-match can't match 29985 to stop 29994 within tolerance → after 3
    # retries it force-closes at entry = BREAKEVEN (the real LOSS is lost).
    assert b.resolve_position() is None
    assert b.resolve_position() is None
    fill = b.resolve_position()
    assert fill.exit_reason == "FORCE_CLOSE_UNMATCHED"
    assert fill.result == "BREAKEVEN"


# ─── 4: missing order ids still resolve safely on a CLEAN exit (price-match) ────

def test_missing_order_ids_clean_target_falls_back_to_price_match(monkeypatch):
    b = _broker(monkeypatch)
    b._last_order_ids = None
    _wire(monkeypatch, b, [
        {"contractId": OUR_CID, "price": 30002.0},
        {"contractId": OUR_CID, "price": 30015.0},  # clean target fill, no orderId
    ])
    fill = b.resolve_position()
    assert fill is not None
    assert fill.result == "WIN" and fill.exit_reason == "TARGET_HIT"


# ─── 5: PaperBroker is untouched — has no order ids; persist guard skips it ─────

def test_paper_broker_has_no_order_ids():
    b = PaperBroker()
    assert getattr(b, "_last_order_ids", None) is None  # persist guard → no-op for paper


# ─── 6: live trading remains disabled (this change does not enable it) ─────────

def test_live_trading_still_blocked(monkeypatch):
    from config.settings import load_config, LiveTradingBlockedError
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(LiveTradingBlockedError):
        load_config()
