"""
tests/test_signa_api_config.py

Signa API config is optional, read-only, and secret-safe.
"""

from __future__ import annotations

from config.settings import load_config


def test_signa_api_key_is_detected_without_storing_secret(monkeypatch):
    monkeypatch.setenv("SIGNA_API_KEY", "secret-value-not-for-config")
    monkeypatch.setenv("SIGNA_API_ENABLED", "false")

    config = load_config("risk_rules.yaml")

    assert config.signa_api_key_configured is True
    assert config.signa_api_enabled is False
    assert not hasattr(config, "signa_api_key")


def test_signa_api_enabled_flag_is_configurable(monkeypatch):
    monkeypatch.setenv("SIGNA_API_ENABLED", "true")
    monkeypatch.setenv("SIGNA_API_KEY", "secret-value-not-for-config")

    config = load_config("risk_rules.yaml")

    assert config.signa_api_enabled is True
    assert config.signa_api_key_configured is True


def test_signa_api_runtime_options_are_loaded(monkeypatch):
    monkeypatch.setenv("SIGNA_API_ENABLED", "true")
    monkeypatch.setenv("SIGNA_API_KEY", "secret-value-not-for-config")
    monkeypatch.setenv("SIGNA_BASE_URL", "https://app.getsigna.ai/")
    monkeypatch.setenv("SIGNA_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("SIGNA_SYMBOL_MAP", "MES:SPY,MNQ:QQQ,MGC:GLD")

    config = load_config("risk_rules.yaml")

    assert config.signa_base_url == "https://app.getsigna.ai"
    assert config.signa_timeout_seconds == 4.5
    assert config.signa_symbol_map["MES"] == "SPY"
    assert config.signa_symbol_map["MNQ"] == "QQQ"
    assert config.signa_symbol_map["MGC"] == "GLD"
    assert not hasattr(config, "signa_api_key")
