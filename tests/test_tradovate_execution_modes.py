"""
tests/test_tradovate_execution_modes.py

Entry execution modes (TRADOVATE_ENTRY_EXECUTION_MODE) + the reliability
hardening that shipped with them:

- default ("legacy") payload is semantically unchanged (Market entry, same
  bracket shape, isAutomated=true) and legacy IOC-limit behavior is untouched;
- marketable_limit / stop_market / stop_limit build the correct native
  Tradovate parent legs, tick-rounded, with the minimum-R:R cap preserved;
- all non-legacy modes hard-fail when TRADOVATE_ENV=live;
- every automated submission carries isAutomated=true (fail-closed guard);
- 429 handling backs off with bounded retries and NEVER retries order paths;
- client-order-identity idempotency: a duplicate or ambiguous submission can
  never create a second parent order;
- provider failures (NoQuote/LiquidationOnly/max-position/...) classify
  explicitly and fail closed — no alternate routing;
- partial entry fills can never leave the journaled quantity larger than the
  actual filled quantity.
"""
from __future__ import annotations

import pytest

import execution.tradovate_supervisor as supervisor
from execution.broker_interface import BracketOrder
from execution.no_fill_taxonomy import (
    NO_FILL_LIQUIDATION_ONLY,
    NO_FILL_MAX_POSITION,
    NO_FILL_NO_QUOTE,
    classify_provider_failure,
)
from execution.tradovate_broker import (
    ENTRY_EXECUTION_MODES,
    TradovateBroker,
    TradovateConfig,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "TRADOVATE_ENTRY_EXECUTION_MODE",
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS",
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES",
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ",
        "MARKETABLE_LIMIT_TICKS",
        "MARKETABLE_LIMIT_TICKS_MES",
        "STOP_LIMIT_ALLOWANCE_TICKS",
        "STOP_LIMIT_ALLOWANCE_TICKS_MES",
        "STOP_ENTRY_CONFIRM_SECONDS",
        "EXIT_MODE",
        "RUNNER_LIVE_ENABLED",
        "LIVE_TRADING_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)
    TradovateBroker._reset_client_order_registry()
    yield
    TradovateBroker._reset_client_order_registry()


def _broker(monkeypatch, env: str = "demo"):
    monkeypatch.setenv("TRADOVATE_ENV", env)
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


def _capture_body(monkeypatch, b, result=None):
    captured = {"calls": 0}

    def fake_post(path, body, **k):
        captured["calls"] += 1
        captured["path"] = path
        captured["body"] = body
        return dict(result or {})

    monkeypatch.setattr(b, "_post", fake_post)
    return captured


def _long_order(**overrides):
    kwargs = dict(
        instrument="MES", direction="LONG", entry=7559.5,
        stop=7557.0, target=7574.5, rr_ratio=6.0, strategy="orb_breakout",
    )
    kwargs.update(overrides)
    return BracketOrder(**kwargs)


def _short_order(**overrides):
    kwargs = dict(
        instrument="MES", direction="SHORT", entry=7559.5,
        stop=7562.0, target=7544.5, rr_ratio=6.0, strategy="orb_breakout",
    )
    kwargs.update(overrides)
    return BracketOrder(**kwargs)


# ── 1-2: default and IOC behavior unchanged ──────────────────────────────────

def test_default_payload_semantically_unchanged(monkeypatch):
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_long_order())
    assert cap["body"] == {
        "accountSpec": "x",
        "accountId": 999,
        "action": "Buy",
        "symbol": "MES",
        "orderQty": 1,
        "orderType": "Market",
        "isAutomated": True,
        "bracket1": {
            "action": "Sell", "orderType": "Limit",
            "price": 7574.5, "timeInForce": "GTC",
        },
        "bracket2": {
            "action": "Sell", "orderType": "Stop",
            "stopPrice": 7557.0, "timeInForce": "GTC",
        },
    }


def test_legacy_ioc_limit_behavior_unchanged(monkeypatch):
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", "2")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_long_order())
    assert cap["body"]["orderType"] == "Limit"
    assert cap["body"]["price"] == 7560.0
    assert cap["body"]["timeInForce"] == "IOC"
    assert cap["body"]["isAutomated"] is True


def test_explicit_ioc_limit_mode_matches_legacy_leg(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "ioc_limit")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", "2")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_long_order())
    assert cap["body"]["orderType"] == "Limit"
    assert cap["body"]["price"] == 7560.0
    assert cap["body"]["timeInForce"] == "IOC"


