from __future__ import annotations

from datetime import date
from pathlib import Path

from integrations.webull_paper_config import WEBULL_SANDBOX_HOST
from options_manager.adapters.webull_sandbox_paper_orders import (
    WEBULL_SANDBOX_BROKER,
    cancel_sandbox_paper_option_order,
    get_sandbox_paper_order_detail,
    submit_sandbox_paper_option_order,
)
from options_manager.broker_boundary import OptionsBrokerPreviewRequest
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
    "WEBULL_APP_KEY": "LIVE_KEY_MUST_NOT_BE_USED",
    "WEBULL_APP_SECRET": "LIVE_SECRET_MUST_NOT_BE_USED",
}


def submit_config(**overrides) -> OptionsManagerConfig:
    values = {
        "broker_boundary_allow_real_preview": True,
        "broker_boundary_allow_sandbox_paper_submit": True,
        "broker_boundary_enabled": True,
        "broker_boundary_max_contracts": 2,
        "broker_boundary_max_notional": 300.0,
        "broker_boundary_max_limit_price": 3.0,
        "broker_boundary_allowed_account_tags": ("agentic_micro_account",),
        "live_options_trading_enabled": False,
    }
    values.update(overrides)
    return OptionsManagerConfig(**values)


def preview_request(**overrides) -> OptionsBrokerPreviewRequest:
    values = {
        "ticket_id": "ticket-123",
        "confirmation_id": "confirm-123",
        "ticker": "AAPL",
        "direction": "CALL",
        "order_action": "BUY_TO_OPEN",
        "quantity": 1,
        "contract_strike": 350.0,
        "contract_expiry": date(2026, 12, 18),
        "limit_price": 1.0,
        "estimated_notional": 100.0,
        "account_tag": "agentic_micro_account",
        "source": "unit_test",
        "dry_run_only": True,
        "executable": False,
    }
    values.update(overrides)
    return OptionsBrokerPreviewRequest(**values)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


ACCOUNT_ROWS = {
    "data": [
        {"account_id": "paper-cash-id", "account_class": "INDIVIDUAL_CASH", "account_type": "CASH"},
        {"account_id": "paper-margin-id", "account_class": "INDIVIDUAL_MARGIN", "account_type": "MARGIN"},
    ]
}


class FakeClient:
    def __init__(
        self,
        *,
        preview=None,
        place=None,
        cancel=None,
        detail=None,
        accounts=None,
        raise_on_place: Exception | None = None,
    ):
        self.calls: list[tuple] = []
        self._preview = preview or FakeResponse(200, {"currency": "USD", "estimated_cost": "100.00", "estimated_transaction_fee": "0.05"})
        self._place = place or FakeResponse(200, {"data": {"order_id": "WB-SANDBOX-1", "client_order_id": "ticket-123"}})
        self._cancel = cancel or FakeResponse(200, {"data": {"order_id": "WB-SANDBOX-1"}})
        self._detail = detail or FakeResponse(200, {"data": {"order_id": "WB-SANDBOX-1", "order_status": "FILLED", "filled_quantity": "1", "avg_fill_price": "0.98", "limit_price": "1.00"}})
        self._accounts = accounts or FakeResponse(200, ACCOUNT_ROWS)
        self._raise_on_place = raise_on_place

    def get_account_list(self):
        self.calls.append(("accounts",))
        return self._accounts

    def preview_option(self, account_id, orders):
        self.calls.append(("preview", account_id, orders))
        return self._preview

    def place_option(self, account_id, orders):
        self.calls.append(("place", account_id, orders))
        if self._raise_on_place:
            raise self._raise_on_place
        return self._place

    def cancel_option(self, account_id, client_order_id):
        self.calls.append(("cancel", account_id, client_order_id))
        return self._cancel

    def get_order_detail(self, account_id, client_order_id):
        self.calls.append(("detail", account_id, client_order_id))
        return self._detail


def factory_for(client: FakeClient):
    created = []

    def factory(key, secret):
        assert key == "SANDBOX_KEY_SENTINEL" and secret == "SANDBOX_SECRET_SENTINEL"
        created.append(client)
        return client

    return factory, created


# ─── gating ──────────────────────────────────────────────────────────────────

