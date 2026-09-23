"""The read-only watcher posts paper-collection-style cards (presentation only)."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

WATCHER_DIR = Path(__file__).resolve().parents[1] / "ops" / "afs_watcher"


def _load_watcher():
    if str(WATCHER_DIR) not in sys.path:
        sys.path.insert(0, str(WATCHER_DIR))
    spec = importlib.util.spec_from_file_location("afs_watcher_card_under_test", WATCHER_DIR / "watcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load_watcher()


class _Resp:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture(monkeypatch):
    sent = []

    def urlopen(req, timeout):
        sent.append(json.loads(req.data.decode("utf-8")))
        return _Resp()

    monkeypatch.setattr(w, "_env_value", lambda route: "https://discord.com/api/webhooks/test")
    monkeypatch.setattr(w.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(w, "log", lambda *_a, **_k: None)
    return sent


def test_notify_posts_card_with_read_only_footer(monkeypatch):
    sent = _capture(monkeypatch)
    state = {"notified": {}}
    w.notify(state, "DISCORD_ROUTE_ERROR", "🚨 ACTION REQUIRED — bot stalled\nService: futures-bot\nLast tick: 14:05 UTC", "k1")
    embed = sent[0]["embeds"][0]
    assert embed["title"] == "🚨 ACTION REQUIRED — bot stalled"
    assert embed["color"] == 0xED4245
    assert {"name": "Service", "value": "futures-bot", "inline": True} in embed["fields"]
    assert embed["footer"]["text"] == w.NOTIFY_PREFIX
    assert "k1" in state["notified"]


def test_notify_falls_back_to_text_without_the_helper(monkeypatch):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(w, "_post_card_or_text", None)
    w.notify({"notified": {}}, "DISCORD_ROUTE_ERROR", "plain " + "x" * 3000, "k2")
    assert set(sent[0]) == {"content"}
    assert len(sent[0]["content"]) == 1900


def test_bootstrap_copies_card_helper_when_present(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for name in ("bootstrap_tmp_state.sh", "watcher.py", "watcher_memory_guard.py", "run_ro.sh"):
        (src / name).write_bytes((WATCHER_DIR / name).read_bytes())
    helpers = Path(__file__).resolve().parents[1] / "notifications"
    for name in ("discord_card.py", "plain_english.py"):
        (src / name).write_bytes((helpers / name).read_bytes())
    state = tmp_path / "state"
    result = subprocess.run(
        ["bash", str(src / "bootstrap_tmp_state.sh")],
        capture_output=True, text=True,
        env={**__import__("os").environ, "AFS_WATCHER_TMP_STATE": str(state)},
    )
    assert result.returncode == 0, result.stderr
    for name in ("discord_card.py", "plain_english.py"):
        assert (state / name).read_bytes() == (helpers / name).read_bytes()


def test_bootstrap_still_works_without_the_optional_helpers(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for name in ("bootstrap_tmp_state.sh", "watcher.py", "watcher_memory_guard.py", "run_ro.sh"):
        (src / name).write_bytes((WATCHER_DIR / name).read_bytes())
    state = tmp_path / "state"
    result = subprocess.run(
        ["bash", str(src / "bootstrap_tmp_state.sh")],
        capture_output=True, text=True,
        env={**__import__("os").environ, "AFS_WATCHER_TMP_STATE": str(state)},
    )
    assert result.returncode == 0, result.stderr
    assert (state / "watcher.py").exists()
    assert not (state / "plain_english.py").exists()


def test_installer_ships_the_card_and_wording_helpers():
    installer = (WATCHER_DIR / "install_afs_watcher_service.sh").read_text()
    assert "notifications/discord_card.py" in installer
    assert "notifications/plain_english.py" in installer


def test_watcher_wording_falls_back_when_plain_english_is_missing(monkeypatch):
    """A partial install without plain_english.py still renders ET times and Buy/Sell."""
    monkeypatch.setattr(w, "_pe", None)
    monkeypatch.setattr(w, "RELEASE_SHA", "3a3d42592e598e16")
    assert w._when("2026-09-23T01:00:00Z") == "9:00 PM ET, Tue Sep 22"
    assert w._when("2026-09-23T01:00:00Z", with_day=False) == "9:00 PM ET"
    assert w._side("SHORT") == "Sell"
    text = w._lane_opened_text("daily_22_5k", {"direction": "SHORT", "entry": 29338.25, "stop": 29634.25,
                                               "target": 28728.25, "entry_time": "2026-09-10T11:10:00+00:00"})
    assert text.splitlines()[0] == "🆕 Daily 2-2 practice sell opened"
    assert "Opened: 7:10 AM ET, Thu Sep 10" in text


def test_a_wording_error_never_breaks_the_tick(monkeypatch):
    logs = []
    monkeypatch.setattr(w, "log", logs.append)
    monkeypatch.setattr(w, "_finding_title", lambda _k: 1 / 0)
    text = w._blocked_discord_text("service_not_active", {"summary": "x"}, "/snap")
    assert text.startswith("🛑 ")
    assert "service_not_active" in text
    assert any("Discord wording failed" in line for line in logs)


def test_rebaseline_card_is_labelled_lines_not_a_run_on_title(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "3a3d42592e598e16")
    text = w._rebaseline_discord_text(
        {"ExecMainPID": "2066185", "release": {"commit": "5fd471636b709ac7"}},
        {"ActiveEnterTimestamp": "Wed 2026-09-23 00:42:26 UTC"},
        "2081619",
    )
    assert text.splitlines() == [
        "✅ Bot restarted on the new version",
        "When: 8:42 PM ET",
        "What to do: nothing — this restart was part of a planned update",
        "-# version 3a3d42592e59 (was 5fd471636b70) · process 2081619",
    ]


def test_event_discord_text_stays_out_of_the_evidence_record(monkeypatch, tmp_path):
    rows, sent = [], []
    monkeypatch.setattr(w, "state_append", lambda _path, line: rows.append(json.loads(line)))
    monkeypatch.setattr(w, "notify", lambda _s, _r, text, _k: sent.append(text))
    monkeypatch.setattr(w, "log", lambda *_a, **_k: None)
    state = {"events_seen": {}, "notified": {}}
    w.emit_event(state, "FIRST_FIRE", "k", {"summary": "s", "discord": "custom card"}, "DISCORD_ROUTE_DAILY_REPORT")
    w.emit_event(state, "MILESTONE", "k2", {"summary": "lane READY"}, "DISCORD_ROUTE_DAILY_REPORT")
    assert "discord" not in rows[0] and rows[0]["summary"] == "s"
    assert sent == ["custom card", "**🏁 Milestone reached**\nlane READY"]


def test_campaign_first_fire_wording_stays_out_of_the_evidence_record(monkeypatch):
    rows, sent = [], []
    monkeypatch.setattr(w, "state_append", lambda _path, line: rows.append(json.loads(line)))
    monkeypatch.setattr(w, "notify", lambda _s, _r, text, _k: sent.append(text))
    monkeypatch.setattr(w, "log", lambda *_a, **_k: None)
    pos = {"direction": "SHORT", "entry": 29338.25, "stop": 29634.25, "target": 28728.25,
           "entry_time": "2026-09-10T11:10:00+00:00"}
    w.emit_event({"events_seen": {}, "notified": {}}, "FIRST_FIRE", "hypothetical_position_open:daily_22_5k:x",
                 {"summary": "daily_22_5k hypothetical position OPEN", "position": pos,
                  "discord": w._lane_opened_text("daily_22_5k", pos)}, "DISCORD_ROUTE_DAILY_REPORT")
    assert set(rows[0]) == {"utc", "kind", "key", "summary", "position"}
    assert sent[0].splitlines() == [
        "🆕 Daily 2-2 practice sell opened",
        "Market: MNQ (Micro Nasdaq)",
        "Sell at: 29,338.25",
        "Stop-loss: 29,634.25 (about $592 per contract)",
        "Profit target: 28,728.25 (about $1,220 per contract)",
        "Opened: 7:10 AM ET, Thu Sep 10",
        "PAPER ONLY · practice tracking, no real order was placed",
    ]
