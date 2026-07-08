"""
tests/test_execution_mode_wording.py

Execution-mode wording must reflect the runtime truth, not just paper_mode:
  - PAPER_MODE=true                              -> "Paper simulator"
  - BROKER=tradovate + TRADOVATE_ENV=demo         -> "Tradovate demo" (real broker,
    real money blocked) — never "PAPER" or "simulated position"
  - LIVE_TRADING_ENABLED=true + TRADOVATE_ENV=live -> "Live" (real money at risk)

This is display-only: no execution/broker/risk/strategy/config behavior changes.
"""
from __future__ import annotations

import pytest


def _isolate_app_logs(monkeypatch, tmp_path) -> None:
    import webhook.app as app_module

    monkeypatch.setattr(app_module._config, "log_dir", str(tmp_path / "logs"))


def _set_mode(monkeypatch, app_module, *, paper_mode, live_trading_enabled, broker="paper", tradovate_env=""):
    monkeypatch.setattr(app_module._config, "paper_mode", paper_mode)
    monkeypatch.setattr(app_module._config, "live_trading_enabled", live_trading_enabled)
    monkeypatch.setenv("BROKER", broker)
    if tradovate_env:
        monkeypatch.setenv("TRADOVATE_ENV", tradovate_env)
    else:
        monkeypatch.delenv("TRADOVATE_ENV", raising=False)


# ─── _execution_mode_info() — the shared source of truth ────────────────────


def test_execution_mode_info_paper_mode(monkeypatch):
    import webhook.app as app_module

    _set_mode(monkeypatch, app_module, paper_mode=True, live_trading_enabled=False, broker="paper")

    info = app_module._execution_mode_info()

    assert info["key"] == "paper"
    assert info["label"] == "Paper simulator"
    assert info["money_blocked"] is True


def test_execution_mode_info_tradovate_demo(monkeypatch):
    import webhook.app as app_module

    _set_mode(
        monkeypatch, app_module,
        paper_mode=False, live_trading_enabled=False,
        broker="tradovate", tradovate_env="demo",
    )

    info = app_module._execution_mode_info()

    assert info["key"] == "demo"
    assert info["label"] == "Tradovate demo"
    assert info["money_blocked"] is True


def test_execution_mode_info_live(monkeypatch):
    import webhook.app as app_module

    _set_mode(
        monkeypatch, app_module,
        paper_mode=False, live_trading_enabled=True,
        broker="tradovate", tradovate_env="live",
    )

    info = app_module._execution_mode_info()

    assert info["key"] == "live"
    assert info["label"] == "Live"
    assert info["money_blocked"] is False


def test_execution_mode_info_live_wording_requires_live_env(monkeypatch):
    """LIVE_TRADING_ENABLED=true alone must not produce 'live' wording without
    TRADOVATE_ENV=live — a demo env with the flag flipped is a misconfiguration,
    not proof of live trading."""
    import webhook.app as app_module

    _set_mode(
        monkeypatch, app_module,
        paper_mode=False, live_trading_enabled=True,
        broker="tradovate", tradovate_env="demo",
    )

    info = app_module._execution_mode_info()

    assert info["key"] != "live"


# ─── /share — server-rendered per request, safe to assert exact wording ─────


def _share_client(monkeypatch, tmp_path, *, paper_mode, live_trading_enabled, broker="paper", tradovate_env=""):
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setenv("WEBHOOK_SECRET", "testsecret")
    _isolate_app_logs(monkeypatch, tmp_path)
    _set_mode(
        monkeypatch, app_module,
        paper_mode=paper_mode, live_trading_enabled=live_trading_enabled,
        broker=broker, tradovate_env=tradovate_env,
    )
    return TestClient(app_module.app)


def test_share_dashboard_paper_mode_wording(monkeypatch, tmp_path):
    client = _share_client(monkeypatch, tmp_path, paper_mode=True, live_trading_enabled=False)

    resp = client.get("/share")

    assert resp.status_code == 200
    assert "PAPER SIMULATOR" in resp.text
    assert "REAL MONEY BLOCKED" in resp.text
    assert "PAPER SYSTEM" not in resp.text
    assert "LIVE MODE" not in resp.text


def test_share_dashboard_tradovate_demo_wording(monkeypatch, tmp_path):
    client = _share_client(
        monkeypatch, tmp_path,
        paper_mode=False, live_trading_enabled=False,
        broker="tradovate", tradovate_env="demo",
    )

    resp = client.get("/share")

    assert resp.status_code == 200
    assert "TRADOVATE DEMO" in resp.text
    assert "REAL MONEY BLOCKED" in resp.text
    # Known mismatches this PR fixes: demo must never render as paper or live.
    assert "PAPER SYSTEM" not in resp.text
    assert "LIVE MODE" not in resp.text
    assert "simulated position" not in resp.text