def test_explicit_ioc_limit_without_tolerance_fails_closed(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "ioc_limit")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    fill = b.execute_bracket(_long_order())
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "EXECUTION_MODE_MISCONFIGURED"
    assert cap["calls"] == 0


def test_market_mode_ignores_tolerance(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "market")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", "2")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_long_order())
    assert cap["body"]["orderType"] == "Market"
    assert "price" not in cap["body"]
    assert cap["body"]["isAutomated"] is True


# ── 3: marketable_limit ──────────────────────────────────────────────────────

def test_marketable_limit_long_payload(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "marketable_limit")
    monkeypatch.setenv("MARKETABLE_LIMIT_TICKS_MES", "8")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_long_order())
    # LONG: entry 7559.5 pushed through by 8 ticks (2.0) → 7561.5,
    # below the 2R cap (7562.75) so uncapped.
    assert cap["body"]["orderType"] == "Limit"
    assert cap["body"]["price"] == 7561.5
    assert cap["body"]["timeInForce"] == "IOC"
    assert cap["body"]["isAutomated"] is True


def test_marketable_limit_short_payload(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "marketable_limit")
    monkeypatch.setenv("MARKETABLE_LIMIT_TICKS_MES", "8")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_short_order())
    # SHORT: entry 7559.5 pushed down 2.0 → 7557.5, above the 2R cap 7556.25.
    assert cap["body"]["orderType"] == "Limit"
    assert cap["body"]["price"] == 7557.5
    assert cap["body"]["timeInForce"] == "IOC"


def test_marketable_limit_cannot_violate_min_rr(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "marketable_limit")
    monkeypatch.setenv("MARKETABLE_LIMIT_TICKS_MES", "64")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_long_order())
    # 64 ticks (16.0) would reach 7575.5 — capped at the 2R-preserving
    # boundary (7574.5 + 2*7557.0)/3 = 7562.833 → tick-floored 7562.75.
    assert cap["body"]["price"] == 7562.75


def test_marketable_limit_zero_ticks_fails_closed(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "marketable_limit")
    monkeypatch.setenv("MARKETABLE_LIMIT_TICKS_MES", "0")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    fill = b.execute_bracket(_long_order())
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "EXECUTION_MODE_MISCONFIGURED"
    assert cap["calls"] == 0


# ── 4-5: stop_market / stop_limit ────────────────────────────────────────────

def test_stop_market_long_payload(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "stop_market")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_long_order())
    assert cap["body"]["orderType"] == "Stop"
    assert cap["body"]["stopPrice"] == 7559.5
    assert "price" not in cap["body"]
    assert cap["body"]["isAutomated"] is True
    assert cap["body"]["bracket1"]["orderType"] == "Limit"
    assert cap["body"]["bracket2"]["orderType"] == "Stop"


def test_stop_market_short_payload(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "stop_market")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_short_order())
    assert cap["body"]["orderType"] == "Stop"
    assert cap["body"]["stopPrice"] == 7559.5
    assert cap["body"]["action"] == "Sell"


def test_stop_limit_long_payload(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "stop_limit")
    monkeypatch.setenv("STOP_LIMIT_ALLOWANCE_TICKS_MES", "4")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_long_order())
    assert cap["body"]["orderType"] == "StopLimit"
    assert cap["body"]["stopPrice"] == 7559.5
    assert cap["body"]["price"] == 7560.5  # trigger + 4 ticks, under the R:R cap
    assert cap["body"]["isAutomated"] is True


def test_stop_limit_short_payload(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "stop_limit")
    monkeypatch.setenv("STOP_LIMIT_ALLOWANCE_TICKS_MES", "4")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_short_order())
    assert cap["body"]["orderType"] == "StopLimit"
    assert cap["body"]["stopPrice"] == 7559.5
    assert cap["body"]["price"] == 7558.5  # trigger − 4 ticks (above 2R cap 7556.25)


def test_stop_limit_allowance_capped_by_min_rr(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "stop_limit")
    monkeypatch.setenv("STOP_LIMIT_ALLOWANCE_TICKS_MES", "64")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(_long_order())
    assert cap["body"]["price"] == 7562.75


# ── 6: tick rounding ─────────────────────────────────────────────────────────

def test_stop_trigger_and_brackets_tick_round(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "stop_market")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    b.execute_bracket(
        _long_order(entry=7559.61, stop=7556.99, target=7574.52)
    )
    assert cap["body"]["stopPrice"] == 7559.5
    assert cap["body"]["bracket1"]["price"] == 7574.5
    assert cap["body"]["bracket2"]["stopPrice"] == 7557.0


