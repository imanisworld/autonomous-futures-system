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


def test_known_blocked_messages_are_compact_and_machine_readable(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "ee2d0b1d7292cae1b6195f8e2045308a818272c3")

    stale = w._blocked_discord_text("watcher_release_stale")
    restart = w._blocked_discord_text("unexpected_restart")

    assert stale == (
        "**BLOCKED — Watcher needs restart**\n"
        "New release is live, but the watcher is still tracking the previous release.\n"
        "**Action:** Restart `afs-watcher` only.\n"
        "`code=watcher_release_stale | release=ee2d0b1d | service=futures-bot`"
    )
    assert restart == (
        "**BLOCKED — Unexpected futures-bot restart**\n"
        "The bot restarted and no approved deployment explains it.\n"
        "**Action:** Verify what restarted futures-bot before continuing.\n"
        "`code=unexpected_restart | release=ee2d0b1d | service=futures-bot`"
    )
    assert "snapshot" not in stale
    assert "snapshot" not in restart


def test_unknown_blocked_key_has_safe_readable_fallback(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "0123456789abcdef")

    text = w._blocked_discord_text("new_unknown_finding")

    assert "**BLOCKED — Watcher finding: New unknown finding**" in text
    assert "**Action:** Inspect the watcher snapshot before continuing." in text
    assert "`code=new_unknown_finding | release=01234567 | service=futures-bot`" in text


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
    assert notifications == [w._blocked_discord_text("watcher_release_stale")]
