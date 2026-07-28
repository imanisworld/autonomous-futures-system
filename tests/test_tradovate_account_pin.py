"""
tests/test_tradovate_account_pin.py

Account-routing visibility + fail-closed guard for Tradovate order submission.

_resolve_account_id has always taken accounts[0] from Tradovate's
/account/list with no check against which account the operator actually
intends to trade — if that login has more than one account (e.g. multiple
demo sub-accounts), whichever one Tradovate lists first silently becomes the
account every order is sent to. TRADOVATE_EXPECTED_ACCOUNT_ID (optional; unset
= no behavior change, visibility-only) lets a deployment pin the expected
account id; execute_bracket refuses to submit when the resolved account
doesn't match, is unresolved, or has zero/negative buying power, and logs the
exact (env, account_id, expected) before every order attempt either way.
"""
from __future__ import annotations

import logging

import pytest

import execution.tradovate_supervisor as supervisor
from execution.broker_interface import BracketOrder
from execution.tradovate_broker import TradovateBroker, TradovateConfig, _parse_expected_account_id


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("TRADOVATE_EXPECTED_ACCOUNT_ID", raising=False)
    TradovateBroker._reset_client_order_registry()
    yield
    TradovateBroker._reset_client_order_registry()


def _broker(monkeypatch, *, expected_account_id: str | None = None, resolved_account_id=999):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("TRADOVATE_USERNAME", "x")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "x")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "1")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "x")
    if expected_account_id is not None:
        monkeypatch.setenv("TRADOVATE_EXPECTED_ACCOUNT_ID", expected_account_id)
    b = TradovateBroker(config=TradovateConfig.from_env())
    monkeypatch.setattr(b, "_authenticate", lambda: True)
    monkeypatch.setattr(b, "_find_contract_id", lambda inst: 123)
    monkeypatch.setattr(supervisor, "tradovate_order_ready", lambda: True)
    b._account_id = resolved_account_id
    return b


def _capture_post(monkeypatch, b, result=None):
    captured = {"calls": 0}

    def fake_post(path, body, **k):
        captured["calls"] += 1
        captured["body"] = body
        return dict(result or {"orderId": 555})

    monkeypatch.setattr(b, "_post", fake_post)
    return captured


def _order(**overrides):
    kwargs = dict(
        instrument="MES", direction="LONG", entry=7559.5,
        stop=7557.0, target=7574.5, rr_ratio=6.0, strategy="orb_breakout",
    )
    kwargs.update(overrides)
    return BracketOrder(**kwargs)


# ── parsing ───────────────────────────────────────────────────────────────

def test_parse_expected_account_id_blank_is_none():
    assert _parse_expected_account_id("") is None
    assert _parse_expected_account_id(None) is None


def test_parse_expected_account_id_numeric():
    assert _parse_expected_account_id("1354122") == 1354122


def test_parse_expected_account_id_rejects_non_numeric():
    with pytest.raises(ValueError):
        _parse_expected_account_id("not-a-number")


# ── no pin configured: legacy behavior unchanged, visibility-only ─────────

def test_no_pin_configured_order_proceeds_unchanged(monkeypatch, caplog):
    b = _broker(monkeypatch, expected_account_id=None, resolved_account_id=999)
    cap = _capture_post(monkeypatch, b)
    with caplog.at_level(logging.INFO):
        b.execute_bracket(_order())
    # The account guard did not block: the parent order was submitted at all
    # (fill-confirmation/naked-position handling beyond that is out of scope
    # here and covered by test_tradovate_execution_modes.py).
    assert cap["calls"] >= 1
    assert any(
        "Order account check: env=demo account_id=999 expected=unset" in r.message
        for r in caplog.records
    )


# ── pin configured and matches: proceeds, balance checked ─────────────────

def test_pin_matches_and_balance_positive_order_proceeds(monkeypatch):
    b = _broker(monkeypatch, expected_account_id="999", resolved_account_id=999)
    monkeypatch.setattr(b, "get_account_balance", lambda: 500.0)
    cap = _capture_post(monkeypatch, b)
    b.execute_bracket(_order())
    assert cap["calls"] >= 1


# ── fail-closed: mismatch never reaches the broker ─────────────────────────

def test_pin_mismatch_blocks_before_any_broker_call(monkeypatch, caplog):
    b = _broker(monkeypatch, expected_account_id="1354122", resolved_account_id=999)
    cap = _capture_post(monkeypatch, b)
    with caplog.at_level(logging.ERROR):
        fill = b.execute_bracket(_order())
    assert cap["calls"] == 0, "order must never reach Tradovate when the account is unpinned-mismatched"
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ACCOUNT_MISMATCH"
    assert any("account_id=999" in r.message and "1354122" in r.message for r in caplog.records)


# ── fail-closed: unresolved account never reaches the broker ───────────────

def test_unresolved_account_blocks_before_any_broker_call(monkeypatch):
    b = _broker(monkeypatch, expected_account_id="1354122", resolved_account_id=None)
    monkeypatch.setattr(b, "_resolve_account_id", lambda: None)  # stays None
    cap = _capture_post(monkeypatch, b)
    fill = b.execute_bracket(_order())
    assert cap["calls"] == 0
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ACCOUNT_UNRESOLVED"


# ── fail-closed: zero/negative buying power never reaches the broker ──────

@pytest.mark.parametrize("balance", [0.0, -50.0])
def test_zero_or_negative_buying_power_blocks(monkeypatch, balance):
    b = _broker(monkeypatch, expected_account_id="999", resolved_account_id=999)
    monkeypatch.setattr(b, "get_account_balance", lambda: balance)
    cap = _capture_post(monkeypatch, b)
    fill = b.execute_bracket(_order())
    assert cap["calls"] == 0
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ACCOUNT_ZERO_BUYING_POWER"


def test_balance_lookup_failure_is_fail_soft_not_blocking(monkeypatch):
    # get_account_balance() returning None (lookup failed) must not itself
    # block trading — only a confirmed non-positive balance does.
    b = _broker(monkeypatch, expected_account_id="999", resolved_account_id=999)
    monkeypatch.setattr(b, "get_account_balance", lambda: None)
    cap = _capture_post(monkeypatch, b)
    b.execute_bracket(_order())
    assert cap["calls"] >= 1
