"""TRADOVATE_ENV must be exactly demo or live when a Tradovate broker is built.

Missing, blank, padded, and mistyped values raise TradovateEnvConfigError.
BROKER=paper does not read the variable.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from execution.paper_broker import PaperBroker
from execution.tradovate_broker import (
    TradovateBroker,
    TradovateConfig,
    TradovateEnvConfigError,
)

_DEMO_URL = "https://demo.tradovateapi.com/v1"
_LIVE_URL = "https://live.tradovateapi.com/v1"
_INVALID = [None, "", " ", "prod", "Demo", "LIVE", "live "]


def _message_names_allowed_values(exc: BaseException) -> None:
    message = str(exc)
    assert "TRADOVATE_ENV" in message
    assert '"demo"' in message
    assert '"live"' in message


def _reject(monkeypatch, raw: str | None) -> None:
    monkeypatch.setenv("TRADOVATE_PASSWORD", "super-secret-value")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "secret-uuid")
    if raw is None:
        monkeypatch.delenv("TRADOVATE_ENV", raising=False)
    else:
        monkeypatch.setenv("TRADOVATE_ENV", raw)
    with pytest.raises(TradovateEnvConfigError) as caught:
        TradovateConfig.from_env()
    _message_names_allowed_values(caught.value)
    message = str(caught.value)
    assert "super-secret-value" not in message
    assert "secret-uuid" not in message
    # A whitespace-only value is a substring of the fixed sentence; every other
    # rejected value must not be copied into the error.
    if raw and raw.strip():
        assert raw not in message


@pytest.mark.parametrize("raw", _INVALID)
def test_from_env_rejects_inexact_values(monkeypatch, raw):
    _reject(monkeypatch, raw)


@pytest.mark.parametrize("raw", _INVALID)
def test_broker_without_config_rejects_inexact_values(monkeypatch, raw):
    monkeypatch.setenv("TRADOVATE_PASSWORD", "super-secret-value")
    if raw is None:
        monkeypatch.delenv("TRADOVATE_ENV", raising=False)
    else:
        monkeypatch.setenv("TRADOVATE_ENV", raw)

    def _no_network(*_args, **_kwargs):
        raise AssertionError("Tradovate env validation made a network call")

    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    with pytest.raises(TradovateEnvConfigError) as caught:
        TradovateBroker()
    _message_names_allowed_values(caught.value)
    assert "super-secret-value" not in str(caught.value)


def test_exact_demo_and_live_select_base_urls_without_network(monkeypatch):
    def _no_network(*_args, **_kwargs):
        raise AssertionError("Tradovate env validation made a network call")

    monkeypatch.setattr("requests.sessions.Session.request", _no_network)
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    demo = TradovateConfig.from_env()
    assert demo.env == "demo"
    assert demo.base_url == _DEMO_URL

    monkeypatch.setenv("TRADOVATE_ENV", "live")
    live = TradovateConfig.from_env()
    assert live.env == "live"
    assert live.base_url == _LIVE_URL


def test_make_broker_tradovate_rejects_invalid_env(monkeypatch):
    from webhook.runner import _make_broker

    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "prod")
    with pytest.raises(TradovateEnvConfigError) as caught:
        _make_broker()
    _message_names_allowed_values(caught.value)
    assert "prod" not in str(caught.value)


def test_startup_refuses_tradovate_with_invalid_env(monkeypatch):
    import webhook.app as app_module

    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "LIVE")

    async def exercise():
        async with app_module._lifespan(app_module.app):
            return "started"

    with pytest.raises(TradovateEnvConfigError) as caught:
        asyncio.run(exercise())
    _message_names_allowed_values(caught.value)
    assert "LIVE" not in str(caught.value)


def test_tv_broker_status_and_manual_close_surface_invalid_env(monkeypatch):
    import webhook.app as app_module

    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "live ")
    app_module._TV_BROKER = None
    try:
        with pytest.raises(TradovateEnvConfigError):
            app_module._tv_broker()
        with pytest.raises(TradovateEnvConfigError):
            app_module._broker_status()
        with pytest.raises(TradovateEnvConfigError):
            app_module._manual_close_all()
    finally:
        app_module._TV_BROKER = None


def test_wide_stop_broker_factory_surfaces_invalid_env(monkeypatch):
    from context.wide_stop_demo_runtime_core import _broker_factory

    monkeypatch.setenv("TRADOVATE_ENV", "Demo")
    with pytest.raises(TradovateEnvConfigError) as caught:
        _broker_factory()
    _message_names_allowed_values(caught.value)
    assert "Demo" not in str(caught.value)


def test_supervisor_does_not_swallow_invalid_env(monkeypatch):
    import execution.tradovate_supervisor as supervisor

    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.delenv("TRADOVATE_ENV", raising=False)

    async def exercise():
        await supervisor.run_tradovate_supervisor(interval_s=0)

    with pytest.raises(TradovateEnvConfigError) as caught:
        asyncio.run(exercise())
    _message_names_allowed_values(caught.value)


def test_reconciler_construction_and_loop_surface_invalid_env(monkeypatch, tmp_path, config):
    from journal.journal_logger import JournalLogger
    from webhook import reconciler

    now = datetime.now(timezone.utc)
    journal = JournalLogger(log_dir=str(tmp_path))
    journal._append(
        {
            "ts": (now - timedelta(minutes=30)).isoformat(),
            "instrument": "MNQ",
            "session": "new_york",
            "decision": "TRADE",
            "risk_check": {"result": "APPROVED"},
            "setup": {
                "direction": "SHORT",
                "entry": 30000.0,
                "stop": 30008.0,
                "target": 29976.0,
                "contracts": 1,
            },
            "outcome": None,
        },
        now.date(),
    )
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", " ")

    with pytest.raises(TradovateEnvConfigError):
        reconciler.reconcile_open_position(config, str(tmp_path), now=now)

    async def instant(_seconds):
        return None

    monkeypatch.setattr(reconciler.asyncio, "sleep", instant)

    async def exercise():
        await asyncio.wait_for(
            reconciler.run_reconciler_loop(config, str(tmp_path), interval_s=0),
            timeout=2,
        )

    with pytest.raises(TradovateEnvConfigError):
        asyncio.run(exercise())


def test_paper_broker_does_not_require_tradovate_env(monkeypatch):
    """Gate: TRADOVATE_ENV is read only after BROKER strip+lower equals tradovate.

    The paper default (and any other broker value) never calls
    TradovateConfig.from_env, so an unset variable leaves paper startup and
    _make_broker unchanged.
    """
    import webhook.app as app_module
    from webhook.runner import _make_broker

    monkeypatch.setenv("BROKER", "paper")
    monkeypatch.delenv("TRADOVATE_ENV", raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")

    broker = _make_broker()
    assert isinstance(broker, PaperBroker)

    async def exercise():
        async with app_module._lifespan(app_module.app):
            return "started"

    assert asyncio.run(exercise()) == "started"
