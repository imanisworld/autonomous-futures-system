from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from integrations.webull_paper_config import WEBULL_SANDBOX_HOST
from integrations.webull_paper_probe import probe_webull_sandbox_accounts

SAFE_API_ENV = {
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
NOW = datetime(2026, 9, 20, 20, 30, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeAccountClient:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def get_account_list(self):
        self.calls += 1
        return self.response


def test_api_disabled_refuses_before_client_creation():
    env = dict(SAFE_API_ENV, WEBULL_SANDBOX_API_ENABLED="false")

    def forbidden_factory(*_args):
        raise AssertionError("client factory must not run")

    result = probe_webull_sandbox_accounts(
        env, account_client_factory=forbidden_factory, observed_at=NOW
    )
    assert result.status == "BLOCKED"
    assert result.reason == "api_disabled"


def test_live_api_enable_refuses_before_client_creation():
    env = dict(SAFE_API_ENV, WEBULL_API_LIVE_ENABLED="true")

    def forbidden_factory(*_args):
        raise AssertionError("client factory must not run")

    result = probe_webull_sandbox_accounts(
        env, account_client_factory=forbidden_factory, observed_at=NOW
    )
    assert result.status == "BLOCKED"
    assert result.reason == "phase0_config_invalid"


def test_safe_probe_uses_only_sandbox_credentials_and_returns_count_only():
    client = FakeAccountClient(FakeResponse(200, {"data": [{"id": "A"}, {"id": "B"}]}))
    received = {}

    def factory(app_key, app_secret):
        received["app_key"] = app_key
        received["app_secret"] = app_secret
        return client

    result = probe_webull_sandbox_accounts(
        SAFE_API_ENV, account_client_factory=factory, observed_at=NOW
    )
    assert result.status == "OK"
    assert result.endpoint == WEBULL_SANDBOX_HOST
    assert result.account_count == 2
    assert received == {
        "app_key": "SANDBOX_KEY_SENTINEL",
        "app_secret": "SANDBOX_SECRET_SENTINEL",
    }
    rendered = repr(result) + repr(result.to_safe_dict())
    for forbidden in (
        "SANDBOX_KEY_SENTINEL",
        "SANDBOX_SECRET_SENTINEL",
        "LIVE_KEY_MUST_NOT_BE_USED",
        "LIVE_SECRET_MUST_NOT_BE_USED",
        '"id": "A"',
    ):
        assert forbidden not in rendered


def test_http_failure_is_sanitized():
    client = FakeAccountClient(FakeResponse(401, {"message": "provider details"}))
    result = probe_webull_sandbox_accounts(
        SAFE_API_ENV, account_client_factory=lambda *_args: client, observed_at=NOW
    )
    assert result.status == "ERROR"
    assert result.reason == "http_401"
    assert "provider details" not in repr(result)


def test_zero_accounts_is_wait_not_success():
    client = FakeAccountClient(FakeResponse(200, {"accounts": []}))
    result = probe_webull_sandbox_accounts(
        SAFE_API_ENV, account_client_factory=lambda *_args: client, observed_at=NOW
    )
    assert result.status == "WAIT"
    assert result.reason == "no_sandbox_accounts"
    assert result.account_count == 0


def test_unknown_schema_fails_closed():
    client = FakeAccountClient(FakeResponse(200, {"unexpected": {"id": "abc"}}))
    result = probe_webull_sandbox_accounts(
        SAFE_API_ENV, account_client_factory=lambda *_args: client, observed_at=NOW
    )
    assert result.status == "ERROR"
    assert result.reason == "account_schema_unrecognized"


def test_module_has_no_production_host_or_order_capability_references():
    source = Path("integrations/webull_paper_probe.py").read_text().lower()
    assert "api.webull.com" not in source
    assert "tradeclient" not in source
    assert "place_order" not in source
    assert "cancel_order" not in source
    assert "replace_order" not in source
    assert "order_operation" not in source
    assert "api.sandbox.webull.com" not in source  # imported from frozen config constant
