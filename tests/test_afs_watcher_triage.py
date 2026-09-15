"""Read-only LLM triage lane (ops/afs_watcher/watcher_triage.py).

What these tests pin, in order of importance:
  1. OFF by default — no key, no network, no post, no state change.
  2. Zero authority — the request carries no tools and the system prompt forbids action.
  3. The packet is small, structured and secret-free (no env, no URLs, no file bodies).
  4. Failures (HTTP error, network, refusal, empty) degrade to a log line and never raise.
  5. Once per raise, daily cap, and the Discord text shape.
  6. watcher.py wiring: triage runs only after an ACTION REQUIRED card, never for plain BLOCKED.
"""
import importlib.util
import io
import json
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


WATCHER_DIR = Path(__file__).parent.parent / "ops" / "afs_watcher"
if str(WATCHER_DIR) not in sys.path:
    sys.path.insert(0, str(WATCHER_DIR))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, WATCHER_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


t = _load("afs_watcher_triage_mod", "watcher_triage.py")
w = _load("afs_watcher_for_triage", "watcher.py")

NOW = datetime(2026, 9, 14, 17, 6, 16, tzinfo=timezone.utc)
D22 = {"direction": "SHORT", "entry": 29338.25, "stop": 29634.25, "target": 28728.25,
       "entry_time": "2026-09-10T11:10:00+00:00", "paper_order_id": "PAPER-38b351ef"}
FINDING = {"summary": "feed-gap alarm: MNQ status=stale stale_since=2026-09-14T17:05:01+00:00",
           "detail": {"samples": ['POST /webhook/alert HTTP/1.0" 401 Unauthorized'], "nested": {"x": 1}}}


def _tick(open_lanes=("daily_22_5k",)):
    return {
        "lanes": {
            "open_positions": list(open_lanes),
            "inventory": {"daily_22_5k": {"open_position": D22 if "daily_22_5k" in open_lanes else None}},
            "newest_5m_mnq_bar_mtime": (NOW - timedelta(minutes=35)).isoformat(),
            "newest_15m_mnq_bar_mtime": (NOW - timedelta(minutes=21)).isoformat(),
            "newest_mes_15m_bar_mtime": (NOW - timedelta(minutes=36)).isoformat(),
            "five_min_feed_stalled": False,
        },
        "runtime": {
            "service": {"ActiveState": "active", "SubState": "running", "NRestarts": "0"},
            "alerts_since_last_tick": {"401": 2},
            "feed": {"instruments": {"MNQ": {"status": "stale", "stale_since": "2026-09-14T17:05:01+00:00"}}},
            "tradovate": {"state": "HEALTHY", "ready": True, "secret_token": "MUST-NOT-LEAK"},
            "broker": {"ok": True, "env": "demo", "position": None},
        },
    }


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen_ok(text="What happened:\nMNQ feed stale.\nWhat to check:\nTradingView alerts.\nDo not touch:\nThe open short.", stop="end_turn"):
    calls = []

    def urlopen(req, timeout=None):
        calls.append((req, timeout))
        body = {"stop_reason": stop, "content": [{"type": "text", "text": text}], "usage": {"input_tokens": 500, "output_tokens": 80}}
        return _Resp(json.dumps(body).encode())

    return urlopen, calls


def _run(monkeypatch, *, key_present=True, urlopen=None, state=None, tick=None, first="2026-09-14T17:06:16Z"):
    env = {"AFS_TRIAGE_API_KEY": "sk-test-key" if key_present else None, "DISCORD_ROUTE_ERROR": "https://discord.com/api/webhooks/1/x"}
    posts, logs = [], []
    monkeypatch.delenv("AFS_TRIAGE_API_KEY", raising=False)
    monkeypatch.setenv("AFS_TRIAGE_API_KEY_FILE", "/nonexistent/.triage_key")
    state = state if state is not None else {"blocked": {}, "notified": {}}
    ok = t.maybe_triage(
        key="feed_MNQ_stale", finding=FINDING, tick=tick or _tick(), state=state, first_utc=first,
        headline="MNQ FEED STALE", release_sha="28f79a65a395af66", service="futures-bot", now=NOW,
        env_value=env.get, notify=lambda _s, route, text, dedupe: posts.append((route, text, dedupe)),
        log=logs.append, recent_events=[{"utc": "2026-09-14T16:51:16Z", "kind": "BLOCKED", "key": "feed_MES_stale", "summary": "MES stale"}],
        urlopen=urlopen or _urlopen_ok()[0],
    )
    return ok, posts, logs, state


