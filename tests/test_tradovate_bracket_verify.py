"""Tests for Tradovate bracket-child verification + naked-position auto-flatten.

A market entry can fill while its protective stop/target children are rejected,
leaving a naked position. `_verify_bracket_children` detects which children are
live (pure, no side effects); `_handle_naked_position` escalates: loud alert +
immediate flatten, returning a CANCELLED fill so no naked position is held.
"""

from execution.broker_interface import BracketOrder
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


def _broker(monkeypatch, orders):
    """A broker whose _get('/order/list') returns `orders`, with no real sleeps."""
    broker = TradovateBroker(config=TradovateConfig())
    broker._account_id = 555
    monkeypatch.setattr(broker, "_get", lambda path, **kw: orders)
    monkeypatch.setattr("execution.tradovate_broker.time.sleep", lambda *_a, **_k: None)
    return broker


def _order(contract_id=10, action="Sell", otype="Stop", status="Working", account=555):
    return {"contractId": contract_id, "action": action, "orderType": otype,
            "ordStatus": status, "accountId": account}


_BRACKET = BracketOrder(instrument="MES", direction="LONG", entry=5900.0,
                        stop=5893.0, target=5915.0, rr_ratio=2.14,
                        strategy="manual_force_open", contracts=1)


# ── detection (pure) ──────────────────────────────────────────────────────────

def test_both_children_live_returns_true_true(monkeypatch):
    broker = _broker(monkeypatch, [_order(otype="Stop"), _order(otype="Limit")])
    assert broker._verify_bracket_children(10, "Sell", _BRACKET) == (True, True)


def test_missing_stop_returns_false_true(monkeypatch):
    broker = _broker(monkeypatch, [_order(otype="Limit")])  # target only
    assert broker._verify_bracket_children(10, "Sell", _BRACKET, retries=1) == (False, True)


def test_no_children_returns_false_false(monkeypatch):
    broker = _broker(monkeypatch, [])
    assert broker._verify_bracket_children(10, "Sell", _BRACKET, retries=1) == (False, False)


def test_ignores_wrong_contract_action_and_filled_status(monkeypatch):
    orders = [
        _order(contract_id=99, otype="Stop"),      # wrong contract
        _order(action="Buy", otype="Limit"),       # entry side, not close side
        _order(otype="Stop", status="Filled"),     # already filled — not protecting
        _order(otype="Limit", status="Canceled"),  # canceled
    ]
    broker = _broker(monkeypatch, orders)
    assert broker._verify_bracket_children(10, "Sell", _BRACKET, retries=1) == (False, False)


def test_get_failure_is_defensive_treats_as_unprotected(monkeypatch):
    broker = TradovateBroker(config=TradovateConfig())
    broker._account_id = 555

    def _boom(path, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(broker, "_get", _boom)
    monkeypatch.setattr("execution.tradovate_broker.time.sleep", lambda *_a, **_k: None)
    # Must not raise; unverifiable → unprotected.
    assert broker._verify_bracket_children(10, "Sell", _BRACKET, retries=2) == (False, False)


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
    # Even if the flatten itself errors, we must not raise — return CANCELLED.
    fill = broker._handle_naked_position(_BRACKET, qty=1, stop_ok=False, target_ok=False)
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "NAKED_BRACKET_AUTO_FLATTENED"
