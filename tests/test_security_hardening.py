"""
tests/test_security_hardening.py

Stage 0 web-exposure hardening:
  - security headers on every response
  - CORS allowlist helper (default '*', env override)
  - PUBLIC_DEMO_MODE default-deny gate
  - webhook secret accepted from header / body / (deprecated) query
"""

from __future__ import annotations

import pytest


def _client(monkeypatch):
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi[testclient] not installed")
    app_module._RATE_BUCKETS.clear()
    return TestClient(app), app_module


def _alert_body(ticker: str = "AAPL", **extra) -> dict:
    body = {
        "ticker": ticker,
        "timestamp": "2026-06-04T12:00:00Z",
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
    }
    body.update(extra)
    return body


# ─── Security headers ─────────────────────────────────────────────────────────

def test_security_headers_present_on_every_response(monkeypatch):
    client, _ = _client(monkeypatch)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert "max-age=" in resp.headers["Strict-Transport-Security"]


# ─── CORS allowlist helper ────────────────────────────────────────────────────

def test_cors_allow_origins_defaults_to_wildcard(monkeypatch):
    import webhook.app as app_module
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    assert app_module._cors_allow_origins() == ["*"]


def test_cors_allow_origins_env_override(monkeypatch):
    import webhook.app as app_module
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.com, http://localhost:8081 ,")
    assert app_module._cors_allow_origins() == ["https://a.com", "http://localhost:8081"]


# ─── PUBLIC_DEMO_MODE default-deny gate ───────────────────────────────────────

def test_public_demo_mode_off_keeps_everything_reachable(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "false")
    client, _ = _client(monkeypatch)
    # The sensitive surface is still reachable (no 404 from the demo gate).
    assert client.get("/status/broker-account").status_code != 404
    assert client.get("/").status_code != 404


def test_public_demo_mode_on_serves_only_sanitized_surface(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "true")
    client, _ = _client(monkeypatch)

    # Allowed sanitized read-only surface still works.
    assert client.get("/health").status_code == 200
    assert client.get("/status/public").status_code == 200
    assert client.get("/share").status_code == 200

    # Everything else is 404 (default-deny), as if it does not exist.
    for path in ("/", "/status/today", "/status/broker-account", "/status/diagnostics"):
        assert client.get(path).status_code == 404, path
    # Order/control surface is gone too.
    assert client.post("/webhook/manual", json={"action": "STATUS"}).status_code == 404


# ─── Webhook secret resolution (header / body / query) ────────────────────────

def test_secret_accepted_from_request_body(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "false")
    client, _ = _client(monkeypatch)
    # Non-futures ticker → auth passes, handler returns IGNORED before the pipeline.
    resp = client.post("/webhook/alert", json=_alert_body(secret="s3cret"))
    assert resp.status_code == 200
    assert resp.json().get("decision") == "IGNORED"


def test_wrong_secret_in_body_is_rejected(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "false")
    client, _ = _client(monkeypatch)
    resp = client.post("/webhook/alert", json=_alert_body(secret="wrong"))
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "env_name",
    ["TRADINGVIEW_WEBHOOK_SECRET", "TRADINGVIEW_WEBHOOK_SECRET_NEXT"],
)
def test_rotation_secret_is_accepted(monkeypatch, env_name):
    monkeypatch.setenv("WEBHOOK_SECRET", "primary-secret")
    monkeypatch.setenv(env_name, "rotation-secret")
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "false")
    client, _ = _client(monkeypatch)

    resp = client.post(
        "/webhook/alert",
        json=_alert_body(),
        headers={"X-Webhook-Secret": "rotation-secret"},
    )

    assert resp.status_code == 200
    assert resp.json().get("decision") == "IGNORED"


def test_rotation_secrets_do_not_replace_primary(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "primary-secret")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "rotation-secret")
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "false")
    client, _ = _client(monkeypatch)

    primary = client.post(
        "/webhook/alert",
        json=_alert_body(),
        headers={"X-Webhook-Secret": "primary-secret"},
    )
    wrong = client.post(
        "/webhook/alert",
        json=_alert_body(),
        headers={"X-Webhook-Secret": "retired-secret"},
    )

    assert primary.status_code == 200
    assert wrong.status_code == 401


def test_secret_in_query_allowed_by_default(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "false")
    monkeypatch.delenv("ALLOW_SECRET_IN_QUERY", raising=False)
    client, _ = _client(monkeypatch)
    resp = client.post("/webhook/alert?secret=s3cret", json=_alert_body())
    assert resp.status_code == 200
    assert resp.json().get("decision") == "IGNORED"


def test_secret_in_query_rejected_when_flag_off(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "false")
    monkeypatch.setenv("ALLOW_SECRET_IN_QUERY", "false")
    client, _ = _client(monkeypatch)
    # Query secret must be ignored → no secret found → 401.
    resp = client.post("/webhook/alert?secret=s3cret", json=_alert_body())
    assert resp.status_code == 401
    # Header still works with the flag off.
    ok = client.post(
        "/webhook/alert", json=_alert_body(), headers={"X-Webhook-Secret": "s3cret"}
    )
    assert ok.status_code == 200
    assert ok.json().get("decision") == "IGNORED"


# ─── Site access code gate ────────────────────────────────────────────────────

def test_site_gate_off_by_default_opens_everything(monkeypatch):
    monkeypatch.delenv("SITE_ACCESS_CODE", raising=False)
    client, _ = _client(monkeypatch)
    # No code configured → no redirect to /gate.
    assert client.get("/status/public").status_code == 200


def test_site_gate_blocks_without_code(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("SITE_ACCESS_CODE", "letmein")
    client, _ = _client(monkeypatch)

    # Browser GET with no cookie → redirected to the /gate code page.
    r = client.get("/status/public", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/gate")

    # API-style call with no cookie → 401, not a redirect.
    assert client.get("/status/public").status_code == 401

    # Ingestion + health stay exempt so TradingView / uptime never get walled off.
    assert client.get("/health").status_code == 200
    ok = client.post("/webhook/alert", json=_alert_body(), headers={"X-Webhook-Secret": "s3cret"})
    assert ok.status_code == 200


def test_site_gate_wrong_then_right_code(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("SITE_ACCESS_CODE", "letmein")
    client, _ = _client(monkeypatch)

    # Wrong code → 401, no cookie set.
    bad = client.post("/gate", data={"code": "nope", "next": "/"}, follow_redirects=False)
    assert bad.status_code == 401

    # Right code → redirect + cookie set; then the gated page opens.
    good = client.post("/gate", data={"code": "letmein", "next": "/status/public"}, follow_redirects=False)
    assert good.status_code == 302
    assert good.headers["location"] == "/status/public"
    assert client.cookies.get("vp_access")
    # Cookie now carries through → access granted.
    assert client.get("/status/public").status_code == 200
