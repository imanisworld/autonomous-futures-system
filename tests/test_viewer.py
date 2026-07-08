"""
tests/test_viewer.py

Sanitized read-only /viewer tier + selective admin gate:
  - viewer auth (cookie / header / bearer / magic-link), fail-closed when unset
  - server-enforced sanitization (explicit fields + redact net) — no leaks
  - cross-tier isolation (viewer cookie ≠ admin cookie)
  - SITE_GATE_SCOPE: sensitive (default) gates only the 4 sensitive reads; full = whole site
  - viewer survives PUBLIC_DEMO_MODE; viewer api is read-only (no mutation)
"""
from __future__ import annotations

import pytest


def _client(monkeypatch, tmp_path, *, token="demo", secret="testsecret",
            code=None, scope=None, demo=False):
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
    except ImportError:  # pragma: no cover
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setenv("WEBHOOK_SECRET", secret)
    if token is not None:
        monkeypatch.setenv("VIEWER_TOKEN", token)
    else:
        monkeypatch.delenv("VIEWER_TOKEN", raising=False)
    if code is not None:
        monkeypatch.setenv("SITE_ACCESS_CODE", code)
    else:
        monkeypatch.delenv("SITE_ACCESS_CODE", raising=False)
    if scope is not None:
        monkeypatch.setenv("SITE_GATE_SCOPE", scope)
    else:
        monkeypatch.delenv("SITE_GATE_SCOPE", raising=False)
    if demo:
        monkeypatch.setenv("PUBLIC_DEMO_MODE", "true")
    else:
        monkeypatch.delenv("PUBLIC_DEMO_MODE", raising=False)

    app_module._RATE_BUCKETS.clear()
    monkeypatch.setattr(app_module._config, "log_dir", str(tmp_path / "logs"))
    return TestClient(app_module.app), app_module


_VIEWER_JSON = (
    "/viewer/api/status", "/viewer/api/dashboard", "/viewer/api/risk",
    "/viewer/api/decisions", "/viewer/api/latest-decision",
)
_FORBIDDEN_KEY_SUBSTRINGS = (
    "latest_webhook", "payload", "context", "account_balance", "account_peak",
    "account_id", "broker_account_id", "journal_path", "secret", "password",
    "api_key", "authorization", "bearer", "order", "fill",
)


def _deep_keys(obj) -> set:
    found = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.add(str(k).lower())
            found |= _deep_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _deep_keys(v)
    return found


# ─── auth ─────────────────────────────────────────────────────────────────────

def test_viewer_disabled_when_token_blank(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, token=None)
    assert client.get("/viewer/api/dashboard").status_code == 401
    assert client.get("/viewer").status_code == 503


def test_viewer_header_and_bearer_auth(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, token="demo")
    assert client.get("/viewer/api/dashboard", headers={"X-Viewer-Token": "demo"}).status_code == 200
    assert client.get("/viewer/api/dashboard", headers={"Authorization": "Bearer demo"}).status_code == 200


def test_viewer_no_or_wrong_token_401(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, token="demo")
    assert client.get("/viewer/api/dashboard").status_code == 401
    assert client.get("/viewer/api/dashboard", headers={"X-Viewer-Token": "nope"}).status_code == 401


def test_magic_link_sets_cookie_then_authed(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, token="demo")
    resp = client.get("/viewer?key=demo", follow_redirects=False)
    assert resp.status_code == 302
    assert "vp_viewer" in resp.cookies
    # cookie persists on the client → subsequent api call is authed
    assert client.get("/viewer/api/dashboard").status_code == 200


def test_typed_token_enter(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, token="demo")
    resp = client.post("/viewer/enter", data={"token": "demo"}, follow_redirects=False)
    assert resp.status_code == 302
    assert "vp_viewer" in resp.cookies
    bad = client.post("/viewer/enter", data={"token": "wrong"}, follow_redirects=False)
    assert bad.status_code == 401


