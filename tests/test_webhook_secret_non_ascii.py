"""Malformed / non-ASCII webhook secrets must be a 401, never a 500.

Incident 2026-09-17 14:30Z–15:00Z (futures-bot on release 3beffb7): a
re-created TradingView alert delivered a `secret` containing a non-ASCII
character. `hmac.compare_digest(str, str)` raises TypeError for non-ASCII
str operands, so `_verify_webhook_secret` escaped as an unhandled exception,
every delivery of that alert returned HTTP 500, TradingView retried four
times and gave up, and the bar was dropped. Authentication failure must be
reported as 401 so the caller sees an auth problem, not a server fault.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

import webhook.app as app_module
from webhook.app import _verify_webhook_secret


def _isolate_app_logs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module._config, "log_dir", str(tmp_path / "logs"))


def test_ascii_valid_secret_is_accepted(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    _verify_webhook_secret("test-secret")  # no exception


def test_ascii_invalid_secret_is_401(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    with pytest.raises(HTTPException) as exc:
        _verify_webhook_secret("wrong-secret")
    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    "provided",
    [
        "test-secret”",  # trailing curly quote pasted into the alert text
        " test-secret",  # leading non-breaking space
        "tést-secret",  # accented letter
        "‘wrong’",  # entirely non-ASCII, also wrong
    ],
)
def test_non_ascii_invalid_secret_is_401_not_type_error(monkeypatch, provided):
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    with pytest.raises(HTTPException) as exc:
        _verify_webhook_secret(provided)
    assert exc.value.status_code == 401


def test_rotation_alias_still_accepted_after_hardening(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "primary-secret")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET_NEXT", "next-secret")
    _verify_webhook_secret("next-secret")  # no exception


def test_alert_endpoint_non_ascii_secret_returns_401_and_queues_nothing(monkeypatch, tmp_path):
    """End to end through the FastAPI route: the same shape as the incident
    (secret in the JSON body), must be a 401 with nothing handed off."""
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    called = {"n": 0}
    monkeypatch.setattr(
        app_module, "process_alert", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    )

    client = TestClient(app, raise_server_exceptions=False)
    body = {
        "ticker": "M2K1!", "timestamp": "2026-09-17T14:45:00+00:00", "timeframe": "15",
        "open": 2920.0, "high": 2925.0, "low": 2918.0, "close": 2922.0,
        "secret": "test-secret”",
    }
    resp = client.post("/webhook/alert", json=body)
    assert resp.status_code == 401
    assert called["n"] == 0
