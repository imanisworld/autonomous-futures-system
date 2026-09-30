"""Tradovate recovery alerts must mean the broker finding is actually gone.

A market close downgrades the same ACTION_REQUIRED finding from BLOCKED to
WARN. That severity change is not a recovery, and it must not send
"Resolved: Broker connection problem". A real recovery is the finding
disappearing because Tradovate is HEALTHY.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


WATCHER_DIR = Path(__file__).parent.parent / "ops" / "afs_watcher"
KEY = "tradovate_ACTION_REQUIRED"


def _load_watcher():
    if str(WATCHER_DIR) not in sys.path:
        sys.path.insert(0, str(WATCHER_DIR))
    spec = importlib.util.spec_from_file_location(
        "afs_watcher_tradovate_resolved", WATCHER_DIR / "watcher.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load_watcher()
NOW = datetime(2026, 9, 29, 21, 5, tzinfo=timezone.utc)


def _resolved(notifications: list[tuple[str, str]]) -> list[str]:
    return [text for _route, text in notifications if text.startswith("✅ Resolved")]


def _arm(tmp_path: Path, monkeypatch, notifications: list[tuple[str, str]]):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    journal = log_dir / "journal_2026-09-29.jsonl"
    journal.write_text('{"record_type":"decision"}\n', encoding="utf-8")
    bar = log_dir / "bars_MNQ.jsonl"
    bar.write_text('{"timeframe":"15m"}\n', encoding="utf-8")
    bar_mtime = (NOW - timedelta(hours=1)).timestamp()
    os.utime(journal, (bar_mtime, bar_mtime))
    os.utime(bar, (bar_mtime, bar_mtime))
    feed_state = log_dir / "feed_gap_alarm_state.json"
    feed_state.write_text(json.dumps({"instruments": {"MNQ": {"status": "healthy"}}}))
    fresh = time.time()
    os.utime(feed_state, (fresh, fresh))

    reliability = {
        "state": "ACTION_REQUIRED",
        "ready": False,
        "market_active": True,
        "failure_reason": "credentials_rejected",
    }

    monkeypatch.setattr(w, "LOG_DIR", log_dir)
    monkeypatch.setattr(w, "FEED_STATE", feed_state)
    monkeypatch.setattr(w, "RELEASE_LINK", release_dir)
    monkeypatch.setattr(w, "RELEASE_DIR", release_dir)
    monkeypatch.setattr(w, "RELEASE_SHA", "a" * 40)
    monkeypatch.setattr(w, "EPOCH", NOW - timedelta(days=1))
    monkeypatch.setattr(w, "now_utc", lambda: NOW)
    monkeypatch.setattr(w, "watcher_triage", None)
    monkeypatch.setattr(w, "capture_snapshot", lambda *_args: tmp_path / "snapshot")
    monkeypatch.setattr(w, "state_append", lambda *_args: None)
    monkeypatch.setattr(w, "log", lambda *_args: None)
    monkeypatch.setattr(
        w,
        "notify",
        lambda _state, route, text, _dedupe: notifications.append((route, text)),
    )
    monkeypatch.setattr(
        w,
        "load_deploy_pins",
        lambda _path: (
            {
                w.COMMIT_PIN: "a" * 40,
                w.FINGERPRINT_PIN: "b" * 64,
                w.EPOCH_PIN: w.iso(w.EPOCH),
                w.EPOCH_PROOF_PIN: w.iso(w.EPOCH),
            },
            None,
        ),
    )
    monkeypatch.setattr(w, "read_prod_text", lambda path: path.read_text(encoding="utf-8"))
    monkeypatch.setattr(w, "read_prod_bytes_tail", lambda path, _n: path.read_bytes())

    def fake_run(cmd, **_kwargs):
        if cmd[:2] == ["systemctl", "show"]:
            return 0, (
                "ActiveState=active\nSubState=running\nExecMainPID=0\n"
                "NRestarts=0\nActiveEnterTimestamp=stable\n"
            )
        if cmd[:2] == ["systemctl", "list-units"]:
            return 0, ""
        if cmd[:2] == ["pgrep", "-af"]:
            return 0, (
                "123 /usr/bin/python3 -m uvicorn webhook.app:app "
                "--host 127.0.0.1 --port 8000\n"
            )
        if cmd and cmd[0] == "journalctl":
            return 0, ""
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(w, "run", fake_run)

    def fake_get(path, **_kwargs):
        if path == "/status/tradovate-reliability":
            return dict(reliability), None
        if path == "/status/broker-account":
            return {"ok": True, "env": "demo", "position": None}, None
        if path == "/status/today":
            return {"live_trading_enabled": False}, None
        raise AssertionError(f"unexpected endpoint: {path}")

    monkeypatch.setattr(w, "http_get_json", fake_get)

    state = {
        "blocked": {},
        "blocked_last_notified": {},
        "notified": {},
        "baseline": {
            "ActiveEnterTimestamp": "stable",
            "NRestarts": "0",
            "ExecMainPID": "0",
        },
        "journal_progress": {
            "path": str(journal),
            "size": journal.stat().st_size,
            "mtime": journal.stat().st_mtime,
            "last_advanced_utc": w.iso(NOW - timedelta(minutes=5)),
            "alerts_since_advance": 0,
            "bar_mtime_at_advance": bar_mtime,
        },
    }

    def tick() -> w.Findings:
        findings = w.Findings()
        w.check_runtime(state, findings, {})
        w.handle_blocked(state, findings, {"verdict": "BLOCKED", "lanes": {}})
        return findings

    return reliability, state, tick


def test_blocked_to_warn_while_tradovate_stays_action_required_does_not_resolve(tmp_path, monkeypatch):
    notifications: list[tuple[str, str]] = []
    reliability, state, tick = _arm(tmp_path, monkeypatch, notifications)

    raised = tick()
    assert [item["key"] for item in raised.blocked()] == [KEY]
    assert raised.warns() == []

    reliability["market_active"] = False
    downgraded = tick()
    assert [item["key"] for item in downgraded.blocked()] == []
    assert [item["key"] for item in downgraded.warns()] == [KEY]
    assert reliability["state"] == "ACTION_REQUIRED"

    resolved = _resolved(notifications)
    assert resolved == [] and KEY in state["blocked"], (
        f"resolved={resolved!r} still_open={KEY in state['blocked']}"
    )


def test_tradovate_unhealthy_to_healthy_sends_exactly_one_resolved(tmp_path, monkeypatch):
    notifications: list[tuple[str, str]] = []
    reliability, state, tick = _arm(tmp_path, monkeypatch, notifications)

    raised = tick()
    assert [item["key"] for item in raised.blocked()] == [KEY]

    reliability["state"] = "HEALTHY"
    reliability["ready"] = True
    reliability["failure_reason"] = None
    recovered = tick()
    assert [item for item in recovered.items if str(item["key"]).startswith("tradovate_")] == []

    resolved = _resolved(notifications)
    assert len(resolved) == 1
    assert resolved[0].splitlines()[0] == "✅ Resolved: Broker connection problem"
    assert KEY not in state["blocked"]
