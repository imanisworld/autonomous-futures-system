import importlib.util
import json
import sys
from pathlib import Path


WATCHER_DIR = Path(__file__).parent.parent / "ops" / "afs_watcher"


def _load_watcher():
    if str(WATCHER_DIR) not in sys.path:
        sys.path.insert(0, str(WATCHER_DIR))
    spec = importlib.util.spec_from_file_location(
        "afs_watcher_notification_format", WATCHER_DIR / "watcher.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load_watcher()


def test_known_blocked_messages_lead_with_a_plain_english_headline(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "ee2d0b1d7292cae1b6195f8e2045308a818272c3")

    stale = w._blocked_discord_text("watcher_release_stale")
    restart = w._blocked_discord_text("unexpected_restart")

    assert stale.splitlines()[0] == "🛑 **BLOCKED — Watcher needs restart**"
    assert "New release is live, but the watcher is still tracking the previous release." in stale
    assert "**What to check:** restart `afs-watcher` only" in stale
    assert stale.splitlines()[-1] == "`code=watcher_release_stale | release=ee2d0b1d | service=futures-bot`"

    assert restart.splitlines()[0] == "🛑 **BLOCKED — Unexpected futures-bot restart**"
    assert "The bot restarted and no approved deployment explains it." in restart
    assert restart.splitlines()[-1] == "`code=unexpected_restart | release=ee2d0b1d | service=futures-bot`"


def test_blocked_message_surfaces_the_first_offending_line_and_snapshot(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "5115b780f2b8")
    sample = '2026-09-11T03:44:23+00:00 futures-bot python[1]: INFO: 127.0.0.1:0 - "POST /webhook/alert HTTP/1.0" 422 Unprocessable Content'
    finding = {
        "summary": "1 non-200 alert responses since 2026-09-11T03:41:15Z",
        "detail": {"counts": {"200": 4, "422": 1}, "samples": [sample]},
    }

    text = w._blocked_discord_text("alert_non200", finding, "/tmp/afs_watcher/snapshots/x")
    lines = text.splitlines()

    assert lines[0] == "🛑 **BLOCKED — Webhook post rejected**"
    assert lines[1] == "1 non-200 alert responses since 2026-09-11T03:41:15Z"
    assert lines[2] == f"↳ `{sample}`"
    assert lines[3].startswith("**What to check:** read the rejected alert lines")
    assert lines[4] == "Snapshot: `/tmp/afs_watcher/snapshots/x`"
    assert lines[5] == "`code=alert_non200 | release=5115b780 | service=futures-bot`"


def test_unknown_blocked_key_has_safe_readable_fallback(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "0123456789abcdef")

    text = w._blocked_discord_text("new_unknown_finding")

    assert text.splitlines()[0] == "🛑 **BLOCKED — New unknown finding**"
    assert "**What to check:** inspect the snapshot; no automatic fix" in text
    assert "`code=new_unknown_finding | release=01234567 | service=futures-bot`" in text


def test_warning_uses_the_same_layout_with_a_warning_icon(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "0123456789abcdef")
    text = w._finding_discord_text("WARNING", "post_epoch_spans_releases", {"summary": "spans 4 releases", "detail": {}}, "/snap")
    assert text.splitlines()[0] == "⚠️ **WARNING — Post-epoch evidence spans several releases**"
    assert "spans 4 releases" in text
    assert "Snapshot: `/snap`" in text


def test_daily_pass_is_multiline_and_readable():
    pops = {"vwap_hold/control": {"candidates": 48, "resolved_filled_economic": 39, "distinct_trading_days": 18}}
    text = w._daily_discord_text("DAILY PASS", "2026-09-10", {"200": 548}, 22, pops, [], "/tmp/afs_watcher/daily/2026-09-10.json")
    assert text.splitlines() == [
        "✅ **DAILY PASS 2026-09-10**",
        "Webhook posts: 548 (all 200)",
        "Post-epoch campaign rows: 22",
        "• vwap_hold/control: 48 cand · 39 filled · 18 days",
        "File: `/tmp/afs_watcher/daily/2026-09-10.json`",
    ]


def test_daily_blocked_lists_discrepancies_and_rejected_posts():
    text = w._daily_discord_text("DAILY BLOCKED", "2026-09-10", {"200": 5, "422": 1}, 0, {}, ["open BLOCKED conditions: ['alert_non200']"], "/f")
    assert text.splitlines()[0] == "🛑 **DAILY BLOCKED 2026-09-10**"
    assert "Webhook posts: 6 — rejected: {'422': 1}" in text
    assert "⚠️ open BLOCKED conditions: ['alert_non200']" in text


def test_notification_presentation_does_not_change_event_or_state_semantics(monkeypatch, tmp_path):
    snapshot = tmp_path / "snapshot"
    state = {
        "blocked": {},
        "blocked_last_notified": {},
        "notified": {},
    }
    findings = w.Findings()
    findings.add(
        "BLOCKED",
        "watcher_release_stale",
        "full technical summary that must remain in the event",
        expected="/release/old",
    )
    events = []
    notifications = []
    monkeypatch.setattr(w, "capture_snapshot", lambda *_args: snapshot)
    monkeypatch.setattr(w, "state_append", lambda _path, payload: events.append(json.loads(payload)))
    monkeypatch.setattr(w, "log", lambda *_args: None)
    monkeypatch.setattr(w, "notify", lambda _state, _route, text, _dedupe: notifications.append(text))
    monkeypatch.setattr(w, "RELEASE_SHA", "ee2d0b1d7292cae1b6195f8e2045308a818272c3")

    w.handle_blocked(state, findings, {"verdict": "BLOCKED"})

    assert state["blocked"]["watcher_release_stale"]["summary"] == (
        "full technical summary that must remain in the event"
    )
    assert state["blocked"]["watcher_release_stale"]["snapshot"] == str(snapshot)
    assert events[0]["key"] == "watcher_release_stale"
    assert events[0]["detail"] == {"expected": "/release/old"}
    assert events[0]["snapshot"] == str(snapshot)
    assert notifications == [
        w._blocked_discord_text("watcher_release_stale", findings.blocked()[0], str(snapshot))
    ]


def test_cleared_blocker_sends_a_cleared_notification(monkeypatch):
    first = w.iso(w.now_utc() - w.timedelta(minutes=7))
    state = {
        "blocked": {"alert_non200": {"first_utc": first, "summary": "x", "snapshot": "/s"}},
        "blocked_last_notified": {"alert_non200": first},
        "notified": {"blocked:alert_non200": first},
    }
    notifications = []
    monkeypatch.setattr(w, "log", lambda *_args: None)
    monkeypatch.setattr(w, "notify", lambda _state, route, text, _dedupe: notifications.append((route, text)))
    monkeypatch.setattr(w, "RELEASE_SHA", "5115b780f2b8")

    w.handle_blocked(state, w.Findings(), {"verdict": "OK"})

    assert state["blocked"] == {}
    assert notifications == [("DISCORD_ROUTE_ERROR", "✅ **cleared — Webhook post rejected** (was blocked 7 min)\n`code=alert_non200 | release=5115b780`")]
