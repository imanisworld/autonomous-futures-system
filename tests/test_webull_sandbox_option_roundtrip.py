from __future__ import annotations

from datetime import date
from pathlib import Path

from integrations.webull_paper_config import WEBULL_SANDBOX_HOST
from options_manager.adapters.webull_sandbox_option_roundtrip import (
    SELL_TO_CLOSE,
    WebullSandboxCloseRequest,
    build_sell_to_close_orders,
    close_sandbox_paper_option_position,
    validate_close_request,
)
from options_manager.adapters.webull_sandbox_paper_orders import (
    WebullSandboxOrderDetail,
)
from options_manager.config import OptionsManagerConfig


SAFE_ENV = {
    "WEBULL_SANDBOX_APP_KEY": "SANDBOX_KEY_SENTINEL",
    "WEBULL_SANDBOX_APP_SECRET": "SANDBOX_SECRET_SENTINEL",
    "WEBULL_SANDBOX_BASE_URL": WEBULL_SANDBOX_HOST,
    "WEBULL_SANDBOX_TRADING_MODE": "paper",
    "WEBULL_SANDBOX_LIVE_TRADING_ENABLED": "false",
    "WEBULL_SANDBOX_API_ENABLED": "true",
    "WEBULL_SANDBOX_PAPER_TRADING_ENABLED": "true",
    "WEBULL_API_LIVE_ENABLED": "false",
}


def cfg(**overrides):
    values = dict(
        broker_boundary_enabled=True,
        broker_boundary_allow_real_preview=True,
        broker_boundary_allow_sandbox_paper_submit=True,
        broker_boundary_max_contracts=2,
        broker_boundary_max_notional=300.0,
        broker_boundary_max_limit_price=3.0,
        live_options_trading_enabled=False,
    )
    values.update(overrides)
    return OptionsManagerConfig(**values)


def request(**overrides):
    values = dict(
        entry_ticket_id="entry-ticket-1",
        exit_ticket_id="exit-ticket-1",
        ticker="AAPL",
        direction="CALL",
        quantity=1,
        contract_strike=350.0,
        contract_expiry=date(2026, 12, 18),
        limit_price=4.25,
    )
    values.update(overrides)
    return WebullSandboxCloseRequest(**values)


def filled_entry(**overrides):
    values = dict(
        status="OK",
        ticket_id="entry-ticket-1",
        client_order_id="entry-ticket-1",
        broker_order_id="WB-ENTRY-1",
        order_state="FILLED",
        terminal=True,
        filled_quantity=1,
        average_fill_price=1.0,
        limit_price=1.0,
        reason=None,
    )
    values.update(overrides)
    return WebullSandboxOrderDetail(**values)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


ACCOUNT_ROWS = {
    "data": [
        {
            "account_id": "paper-cash-id",
            "account_class": "INDIVIDUAL_CASH",
            "account_type": "CASH",
        }
    ]
}


class FakeClient:
    def __init__(
        self,
        *,
        preview=None,
        place=None,
        accounts=None,
        raise_on_preview=None,
        raise_on_place=None,
    ):
        self.calls = []
        self._preview = preview or FakeResponse(200, {"estimated_cost": "0"})
        self._place = place or FakeResponse(
            200, {"data": {"order_id": "WB-EXIT-1", "client_order_id": "exit-ticket-1"}}
        )
        self._accounts = accounts or FakeResponse(200, ACCOUNT_ROWS)
        self._raise_on_preview = raise_on_preview
        self._raise_on_place = raise_on_place

    def get_account_list(self):
        self.calls.append(("accounts",))
        return self._accounts

    def preview_option(self, account_id, orders):
        self.calls.append(("preview", account_id, orders))
        if self._raise_on_preview:
            raise self._raise_on_preview
        return self._preview

    def place_option(self, account_id, orders):
        self.calls.append(("place", account_id, orders))
        if self._raise_on_place:
            raise self._raise_on_place
        return self._place


def factory_for(client):
    created = []

    def factory(key, secret):
        assert key == "SANDBOX_KEY_SENTINEL"
        assert secret == "SANDBOX_SECRET_SENTINEL"
        created.append(client)
        return client

    return factory, created


class FakeServerException(Exception):
    def __init__(self, code, http_status):
        super().__init__(f"HTTP Status: {http_status}, Code: {code}")
        self.error_code = code
        self.http_status = http_status


def test_close_request_requires_proven_filled_entry():
    req = request()

    assert validate_close_request(req, filled_entry(), cfg()) is None
    assert (
        validate_close_request(
            req, filled_entry(order_state="WORKING", terminal=False), cfg()
        )
        == "entry_order_not_filled"
    )
    assert (
        validate_close_request(
            req, filled_entry(client_order_id="another-entry"), cfg()
        )
        == "entry_ticket_detail_mismatch"
    )
    assert (
        validate_close_request(req, filled_entry(filled_quantity=0), cfg())
        == "close_quantity_exceeds_filled_quantity"
    )


def test_close_request_rejects_duplicate_ticket_and_excess_quantity():
    assert (
        validate_close_request(
            request(exit_ticket_id="entry-ticket-1"), filled_entry(), cfg()
        )
        == "exit_ticket_id_must_differ_from_entry"
    )
    assert (
        validate_close_request(
            request(quantity=3),
            filled_entry(filled_quantity=3),
            cfg(broker_boundary_max_contracts=2),
        )
        == "quantity_exceeds_boundary_cap"
    )