def test_share_dashboard_live_wording(monkeypatch, tmp_path):
    client = _share_client(
        monkeypatch, tmp_path,
        paper_mode=False, live_trading_enabled=True,
        broker="tradovate", tradovate_env="live",
    )

    resp = client.get("/share")

    assert resp.status_code == 200
    assert "LIVE MONEY AT RISK" in resp.text
    assert "REAL MONEY BLOCKED" not in resp.text
    assert "PAPER SYSTEM" not in resp.text


# ─── Dashboard INIT payload + source-level regression checks ───────────────


def test_dashboard_init_includes_tradovate_env(monkeypatch, tmp_path):
    import webhook.app as app_module

    _isolate_app_logs(monkeypatch, tmp_path)
    _set_mode(
        monkeypatch, app_module,
        paper_mode=False, live_trading_enabled=False,
        broker="tradovate", tradovate_env="demo",
    )

    init = app_module._dashboard_init({"paper_mode": False, "live_trading_enabled": False})

    assert init["broker"] == "TRADOVATE"
    assert init["tradovate_env"] == "demo"


def test_dashboard_source_no_longer_conflates_demo_with_paper_or_live(monkeypatch, tmp_path):
    """Regression check on the rendered page source: the client-side mode
    label/word must be derived from the shared execMode() helper (which knows
    about Tradovate demo), not the old two-way paper_mode-only ternaries."""
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setenv("WEBHOOK_SECRET", "testsecret")
    _isolate_app_logs(monkeypatch, tmp_path)

    resp = TestClient(app_module.app).get("/")

    assert resp.status_code == 200
    assert "function execMode()" in resp.text
    assert "TRADOVATE DEMO" in resp.text
    # The old buggy two-way ternaries must be gone, not just shadowed.
    assert "INIT.live_trading_enabled && !INIT.paper_mode ? 'LIVE' : 'PAPER'" not in resp.text
    assert "INIT.paper_mode ? '📄 PAPER MODE' : '⚡ LIVE MODE'" not in resp.text
    # renderStatusBar()'s MODE segment must also delegate to execMode(), not a
    # standalone paper-vs-live ternary.
    assert "INIT.paper_mode ? 'PAPER' : 'LIVE'" not in resp.text


# ─── /status/public — structured, correctly-labeled execution mode ─────────


def _public_client(monkeypatch, tmp_path, *, paper_mode, live_trading_enabled, broker="paper", tradovate_env=""):
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setenv("WEBHOOK_SECRET", "testsecret")
    _isolate_app_logs(monkeypatch, tmp_path)
    _set_mode(
        monkeypatch, app_module,
        paper_mode=paper_mode, live_trading_enabled=live_trading_enabled,
        broker=broker, tradovate_env=tradovate_env,
    )
    return TestClient(app_module.app)


def test_status_public_reports_tradovate_demo_accurately(monkeypatch, tmp_path):
    client = _public_client(
        monkeypatch, tmp_path,
        paper_mode=False, live_trading_enabled=False,
        broker="tradovate", tradovate_env="demo",
    )

    resp = client.get("/status/public")

    assert resp.status_code == 200
    data = resp.json()
    assert data["execution_mode"] == "demo"
    assert data["mode"] == "demo"
    assert data["broker"] == "tradovate"
    assert data["broker_env"] == "demo"
    assert data["live_money_enabled"] is False
    assert data["paper_simulator"] is False
    assert data["display_label"] == "Tradovate demo"


def test_status_public_paper_mode(monkeypatch, tmp_path):
    client = _public_client(monkeypatch, tmp_path, paper_mode=True, live_trading_enabled=False)

    data = client.get("/status/public").json()

    assert data["execution_mode"] == "paper"
    assert data["paper_simulator"] is True
    assert data["live_money_enabled"] is False
    assert data["display_label"] == "Paper simulator"


def test_status_public_live(monkeypatch, tmp_path):
    client = _public_client(
        monkeypatch, tmp_path,
        paper_mode=False, live_trading_enabled=True,
        broker="tradovate", tradovate_env="live",
    )

    data = client.get("/status/public").json()

    assert data["execution_mode"] == "live"
    assert data["live_money_enabled"] is True
    assert data["paper_simulator"] is False
