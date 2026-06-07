"""
tests/test_tradovate_failclosed.py

Fail-CLOSED guarantees for the close / naked-flatten paths. A failed broker close
must NEVER be reported as success, and a naked position whose flatten can't be
confirmed must be treated as STILL OPEN (not silently booked flat). This is the
class of bug that produced a phantom flat-vs-live mismatch.
"""
from __future__ import annotations

from types import SimpleNamespace

from execution.tradovate_broker import TradovateBroker, TradovateConfig
from execution.broker_interface import BracketOrder


def _broker(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("TRADOVATE_USERNAME", "x")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "x")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "1")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "x")
    b = TradovateBroker(config=TradovateConfig.from_env())
    monkeypatch.setattr(b, "_authenticate", lambda: True)
    b._account_id = 999
    return b


def _open_pos():
    return SimpleNamespace(open=True, instrument="MNQ", direction="SHORT", quantity=1)


def _order():
    return BracketOrder(instrument="MNQ", direction="SHORT", entry=30000.0,
                        stop=30008.0, target=29976.0, rr_ratio=3.0, strategy="vwap_hold")


# ── #1: flatten reflects the real liquidate result ───────────────────────────

def test_flatten_fails_closed_when_liquidate_rejected(monkeypatch):
    b = _broker(monkeypatch)
    monkeypatch.setattr(b, "get_position", _open_pos)
    monkeypatch.setattr(b, "_find_contract_id", lambda inst: 123)
    monkeypatch.setattr(b, "_cancel_working_orders", lambda: 0)
    # Tradovate 200-with-failure body → must NOT be reported as a close.
    monkeypatch.setattr(b, "_post", lambda p, body, **k: {"failureReason": "NotAllowed"})
    result = b.flatten_position()
    assert result["close_sent"] is False
    assert "liquidate rejected" in (result.get("error") or "")


def test_flatten_reports_success_on_clean_liquidate(monkeypatch):
    b = _broker(monkeypatch)
    monkeypatch.setattr(b, "get_position", _open_pos)
    monkeypatch.setattr(b, "_find_contract_id", lambda inst: 123)
    monkeypatch.setattr(b, "_cancel_working_orders", lambda: 1)
    monkeypatch.setattr(b, "_post", lambda p, body, **k: {"orderId": 555})
    result = b.flatten_position()
    assert result["close_sent"] is True


# ── #2: naked-position handler fails closed ──────────────────────────────────

def test_naked_position_open_when_flatten_unconfirmed(monkeypatch):
    b = _broker(monkeypatch)
    monkeypatch.setattr(b, "_alert_naked_position", lambda *a, **k: None)
    monkeypatch.setattr(b, "flatten_position",
                        lambda: {"close_sent": False, "cancelled_orders": True})
    fill = b._handle_naked_position(_order(), 1, stop_ok=False, target_ok=True)
    assert fill.result == "OPEN"                       # assume live — fail closed
    assert fill.exit_reason == "NAKED_FLATTEN_UNCONFIRMED"


def test_naked_position_open_when_flatten_raises(monkeypatch):
    b = _broker(monkeypatch)
    monkeypatch.setattr(b, "_alert_naked_position", lambda *a, **k: None)

    def boom():
        raise RuntimeError("broker down")

    monkeypatch.setattr(b, "flatten_position", boom)
    fill = b._handle_naked_position(_order(), 1, stop_ok=False, target_ok=True)
    assert fill.result == "OPEN"                       # exception → assume live


def test_naked_position_flat_only_when_close_confirmed(monkeypatch):
    b = _broker(monkeypatch)
    monkeypatch.setattr(b, "_alert_naked_position", lambda *a, **k: None)
    monkeypatch.setattr(b, "flatten_position",
                        lambda: {"close_sent": True, "cancelled_orders": True})
    fill = b._handle_naked_position(_order(), 1, stop_ok=False, target_ok=True)
    assert fill.result == "CANCELLED"                  # confirmed close → flat
    assert fill.exit_reason == "NAKED_BRACKET_AUTO_FLATTENED"
