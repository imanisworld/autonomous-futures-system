"""
tests/test_tradovate_entry_fill.py

#2 — entry-leg slippage cap. By default the entry is a Market order (legacy,
guaranteed fill). When ENTRY_SLIPPAGE_TOLERANCE_TICKS > 0 the entry is sent as a
Limit capped at entry ± tolerance so a fast breakout can't fill far past plan
(the 2026-06-18 orb fills slipped 26–52 ticks past the planned entry).
"""
from __future__ import annotations

from execution.tradovate_broker import TradovateBroker, TradovateConfig
from execution.broker_interface import BracketOrder
import execution.tradovate_supervisor as supervisor


def _broker(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("TRADOVATE_USERNAME", "x")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "x")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "1")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "x")
    b = TradovateBroker(config=TradovateConfig.from_env())
    monkeypatch.setattr(b, "_authenticate", lambda: True)
    monkeypatch.setattr(b, "_find_contract_id", lambda inst: 123)
    monkeypatch.setattr(supervisor, "tradovate_order_ready", lambda: True)
    b._account_id = 999
    return b


def _capture_body(monkeypatch, b):
    captured = {}
    def fake_post(path, body, **k):
        captured["path"] = path
        captured["body"] = body
        return {}  # no orderId → clean cancelled-fill return, body already captured
    monkeypatch.setattr(b, "_post", fake_post)
    return captured


def _long_order():
    # entry 7559.5, MES tick 0.25 — matches the 2026-06-18 orb_breakout plan
    return BracketOrder(instrument="MES", direction="LONG", entry=7559.5,
                        stop=7557.0, target=7574.5, rr_ratio=6.0, strategy="orb_breakout")


def test_entry_is_market_by_default(monkeypatch):
    monkeypatch.delenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", raising=False)
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_long_order())
    assert cap["body"]["orderType"] == "Market"
    assert "price" not in cap["body"]


def test_entry_is_capped_limit_when_tolerance_set(monkeypatch):
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", "2")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_long_order())
    # LONG cap = entry + 2 ticks = 7559.5 + 0.5 = 7560.0 (snapped to grid)
    assert cap["body"]["orderType"] == "Limit"
    assert cap["body"]["price"] == 7560.0


def test_short_entry_cap_is_below_plan(monkeypatch):
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", "2")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    order = BracketOrder(instrument="MES", direction="SHORT", entry=7550.0,
                         stop=7557.5, target=7527.5, rr_ratio=3.0, strategy="vwap_hold")
    b.execute_bracket(order)
    # SHORT cap = entry - 2 ticks = 7550.0 - 0.5 = 7549.5
    assert cap["body"]["orderType"] == "Limit"
    assert cap["body"]["price"] == 7549.5


# ── #2 limit-entry no-fill lifecycle (IOC emulation: poll → open-or-cancel) ──
import execution.tradovate_broker as tb


def _mock_oso(monkeypatch, b, entry_status, posts=None):
    """placeOSO succeeds (ids 111/222/333); /order/item returns entry_status."""
    posts = posts if posts is not None else []
    def fake_post(path, body, **k):
        posts.append((path, body))
        if path == "/order/placeOSO":
            return {"orderId": 111, "oso1Id": 222, "oso2Id": 333}
        return {}
    def fake_get(path, **k):
        if "/order/item" in path:
            return {"ordStatus": entry_status}
        return []
    monkeypatch.setattr(b, "_post", fake_post)
    monkeypatch.setattr(b, "_get", fake_get)
    monkeypatch.setattr(tb.time, "sleep", lambda *a, **k: None)  # no real backoff in tests
    return posts


def test_limit_entry_fill_opens_position(monkeypatch):
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", "2")
    b = _broker(monkeypatch)
    _mock_oso(monkeypatch, b, entry_status="Filled")
    monkeypatch.setattr(b, "_verify_bracket_children", lambda **k: (True, True))
    fill = b.execute_bracket(_long_order())
    assert fill.result == "OPEN"
    assert b._last_position is not None and b._last_position.open


def test_limit_entry_dead_opens_nothing(monkeypatch):
    # IOC-cancelled / rejected entry → no fill → CANCELLED, journal NOT marked open.
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", "2")
    b = _broker(monkeypatch)
    _mock_oso(monkeypatch, b, entry_status="Canceled")
    fill = b.execute_bracket(_long_order())
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ENTRY_NOT_FILLED"
    assert b._last_position is None


def test_limit_entry_resting_cancels_oso_no_position(monkeypatch):
    # Resting Working limit (IOC ignored) → cancel entry + both children, no position.
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", "2")
    b = _broker(monkeypatch)
    posts = _mock_oso(monkeypatch, b, entry_status="Working")
    fill = b.execute_bracket(_long_order())
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ENTRY_NOT_FILLED"
    assert b._last_position is None
    cancels = {body.get("orderId") for path, body in posts if path == "/order/cancelorder"}
    assert cancels == {111, 222, 333}   # entry + target + stop torn down


def test_market_entry_skips_nofill_guard(monkeypatch):
    # tol=0 → Market entry → no fill poll, no ENTRY_NOT_FILLED path (regression).
    monkeypatch.delenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", raising=False)
    b = _broker(monkeypatch)
    posts = _mock_oso(monkeypatch, b, entry_status="Working")
    monkeypatch.setattr(b, "_verify_bracket_children", lambda **k: (True, True))
    fill = b.execute_bracket(_long_order())
    assert fill.result == "OPEN"
    assert not [p for p, _ in posts if p == "/order/cancelorder"]


# ── #2 per-instrument tolerance (MES 4t / MNQ 16t cannot be one global value) ──
def test_per_instrument_tolerance_mes_vs_mnq(monkeypatch):
    monkeypatch.delenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", raising=False)
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "4")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "16")
    # MES LONG entry 7559.5 → +4 ticks = +1.0pt → cap 7560.5
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_long_order())
    assert cap["body"]["orderType"] == "Limit"
    assert cap["body"]["price"] == 7560.5
    assert cap["body"]["timeInForce"] == "IOC"
    # MNQ LONG entry 30438.75 → +16 ticks = +4.0pt → cap 30442.75 (NOT 1.0pt)
    b2 = _broker(monkeypatch)
    cap2 = _capture_body(monkeypatch, b2)
    mnq = BracketOrder(instrument="MNQ", direction="LONG", entry=30438.75,
                       stop=30418.75, target=30488.75, rr_ratio=2.5, strategy="orb_reclaim")
    b2.execute_bracket(mnq)
    assert cap2["body"]["price"] == 30442.75


def test_global_fallback_when_no_per_instrument(monkeypatch):
    monkeypatch.delenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", raising=False)
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", "2")  # global only
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_long_order())  # MES → no _MES → global 2t → +0.5 → 7560.0
    assert cap["body"]["price"] == 7560.0
