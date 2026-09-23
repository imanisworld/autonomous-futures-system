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
    card = Path(__file__).resolve().parents[1] / "notifications" / "discord_card.py"
    (src / "discord_card.py").write_bytes(card.read_bytes())
    state = tmp_path / "state"
    result = subprocess.run(
        ["bash", str(src / "bootstrap_tmp_state.sh")],
        capture_output=True, text=True,
        env={**__import__("os").environ, "AFS_WATCHER_TMP_STATE": str(state)},
    )
    assert result.returncode == 0, result.stderr
    assert (state / "discord_card.py").read_bytes() == card.read_bytes()


def test_installer_ships_the_card_helper():
    installer = (WATCHER_DIR / "install_afs_watcher_service.sh").read_text()
    assert "notifications/discord_card.py" in installer
