from __future__ import annotations

import importlib
import sys

from integrations.webull_paper_config import (
    load_webull_paper_config,
    redacted_webull_env_names,
)


BASE_ENV = {
    "WEBULL_APP_KEY": "app-key-secret-value",
    "WEBULL_APP_SECRET": "app-secret-secret-value",
    "WEBULL_TRADING_MODE": "paper",
    "WEBULL_LIVE_TRADING_ENABLED": "false",
    "WEBULL_API_ENABLED": "false",
    "WEBULL_PAPER_TRADING_ENABLED": "true",
}


def test_webull_phase0_safe_default_blocks_network_calls():
    config = load_webull_paper_config(BASE_ENV)

    assert config.app_key_configured is True
    assert config.app_secret_configured is True
    assert config.trading_mode == "paper"
    assert config.live_trading_enabled is False
    assert config.api_enabled is False
    assert config.paper_trading_enabled is True
    assert config.paper_only_safe is True
    assert config.network_calls_allowed is False
    assert config.errors == ()


def test_webull_config_never_exposes_secret_values():
    config = load_webull_paper_config(BASE_ENV)
    summary = config.redacted_summary()
    rendered = repr(config) + repr(summary)

    assert "app-key-secret-value" not in rendered
    assert "app-secret-secret-value" not in rendered
    assert summary["WEBULL_APP_KEY"] == "PRESENT_REDACTED"
    assert summary["WEBULL_APP_SECRET"] == "PRESENT_REDACTED"
    assert set(redacted_webull_env_names()) == {"WEBULL_APP_KEY", "WEBULL_APP_SECRET"}


def test_webull_live_mode_fails_closed():
    env = dict(BASE_ENV, WEBULL_TRADING_MODE="live")

    config = load_webull_paper_config(env)

    assert config.paper_only_safe is False
    assert config.network_calls_allowed is False
    assert "WEBULL_TRADING_MODE must equal paper" in config.errors


def test_webull_live_trading_flag_fails_closed():
    env = dict(BASE_ENV, WEBULL_LIVE_TRADING_ENABLED="true")

    config = load_webull_paper_config(env)

    assert config.paper_only_safe is False
    assert config.network_calls_allowed is False
    assert "WEBULL_LIVE_TRADING_ENABLED must be false" in config.errors


def test_webull_paper_trading_flag_required():
    env = dict(BASE_ENV, WEBULL_PAPER_TRADING_ENABLED="false")

    config = load_webull_paper_config(env)

    assert config.paper_only_safe is False
    assert config.network_calls_allowed is False
    assert "WEBULL_PAPER_TRADING_ENABLED must be true" in config.errors


def test_webull_api_enabled_requires_credentials():
    env = {
        "WEBULL_TRADING_MODE": "paper",
        "WEBULL_LIVE_TRADING_ENABLED": "false",
        "WEBULL_API_ENABLED": "true",
        "WEBULL_PAPER_TRADING_ENABLED": "true",
    }

    config = load_webull_paper_config(env)

    assert config.paper_only_safe is False
    assert config.network_calls_allowed is False
    assert "WEBULL_API_ENABLED requires WEBULL_APP_KEY to be configured" in config.errors
    assert "WEBULL_API_ENABLED requires WEBULL_APP_SECRET to be configured" in config.errors


def test_webull_config_module_does_not_import_webull_sdk(monkeypatch):
    for name in list(sys.modules):
        if name == "webull" or name.startswith("webull."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    import integrations.webull_paper_config as module
    importlib.reload(module)
    module.load_webull_paper_config(BASE_ENV)

    assert "webull" not in sys.modules

def test_webull_missing_live_flag_fails_closed():
    env = dict(BASE_ENV)
    env.pop("WEBULL_LIVE_TRADING_ENABLED")
    config = load_webull_paper_config(env)
    assert config.paper_only_safe is False
    assert config.network_calls_allowed is False
    assert "WEBULL_LIVE_TRADING_ENABLED must be explicitly true or false" in config.errors


def test_webull_unknown_live_flag_fails_closed():
    env = dict(BASE_ENV, WEBULL_LIVE_TRADING_ENABLED="maybe")
    config = load_webull_paper_config(env)
    assert config.paper_only_safe is False
    assert config.network_calls_allowed is False
    assert "WEBULL_LIVE_TRADING_ENABLED must be explicitly true or false" in config.errors


def test_webull_missing_api_flag_fails_closed():
    env = dict(BASE_ENV)
    env.pop("WEBULL_API_ENABLED")
    config = load_webull_paper_config(env)
    assert config.paper_only_safe is False
    assert "WEBULL_API_ENABLED must be explicitly true or false" in config.errors


def test_webull_credentials_required_even_when_api_disabled():
    env = dict(BASE_ENV, WEBULL_APP_KEY="", WEBULL_APP_SECRET="")
    config = load_webull_paper_config(env)
    assert config.paper_only_safe is False
    assert config.network_calls_allowed is False
    assert "WEBULL_APP_KEY must be configured" in config.errors
    assert "WEBULL_APP_SECRET must be configured" in config.errors
