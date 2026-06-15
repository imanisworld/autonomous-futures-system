"""Tests for Tradovate bracket-child verification + naked-position auto-flatten.

A market entry can fill while its protective stop/target children are rejected,
leaving a naked position. `_verify_bracket_children` detects which children are
live (pure, no side effects); `_handle_naked_position` escalates: loud alert +
immediate flatten, returning a CANCELLED fill so no naked position is held.
"""

from execution.broker_interface import BracketOrder
from execution.broker_interface import Position
from execution.tradovate_broker import TradovateBroker, TradovateConfig


def test_config_from_env_accepts_numeric_api_key_id(monkeypatch):
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "13833")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "secret")

    config = TradovateConfig.from_env()

    assert config.cid == 13833
    assert config.secret == "secret"


def test_config_from_env_rejects_pasted_cid_secret_combo(monkeypatch):
    monkeypatch.setenv(
        "TRADOVATE_API_KEY_ID",
        "cid: 13833, secret: 24b71877-b166-4f98-9fe0-7cb3266e6ee1",
    )

    try:
        TradovateConfig.from_env()
    except ValueError as exc:
        assert "numeric CID only" in str(exc)
    else:
        raise AssertionError("malformed TRADOVATE_API_KEY_ID should fail clearly")


def _broker(monkeypatch, items_by_id):
    """A broker whose _get('/order/item?id=N') returns items_by_id[N].

    Models Tradovate's /order/item — the OSO child orders are looked up by the
    oso1Id/oso2Id that placeOSO returns (NOT scanned from /order/list, which
    omits orderType).
    """
    broker = TradovateBroker(config=TradovateConfig())
    broker._account_id = 555

    def _get(path, **kw):
        if "id=" in path:
            oid = int(path.split("id=")[1])
            if oid in items_by_id:
                return items_by_id[oid]
            raise RuntimeError("order not found")
        return []

    monkeypatch.setattr(broker, "_get", _get)
    monkeypatch.setattr("execution.tradovate_broker.time.sleep", lambda *_a, **_k: None)
    return broker


_BRACKET = BracketOrder(instrument="MES", direction="LONG", entry=5900.0,
                        stop=5893.0, target=5915.0, rr_ratio=2.14,
                        strategy="manual_force_open", contracts=1)


# ── detection (via OSO child ids + /order/item status) ────────────────────────

def test_both_children_working_returns_true_true(monkeypatch):
    broker = _broker(monkeypatch, {101: {"ordStatus": "Working"}, 102: {"ordStatus": "Working"}})
    assert broker._verify_bracket_children(stop_id=101, target_id=102, order=_BRACKET) == (True, True)


def test_missing_stop_id_returns_false_true(monkeypatch):
    broker = _broker(monkeypatch, {102: {"ordStatus": "Working"}})
    assert broker._verify_bracket_children(stop_id=None, target_id=102, order=_BRACKET, retries=1) == (False, True)


def test_no_child_ids_returns_false_false(monkeypatch):
    broker = _broker(monkeypatch, {})
    assert broker._verify_bracket_children(stop_id=None, target_id=None, order=_BRACKET, retries=1) == (False, False)


def test_rejected_child_is_not_live(monkeypatch):
    broker = _broker(monkeypatch, {101: {"ordStatus": "Rejected"}, 102: {"ordStatus": "Working"}})
    assert broker._verify_bracket_children(stop_id=101, target_id=102, order=_BRACKET, retries=1) == (False, True)