# ─── sanitization ─────────────────────────────────────────────────────────────

def test_redact_drops_sensitive_keeps_safe():
    import webhook.viewer as v
    out = v.redact({
        "today_pnl": 12.5, "direction": "SHORT",
        "api_key": "x", "account_id": 12345678, "latest_webhook": {"a": 1},
        "nested": {"secret": "s", "win_rate": 100.0},
    })
    assert out["today_pnl"] == 12.5
    assert out["direction"] == "SHORT"
    assert out["nested"]["win_rate"] == 100.0
    assert "api_key" not in out and "account_id" not in out and "latest_webhook" not in out
    assert "secret" not in out["nested"]


def test_viewer_responses_carry_no_sensitive_keys(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, token="demo")
    h = {"X-Viewer-Token": "demo"}
    for path in _VIEWER_JSON:
        resp = client.get(path, headers=h)
        assert resp.status_code == 200, path
        keys = _deep_keys(resp.json())
        leaked = [k for k in keys if any(s in k for s in _FORBIDDEN_KEY_SUBSTRINGS)]
        assert not leaked, f"{path} leaked {leaked}"


def test_candidate_prices_flag(monkeypatch, tmp_path):
    import webhook.viewer as v
    client, _ = _client(monkeypatch, tmp_path, token="demo")
    entry = {
        "type": "DECISION", "ts": "2026-06-17T19:00:00Z", "instrument": "MES",
        "decision": "NO_TRADE", "reason": "rr too low",
        "setup": {"direction": "SHORT", "entry": 7582.0, "stop": 7589.0, "target": 7559.0, "rr_ratio": 3.3},
    }
    monkeypatch.setattr(v, "_read_today_entries", lambda: [entry])
    h = {"X-Viewer-Token": "demo"}

    on = client.get("/viewer/api/latest-decision", headers=h).json()
    assert on["candidate_status"] == "PRESENT"
    assert on["candidate"]["entry"] == 7582.0 and on["candidate"]["target"] == 7559.0
    assert on["execution"] == "DISABLED" and on["no_trade_taken"] is True

    monkeypatch.setenv("VIEWER_SHOW_CANDIDATE_PRICES", "false")
    off = client.get("/viewer/api/latest-decision", headers=h).json()
    assert off["candidate"]["direction"] == "SHORT"
    assert "entry" not in off["candidate"]


# ─── cross-tier isolation ─────────────────────────────────────────────────────

def test_viewer_cookie_cannot_reach_admin(monkeypatch, tmp_path):
    import webhook.viewer as v
    client, app_module = _client(monkeypatch, tmp_path, token="demo", code="admincode")
    client.cookies.set("vp_viewer", v._viewer_cookie_token())
    # viewer cookie does NOT satisfy the admin gate on a sensitive read
    assert client.get("/status/broker-account").status_code == 401


def test_admin_cookie_cannot_reach_viewer(monkeypatch, tmp_path):
    client, app_module = _client(monkeypatch, tmp_path, token="demo", code="admincode")
    client.cookies.set("vp_access", app_module._gate_token())
    assert client.get("/viewer/api/dashboard").status_code == 401


# ─── selective gate ───────────────────────────────────────────────────────────

def test_selective_gate_blocks_only_sensitive(monkeypatch, tmp_path):
    client, app_module = _client(monkeypatch, tmp_path, token="demo", code="admincode", scope="sensitive")
    # sensitive reads require the admin cookie
    assert client.get("/status/broker-account").status_code == 401
    assert client.get("/status/diagnostics").status_code == 401
    # public dashboard data stays open
    assert client.get("/status/today").status_code == 200
    assert client.get("/status/risk").status_code == 200
    # with the admin cookie, sensitive reads are reachable (not gate-blocked)
    client.cookies.set("vp_access", app_module._gate_token())
    assert client.get("/status/broker-account").status_code != 401


