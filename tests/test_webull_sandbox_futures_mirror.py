from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

from execution.broker_interface import BracketOrder, Fill
from execution.webull_sandbox_futures_mirror import (
    WEBULL_SANDBOX_FUTURES_BROKER,
    cancel_mirror_order,
    get_mirror_order_detail,
    load_mirror_config,
    mirror_block_reason,
    mirror_client_order_id,
    mirror_entry,
    mirror_exit,
    mirror_symbol,
    select_futures_account,
)
from integrations.webull_paper_config import WEBULL_SANDBOX_HOST

SAFE_ENV = {
    "WEBULL_SANDBOX_APP_KEY": "SANDBOX_KEY_SENTINEL",
    "WEBULL_SANDBOX_APP_SECRET": "SANDBOX_SECRET_SENTINEL",
    "WEBULL_SANDBOX_BASE_URL": WEBULL_SANDBOX_HOST,
    "WEBULL_SANDBOX_TRADING_MODE": "paper",
    "WEBULL_SANDBOX_LIVE_TRADING_ENABLED": "false",
    "WEBULL_SANDBOX_API_ENABLED": "true",
    "WEBULL_SANDBOX_PAPER_TRADING_ENABLED": "true",
    "WEBULL_API_LIVE_ENABLED": "false",
    "WEBULL_FUTURES_MIRROR_ENABLED": "true",
}
TODAY = date(2026, 9, 21)  # front month = Z6 for MNQ/MES


def bracket(**overrides) -> BracketOrder:
    values = dict(
        instrument="MNQ",
        direction="LONG",
        entry=30100.25,
        stop=30050.0,
        target=30200.75,
        rr_ratio=2.0,
        strategy="unit_lane",
        contracts=1,
        client_order_id="sig-abc-123",
    )
    values.update(overrides)
    return BracketOrder(**values)


def fill(**overrides) -> Fill:
    values = dict(
        instrument="MES",
        direction="SHORT",
        contracts=1,
        entry_price=7740.0,
        exit_price=7730.0,
        exit_reason="TARGET_HIT",
        result="WIN",
        pnl_ticks=40.0,
        pnl_dollars=50.0,
        paper_order_id="paper-77",
    )
    values.update(overrides)
    return Fill(**values)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


ACCOUNTS = [
    {"account_id": "fut-id", "account_class": "FUTURES", "account_type": "MARGIN"},
    {"account_id": "cash-id", "account_class": "INDIVIDUAL_CASH", "account_type": "CASH"},
    {"account_id": "crypto-id", "account_class": "CRYPTO", "account_type": "CASH"},
]


class FakeClient:
    def __init__(self, place=None, cancel=None, detail=None):
        self.placed: list[tuple[str, list[dict]]] = []
        self.cancelled: list[tuple[str, str]] = []
        self.detail_calls: list[tuple[str, str]] = []
        self._place = place
        self._cancel = cancel
        self._detail = detail

    def get_account_list(self):
        return FakeResponse(200, ACCOUNTS)

    def place_order(self, account_id, orders):
        self.placed.append((account_id, orders))
        if isinstance(self._place, Exception):
            raise self._place
        return self._place or FakeResponse(
            200, {"client_order_id": orders[0]["client_order_id"], "order_id": "BRK-1"}
        )

    def cancel_order(self, account_id, client_order_id):
        self.cancelled.append((account_id, client_order_id))
        if isinstance(self._cancel, Exception):
            raise self._cancel
        return self._cancel or FakeResponse(
            200, {"client_order_id": client_order_id, "order_id": "BRK-1"}
        )

    def get_order_detail(self, account_id, client_order_id):
        self.detail_calls.append((account_id, client_order_id))
        if isinstance(self._detail, Exception):
            raise self._detail
        return self._detail or FakeResponse(
            200,
            {
                "client_order_id": client_order_id,
                "combo_order_id": "BRK-1",
                "orders": [
                    {
                        "symbol": "MESZ6",
                        "status": "CANCELLED",
                        "filled_quantity": "0",
                        "order_id": "BRK-1",
                    }
                ],
            },
        )


class FakeServerException(Exception):
    def __init__(self, error_code, http_status):
        super().__init__(error_code)
        self.error_code = error_code
        self.http_status = http_status


