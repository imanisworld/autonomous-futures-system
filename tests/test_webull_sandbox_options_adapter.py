from __future__ import annotations

from datetime import date
from pathlib import Path

from integrations.webull_paper_config import WEBULL_SANDBOX_HOST
from options_manager.adapters.webull_sandbox import (
    WEBULL_SANDBOX_BROKER,
    discover_sandbox_option_contracts,
    preview_sandbox_option_order,
    read_sandbox_individual_cash_account,
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
def preview_config(**overrides) -> OptionsManagerConfig:
    values = {
        "broker_boundary_allow_real_preview": True,
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
        {
            "account_id": "paper-cash-id",
            "account_class": "INDIVIDUAL_CASH",
            "account_type": "CASH",
        },
        {
            "account_id": "paper-margin-id",
            "account_class": "INDIVIDUAL_MARGIN",
            "account_type": "MARGIN",
        },
    ]
}


class FakeAccountClient:
    def __init__(self):
        self.balance_ids = []
        self.position_ids = []

    def get_account_list(self):
        return FakeResponse(200, ACCOUNT_ROWS)

    def get_account_balance(self, account_id):
        self.balance_ids.append(account_id)
        return FakeResponse(
            200,
            {
                "data": {
                    "currency": "USD",
                    "net_liquidation": "1000000.00",
                    "cash_balance": "1000000.00",
                }
            },
        )

    def get_account_position(self, account_id):
        self.position_ids.append(account_id)
        return FakeResponse(200, {"data": []})


class FakeInstrumentClient:
    def get_option_contracts(self, ticker):
        assert ticker == "AAPL"
        return FakeResponse(
            200,
            {
                "data": [
                    {
                        "symbol": "AAPL261218C00350000",
                        "option_expire_date": "2026-12-18",
                        "strike_price": "350",
                        "option_type": "CALL",
                    },
                    {
                        "symbol": "AAPL261218P00350000",
                        "option_expire_date": "2026-12-18",
                        "strike_price": "350",
                        "option_type": "PUT",
                    },
                ]
            },
        )
class FakePreviewClient:
    def __init__(self):
        self.account_ids = []
        self.orders = []

    def get_account_list(self):
        return FakeResponse(200, ACCOUNT_ROWS)

    def preview_option(self, account_id, orders):
        self.account_ids.append(account_id)
        self.orders.append(orders)
        return FakeResponse(
            200,
            {
                "data": {
                    "currency": "USD",
                    "estimated_cost": "100.00",
                    "estimated_transaction_fee": "0.03",
                }
            },
        )


def test_live_options_mode_blocks_before_any_client_creation():
    def forbidden_factory(*_args):
        raise AssertionError("client factory must not run")

    result = read_sandbox_individual_cash_account(
        preview_config(live_options_trading_enabled=True),
        SAFE_ENV,
        account_client_factory=forbidden_factory,
    )
    assert result.status == "BLOCKED"
    assert result.reason == "live_options_trading_enabled"
def test_non_sandbox_host_blocks_before_any_client_creation():
    env = dict(SAFE_ENV, WEBULL_SANDBOX_BASE_URL="wrong.example")

    def forbidden_factory(*_args):
        raise AssertionError("client factory must not run")

    result = read_sandbox_individual_cash_account(
        preview_config(),
        env,
        account_client_factory=forbidden_factory,
    )
    assert result.status == "BLOCKED"
    assert result.reason == "webull_sandbox_config_invalid"


def test_account_snapshot_selects_only_individual_cash_and_never_returns_id():
    client = FakeAccountClient()
    received = {}

    def factory(key, secret):
        received["key"] = key
        received["secret"] = secret
        return client

    result = read_sandbox_individual_cash_account(
        preview_config(), SAFE_ENV, account_client_factory=factory
    )
    assert result.status == "OK"
    assert result.account_class == "INDIVIDUAL_CASH"
    assert result.account_type == "CASH"
    assert result.currency == "USD"
    assert result.net_liquidation == 1_000_000.0
    assert result.cash_balance == 1_000_000.0
    assert result.position_count == 0
    assert client.balance_ids == ["paper-cash-id"]
    assert client.position_ids == ["paper-cash-id"]
    assert received == {
        "key": "SANDBOX_KEY_SENTINEL",
        "secret": "SANDBOX_SECRET_SENTINEL",
    }
    rendered = repr(result)
    assert "paper-cash-id" not in rendered
    assert "SANDBOX_KEY_SENTINEL" not in rendered
    assert "LIVE_KEY_MUST_NOT_BE_USED" not in rendered


