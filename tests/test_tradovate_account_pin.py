"""
tests/test_tradovate_account_pin.py

Account-routing visibility + fail-closed guard for Tradovate order submission.

Account selection (_select_account_id, shared by _resolve_account_id and
reliability_heartbeat) has always taken accounts[0] from Tradovate's
/account/list with no check against which account the operator actually
intends to trade — if that login has more than one account (e.g. multiple
demo sub-accounts), whichever one Tradovate lists first silently becomes the
account every order is sent to.

TRADOVATE_EXPECTED_ACCOUNT_ID (optional) fixes this rather than merely
detecting it:
  - unset: legacy accounts[0] selection, UNCHANGED, visibility-only logging.
  - set: the FULL /account/list is searched for the exact matching id and
    that account is selected regardless of list position -- never
    accounts[0] once a pin exists. Fails closed (no account resolved, no
    order sent) if the expected id is absent, the list is empty/malformed,
    or the id appears more than once (ambiguous).

execute_bracket additionally fails closed if the resolved account's balance
can't be verified as positive -- a lookup failure (raises internally or
returns None) is treated as unverifiable, not "ok to trade". That balance
is Tradovate's cash-balance snapshot (totalCashValue/cashBalance/netLiq/
balance/amount) -- an account cash/equity figure, not a computed margin
"buying power" number, so it is never described as buying power here.

111111 / 222222 / 333333 used below are arbitrary synthetic ids for these
test fixtures only -- NOT real, confirmed Tradovate account numbers (demo
or otherwise).
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


class _FakeAccountListResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


_TWO_ACCOUNTS = [
    {"id": 111111, "name": "acct-a"},
    {"id": 222222, "name": "acct-b"},
]


# ── parsing ───────────────────────────────────────────────────────────────

def test_parse_expected_account_id_blank_is_none():
    assert _parse_expected_account_id("") is None
    assert _parse_expected_account_id(None) is None


def test_parse_expected_account_id_numeric():
    assert _parse_expected_account_id("222222") == 222222


def test_parse_expected_account_id_rejects_non_numeric():
    with pytest.raises(ValueError):
        _parse_expected_account_id("not-a-number")


# ── pin unset: legacy accounts[0] selection, unchanged for compatibility ──

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


def test_pin_unset_resolution_still_picks_accounts_0_in_a_multi_account_list(monkeypatch):
    # Compatibility requirement: with no pin configured, resolving a REAL
    # multi-account /account/list response must still behave exactly as
    # before this fix -- accounts[0], no search, no filtering.
    b = _broker(monkeypatch, expected_account_id=None, resolved_account_id=None)
    monkeypatch.setattr(b._session, "get", lambda url, **k: _FakeAccountListResp(_TWO_ACCOUNTS))
    b._resolve_account_id()
    assert b._account_id == 111111


# ── pin configured and the expected account IS accounts[0]: proceeds ──────

def test_pin_matches_and_balance_positive_order_proceeds(monkeypatch):
    b = _broker(monkeypatch, expected_account_id="999", resolved_account_id=999)
    monkeypatch.setattr(b, "get_account_balance", lambda: 500.0)
    cap = _capture_post(monkeypatch, b)
    b.execute_bracket(_order())
    assert cap["calls"] >= 1
    assert cap["body"]["accountId"] == 999


# ── the actual routing fix: expected account NOT first is still selected ──

def test_expected_account_second_in_list_is_selected_and_receives_the_order(monkeypatch):
    b = _broker(monkeypatch, expected_account_id="222222", resolved_account_id=None)
    monkeypatch.setattr(b._session, "get", lambda url, **k: _FakeAccountListResp(_TWO_ACCOUNTS))
    monkeypatch.setattr(b, "get_account_balance", lambda: 500.0)
    cap = _capture_post(monkeypatch, b)
    b.execute_bracket(_order())
    assert b._account_id == 222222, "the pinned account must be selected regardless of its position in the list"
    assert cap["calls"] >= 1
    assert cap["body"]["accountId"] == 222222, "the submitted order payload must carry the pinned account id"


# ── fail-closed: expected account absent from the list blocks everything ──

def test_expected_account_absent_blocks_all_submission(monkeypatch, caplog):
    b = _broker(monkeypatch, expected_account_id="333333", resolved_account_id=None)
    monkeypatch.setattr(b._session, "get", lambda url, **k: _FakeAccountListResp(_TWO_ACCOUNTS))
    cap = _capture_post(monkeypatch, b)
    with caplog.at_level(logging.ERROR):
        fill = b.execute_bracket(_order())
    assert b._account_id is None, "must never fall back to accounts[0] once a pin is configured"
    assert cap["calls"] == 0
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ACCOUNT_MISMATCH"
    assert any("333333" in r.message for r in caplog.records)


def test_expected_account_appearing_twice_is_ambiguous_and_blocks(monkeypatch):
    # Ids should be unique per login; a duplicate is refused, not guessed.
    dup = [{"id": 222222, "name": "a"}, {"id": 222222, "name": "b"}]
    b = _broker(monkeypatch, expected_account_id="222222", resolved_account_id=None)
    monkeypatch.setattr(b._session, "get", lambda url, **k: _FakeAccountListResp(dup))
    cap = _capture_post(monkeypatch, b)
    fill = b.execute_bracket(_order())
    assert b._account_id is None
    assert cap["calls"] == 0
    assert fill.exit_reason == "ACCOUNT_MISMATCH"


# ── fail-closed: empty or malformed /account/list blocks (pin configured) ─

@pytest.mark.parametrize("payload", [
    [],                       # empty
    {"id": 222222},           # malformed: a dict, not a list
    ["not-a-dict", 42],       # malformed: list of non-dict entries
    None,                     # malformed: no body at all
])
def test_empty_or_malformed_account_list_blocks_when_pin_configured(monkeypatch, payload):
    b = _broker(monkeypatch, expected_account_id="222222", resolved_account_id=None)
    monkeypatch.setattr(b._session, "get", lambda url, **k: _FakeAccountListResp(payload))
    cap = _capture_post(monkeypatch, b)
    fill = b.execute_bracket(_order())
    assert b._account_id is None
    assert cap["calls"] == 0
    assert fill.exit_reason == "ACCOUNT_MISMATCH"


# ── fail-closed: unresolved account (pin unset) never reaches the broker ──

def test_unresolved_account_blocks_before_any_broker_call(monkeypatch):
    b = _broker(monkeypatch, expected_account_id=None, resolved_account_id=None)
    monkeypatch.setattr(b, "_resolve_account_id", lambda: None)  # stays None
    cap = _capture_post(monkeypatch, b)
    fill = b.execute_bracket(_order())
    assert cap["calls"] == 0
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ACCOUNT_UNRESOLVED"


# ── fail-closed: non-positive balance blocks ───────────────────────────────

@pytest.mark.parametrize("balance", [0.0, -50.0])
def test_zero_or_negative_balance_blocks(monkeypatch, balance):
    b = _broker(monkeypatch, expected_account_id="999", resolved_account_id=999)
    monkeypatch.setattr(b, "get_account_balance", lambda: balance)
    cap = _capture_post(monkeypatch, b)
    fill = b.execute_bracket(_order())
    assert cap["calls"] == 0
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ACCOUNT_NONPOSITIVE_BALANCE"


# ── fail-closed: an unverifiable balance (lookup failed) blocks too ───────

def test_balance_lookup_failure_blocks(monkeypatch, caplog):
    # get_account_balance() returning None (lookup failed, or an internal
    # exception it already swallows) must be treated as UNVERIFIABLE and
    # fail closed -- not silently allowed through.
    b = _broker(monkeypatch, expected_account_id="999", resolved_account_id=999)
    monkeypatch.setattr(b, "get_account_balance", lambda: None)
    cap = _capture_post(monkeypatch, b)
    with caplog.at_level(logging.ERROR):
        fill = b.execute_bracket(_order())
    assert cap["calls"] == 0
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ACCOUNT_BALANCE_LOOKUP_FAILED"
    assert any("balance could not be verified" in r.message for r in caplog.records)