def test_submit_blocked_without_explicit_submit_opt_in_and_no_client_created():
    client = FakeClient()
    factory, created = factory_for(client)
    result = submit_sandbox_paper_option_order(
        preview_request(), submit_config(broker_boundary_allow_sandbox_paper_submit=False), SAFE_ENV,
        client_factory=factory,
    )
    assert result.status == "BLOCKED"
    assert result.reason == "sandbox_paper_submit_not_enabled"
    assert result.submitted is False
    assert created == [] and client.calls == []


def test_submit_blocked_when_real_preview_not_enabled():
    client = FakeClient()
    factory, created = factory_for(client)
    result = submit_sandbox_paper_option_order(
        preview_request(), submit_config(broker_boundary_allow_real_preview=False), SAFE_ENV,
        client_factory=factory,
    )
    assert result.status == "BLOCKED"
    assert result.reason == "real_broker_preview_not_enabled"
    assert created == []


def test_submit_blocked_when_live_options_trading_enabled():
    client = FakeClient()
    factory, created = factory_for(client)
    result = submit_sandbox_paper_option_order(
        preview_request(), submit_config(live_options_trading_enabled=True), SAFE_ENV,
        client_factory=factory,
    )
    assert result.status == "BLOCKED"
    assert result.reason == "live_options_trading_enabled"
    assert created == []


def test_submit_blocked_on_non_sandbox_host_or_live_flag():
    client = FakeClient()
    factory, created = factory_for(client)
    for env in (
        {**SAFE_ENV, "WEBULL_SANDBOX_BASE_URL": "api.webull.com"},
        {**SAFE_ENV, "WEBULL_API_LIVE_ENABLED": "true"},
        {**SAFE_ENV, "WEBULL_SANDBOX_LIVE_TRADING_ENABLED": "true"},
        {**SAFE_ENV, "WEBULL_SANDBOX_TRADING_MODE": "live"},
    ):
        result = submit_sandbox_paper_option_order(preview_request(), submit_config(), env, client_factory=factory)
        assert result.status == "BLOCKED", env
        assert result.reason == "webull_sandbox_config_invalid"
    assert created == []


def test_submit_rechecks_broker_boundary_before_any_client_call():
    client = FakeClient()
    factory, created = factory_for(client)
    result = submit_sandbox_paper_option_order(
        preview_request(quantity=5), submit_config(), SAFE_ENV, client_factory=factory,
    )
    assert result.status == "REJECTED"
    assert result.reason.startswith("broker_boundary:quantity")
    assert created == []


# ─── happy path ──────────────────────────────────────────────────────────────

def test_submit_previews_then_places_single_leg_and_uses_ticket_as_client_order_id():
    client = FakeClient()
    factory, _ = factory_for(client)
    result = submit_sandbox_paper_option_order(preview_request(), submit_config(), SAFE_ENV, client_factory=factory)

    assert result.status == "SUBMITTED"
    assert result.submitted is True
    assert result.broker == WEBULL_SANDBOX_BROKER
    assert result.endpoint == WEBULL_SANDBOX_HOST and result.mode == "paper"
    assert result.ticket_id == "ticket-123"
    assert result.client_order_id == "ticket-123"
    assert result.broker_order_id == "WB-SANDBOX-1"
    assert result.estimated_cost == 100.0 and result.estimated_transaction_fee == 0.05
    assert "sandbox_paper_only" in result.warnings

    kinds = [c[0] for c in client.calls]
    assert kinds == ["accounts", "preview", "place"], kinds
    _, account_id, orders = client.calls[2]
    assert account_id == "paper-cash-id"
    assert len(orders) == 1 and len(orders[0]["legs"]) == 1
    order = orders[0]
    assert order["client_order_id"] == "ticket-123"
    assert order["order_type"] == "LIMIT" and order["side"] == "BUY"
    assert order["position_intent"] == "BUY_TO_OPEN"
    assert order["instrument_type"] == "OPTION"
    assert order["market"] == "US" and order["symbol"] == "AAPL"
    assert order["option_strategy"] == "SINGLE" and order["time_in_force"] == "DAY"
    leg = order["legs"][0]
    assert leg["instrument_type"] == "OPTION" and leg["market"] == "US"
    assert leg["symbol"] == "AAPL" and leg["option_type"] == "CALL"
    assert leg["strike_price"] == "350" and leg["option_expire_date"] == "2026-12-18"
    # the preview and the placed order are byte-identical
    assert client.calls[1][2] == orders


