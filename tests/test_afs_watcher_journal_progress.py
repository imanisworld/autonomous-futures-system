"""Regression tests for the live watcher's journal-stall signal."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


WATCHER_DIR = Path(__file__).parent.parent / "ops" / "afs_watcher"


def _load_watcher():
    if str(WATCHER_DIR) not in sys.path:
        sys.path.insert(0, str(WATCHER_DIR))
    spec = importlib.util.spec_from_file_location(
        "afs_watcher_journal_progress", WATCHER_DIR / "watcher.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load_watcher()
NOW = datetime(2026, 9, 5, 16, 0, tzinfo=timezone.utc)


def _check_runtime(tmp_path: Path, monkeypatch, *, newer_15m_bar: bool, legacy_state: bool = False):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    release_dir = tmp_path / "release"
    release_dir.mkdir()

    journal = log_dir / "journal_2026-09-05.jsonl"
    journal.write_text('{"record_type":"decision"}\n', encoding="utf-8")
    journal_mtime = (NOW - timedelta(hours=2)).timestamp()
    os.utime(journal, (journal_mtime, journal_mtime))

    baseline_bar_mtime = (NOW - timedelta(hours=1)).timestamp()
    bar = log_dir / "bars_MNQ.jsonl"
    bar.write_text('{"timeframe":"15m"}\n', encoding="utf-8")
    current_bar_mtime = baseline_bar_mtime + (900 if newer_15m_bar else 0)
    os.utime(bar, (current_bar_mtime, current_bar_mtime))

    # Fresh 5m traffic is deliberately outside the top-level bars_*.jsonl glob.
    tf5m = log_dir / "tf5m"
    tf5m.mkdir()
    five_min_bar = tf5m / "bars_MNQ.jsonl"
    five_min_bar.write_text('{"timeframe":"5m"}\n', encoding="utf-8")
    os.utime(five_min_bar, (NOW.timestamp(), NOW.timestamp()))

    feed_state = log_dir / "feed_gap_alarm_state.json"
    feed_state.write_text(json.dumps({"instruments": {"MNQ": {"status": "healthy"}}}))

    monkeypatch.setattr(w, "LOG_DIR", log_dir)
    monkeypatch.setattr(w, "FEED_STATE", feed_state)
    monkeypatch.setattr(w, "RELEASE_LINK", release_dir)
    monkeypatch.setattr(w, "RELEASE_DIR", release_dir)
    monkeypatch.setattr(w, "RELEASE_SHA", "a" * 40)
    monkeypatch.setattr(w, "EPOCH", NOW - timedelta(days=1))
    monkeypatch.setattr(w, "now_utc", lambda: NOW)
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
            return 0, "ActiveState=active\nSubState=running\nExecMainPID=0\nNRestarts=0\nActiveEnterTimestamp=stable\n"
        if cmd[:2] == ["systemctl", "list-units"]:
            return 0, ""
        if cmd[:2] == ["pgrep", "-af"]:
            return 0, "123 uvicorn webhook.app\n"
        if cmd and cmd[0] == "journalctl":
            return 0, '2026-09-05 POST /webhook/alert HTTP/1.1" 200\n'
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(w, "run", fake_run)

    def fake_get(path, **_kwargs):
        if path == "/status/tradovate-reliability":
            return {"state": "HEALTHY", "ready": True, "market_active": True}, None
        if path == "/status/broker-account":
            return {"ok": True, "env": "demo", "position": None}, None
        if path == "/status/today":
            return {"live_trading_enabled": False}, None
        raise AssertionError(f"unexpected endpoint: {path}")

    monkeypatch.setattr(w, "http_get_json", fake_get)

    st = journal.stat()
    progress = {
        "path": str(journal),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "last_advanced_utc": w.iso(NOW - timedelta(hours=1)),
        "alerts_since_advance": 2,
    }
    if not legacy_state:
        progress["bar_mtime_at_advance"] = baseline_bar_mtime
    state = {
        "journal_progress": progress,
        "baseline": {
            "ActiveEnterTimestamp": "stable",
            "NRestarts": "0",
            "ExecMainPID": "0",
        },
    }
    findings = w.Findings()
    tick = {}
    w.check_runtime(state, findings, tick)
    return state, findings, tick


def test_five_minute_only_traffic_does_not_report_journal_stall(tmp_path, monkeypatch):
    _, findings, tick = _check_runtime(tmp_path, monkeypatch, newer_15m_bar=False)

    assert "journal_not_advancing" not in {item["key"] for item in findings.items}
    assert tick["runtime"]["fifteen_min_bar_since_journal_advance"] is False


def test_new_fifteen_minute_bar_without_journal_growth_reports_real_stall(tmp_path, monkeypatch):
    _, findings, tick = _check_runtime(tmp_path, monkeypatch, newer_15m_bar=True)

    stalls = [item for item in findings.items if item["key"] == "journal_not_advancing"]
    assert len(stalls) == 1
    assert "a 15m bar arrived" in stalls[0]["summary"]
    assert tick["runtime"]["fifteen_min_bar_since_journal_advance"] is True


def test_pre_fix_state_baselines_current_bar_without_false_block(tmp_path, monkeypatch):
    state, findings, tick = _check_runtime(
        tmp_path, monkeypatch, newer_15m_bar=True, legacy_state=True
    )

    assert "journal_not_advancing" not in {item["key"] for item in findings.items}
    assert state["journal_progress"]["bar_mtime_at_advance"]
    assert tick["runtime"]["fifteen_min_bar_since_journal_advance"] is False