# ── 8: live hard block ───────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", [m for m in ENTRY_EXECUTION_MODES if m != "legacy"])
def test_non_legacy_modes_hard_blocked_in_live(monkeypatch, mode):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", mode)
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    b = _broker(monkeypatch, env="live")
    import execution.live_preflight as preflight
    monkeypatch.setattr(preflight, "live_order_ready", lambda: True)
    cap = _capture_body(monkeypatch, b)
    fill = b.execute_bracket(_long_order())
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "EXECUTION_MODE_NOT_ALLOWED_LIVE"
    assert cap["calls"] == 0


def test_unknown_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "yolo")
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b)
    fill = b.execute_bracket(_long_order())
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "EXECUTION_MODE_INVALID"
    assert cap["calls"] == 0


# ── 9: isAutomated compliance guard ──────────────────────────────────────────

def test_post_guard_rejects_order_creation_without_isautomated(monkeypatch):
    b = _broker(monkeypatch)
    sent = {}
    monkeypatch.setattr(
        b, "_send", lambda method, path, **k: sent.update(path=path) or {}
    )
    with pytest.raises(ValueError, match="isAutomated"):
        b._post("/order/placeOSO", {"orderQty": 1})
    with pytest.raises(ValueError, match="isAutomated"):
        b._post("/order/placeOrder", {"isAutomated": False})
    assert not sent
    b._post("/order/placeOSO", {"isAutomated": True})
    assert sent["path"] == "/order/placeOSO"


def test_non_order_posts_bypass_guard(monkeypatch):
    b = _broker(monkeypatch)
    sent = {}
    monkeypatch.setattr(
        b, "_send", lambda method, path, **k: sent.update(path=path) or {}
    )
    b._post("/order/cancelorder", {"orderId": 1})
    assert sent["path"] == "/order/cancelorder"


# ── 10: 429 backoff ──────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, status_code, headers=None, payload=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_429_backs_off_bounded_and_honors_retry_after(monkeypatch):
    import execution.tradovate_broker as tb
    b = _broker(monkeypatch)
    sleeps = []
    monkeypatch.setattr(tb.time, "sleep", lambda s: sleeps.append(s))
    responses = [
        _FakeResp(429, headers={"Retry-After": "7"}),
        _FakeResp(429),
        _FakeResp(200, payload={"ok": True}),
    ]

    class _FakeSession:
        def get(self, url, **kwargs):
            return responses.pop(0)

    b._session = _FakeSession()
    assert b._get("/account/list") == {"ok": True}
    assert sleeps[0] == 7.0            # server-requested wait honored
    assert 0.5 <= sleeps[1] <= 30.0    # bounded exponential fallback
    assert len(sleeps) == 2            # exactly 2 retries — no tight loop


def test_429_on_order_creation_is_never_retried(monkeypatch):
    import execution.tradovate_broker as tb
    import requests
    b = _broker(monkeypatch)
    sleeps = []
    monkeypatch.setattr(tb.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    class _FakeSession:
        def post(self, url, **kwargs):
            calls["n"] += 1
            return _FakeResp(429)

    b._session = _FakeSession()
    with pytest.raises(requests.HTTPError):
        b._post("/order/placeOSO", {"isAutomated": True})
    assert calls["n"] == 1
    assert sleeps == []


# ── 11: client-order-identity idempotency ────────────────────────────────────

def test_duplicate_client_order_id_refused(monkeypatch):
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b, result={"orderId": 11, "oso1Id": 12, "oso2Id": 13})
    monkeypatch.setattr(b, "_verify_bracket_children", lambda **k: (True, True))
    order = _long_order(client_order_id="AFS-abc")
    first = b.execute_bracket(order)
    assert first.result == "OPEN"
    assert cap["body"]["clOrdId"] == "AFS-abc"
    second = b.execute_bracket(_long_order(client_order_id="AFS-abc"))
    assert second.result == "CANCELLED"
    assert second.exit_reason == "DUPLICATE_CLIENT_ORDER_ID"
    assert cap["calls"] == 1  # the broker never fired a second OSO


def test_ambiguous_submission_blocks_retry_until_reconciled(monkeypatch):
    b = _broker(monkeypatch)
    calls = {"n": 0}

    def exploding_post(path, body, **k):
        calls["n"] += 1
        raise TimeoutError("socket timeout after send")

    monkeypatch.setattr(b, "_post", exploding_post)
    first = b.execute_bracket(_long_order(client_order_id="AFS-amb"))
    assert first.result == "CANCELLED"          # outer handler fails closed
    retry = b.execute_bracket(_long_order(client_order_id="AFS-amb"))
    assert retry.result == "CANCELLED"
    assert retry.exit_reason == "SUBMIT_AMBIGUOUS_UNRECONCILED"
    assert calls["n"] == 1                      # no blind re-fire


