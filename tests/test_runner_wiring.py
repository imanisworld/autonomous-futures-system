"""The runner exit + per-instrument stop multiplier are config-wired.

Before this, PaperBroker had runner_mode but _paper_broker() never passed it, so
the runner could never turn on. These lock the wiring.
"""

from __future__ import annotations

from config.settings import load_config
from webhook.runner import _paper_broker


def test_runner_defaults_off(monkeypatch):
    monkeypatch.delenv("RUNNER_MODE", raising=False)
    c = load_config()
    assert c.runner_mode is False
    assert _paper_broker(1500.0, c)._runner_mode is False


def test_runner_mode_env_enables_and_flows_to_broker(monkeypatch):
    monkeypatch.setenv("RUNNER_MODE", "true")
    monkeypatch.setenv("RUNNER_ACTIVATION_R", "1.0")
    monkeypatch.setenv("RUNNER_TRAIL_R", "0.5")
    c = load_config()
    assert c.runner_mode is True
    assert c.runner_activation_r == 1.0
    assert c.runner_trail_r == 0.5
    b = _paper_broker(1500.0, c)
    assert b._runner_mode is True
    assert b._runner_activation_r == 1.0
    assert b._runner_trail_r == 0.5


def test_stop_multiplier_per_instrument_field_present():
    c = load_config()
    # default empty dict (== all 1.0); the field exists and is a dict
    assert isinstance(c.stop_multiplier_per_instrument, dict)