def test_submit_stops_when_broker_preview_rejects():
    client = FakeClient(preview=FakeResponse(400, {"code": "INSUFFICIENT_FUNDS"}))
    factory, _ = factory_for(client)
    result = submit_sandbox_paper_option_order(preview_request(), submit_config(), SAFE_ENV, client_factory=factory)
    assert result.status == "REJECTED"
    assert result.reason == "preview_http_400"
    assert result.submitted is False
    assert [c[0] for c in client.calls] == ["accounts", "preview"]


def test_submit_reports_placement_http_rejection_without_claiming_submission():
    client = FakeClient(place=FakeResponse(422, {"code": "MARKET_CLOSED"}))
    factory, _ = factory_for(client)
    result = submit_sandbox_paper_option_order(preview_request(), submit_config(), SAFE_ENV, client_factory=factory)
    assert result.status == "REJECTED"
    assert result.reason == "place_http_422"
    assert result.submitted is False and result.broker_order_id is None


def test_submit_flags_unknown_outcome_when_place_raises_after_send():
    client = FakeClient(raise_on_place=TimeoutError("socket timeout"))
    factory, _ = factory_for(client)
    result = submit_sandbox_paper_option_order(preview_request(), submit_config(), SAFE_ENV, client_factory=factory)
    assert result.status == "ERROR"
    assert result.reason == "place_request_failed:TimeoutError"
    assert result.submitted is False
    assert "placement_outcome_unknown_check_order_detail" in result.warnings


def test_submit_fails_closed_when_cash_account_ambiguous():
    rows = {"data": ACCOUNT_ROWS["data"] + [{"account_id": "second-cash", "account_class": "INDIVIDUAL_CASH", "account_type": "CASH"}]}
    client = FakeClient(accounts=FakeResponse(200, rows))
    factory, _ = factory_for(client)
    result = submit_sandbox_paper_option_order(preview_request(), submit_config(), SAFE_ENV, client_factory=factory)
    assert result.status == "ERROR"
    assert result.reason == "sandbox_individual_cash_account_ambiguous"
    assert [c[0] for c in client.calls] == ["accounts"]


class FakeServerException(Exception):
    """Shape of the SDK's ServerException: error_code + http_status attrs."""
    def __init__(self, code, http_status):
        super().__init__(f"HTTP Status: {http_status}, Code: {code}")
        self.error_code = code
        self.http_status = http_status


def test_submit_maps_broker_4xx_exception_to_clean_rejection_not_unknown_outcome():
    # Observed on the real sandbox 2026-09-21 (outside 8:00-16:00 ET):
    exc = FakeServerException("OPENAPI_OPTION_CAN_NOT_TRADING_FOR_NON_TRADING_HOURS", 417)
    client = FakeClient(raise_on_place=exc)
    factory, _ = factory_for(client)
    result = submit_sandbox_paper_option_order(preview_request(), submit_config(), SAFE_ENV, client_factory=factory)
    assert result.status == "REJECTED"
    assert result.reason == "place_broker:OPENAPI_OPTION_CAN_NOT_TRADING_FOR_NON_TRADING_HOURS"
    assert result.submitted is False and result.broker_order_id is None
    assert "placement_outcome_unknown_check_order_detail" not in result.warnings
    assert result.estimated_cost == 100.0  # preview data still reported


def test_submit_keeps_unknown_outcome_warning_for_broker_5xx():
    client = FakeClient(raise_on_place=FakeServerException("INTERNAL", 500))
    factory, _ = factory_for(client)
    result = submit_sandbox_paper_option_order(preview_request(), submit_config(), SAFE_ENV, client_factory=factory)
    assert result.status == "ERROR"
    assert "placement_outcome_unknown_check_order_detail" in result.warnings


# ─── cancel / detail ─────────────────────────────────────────────────────────