# ── 1. off by default ────────────────────────────────────────────────────────
def test_no_key_means_no_network_no_post_no_state(monkeypatch):
    urlopen, calls = _urlopen_ok()
    ok, posts, logs, state = _run(monkeypatch, key_present=False, urlopen=urlopen)
    assert ok is False and posts == [] and calls == [] and logs == []
    assert state["triage"]["done"] == {} and state["triage"]["count"] == 0


# ── 2. zero authority + 3. packet content ────────────────────────────────────
def test_request_has_no_tools_and_packet_is_secret_free(monkeypatch):
    urlopen, calls = _urlopen_ok()
    ok, posts, logs, _ = _run(monkeypatch, urlopen=urlopen)
    assert ok is True and len(calls) == 1
    req, timeout = calls[0]
    assert req.full_url == t.API_URL and req.get_method() == "POST" and timeout == t.TIMEOUT_S
    assert req.get_header("X-api-key") == "sk-test-key"
    body = json.loads(req.data.decode())
    assert body["model"] == "claude-opus-5"
    assert "tools" not in body and "tool_choice" not in body
    assert "no authority" in body["system"] or "no tools and no authority" in body["system"]
    assert body["messages"][0]["role"] == "user"
    packet = json.loads(body["messages"][0]["content"].split("\n", 1)[1])
    # what the model needs
    assert packet["finding"]["code"] == "feed_MNQ_stale"
    assert packet["instrument"] == "MNQ"
    assert packet["exposure"]["open_positions"] == [{"lane": "daily_22_5k", "direction": "SHORT", "entry": 29338.25,
                                                      "stop": 29634.25, "target": 28728.25, "entry_time": "2026-09-10T11:10:00+00:00"}]
    assert packet["bars"]["newest_5m_mnq_age_min"] == 35
    assert packet["runtime"]["release_sha"] == "28f79a65a395"
    assert packet["runtime"]["webhook_posts_since_last_tick"] == {"401": 2}
    assert packet["recent_watcher_facts"][0]["key"] == "feed_MES_stale"
    assert packet["authority"].startswith("none")
    # what it must never see: secrets, the paper order id, nested detail blobs, the key itself
    raw = body["messages"][0]["content"]
    assert "MUST-NOT-LEAK" not in raw and "PAPER-38b351ef" not in raw and "sk-test-key" not in raw
    assert "nested" not in packet["finding"]["detail"]
    assert packet["finding"]["samples"] == ['POST /webhook/alert HTTP/1.0" 401 Unauthorized']


def test_discord_post_shape_and_route(monkeypatch):
    ok, posts, logs, state = _run(monkeypatch)
    assert ok is True and len(posts) == 1
    route, text, dedupe = posts[0]
    assert route == "DISCORD_ROUTE_ERROR"          # no DISCORD_ROUTE_TRIAGE configured → error route
    assert dedupe == "triage:feed_MNQ_stale:2026-09-14T17:06:16Z"
    lines = text.splitlines()
    assert lines[0] == "🧭 **TRIAGE — MNQ FEED STALE** (advisory, read-only)"
    assert lines[1] == "What happened:" and "Do not touch:" in lines
    assert lines[-1] == "`feed_MNQ_stale · claude-opus-5 · no authority`"
    assert len(text) <= t.DISCORD_LIMIT
    assert state["triage"]["done"]["feed_MNQ_stale:2026-09-14T17:06:16Z"] == "posted"
    assert any(l.startswith("TRIAGE posted feed_MNQ_stale") for l in logs)