def test_item_read_failure_fails_closed(monkeypatch):
    """If /order/item is unreadable, treat the child as unconfirmed."""
    broker = TradovateBroker(config=TradovateConfig())
    broker._account_id = 555

    def _boom(path, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(broker, "_get", _boom)
    monkeypatch.setattr("execution.tradovate_broker.time.sleep", lambda *_a, **_k: None)
    assert broker._verify_bracket_children(stop_id=101, target_id=102, order=_BRACKET, retries=2) == (False, False)


# ── read-after-write lag (the real prod bug: /order/item 404s for a few seconds
#    after placeOSO even though the OSO was accepted and the children exist) ────

def test_child_404_then_live_within_window_confirms(monkeypatch):
    """A child id that 404s for the first polls then resolves to Working must
    confirm — NOT be flattened as naked. This is the exact prod failure that
    booked every trade to $0 (Jun 12 + Jun 15: '/order/item id=… failed: 404')."""
    broker = TradovateBroker(config=TradovateConfig())
    broker._account_id = 555
    state = {"stop_calls": 0}

    def _get(path, **kw):
        if "id=101" in path:                 # stop child: 404 twice, then live
            state["stop_calls"] += 1
            if state["stop_calls"] <= 2:
                raise RuntimeError("404 Client Error: Not Found")
            return {"ordStatus": "Working"}
        if "id=102" in path:                 # target child: live immediately
            return {"ordStatus": "Working"}
        return []                            # /order/list: empty (lagging too)

    monkeypatch.setattr(broker, "_get", _get)
    monkeypatch.setattr("execution.tradovate_broker.time.sleep", lambda *_a, **_k: None)
    assert broker._verify_bracket_children(stop_id=101, target_id=102, order=_BRACKET) == (True, True)
    assert state["stop_calls"] == 3          # polled past the 404s instead of failing closed


def test_child_404_but_present_in_order_list_confirms(monkeypatch):
    """If /order/item?id= keeps 404ing but the order is visible in /order/list,
    the list fallback confirms it live (the list often updates before item-by-id)."""
    broker = TradovateBroker(config=TradovateConfig())
    broker._account_id = 555

    def _get(path, **kw):
        if "/order/list" in path:
            return [{"id": 101, "ordStatus": "Working"}, {"id": 102, "ordStatus": "Working"}]
        raise RuntimeError("404 Client Error: Not Found")   # item-by-id always 404s

    monkeypatch.setattr(broker, "_get", _get)
    monkeypatch.setattr("execution.tradovate_broker.time.sleep", lambda *_a, **_k: None)
    assert broker._verify_bracket_children(stop_id=101, target_id=102, order=_BRACKET) == (True, True)


def test_child_dead_in_order_list_fails_immediately(monkeypatch):
    """A child that 404s by id but shows Rejected in /order/list is genuinely dead."""
    broker = TradovateBroker(config=TradovateConfig())
    broker._account_id = 555

    def _get(path, **kw):
        if "/order/list" in path:
            return [{"id": 101, "ordStatus": "Rejected"}, {"id": 102, "ordStatus": "Working"}]
        raise RuntimeError("404 Client Error: Not Found")

    monkeypatch.setattr(broker, "_get", _get)
    monkeypatch.setattr("execution.tradovate_broker.time.sleep", lambda *_a, **_k: None)
    assert broker._verify_bracket_children(stop_id=101, target_id=102, order=_BRACKET) == (False, True)


def test_child_404_entire_window_fails_closed(monkeypatch):
    """If neither /order/item nor /order/list ever confirms within the window,
    still fail closed → naked-flatten path. Safety is preserved."""
    broker = TradovateBroker(config=TradovateConfig())
    broker._account_id = 555

    def _get(path, **kw):
        if "/order/list" in path:
            return []                         # never shows up
        raise RuntimeError("404 Client Error: Not Found")

    monkeypatch.setattr(broker, "_get", _get)
    monkeypatch.setattr("execution.tradovate_broker.time.sleep", lambda *_a, **_k: None)
    # small window so the test is fast; real default is wider
    assert broker._verify_bracket_children(stop_id=101, target_id=102, order=_BRACKET, retries=4) == (False, False)


def test_default_confirm_window_is_wider_than_legacy(monkeypatch):
    """Guard the regression: the default poll window must be materially wider
    than the old 3×0.5s that mistook read-lag for a missing bracket."""
    assert TradovateBroker._BRACKET_CONFIRM_RETRIES >= 8
    assert TradovateBroker._BRACKET_CONFIRM_RETRIES * TradovateBroker._BRACKET_CONFIRM_DELAY >= 5.0


def test_flatten_liquidates_before_cancel(monkeypatch):
    broker = TradovateBroker(config=TradovateConfig())
    broker._account_id = 555
    calls = []
    pos = Position(
        instrument="MES",
        direction="LONG",
        entry_price=5900.0,
        stop=5893.0,
        target=5915.0,
        quantity=1,
        open=True,
    )

    monkeypatch.setattr(broker, "_authenticate", lambda: True)
    monkeypatch.setattr(broker, "get_position", lambda: calls.append("get_position") or pos)
    monkeypatch.setattr(broker, "_find_contract_id", lambda instrument: 123)
    monkeypatch.setattr(broker, "_post", lambda path, body: calls.append(path) or {"ok": True})
    monkeypatch.setattr(broker, "_cancel_working_orders", lambda: calls.append("cancel_orders") or 2)

    result = broker.flatten_position()

    assert result["close_sent"] is True
    assert result["cancelled_orders"] is True
    assert calls == ["get_position", "/order/liquidateposition", "cancel_orders"]


def test_flatten_repolls_position_before_deciding_no_liquidation(monkeypatch):
    broker = TradovateBroker(config=TradovateConfig())
    calls = []

    monkeypatch.setattr(broker, "_authenticate", lambda: True)
    monkeypatch.setattr(broker, "get_position", lambda: calls.append("get_position") or None)
    monkeypatch.setattr(broker, "_cancel_working_orders", lambda: calls.append("cancel_orders") or 0)

    result = broker.flatten_position()

    assert result["close_sent"] is False
    assert calls == ["get_position", "cancel_orders"]


# ── escalation: alert + auto-flatten ──────────────────────────────────────────

def test_handle_naked_position_alerts_flattens_and_returns_cancelled(monkeypatch):
    broker = TradovateBroker(config=TradovateConfig())
    alerts, flattens = [], []
    monkeypatch.setattr(broker, "_alert_naked_position",
                        lambda order, *, stop_ok, target_ok: alerts.append((stop_ok, target_ok)))
    monkeypatch.setattr(broker, "flatten_position",
                        lambda: flattens.append(True) or {"close_sent": True, "cancelled_orders": True})

    fill = broker._handle_naked_position(_BRACKET, qty=1, stop_ok=False, target_ok=True)

    assert alerts == [(False, True)]          # alerted with the missing side
    assert flattens == [True]                 # flatten actually invoked
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "NAKED_BRACKET_AUTO_FLATTENED"


def test_handle_naked_position_survives_flatten_failure(monkeypatch):
    broker = TradovateBroker(config=TradovateConfig())
    monkeypatch.setattr(broker, "_alert_naked_position", lambda *a, **k: None)

    def _boom():
        raise RuntimeError("liquidate failed")

    monkeypatch.setattr(broker, "flatten_position", _boom)
    # Even if the flatten itself errors, we must not raise. And — fail CLOSED —
    # an unconfirmed close must NOT be booked flat: return OPEN (assume the
    # position is live) so the runner keeps tracking it for manual reconciliation.
    fill = broker._handle_naked_position(_BRACKET, qty=1, stop_ok=False, target_ok=False)
    assert fill.result == "OPEN"
    assert fill.exit_reason == "NAKED_FLATTEN_UNCONFIRMED"