def test_close_payload_is_explicit_sell_to_close_single_leg_limit_day():
    orders = build_sell_to_close_orders(request())
    assert len(orders) == 1
    order = orders[0]
    assert order["client_order_id"] == "exit-ticket-1"
    assert order["side"] == "SELL"
    assert order["position_intent"] == SELL_TO_CLOSE
    assert order["order_type"] == "LIMIT"
    assert order["time_in_force"] == "DAY"
    assert order["option_strategy"] == "SINGLE"
    assert order["quantity"] == "1"
    assert order["limit_price"] == "4.2500"
    assert len(order["legs"]) == 1
    leg = order["legs"][0]
    assert leg["side"] == "SELL"
    assert leg["quantity"] == "1"
    assert leg["symbol"] == "AAPL"
    assert leg["strike_price"] == "350"
    assert leg["option_expire_date"] == "2026-12-18"
    assert leg["option_type"] == "CALL"
    assert leg["instrument_type"] == "OPTION"
    assert leg["market"] == "US"


def test_profitable_close_is_not_trapped_by_entry_side_limit_or_notional_caps():
    req = request(limit_price=12.0, quantity=1)
    # Entry-side cap remains $3/$300, but close-side price may legitimately
    # exceed it after a profitable move.
    assert validate_close_request(req, filled_entry(), cfg()) is None


def test_unsafe_webull_config_blocks_before_client_creation():
    client = FakeClient()
    factory, created = factory_for(client)

    for env in (
        {**SAFE_ENV, "WEBULL_SANDBOX_BASE_URL": "api.webull.com"},
        {**SAFE_ENV, "WEBULL_SANDBOX_LIVE_TRADING_ENABLED": "true"},
        {**SAFE_ENV, "WEBULL_API_LIVE_ENABLED": "true"},
        {**SAFE_ENV, "WEBULL_SANDBOX_TRADING_MODE": "live"},
    ):
        result = close_sandbox_paper_option_position(
            request(), filled_entry(), cfg(), env, client_factory=factory
        )
        assert result.status == "BLOCKED"
        assert result.submitted is False

    assert created == []
    assert client.calls == []


def test_submit_opt_in_is_required_before_any_client_call():
    client = FakeClient()
    factory, created = factory_for(client)
    result = close_sandbox_paper_option_position(
        request(),
        filled_entry(),
        cfg(broker_boundary_allow_sandbox_paper_submit=False),
        SAFE_ENV,
        client_factory=factory,
    )
    assert result.status == "BLOCKED"
    assert result.reason == "sandbox_paper_submit_not_enabled"
    assert created == []


def test_close_previews_exact_order_then_places_same_order():
    client = FakeClient()
    factory, _ = factory_for(client)
    result = close_sandbox_paper_option_position(
        request(), filled_entry(), cfg(), SAFE_ENV, client_factory=factory
    )

    assert result.status == "SUBMITTED"
    assert result.submitted is True
    assert result.entry_ticket_id == "entry-ticket-1"
    assert result.exit_ticket_id == "exit-ticket-1"
    assert result.client_order_id == "exit-ticket-1"
    assert result.broker_order_id == "WB-EXIT-1"
    assert "sandbox_paper_only" in result.warnings
    assert "sell_to_close_only" in result.warnings

    kinds = [call[0] for call in client.calls]
    assert kinds == ["accounts", "preview", "place"]
    assert client.calls[1][2] == client.calls[2][2]
    order = client.calls[2][2][0]
    assert order["position_intent"] == SELL_TO_CLOSE


def test_preview_rejection_prevents_place():
    client = FakeClient(preview=FakeResponse(400, {"code": "NO_POSITION"}))
    factory, _ = factory_for(client)
    result = close_sandbox_paper_option_position(
        request(), filled_entry(), cfg(), SAFE_ENV, client_factory=factory
    )
    assert result.status == "REJECTED"
    assert result.reason == "preview_http_400"
    assert [call[0] for call in client.calls] == ["accounts", "preview"]


def test_broker_4xx_place_exception_is_clean_rejection():
    client = FakeClient(
        raise_on_place=FakeServerException("OPENAPI_OPTION_POSITION_NOT_ENOUGH", 417)
    )
    factory, _ = factory_for(client)
    result = close_sandbox_paper_option_position(
        request(), filled_entry(), cfg(), SAFE_ENV, client_factory=factory
    )
    assert result.status == "REJECTED"
    assert result.submitted is False
    assert "OPENAPI_OPTION_POSITION_NOT_ENOUGH" in (result.reason or "")
    assert "close_outcome_unknown_check_order_detail" not in result.warnings


def test_transport_failure_after_place_has_unknown_outcome_warning():
    client = FakeClient(raise_on_place=TimeoutError("timeout"))
    factory, _ = factory_for(client)
    result = close_sandbox_paper_option_position(
        request(), filled_entry(), cfg(), SAFE_ENV, client_factory=factory
    )
    assert result.status == "ERROR"
    assert result.submitted is False
    assert "close_outcome_unknown_check_order_detail" in result.warnings


def test_roundtrip_module_has_no_live_host_or_opening_sell_intent():
    source = Path(
        "options_manager/adapters/webull_sandbox_option_roundtrip.py"
    ).read_text()
    lowered = source.lower()
    assert "api.webull.com" not in lowered
    assert "sell_to_open" not in lowered
    assert '"position_intent": "buy_to_open"' not in lowered
    assert "market_order" not in lowered
    assert "replace_option" not in lowered
    assert "position_intent" in lowered
    assert "sell_to_close" in lowered


def test_no_runtime_path_imports_roundtrip_module():
    needle = "webull_sandbox_option_roundtrip"
    for root in ("alert_ranker", "webhook", "execution", "options_companion"):
        path = Path(root)
        if not path.exists():
            continue
        for file in path.rglob("*.py"):
            assert needle not in file.read_text(), file