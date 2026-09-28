"""Broker hard-cap refusals must not touch the network or the Webull mirror."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import requests

from execution.broker_interface import BracketOrder


def _order(contracts):
    return BracketOrder(
        instrument="MNQ",
        direction="LONG",
        entry=100.0,
        stop=90.0,
        target=120.0,
        rr_ratio=2.0,
        strategy="orb_reclaim",
        contracts=contracts,
    )


@pytest.fixture
def net(monkeypatch):
    calls = []

    def rec(tag):
        def _f(*a, **k):
            calls.append((tag, a[1:3] if len(a) > 2 else a, k.get("url")))
            raise ConnectionError(f"network touched via {tag}")

        return _f

    monkeypatch.setattr(socket.socket, "connect", rec("socket.connect"))
    monkeypatch.setattr(socket, "create_connection", rec("socket.create_connection"))
    monkeypatch.setattr(requests.Session, "request", rec("Session.request"))
    for name in ("get", "post", "put", "delete", "request"):
        monkeypatch.setattr(requests, name, rec(f"requests.{name}"))
    return calls


@pytest.fixture
def creds(monkeypatch, isolate_live_broker_env):
    # Fake credentials so pre-fix code would try to log in. Depends on the
    # autouse env scrub so these values are the ones execute_bracket sees.
    monkeypatch.setenv("TRADOVATE_USERNAME", "qa_user")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "qa_pw")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "123")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "qa_secret")


def _tv():
    from execution.tradovate_broker import TradovateBroker, TradovateConfig

    try:
        cfg = TradovateConfig.from_env()
    except Exception:
        cfg = TradovateConfig(env="demo", expected_account_id=1)
    return TradovateBroker(config=cfg)


def _set_cap(monkeypatch, cap):
    if cap is None:
        monkeypatch.delenv("MAX_CONTRACTS_HARD_CAP", raising=False)
    else:
        monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", cap)


@pytest.mark.parametrize(
    "cap,qty",
    [
        ("1", 4),
        ("1", 2),
        (None, 1),
        ("", 1),
        ("abc", 1),
        ("0", 1),
        ("-3", 1),
        ("1.5", 1),
        ("²", 1),
        ("1", 0),
        ("1", -2),
        ("1", None),
        ("1", True),
    ],
)
def test_tradovate_refusal_zero_network(monkeypatch, net, creds, cap, qty):
    _set_cap(monkeypatch, cap)
    fill = _tv().execute_bracket(_order(qty))
    assert net == [], f"network touched: {net}"
    assert fill.result == "CANCELLED"
    assert fill.contracts == qty
    assert "MAX_CONTRACTS_HARD_CAP" in (fill.exit_reason or "")


def test_tradovate_control_at_cap_does_touch_network(monkeypatch, net, creds):
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    _tv().execute_bracket(_order(1))
    assert net, "control expected at least one network attempt (login)"


def _mirror_sync(monkeypatch):
    import execution.paper_mirror_hook as hook
    import execution.webull_sandbox_futures_mirror as mirror

    seen = []
    monkeypatch.setenv("WEBULL_FUTURES_MIRROR_ENABLED", "true")
    monkeypatch.setattr(hook, "_dispatch", lambda fn: fn())
    monkeypatch.setattr(hook, "mirror_enabled", lambda: True, raising=False)
    monkeypatch.setattr(mirror, "mirror_entry", lambda order, **k: seen.append(order.contracts))
    return seen


@pytest.mark.parametrize(
    "cap,qty",
    [
        ("1", 3),
        (None, 1),
        ("abc", 1),
        ("0", 1),
        ("²", 1),
        ("1", 0),
        ("1", -2),
        ("1", None),
    ],
)
def test_paper_refusal_skips_webull_mirror(monkeypatch, net, cap, qty):
    from execution.paper_broker import PaperBroker

    _set_cap(monkeypatch, cap)
    seen = _mirror_sync(monkeypatch)
    broker = PaperBroker()
    fill = broker.execute_bracket(_order(qty))
    assert fill.result == "CANCELLED" and fill.contracts == qty
    assert seen == [] and net == [] and broker._position is None


def test_paper_control_at_cap_mirror_called(monkeypatch, net):
    from execution.paper_broker import PaperBroker

    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    seen = _mirror_sync(monkeypatch)
    fill = PaperBroker().execute_bracket(_order(1))
    assert fill.result == "OPEN" and seen == [1]
    assert net == []


def test_app_import_refuses_when_hard_cap_missing():
    """webhook.app calls load_config() at import. conftest's cap must not hide that."""
    env = os.environ.copy()
    env.pop("MAX_CONTRACTS_HARD_CAP", None)
    env.pop("RELEASE_INTEGRITY_ENFORCED", None)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-c", "import webhook.app"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, combined
    assert "ConfigError" in combined or "SystemExit" in combined
    assert "MAX_CONTRACTS_HARD_CAP" in combined