def test_cancel_requires_submit_opt_in_and_targets_client_order_id():
    client = FakeClient()
    factory, created = factory_for(client)
    blocked = cancel_sandbox_paper_option_order(
        "ticket-123", submit_config(broker_boundary_allow_sandbox_paper_submit=False), SAFE_ENV, client_factory=factory,
    )
    assert blocked.status == "BLOCKED" and created == []

    result = cancel_sandbox_paper_option_order("ticket-123", submit_config(), SAFE_ENV, client_factory=factory)
    assert result.status == "CANCEL_REQUESTED"
    assert result.client_order_id == "ticket-123"
    assert result.broker_order_id == "WB-SANDBOX-1"
    assert client.calls[-1] == ("cancel", "paper-cash-id", "ticket-123")


def test_cancel_rejects_empty_ticket_without_client():
    client = FakeClient()
    factory, created = factory_for(client)
    result = cancel_sandbox_paper_option_order("  ", submit_config(), SAFE_ENV, client_factory=factory)
    assert result.status == "REJECTED" and result.reason == "ticket_id_missing"
    assert created == []


def test_order_detail_is_read_only_and_does_not_need_submit_opt_in():
    client = FakeClient()
    factory, _ = factory_for(client)
    result = get_sandbox_paper_order_detail(
        "ticket-123", submit_config(broker_boundary_allow_sandbox_paper_submit=False, broker_boundary_allow_real_preview=False),
        SAFE_ENV, client_factory=factory,
    )
    assert result.status == "OK"
    assert result.order_state == "FILLED" and result.terminal is True
    assert result.filled_quantity == 1
    assert result.average_fill_price == 0.98 and result.limit_price == 1.0
    assert result.broker_order_id == "WB-SANDBOX-1"
    assert [c[0] for c in client.calls] == ["accounts", "detail"]


def test_order_detail_not_found_and_open_states():
    client = FakeClient(detail=FakeResponse(404, {}))
    factory, _ = factory_for(client)
    assert get_sandbox_paper_order_detail("ticket-x", submit_config(), SAFE_ENV, client_factory=factory).status == "NOT_FOUND"

    client = FakeClient(detail=FakeResponse(200, {"data": {"order_id": "WB-2", "order_status": "WORKING", "filled_quantity": "0"}}))
    factory, _ = factory_for(client)
    result = get_sandbox_paper_order_detail("ticket-y", submit_config(), SAFE_ENV, client_factory=factory)
    assert result.status == "OK" and result.order_state == "WORKING" and result.terminal is False


# ─── source boundary ─────────────────────────────────────────────────────────

def test_paper_order_module_exposes_no_live_host_stock_or_replace_capability():
    source = Path("options_manager/adapters/webull_sandbox_paper_orders.py").read_text().lower()
    assert "api.webull.com" not in source
    assert "tradeclient" not in source
    assert "place_order_request" not in source      # stock/generic order path
    assert "replace_option" not in source
    assert "replace_order" not in source
    assert "cancel_order_request" not in source     # stock/generic cancel path
    assert "risk/risk_engine" not in source
    assert "strategy/" not in source
    assert "allow_sandbox_paper_submit" in source  # explicit opt-in is referenced


def test_preview_adapter_still_exposes_no_submission_capability():
    source = Path("options_manager/adapters/webull_sandbox.py").read_text().lower()
    assert "place_option" not in source
    assert "cancel_option" not in source
    assert "webull_sandbox_paper_orders" not in source


def test_no_runtime_module_imports_the_paper_order_lane_yet():
    for root in ("alert_ranker", "webhook", "options_manager/runtime", "options_manager/scanner"):
        p = Path(root)
        if not p.exists():
            continue
        for f in p.rglob("*.py"):
            assert "webull_sandbox_paper_orders" not in f.read_text(), f


def test_config_flag_defaults_off_and_reads_env(monkeypatch):
    assert OptionsManagerConfig().broker_boundary_allow_sandbox_paper_submit is False
    monkeypatch.setenv("OPTIONS_MANAGER_BROKER_BOUNDARY_ALLOW_SANDBOX_PAPER_SUBMIT", "true")
    assert OptionsManagerConfig.from_env().broker_boundary_allow_sandbox_paper_submit is True