from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from integrations.webull_paper_probe import (
    WEBULL_SANDBOX_TRADING_ENDPOINT,
    probe_webull_sandbox_accounts,
)

SAFE_API_ENV = {
    "WEBULL_APP_KEY": "KEY_SENTINEL",
    "WEBULL_APP_SECRET": "SECRET_SENTINEL",
    "WEBULL_TRADING_MODE": "paper",
    "WEBULL_LIVE_TRADING_ENABLED": "false",
    "WEBULL_API_ENABLED": "true",
    "WEBULL_PAPER_TRADING_ENABLED": "true",
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
    env = dict(SAFE_API_ENV, WEBULL_API_ENABLED="false")

    def forbidden_factory(*_args):
        raise AssertionError("client factory must not run")

    result = probe_webull_sandbox_accounts(
        env, account_client_factory=forbidden_factory, observed_at=NOW
    )
    assert result.status == "BLOCKED"
    assert result.reason == "api_disabled"
    assert result.reachable is False
    assert result.paper_context_verified is False


def test_live_mode_refuses_before_client_creation():
    env = dict(SAFE_API_ENV, WEBULL_TRADING_MODE="live")

    def forbidden_factory(*_args):
        raise AssertionError("client factory must not run")

    result = probe_webull_sandbox_accounts(
        env, account_client_factory=forbidden_factory, observed_at=NOW
    )
    assert result.status == "BLOCKED"
    assert result.reason == "phase0_config_invalid"


def test_safe_probe_uses_sandbox_and_returns_count_only():
    client = FakeAccountClient(FakeResponse(200, {"data": [{"id": "A"}, {"id": "B"}]}))
    result = probe_webull_sandbox_accounts(
        SAFE_API_ENV,
        account_client_factory=lambda *_args: client,
        observed_at=NOW,
    )
    assert result.status == "OK"
    assert result.endpoint == WEBULL_SANDBOX_TRADING_ENDPOINT
    assert result.reachable is True
    assert result.paper_context_verified is True
    assert result.account_count == 2
    assert result.reason is None
    assert client.calls == 1
    rendered = repr(result) + repr(result.to_safe_dict())
    assert "KEY_SENTINEL" not in rendered
    assert "SECRET_SENTINEL" not in rendered
    assert '"id": "A"' not in rendered


def test_http_failure_is_sanitized():
    client = FakeAccountClient(FakeResponse(401, {"message": "provider details"}))
    result = probe_webull_sandbox_accounts(
        SAFE_API_ENV,
        account_client_factory=lambda *_args: client,
        observed_at=NOW,
    )
    assert result.status == "ERROR"
    assert result.reason == "http_401"
    assert result.reachable is True
    assert result.paper_context_verified is True
    assert "provider details" not in repr(result)


def test_zero_accounts_is_wait_not_success():
    client = FakeAccountClient(FakeResponse(200, {"accounts": []}))
    result = probe_webull_sandbox_accounts(
        SAFE_API_ENV,
        account_client_factory=lambda *_args: client,
        observed_at=NOW,
    )
    assert result.status == "WAIT"
    assert result.reason == "no_sandbox_accounts"
    assert result.account_count == 0


def test_unknown_schema_fails_closed():
    client = FakeAccountClient(FakeResponse(200, {"unexpected": {"id": "abc"}}))
    result = probe_webull_sandbox_accounts(
        SAFE_API_ENV,
        account_client_factory=lambda *_args: client,
        observed_at=NOW,
    )
    assert result.status == "ERROR"
    assert result.reason == "account_schema_unrecognized"
    assert result.account_count is None


def test_client_exception_does_not_expose_exception_text():
    class ProbeError(RuntimeError):
        pass

    def failing_factory(app_key, app_secret):
        raise ProbeError(f"{app_key}:{app_secret}")

    result = probe_webull_sandbox_accounts(
        SAFE_API_ENV,
        account_client_factory=failing_factory,
        observed_at=NOW,
    )
    rendered = repr(result)
    assert result.status == "BLOCKED"
    assert result.reason == "client_init_failed:ProbeError"
    assert "KEY_SENTINEL" not in rendered
    assert "SECRET_SENTINEL" not in rendered


def test_module_has_no_production_host_or_order_capability_references():
    source = Path("integrations/webull_paper_probe.py").read_text().lower()
    assert "https://api.webull.com" not in source
    assert "tradeclient" not in source
    assert "place_order" not in source
    assert "cancel_order" not in source
    assert "replace_order" not in source
    assert "order_operation" not in source
    assert "api.sandbox.webull.com" in source