def forbidden(*_args):
    raise AssertionError("client factory must not run")


# ─── config / gating ─────────────────────────────────────────────────────────

def test_mirror_is_off_by_default_and_never_creates_a_client():
    env = {k: v for k, v in SAFE_ENV.items() if k != "WEBULL_FUTURES_MIRROR_ENABLED"}
    assert mirror_block_reason(env) == "webull_futures_mirror_not_enabled"
    result = mirror_entry(bracket(), env, today=TODAY, client_factory=forbidden)
    assert result.status == "BLOCKED"
    assert result.reason == "webull_futures_mirror_not_enabled"


def test_mirror_flag_alone_cannot_bypass_paper_only_safety():
    env = dict(SAFE_ENV, WEBULL_SANDBOX_BASE_URL="api.webull.com")
    assert mirror_block_reason(env) == "webull_sandbox_config_invalid"
    assert mirror_entry(bracket(), env, today=TODAY, client_factory=forbidden).status == "BLOCKED"
    env = dict(SAFE_ENV, WEBULL_SANDBOX_LIVE_TRADING_ENABLED="true")
    assert mirror_block_reason(env) == "webull_sandbox_config_invalid"
    env = dict(SAFE_ENV, WEBULL_API_LIVE_ENABLED="true")
    assert mirror_block_reason(env) == "webull_sandbox_config_invalid"


def test_mirror_config_defaults_and_validation():
    cfg = load_mirror_config(SAFE_ENV)
    assert cfg.enabled is True
    assert cfg.max_contracts == 1
    assert cfg.allowed_roots == ("MNQ", "MES")
    assert cfg.errors == ()

    bad = load_mirror_config(dict(SAFE_ENV, WEBULL_FUTURES_MIRROR_MAX_CONTRACTS="0"))
    assert bad.errors
    assert mirror_block_reason(dict(SAFE_ENV, WEBULL_FUTURES_MIRROR_MAX_CONTRACTS="0")) == (
        "webull_futures_mirror_config_invalid"
    )
    custom = load_mirror_config(dict(SAFE_ENV, WEBULL_FUTURES_MIRROR_INSTRUMENTS="mes1!, m2k"))
    assert custom.allowed_roots == ("MES", "M2K")


# ─── account selection ───────────────────────────────────────────────────────

def test_select_futures_account_only_futures_class_and_fails_closed():
    assert select_futures_account(ACCOUNTS) == ("fut-id", None)
    assert select_futures_account({"data": ACCOUNTS}) == ("fut-id", None)
    assert select_futures_account([ACCOUNTS[1]]) == (None, "sandbox_futures_account_missing")
    two = ACCOUNTS + [{"account_id": "fut-2", "account_class": "FUTURES"}]
    assert select_futures_account(two) == (None, "sandbox_futures_account_ambiguous")
    assert select_futures_account("nope") == (None, "account_schema_unrecognized")


# ─── symbol / identity ───────────────────────────────────────────────────────

def test_mirror_symbol_uses_front_month_and_rejects_unknown_roots():
    assert mirror_symbol("MNQ", TODAY) == "MNQZ6"
    assert mirror_symbol("MES1!", TODAY) == "MESZ6"
    assert mirror_symbol("MGC", TODAY) is None


def test_client_order_id_is_deterministic_per_source_and_leg():
    a = mirror_client_order_id("sig-1", "entry", "fb")
    b = mirror_client_order_id("sig-1", "entry", "other-fb")
    c = mirror_client_order_id("sig-1", "exit", "fb")
    d = mirror_client_order_id(None, "entry", "fb")
    assert a == b
    assert a != c
    assert a != d
    assert len(a) == 32 and all(ch in "0123456789abcdef" for ch in a)


# ─── entry ───────────────────────────────────────────────────────────────────

