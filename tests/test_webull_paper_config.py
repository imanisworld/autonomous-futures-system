from __future__ import annotations

import importlib
import sys

from integrations.webull_paper_config import (
    WEBULL_SANDBOX_HOST,
    load_webull_paper_config,
    redacted_webull_env_names,
)

BASE_ENV = {
    "WEBULL_SANDBOX_APP_KEY": "sandbox-key-sentinel",
    "WEBULL_SANDBOX_APP_SECRET": "sandbox-secret-sentinel",
    "WEBULL_SANDBOX_BASE_URL": WEBULL_SANDBOX_HOST,
    "WEBULL_SANDBOX_TRADING_MODE": "paper",
    "WEBULL_SANDBOX_LIVE_TRADING_ENABLED": "false",
    "WEBULL_SANDBOX_API_ENABLED": "false",
    "WEBULL_SANDBOX_PAPER_TRADING_ENABLED": "true",
    "WEBULL_API_LIVE_ENABLED": "false",
}


def test_phase0_safe_default_blocks_network_calls():
    config = load_webull_paper_config(BASE_ENV)
    assert config.secrets_configured is True
    assert config.paper_only_safe is True
    assert config.network_calls_allowed is False
    assert config.errors == ()


def test_redacted_summary_never_exposes_sandbox_secret_values():
    config = load_webull_paper_config(BASE_ENV)
    summary = config.redacted_summary()
    rendered = repr(config) + repr(summary)
    assert BASE_ENV["WEBULL_SANDBOX_APP_KEY"] not in rendered
    assert BASE_ENV["WEBULL_SANDBOX_APP_SECRET"] not in rendered
    assert summary["WEBULL_SANDBOX_APP_KEY"] == "PRESENT_REDACTED"
    assert summary["WEBULL_SANDBOX_APP_SECRET"] == "PRESENT_REDACTED"
    assert set(redacted_webull_env_names()) == {
        "WEBULL_SANDBOX_APP_KEY",
        "WEBULL_SANDBOX_APP_SECRET",
    }


def test_live_api_enable_fails_closed():
    config = load_webull_paper_config(dict(BASE_ENV, WEBULL_API_LIVE_ENABLED="true"))
    assert config.paper_only_safe is False
    assert config.network_calls_allowed is False
    assert "WEBULL_API_LIVE_ENABLED must be false" in config.errors


def test_sandbox_live_flag_fails_closed():
    config = load_webull_paper_config(
        dict(BASE_ENV, WEBULL_SANDBOX_LIVE_TRADING_ENABLED="true")
    )
    assert config.paper_only_safe is False
    assert "WEBULL_SANDBOX_LIVE_TRADING_ENABLED must be false" in config.errors


def test_non_sandbox_host_fails_closed():
    config = load_webull_paper_config(
        dict(BASE_ENV, WEBULL_SANDBOX_BASE_URL="api.webull.com")
    )
    assert config.paper_only_safe is False
    assert config.network_calls_allowed is False
    assert "WEBULL_SANDBOX_BASE_URL must equal api.sandbox.webull.com" in config.errors


def test_sandbox_mode_must_be_paper():
    config = load_webull_paper_config(
        dict(BASE_ENV, WEBULL_SANDBOX_TRADING_MODE="live")
    )
    assert config.paper_only_safe is False
    assert "WEBULL_SANDBOX_TRADING_MODE must equal paper" in config.errors


def test_missing_or_unknown_safety_flags_fail_closed():
    for key in (
        "WEBULL_SANDBOX_LIVE_TRADING_ENABLED",
        "WEBULL_SANDBOX_API_ENABLED",
        "WEBULL_SANDBOX_PAPER_TRADING_ENABLED",
        "WEBULL_API_LIVE_ENABLED",
    ):
        env = dict(BASE_ENV)
        env.pop(key)
        config = load_webull_paper_config(env)
        assert config.paper_only_safe is False
        assert config.network_calls_allowed is False
        assert any(key in error for error in config.errors)

        env = dict(BASE_ENV, **{key: "maybe"})
        config = load_webull_paper_config(env)
        assert config.paper_only_safe is False
        assert config.network_calls_allowed is False
        assert any(key in error for error in config.errors)


def test_sandbox_credentials_are_required_even_when_api_disabled():
    env = dict(BASE_ENV, WEBULL_SANDBOX_APP_KEY="", WEBULL_SANDBOX_APP_SECRET="")
    config = load_webull_paper_config(env)
    assert config.paper_only_safe is False
    assert "WEBULL_SANDBOX_APP_KEY must be configured" in config.errors
    assert "WEBULL_SANDBOX_APP_SECRET must be configured" in config.errors


def test_generic_live_credentials_do_not_satisfy_sandbox_config():
    env = dict(BASE_ENV)
    env["WEBULL_SANDBOX_APP_KEY"] = ""
    env["WEBULL_SANDBOX_APP_SECRET"] = ""
    env["WEBULL_APP_KEY"] = "LIVE_KEY_SENTINEL"
    env["WEBULL_APP_SECRET"] = "LIVE_SECRET_SENTINEL"
    config = load_webull_paper_config(env)
    assert config.paper_only_safe is False
    assert config.secrets_configured is False


def test_config_module_does_not_import_webull_sdk(monkeypatch):
    for name in list(sys.modules):
        if name == "webull" or name.startswith("webull."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    import integrations.webull_paper_config as module
    importlib.reload(module)
    module.load_webull_paper_config(BASE_ENV)
    assert "webull" not in sys.modules