def test_ambiguous_individual_cash_account_fails_closed():
    class AmbiguousClient(FakeAccountClient):
        def get_account_list(self):
            rows = list(ACCOUNT_ROWS["data"]) + [
                {
                    "account_id": "second-paper-cash",
                    "account_class": "INDIVIDUAL_CASH",
                    "account_type": "CASH",
                }
            ]
            return FakeResponse(200, {"data": rows})

    result = read_sandbox_individual_cash_account(
        preview_config(), SAFE_ENV, account_client_factory=lambda *_: AmbiguousClient()
    )
    assert result.status == "ERROR"
    assert result.reason == "sandbox_individual_cash_account_ambiguous"


def test_contract_discovery_normalizes_real_contract_metadata():
    result = discover_sandbox_option_contracts(
        "aapl",
        preview_config(),
        SAFE_ENV,
        observed_on=date(2026, 9, 20),
        min_dte=45,
        instrument_client_factory=lambda *_: FakeInstrumentClient(),
    )
    assert result.status == "OK"
    assert result.ticker == "AAPL"
    assert len(result.contracts) == 2
    assert result.contracts[0].dte == 89
    assert {item.option_type for item in result.contracts} == {"CALL", "PUT"}
    assert {item.option_symbol for item in result.contracts} == {
        "AAPL261218C00350000",
        "AAPL261218P00350000",
    }


def test_preview_requires_explicit_real_preview_opt_in_before_client_creation():
    def forbidden_factory(*_args):
        raise AssertionError("preview client factory must not run")

    result = preview_sandbox_option_order(
        preview_request(),
        preview_config(broker_boundary_allow_real_preview=False),
        SAFE_ENV,
        preview_client_factory=forbidden_factory,
    )
    assert result.status == "BLOCKED"
    assert result.reason == "real_broker_preview_not_enabled"


def test_preview_rechecks_existing_broker_boundary_before_provider_call():
    def forbidden_factory(*_args):
        raise AssertionError("preview client factory must not run")

    result = preview_sandbox_option_order(
        preview_request(executable=True),
        preview_config(),
        SAFE_ENV,
        preview_client_factory=forbidden_factory,
    )
    assert result.status == "REJECTED"
    assert "broker_boundary:executable" in result.reason


def test_preview_builds_single_leg_sandbox_request_and_stays_non_submitted():
    client = FakePreviewClient()
    received = {}

    def factory(key, secret):
        received["key"] = key
        received["secret"] = secret
        return client

    result = preview_sandbox_option_order(
        preview_request(),
        preview_config(),
        SAFE_ENV,
        preview_client_factory=factory,
    )
    assert result.preview_ready is True
    assert result.status == "PREVIEW_READY"
    assert result.broker == WEBULL_SANDBOX_BROKER
    assert result.executable is False
    assert result.submitted is False
    assert result.broker_order_id is None
    assert result.currency == "USD"
    assert result.estimated_cost == 100.0
    assert result.estimated_transaction_fee == 0.03
    assert received == {
        "key": "SANDBOX_KEY_SENTINEL",
        "secret": "SANDBOX_SECRET_SENTINEL",
    }
    assert client.account_ids == ["paper-cash-id"]

    order = client.orders[0][0]
    assert order["combo_type"] == "NORMAL"
    assert order["order_type"] == "LIMIT"
    assert order["option_strategy"] == "SINGLE"
    assert order["side"] == "BUY"
    assert order["quantity"] == "1"
    assert order["limit_price"] == "1.0000"
    leg = order["legs"][0]
    assert leg == {
        "side": "BUY",
        "quantity": "1",
        "symbol": "AAPL",
        "strike_price": "350",
        "option_expire_date": "2026-12-18",
        "instrument_type": "OPTION",
        "option_type": "CALL",
        "market": "US",
    }


def test_adapter_source_exposes_no_submission_or_live_broker_capability():
    source = Path("options_manager/adapters/webull_sandbox.py").read_text()
    lowered = source.lower()
    assert "api.webull.com" not in lowered
    assert "tradeclient" not in lowered
    assert "place_order" not in lowered
    assert "place_option" not in lowered
    assert "cancel_order" not in lowered
    assert "replace_order" not in lowered
    assert "replace_option" not in lowered
    assert "risk/risk_engine" not in lowered
    assert "strategy/" not in lowered
