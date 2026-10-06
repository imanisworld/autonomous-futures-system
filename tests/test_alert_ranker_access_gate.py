"""Regression tests for the options scanner in-app access gate.

The scanner relied on nginx basic auth alone; nginx auth was removed on
2026-09-21, exposing shadow-journal reads and mutators through ``/scanner/``.
These tests pin the in-app gate so exposure can no longer depend on proxy
configuration alone.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from alert_ranker.access_gate import PUBLIC_PATHS, SESSION_COOKIE
from alert_ranker.app import create_app
from alert_ranker.config import load_config
from tests.test_alert_ranker import candidate_payload, scanner_config

LOCAL = ("127.0.0.1", 50000)
LOCAL_V6 = ("::1", 50000)
REMOTE = ("203.0.113.7", 50000)
TOKEN = "s3cret-scanner-token-for-tests"

# Every non-public route the scanner registers, with a representative method.
PRIVATE_READS = [
    "/status",
    "/watchlist",
    "/terminal",
    "/shadow-journal",
    "/shadow-journal/summary",
    "/shadow-journal/1",
    "/signa/context/recent",
    "/signa/context/board",
    "/rh-options/recent",
    "/rh-options/sample",
    "/rh-options/sample-text",
    "/rh-options",
    "/",
    "/dashboard",
    "/docs",
    "/openapi.json",
]
PRIVATE_WRITES = [
    ("post", "/shadow-journal/reconcile"),
    ("patch", "/shadow-journal/1/outcome"),
    ("post", "/webhook/alert"),
    ("post", "/signa/context/ingest"),
    ("post", "/signa/context/pull"),
    ("post", "/rh-options/evaluate"),
    ("post", "/rh-options/evaluate-text"),
    ("post", "/rh-options/morning-check"),
    ("post", "/rh-options/kill-switch"),
    ("post", "/rh-options/check-positions"),
    ("post", "/rh-options/check-positions-auto"),
    ("post", "/rh-options/manage"),
]


def _app(tmp_path, token: str = ""):
    return create_app(replace(scanner_config(tmp_path), access_token=token))


def test_every_registered_route_is_classified(tmp_path):
    """A new route must be consciously added to a test list (and so to the gate)."""
    app = _app(tmp_path)
    registered = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if "{" in path:
            path = path.replace("{shadow_id}", "1")
        registered.add(path)
    known = set(PRIVATE_READS) | {p for _, p in PRIVATE_WRITES} | set(PUBLIC_PATHS)
    known |= {"/docs/oauth2-redirect", "/redoc"}
    assert registered - known == set()


@pytest.mark.parametrize("path", PRIVATE_READS)
def test_remote_private_reads_are_refused_without_token(tmp_path, path):
    client = TestClient(_app(tmp_path), client=REMOTE)
    resp = client.get(path)
    assert resp.status_code == 401
    assert resp.json() == {"error": "unauthorized", "detail": "options scanner access required"}


@pytest.mark.parametrize("method,path", PRIVATE_WRITES)
def test_remote_mutators_are_refused_before_handler_runs(tmp_path, method, path):
    client = TestClient(_app(tmp_path), client=REMOTE)
    resp = getattr(client, method)(path, json={"ticker": "SPY", "status": "WIN"})
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized"


@pytest.mark.parametrize(
    "header",
    ["x-forwarded-for", "x-real-ip", "forwarded", "x-forwarded-host", "x-forwarded-proto"],
)
def test_proxied_loopback_request_is_not_trusted_without_token(tmp_path, header):
    """nginx connects from 127.0.0.1; its forwarding headers must defeat local trust."""
    client = TestClient(_app(tmp_path), client=LOCAL)
    resp = client.get("/shadow-journal/summary", headers={header: "198.51.100.9"})
    assert resp.status_code == 401
    write = client.post("/shadow-journal/reconcile", headers={header: "198.51.100.9"})
    assert write.status_code == 401


@pytest.mark.parametrize("peer", [LOCAL, LOCAL_V6])
def test_direct_loopback_is_served_when_no_token_configured(tmp_path, peer):
    client = TestClient(_app(tmp_path), client=peer)
    assert client.get("/watchlist").json() == {"watchlist": ["AAPL"]}


def test_public_paths_stay_public(tmp_path):
    with TestClient(_app(tmp_path, TOKEN), client=REMOTE) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["access_gate"] == "token"
        public = client.get("/public/status")
        assert public.status_code == 200
        assert public.json().get("execution_authority") is False


def test_health_reports_fallback_mode_without_token(tmp_path):
    with TestClient(_app(tmp_path), client=REMOTE) as client:
        assert client.get("/health").json()["access_gate"] == "direct_loopback_only"


def test_configured_token_is_required_even_from_loopback(tmp_path):
    client = TestClient(_app(tmp_path, TOKEN), client=LOCAL)
    assert client.get("/watchlist").status_code == 401
    assert client.get("/watchlist", headers={"x-afs-scanner-token": "wrong"}).status_code == 401
    assert client.get("/watchlist", headers={"x-afs-scanner-token": TOKEN}).status_code == 200
    assert (
        client.get("/watchlist", headers={"authorization": f"Bearer {TOKEN}"}).status_code
        == 200
    )


def test_token_admits_remote_mutator(tmp_path):
    with TestClient(_app(tmp_path, TOKEN), client=REMOTE) as client:
        refused = client.post("/webhook/alert", json=candidate_payload())
        assert refused.status_code == 401
        ok = client.post(
            "/webhook/alert",
            json=candidate_payload(),
            headers={"x-afs-scanner-token": TOKEN, "x-forwarded-for": "198.51.100.9"},
        )
        assert ok.status_code == 200
        assert ok.json()["advisory_only"] is True


def test_login_sets_session_cookie_and_wrong_token_is_refused(tmp_path):
    client = TestClient(_app(tmp_path, TOKEN), client=REMOTE, follow_redirects=False)
    bad = client.post("/login", data={"token": "nope"})
    assert bad.status_code == 401
    assert SESSION_COOKIE not in bad.cookies

    good = client.post("/login", data={"token": TOKEN})
    assert good.status_code == 303
    assert good.headers["location"] == "./"
    cookie = good.cookies.get(SESSION_COOKIE)
    assert cookie and TOKEN not in cookie  # derived value, never the raw token
    set_cookie = good.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "secure" in set_cookie and "samesite=strict" in set_cookie

    authed = TestClient(_app(tmp_path, TOKEN), client=REMOTE, cookies={SESSION_COOKIE: cookie})
    assert authed.get("/watchlist").status_code == 200


def test_rotating_token_invalidates_existing_sessions(tmp_path):
    client = TestClient(_app(tmp_path, TOKEN), client=REMOTE, follow_redirects=False)
    cookie = client.post("/login", data={"token": TOKEN}).cookies.get(SESSION_COOKIE)
    rotated = TestClient(
        _app(tmp_path, TOKEN + "-rotated"), client=REMOTE, cookies={SESSION_COOKIE: cookie}
    )
    assert rotated.get("/watchlist").status_code == 401


def test_login_without_configured_token_does_not_open_anything(tmp_path):
    client = TestClient(_app(tmp_path), client=REMOTE)
    resp = client.post("/login", data={"token": ""})
    assert resp.status_code == 503
    assert client.get("/watchlist").status_code == 401


def test_browser_gets_login_form_not_data(tmp_path):
    client = TestClient(_app(tmp_path, TOKEN), client=REMOTE)
    resp = client.get("/shadow-journal", headers={"accept": "text/html"})
    assert resp.status_code == 401
    assert 'action="login"' in resp.text
    assert "items" not in resp.text


def test_load_config_reads_access_token_and_hides_it_from_repr():
    cfg = load_config([("OPTIONS_SCANNER_ACCESS_TOKEN", f"  {TOKEN}  ")])
    assert cfg.access_token == TOKEN
    assert TOKEN not in repr(cfg)
    assert load_config([]).access_token == ""