def test_entry_mirrors_single_limit_leg_on_futures_account():
    client = FakeClient()
    received = {}

    def factory(key, secret):
        received.update(key=key, secret=secret)
        return client

    result = mirror_entry(bracket(), SAFE_ENV, today=TODAY, client_factory=factory)
    assert result.status == "SUBMITTED"
    assert result.submitted is True
    assert result.broker == WEBULL_SANDBOX_FUTURES_BROKER
    assert result.leg == "entry"
    assert result.symbol == "MNQZ6"
    assert result.side == "BUY"
    assert result.quantity == 1
    assert result.order_type == "LIMIT"
    assert result.limit_price == 30100.25
    assert result.broker_order_id == "BRK-1"
    assert result.client_order_id == mirror_client_order_id("sig-abc-123", "entry", "")
    assert received == {"key": "SANDBOX_KEY_SENTINEL", "secret": "SANDBOX_SECRET_SENTINEL"}

    account_id, orders = client.placed[0]
    assert account_id == "fut-id"
    assert len(orders) == 1
    assert orders[0] == {
        "client_order_id": result.client_order_id,
        "combo_type": "NORMAL",
        "order_type": "LIMIT",
        "quantity": "1",
        "side": "BUY",
        "time_in_force": "DAY",
        "entrust_type": "QTY",
        "symbol": "MNQZ6",
        "instrument_type": "FUTURES",
        "market": "US",
        "limit_price": "30100.25",
    }
    assert "fut-id" not in repr(result)


def test_entry_short_market_when_forced_and_quantity_capped():
    client = FakeClient()
    result = mirror_entry(
        bracket(direction="SHORT", force_market_entry=True, contracts=5),
        dict(SAFE_ENV, WEBULL_FUTURES_MIRROR_MAX_CONTRACTS="2"),
        today=TODAY,
        client_factory=lambda *_: client,
    )
    assert result.status == "SUBMITTED"
    assert result.side == "SELL"
    assert result.order_type == "MARKET"
    assert result.quantity == 2
    order = client.placed[0][1][0]
    assert "limit_price" not in order
    assert order["quantity"] == "2"


def test_entry_blocks_instruments_outside_allow_list_before_client():
    result = mirror_entry(bracket(instrument="MGC"), SAFE_ENV, today=TODAY, client_factory=forbidden)
    assert result.status == "BLOCKED"
    assert result.reason == "instrument_not_mirrored"
    result = mirror_entry(
        bracket(instrument="M2K"),
        dict(SAFE_ENV, WEBULL_FUTURES_MIRROR_INSTRUMENTS="MNQ"),
        today=TODAY,
        client_factory=forbidden,
    )
    assert result.reason == "instrument_not_mirrored"


def test_entry_blocks_bad_direction_and_quantity_before_client():
    assert mirror_entry(
        bracket(direction="FLAT"), SAFE_ENV, today=TODAY, client_factory=forbidden
    ).reason == "direction_invalid"
    assert mirror_entry(
        bracket(contracts=0), SAFE_ENV, today=TODAY, client_factory=forbidden
    ).reason == "quantity_invalid"


def test_entry_maps_broker_4xx_to_rejected_and_transport_to_error():
    client = FakeClient(place=FakeServerException("SOME_BROKER_CODE", 417))
    result = mirror_entry(bracket(), SAFE_ENV, today=TODAY, client_factory=lambda *_: client)
    assert result.status == "REJECTED"
    assert result.reason == "broker:SOME_BROKER_CODE"

    client = FakeClient(place=RuntimeError("boom"))
    result = mirror_entry(bracket(), SAFE_ENV, today=TODAY, client_factory=lambda *_: client)
    assert result.status == "ERROR"
    assert result.reason == "place_request_failed:RuntimeError"

    client = FakeClient(place=FakeResponse(500, {}))
    result = mirror_entry(bracket(), SAFE_ENV, today=TODAY, client_factory=lambda *_: client)
    assert result.status == "REJECTED"
    assert result.reason == "place_http_error"


def test_entry_errors_when_futures_account_missing():
    class NoFutures(FakeClient):
        def get_account_list(self):
            return FakeResponse(200, [ACCOUNTS[1]])

    client = NoFutures()
    result = mirror_entry(bracket(), SAFE_ENV, today=TODAY, client_factory=lambda *_: client)
    assert result.status == "ERROR"
    assert result.reason == "sandbox_futures_account_missing"
    assert client.placed == []


# ─── exit ────────────────────────────────────────────────────────────────────