def test_prefers_dedicated_triage_route_when_configured(monkeypatch):
    env = {"AFS_TRIAGE_API_KEY": "k", "DISCORD_ROUTE_TRIAGE": "https://discord.com/api/webhooks/2/y"}
    posts = []
    monkeypatch.setenv("AFS_TRIAGE_API_KEY_FILE", "/nonexistent")
    t.maybe_triage(key="feed_MNQ_stale", finding=FINDING, tick=_tick(), state={"blocked": {}}, first_utc="x",
                   headline="H", release_sha="abc", service="s", now=NOW, env_value=env.get,
                   notify=lambda _s, r, _t, _d: posts.append(r), log=lambda *_: None, urlopen=_urlopen_ok()[0])
    assert posts == ["DISCORD_ROUTE_TRIAGE"]


def test_long_advice_is_truncated_to_discord_limit():
    text = t.triage_discord_text("k", "H", "x" * 5000)
    assert len(text) <= t.DISCORD_LIMIT and text.endswith("`k · claude-opus-5 · no authority`") and "…" in text


# ── 4. failure isolation ─────────────────────────────────────────────────────
def test_http_error_degrades_to_log_line(monkeypatch):
    def urlopen(req, timeout=None):
        raise urllib.error.HTTPError(t.API_URL, 429, "rate limited", {}, io.BytesIO(b"{}"))
    ok, posts, logs, state = _run(monkeypatch, urlopen=urlopen)
    assert ok is False and posts == [] and logs == ["TRIAGE FAILED feed_MNQ_stale: HTTP 429"]
    assert state["triage"]["done"]["feed_MNQ_stale:2026-09-14T17:06:16Z"] == "attempted"


def test_network_error_degrades_to_log_line(monkeypatch):
    def urlopen(req, timeout=None):
        raise OSError("connection refused")
    ok, posts, logs, _ = _run(monkeypatch, urlopen=urlopen)
    assert ok is False and posts == [] and logs[0].startswith("TRIAGE FAILED feed_MNQ_stale: OSError")


def test_refusal_and_empty_replies_post_nothing(monkeypatch):
    for kwargs in ({"stop": "refusal", "text": "x"}, {"text": ""}):
        urlopen, _ = _urlopen_ok(**kwargs)
        ok, posts, logs, _ = _run(monkeypatch, urlopen=urlopen)
        assert ok is False and posts == []
        assert logs[-1].startswith("TRIAGE feed_MNQ_stale: no usable reply")


# ── 5. once per raise, daily cap ─────────────────────────────────────────────
def test_same_raise_is_triaged_once_and_new_raise_again(monkeypatch):
    urlopen, calls = _urlopen_ok()
    state = {"blocked": {}, "notified": {}}
    _run(monkeypatch, urlopen=urlopen, state=state)
    _run(monkeypatch, urlopen=urlopen, state=state)                       # reminder tick, same episode
    assert len(calls) == 1
    _run(monkeypatch, urlopen=urlopen, state=state, first="2026-09-14T22:30:00Z")  # a later, new raise
    assert len(calls) == 2


def test_daily_cap_stops_calls(monkeypatch):
    urlopen, calls = _urlopen_ok()
    state = {"blocked": {}, "notified": {}}
    for i in range(t.DAILY_CAP + 3):
        _run(monkeypatch, urlopen=urlopen, state=state, first=f"2026-09-14T00:{i:02d}:00Z")
    assert len(calls) == t.DAILY_CAP
    assert list(state["triage"]["done"].values()).count("capped") == 3