def test_gate_off_when_no_code(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, token="demo", code=None)
    # no SITE_ACCESS_CODE → nothing gated (pre-existing public posture)
    assert client.get("/status/broker-account").status_code == 200
    assert client.get("/status/today").status_code == 200


def test_full_scope_gates_whole_site(monkeypatch, tmp_path):
    client, app_module = _client(monkeypatch, tmp_path, token="demo", code="admincode", scope="full")
    assert client.get("/status/today").status_code == 401  # whole site gated
    client.cookies.set("vp_access", app_module._gate_token())
    assert client.get("/status/today").status_code == 200


# ─── /status/test-bracket sensitive-path gating ───────────────────────────────

def test_test_bracket_requires_gate_when_configured(monkeypatch, tmp_path):
    """/status/test-bracket is an admin-style dry-run tool (resolves live
    contract/quote/account state) — it must be gated the same as the other
    sensitive reads once SITE_ACCESS_CODE is set, even though it never places
    an order."""
    client, app_module = _client(monkeypatch, tmp_path, token="demo", code="admincode", scope="sensitive")
    assert client.get("/status/test-bracket").status_code == 401
    client.cookies.set("vp_access", app_module._gate_token())
    assert client.get("/status/test-bracket").status_code != 401


def test_test_bracket_open_when_no_code_configured(monkeypatch, tmp_path):
    """Unchanged pre-existing behavior: with no SITE_ACCESS_CODE set, the gate
    is off entirely and /status/test-bracket is reachable (still never places
    an order — it's read-only regardless of gate state)."""
    client, _ = _client(monkeypatch, tmp_path, token="demo", code=None)
    assert client.get("/status/test-bracket").status_code != 401


# ─── /status/broker-account account_id sanitization ───────────────────────────

def test_broker_account_id_sanitized_without_gate_session(monkeypatch, tmp_path):
    """account_id is not a secret, but it should not be handed to a caller with
    no site-gate session — this holds even when the gate is off entirely
    (blank SITE_ACCESS_CODE, the default public posture), which is exactly the
    state that previously leaked it unauthenticated."""
    client, app_module = _client(monkeypatch, tmp_path, token="demo", code=None)
    monkeypatch.setenv("BROKER", "tradovate")
    app_module._ACCOUNT_CACHE.clear()
    monkeypatch.setattr(
        app_module, "_account_summary_blocking",
        lambda: {"ok": True, "account_id": 987654, "equity": 50000.0},
    )
    resp = client.get("/status/broker-account")
    assert resp.status_code == 200
    data = resp.json()
    assert data["account_id"] is None
    assert data["equity"] == 50000.0  # rest of the payload is untouched


def test_broker_account_id_visible_with_valid_gate_session(monkeypatch, tmp_path):
    """A caller who has actually authenticated through the site gate keeps
    seeing account_id — the sanitization is only for unauthenticated callers,
    not a blanket removal of the field."""
    client, app_module = _client(monkeypatch, tmp_path, token="demo", code="admincode", scope="sensitive")
    monkeypatch.setenv("BROKER", "tradovate")
    app_module._ACCOUNT_CACHE.clear()
    monkeypatch.setattr(
        app_module, "_account_summary_blocking",
        lambda: {"ok": True, "account_id": 987654, "equity": 50000.0},
    )
    client.cookies.set("vp_access", app_module._gate_token())
    resp = client.get("/status/broker-account")
    assert resp.status_code == 200
    assert resp.json()["account_id"] == 987654


# ─── env interplay + read-only ────────────────────────────────────────────────

def test_viewer_survives_public_demo_mode(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, token="demo", demo=True)
    assert client.get("/viewer").status_code != 404            # viewer reachable
    assert client.get("/status/broker-account").status_code == 404  # admin 404'd by demo gate


def test_viewer_api_is_read_only(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path, token="demo")
    h = {"X-Viewer-Token": "demo"}
    for method in (client.put, client.delete, client.patch):
        assert method("/viewer/api/status", headers=h).status_code in (404, 405)
