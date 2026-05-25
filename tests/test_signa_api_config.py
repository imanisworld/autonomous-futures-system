"""
tests/test_signa_api_config.py

Signa API planning is inactive and secret-safe until a later data-source phase.
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