def test_explicit_rejection_frees_identity_for_future_signals(monkeypatch):
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b, result={"failureReason": "NoQuote"})
    first = b.execute_bracket(_long_order(client_order_id="AFS-rej"))
    assert first.result == "CANCELLED"
    # A clean rejection created nothing server-side — same identity may retry.
    second = b.execute_bracket(_long_order(client_order_id="AFS-rej"))
    assert second.result == "CANCELLED"
    assert cap["calls"] == 2


# ── 12: provider-failure classification, fail closed ─────────────────────────

@pytest.mark.parametrize(
    "reason,bucket",
    [
        ("NoQuote", NO_FILL_NO_QUOTE),
        ("LiquidationOnly", NO_FILL_LIQUIDATION_ONLY),
        ("BackMonthProhibited", NO_FILL_LIQUIDATION_ONLY),
        ("MaxPositionLimit exceeded", NO_FILL_MAX_POSITION),
    ],
)
def test_provider_failures_classified_and_fail_closed(monkeypatch, reason, bucket):
    b = _broker(monkeypatch)
    cap = _capture_body(monkeypatch, b, result={"failureReason": reason})
    fill = b.execute_bracket(_long_order())
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "TRADOVATE_REJECTED"
    assert fill.no_fill_reason == bucket
    assert cap["calls"] == 1  # no alternate execution route was attempted


def test_classify_provider_failure_unknown_returns_none():
    assert classify_provider_failure("SomethingNovel") is None
    assert classify_provider_failure(None) is None


# ── 13: partial fills never leave an oversized journaled quantity ────────────

def test_partial_entry_fill_resizes_position_to_actual(monkeypatch):
    b = _broker(monkeypatch)
    _capture_body(monkeypatch, b, result={"orderId": 11, "oso1Id": 12, "oso2Id": 13})
    monkeypatch.setattr(b, "_verify_bracket_children", lambda **k: (True, True))
    monkeypatch.setattr(b, "_entry_filled_qty", lambda oid: 1)
    fill = b.execute_bracket(_long_order(contracts=2))
    assert fill.result == "OPEN"
    assert fill.contracts == 1
    assert b._last_position.quantity == 1


def test_full_fill_keeps_submitted_quantity(monkeypatch):
    b = _broker(monkeypatch)
    _capture_body(monkeypatch, b, result={"orderId": 11, "oso1Id": 12, "oso2Id": 13})
    monkeypatch.setattr(b, "_verify_bracket_children", lambda **k: (True, True))
    monkeypatch.setattr(b, "_entry_filled_qty", lambda oid: 2)
    fill = b.execute_bracket(_long_order(contracts=2))
    assert fill.result == "OPEN"
    assert fill.contracts == 2
    assert b._last_position.quantity == 2


# ── stop entries fail closed when unfilled in their window ───────────────────

def test_stop_entry_unfilled_cancels_oso_and_fails_closed(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "stop_market")
    monkeypatch.setenv("STOP_ENTRY_CONFIRM_SECONDS", "2")
    b = _broker(monkeypatch)
    _capture_body(monkeypatch, b, result={"orderId": 11, "oso1Id": 12, "oso2Id": 13})
    monkeypatch.setattr(b, "_entry_status", lambda oid, **k: "working")
    cancelled = {}
    monkeypatch.setattr(
        b, "_cancel_oso", lambda *ids: cancelled.update(ids=ids) or 3
    )
    fill = b.execute_bracket(_long_order())
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ENTRY_NOT_FILLED"
    assert fill.order_type == "Stop"
    assert cancelled["ids"] == (11, 12, 13)
    assert b._last_position is None


def test_stop_entry_filled_opens_position(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "stop_market")
    b = _broker(monkeypatch)
    _capture_body(monkeypatch, b, result={"orderId": 11, "oso1Id": 12, "oso2Id": 13})
    monkeypatch.setattr(b, "_entry_status", lambda oid, **k: "filled")
    monkeypatch.setattr(b, "_verify_bracket_children", lambda **k: (True, True))
    fill = b.execute_bracket(_long_order())
    assert fill.result == "OPEN"
    assert b._last_position is not None