# ── 6. watcher wiring ────────────────────────────────────────────────────────
def _wire(monkeypatch):
    seen = []
    monkeypatch.setattr(w, "capture_snapshot", lambda *_a: Path("/tmp/afs_watcher/snapshots/x"))
    monkeypatch.setattr(w, "state_append", lambda *_a: None)
    monkeypatch.setattr(w, "log", lambda *_a: None)
    monkeypatch.setattr(w, "notify", lambda *_a: None)
    monkeypatch.setattr(w, "_recent_events", lambda n=8: [])
    monkeypatch.setattr(w.watcher_triage, "maybe_triage", lambda **kw: seen.append(kw) or True)
    return seen


def test_watcher_triages_only_action_required_raises(monkeypatch):
    seen = _wire(monkeypatch)
    tick = {"verdict": "BLOCKED", "lanes": {"open_positions": ["daily_22_5k"],
                                             "inventory": {"daily_22_5k": {"open_position": D22}},
                                             "newest_5m_mnq_bar_mtime": w.iso(w.now_utc() - timedelta(minutes=35))}}
    findings = w.Findings()
    findings.add("BLOCKED", "feed_MNQ_stale", "stale")
    findings.add("BLOCKED", "post_epoch_spans_releases", "provenance")   # plain BLOCKED, never triaged
    state = {"blocked": {}, "blocked_last_notified": {}, "notified": {}}
    w.handle_blocked(state, findings, tick)
    assert [kw["key"] for kw in seen] == ["feed_MNQ_stale"]
    assert seen[0]["headline"] == "MNQ FEED STALE" and seen[0]["first_utc"] == state["blocked"]["feed_MNQ_stale"]["first_utc"]
    assert seen[0]["env_value"] is w._env_value and seen[0]["notify"] is w.notify


def test_watcher_never_triages_without_exposure(monkeypatch):
    seen = _wire(monkeypatch)
    findings = w.Findings()
    findings.add("BLOCKED", "feed_MNQ_stale", "stale")
    w.handle_blocked({"blocked": {}, "blocked_last_notified": {}, "notified": {}}, findings,
                     {"verdict": "BLOCKED", "lanes": {"open_positions": [], "inventory": {}}})
    assert seen == []


def test_triage_exception_never_breaks_handle_blocked(monkeypatch):
    _wire(monkeypatch)
    logs = []
    monkeypatch.setattr(w, "log", logs.append)

    def boom(**kw):
        raise RuntimeError("api down")
    monkeypatch.setattr(w.watcher_triage, "maybe_triage", boom)
    findings = w.Findings()
    findings.add("BLOCKED", "service_not_active", "futures-bot ActiveState=failed")
    state = {"blocked": {}, "blocked_last_notified": {}, "notified": {}}
    w.handle_blocked(state, findings, {"verdict": "BLOCKED", "lanes": {}})
    assert "service_not_active" in state["blocked"]
    assert any(l.startswith("TRIAGE FAILED service_not_active: RuntimeError") for l in logs)


def test_watcher_runs_without_the_triage_module(monkeypatch):
    seen = _wire(monkeypatch)
    monkeypatch.setattr(w, "watcher_triage", None)
    findings = w.Findings()
    findings.add("BLOCKED", "service_not_active", "down")
    w.handle_blocked({"blocked": {}, "blocked_last_notified": {}, "notified": {}}, findings, {"verdict": "BLOCKED", "lanes": {}})
    assert seen == []


@pytest.mark.parametrize("source", ["env_file", "process_env", "key_file"])
def test_api_key_resolution_order(monkeypatch, tmp_path, source):
    monkeypatch.delenv("AFS_TRIAGE_API_KEY", raising=False)
    kf = tmp_path / ".triage_key"
    monkeypatch.setenv("AFS_TRIAGE_API_KEY_FILE", str(kf))
    if source == "env_file":
        assert t.api_key({"AFS_TRIAGE_API_KEY": " k1 "}.get) == "k1"
    elif source == "process_env":
        monkeypatch.setenv("AFS_TRIAGE_API_KEY", "k2")
        assert t.api_key({}.get) == "k2"
    else:
        kf.write_text("k3\n")
        assert t.api_key({}.get) == "k3"
    assert t.api_key({}.get) in ("k2", "k3", None)