def test_exit_mirrors_opposite_side_market_order():
    client = FakeClient()
    result = mirror_exit(fill(), SAFE_ENV, today=TODAY, client_factory=lambda *_: client)
    assert result.status == "SUBMITTED"
    assert result.leg == "exit"
    assert result.symbol == "MESZ6"
    assert result.side == "BUY"  # flattening a SHORT
    assert result.order_type == "MARKET"
    assert result.limit_price is None
    assert result.client_order_id == mirror_client_order_id("paper-77", "exit", "")
    order = client.placed[0][1][0]
    assert order["side"] == "BUY" and order["order_type"] == "MARKET"
    assert "limit_price" not in order


def test_exit_prefers_explicit_source_id_for_identity():
    client = FakeClient()
    result = mirror_exit(
        fill(), SAFE_ENV, source_id="sig-abc-123", today=TODAY, client_factory=lambda *_: client
    )
    assert result.client_order_id == mirror_client_order_id("sig-abc-123", "exit", "")
    entry_id = mirror_client_order_id("sig-abc-123", "entry", "")
    assert result.client_order_id != entry_id


def test_exit_skips_no_fill_and_open_outcomes_before_client():
    cancelled = fill(exit_price=None, result="CANCELLED", exit_reason="MANUAL_CANCEL")
    result = mirror_exit(cancelled, SAFE_ENV, today=TODAY, client_factory=forbidden)
    assert result.status == "BLOCKED"
    assert result.reason == "fill_not_exited"
    still_open = fill(result="OPEN")
    assert mirror_exit(still_open, SAFE_ENV, today=TODAY, client_factory=forbidden).reason == (
        "fill_not_exited"
    )


# ─── cancel / detail ─────────────────────────────────────────────────────────

def test_cancel_and_detail_round_trip():
    client = FakeClient()
    cid = mirror_client_order_id("sig-abc-123", "entry", "")
    cancel = cancel_mirror_order(cid, SAFE_ENV, client_factory=lambda *_: client)
    assert cancel.status == "CANCELLED"
    assert cancel.broker_order_id == "BRK-1"
    assert client.cancelled == [("fut-id", cid)]

    detail = get_mirror_order_detail(cid, SAFE_ENV, client_factory=lambda *_: client)
    assert detail.status == "OK"
    assert detail.order_state == "CANCELLED"
    assert detail.terminal is True
    assert detail.filled_quantity == 0
    assert detail.broker_order_id == "BRK-1"


def test_cancel_and_detail_are_gated_like_submit():
    cancel = cancel_mirror_order("x", dict(SAFE_ENV, WEBULL_FUTURES_MIRROR_ENABLED="false"), client_factory=forbidden)
    assert cancel.status == "BLOCKED"
    detail = get_mirror_order_detail("x", dict(SAFE_ENV, WEBULL_FUTURES_MIRROR_ENABLED="false"), client_factory=forbidden)
    assert detail.status == "BLOCKED"


def test_cancel_maps_broker_rejection():
    client = FakeClient(cancel=FakeServerException("ORDER_NOT_FOUND", 404))
    cancel = cancel_mirror_order("x", SAFE_ENV, client_factory=lambda *_: client)
    assert cancel.status == "REJECTED"
    assert cancel.reason == "broker:ORDER_NOT_FOUND"


# ─── isolation guards ────────────────────────────────────────────────────────

# The only sanctioned bridge is execution/paper_mirror_hook.py, which imports
# the mirror lazily and only when WEBULL_FUTURES_MIRROR_ENABLED is true.
_RUNTIME_MODULES = (
    "webhook/app.py",
    "webhook/runner.py",
    "main.py",
    "execution/paper_broker.py",
    "execution/tradovate_broker.py",
    "risk/risk_engine.py",
    "strategy/signal_engine.py",
)


def test_no_runtime_module_imports_the_mirror_lane_directly():
    for rel in _RUNTIME_MODULES:
        path = Path(rel)
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any("webull_sandbox_futures_mirror" in n for n in names), rel


def test_adapter_source_never_targets_live_host_or_combo_orders():
    source = Path("execution/webull_sandbox_futures_mirror.py").read_text().lower()
    assert "api.webull.com" not in source
    assert "batch_place_order" not in source
    assert "replace_order" not in source
    assert '"oto"' not in source and '"oco"' not in source
