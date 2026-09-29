"""Regression coverage for the read-only watcher clock/NTP guard."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent
WATCHER_DIR = ROOT / "ops" / "afs_watcher"


def _load_watcher():
    if str(WATCHER_DIR) not in sys.path:
        sys.path.insert(0, str(WATCHER_DIR))
    spec = importlib.util.spec_from_file_location(
        "afs_watcher_clock_guard", WATCHER_DIR / "watcher.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load_watcher()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("yes\n", True), ("true\n", True), ("1\n", True), ("no\n", False), ("false\n", False), ("0\n", False)],
)
def test_ntp_sync_state_parses_boolean_values(monkeypatch, raw, expected):
    monkeypatch.setattr(w, "run", lambda cmd, timeout=15: (0, raw))
    got = w._ntp_sync_state()
    assert got["synchronized"] is expected
    assert got["source"] == "timedatectl.NTPSynchronized"
    assert "error" not in got


def test_ntp_sync_state_fails_closed_on_command_error(monkeypatch):
    monkeypatch.setattr(w, "run", lambda cmd, timeout=15: (1, "Failed to connect"))
    got = w._ntp_sync_state()
    assert got["synchronized"] is None
    assert "timedatectl exited 1" in got["error"]


def test_ntp_sync_state_fails_closed_when_query_cannot_execute(monkeypatch):
    def boom(_cmd, timeout=15):
        raise FileNotFoundError("timedatectl missing")

    monkeypatch.setattr(w, "run", boom)
    got = w._ntp_sync_state()
    assert got["synchronized"] is None
    assert "timedatectl query failed" in got["error"]
    assert "FileNotFoundError" in got["error"]


def test_ntp_sync_state_fails_closed_on_unexpected_value(monkeypatch):
    monkeypatch.setattr(w, "run", lambda cmd, timeout=15: (0, "maybe\n"))
    got = w._ntp_sync_state()
    assert got["synchronized"] is None
    assert "unexpected NTPSynchronized value" in got["error"]


def test_clock_guard_records_healthy_sample(monkeypatch):
    monkeypatch.setattr(w, "_ntp_sync_state", lambda: {
        "source": "timedatectl.NTPSynchronized",
        "synchronized": True,
    })
    monkeypatch.setattr(w.time, "time", lambda: 1300.2)
    monkeypatch.setattr(w.time, "monotonic", lambda: 800.0)
    state = {"clock_guard": {"wall_epoch": 1000.0, "monotonic_seconds": 500.0}}
    findings = w.Findings()
    tick = {}

    w.check_clock(state, findings, tick)

    assert findings.blocked() == []
    assert tick["clock"]["step_seconds"] == pytest.approx(0.2)
    assert state["clock_guard"]["wall_epoch"] == 1300.2
    assert state["clock_guard"]["monotonic_seconds"] == 800.0


@pytest.mark.parametrize(
    ("wall_now", "expected_direction"),
    [(1303.0, "forward"), (1297.0, "backward")],
)
def test_clock_guard_blocks_large_wall_clock_step(monkeypatch, wall_now, expected_direction):
    monkeypatch.setattr(w, "_ntp_sync_state", lambda: {
        "source": "timedatectl.NTPSynchronized",
        "synchronized": True,
    })
    monkeypatch.setattr(w.time, "time", lambda: wall_now)
    monkeypatch.setattr(w.time, "monotonic", lambda: 800.0)
    state = {"clock_guard": {"wall_epoch": 1000.0, "monotonic_seconds": 500.0}}
    findings = w.Findings()
    tick = {}

    w.check_clock(state, findings, tick)

    blocked = {row["key"]: row for row in findings.blocked()}
    assert set(blocked) == {"clock_step_detected"}
    assert expected_direction in blocked["clock_step_detected"]["summary"]
    assert abs(tick["clock"]["step_seconds"]) >= w.CLOCK_STEP_BLOCK_SECONDS


def test_clock_guard_blocks_unsynchronized_ntp(monkeypatch):
    monkeypatch.setattr(w, "_ntp_sync_state", lambda: {
        "source": "timedatectl.NTPSynchronized",
        "synchronized": False,
    })
    monkeypatch.setattr(w.time, "time", lambda: 1000.0)
    monkeypatch.setattr(w.time, "monotonic", lambda: 500.0)
    findings = w.Findings()

    w.check_clock({}, findings, {})

    assert {row["key"] for row in findings.blocked()} == {"clock_ntp_unsynchronized"}


def test_clock_guard_blocks_unverifiable_ntp(monkeypatch):
    monkeypatch.setattr(w, "_ntp_sync_state", lambda: {
        "source": "timedatectl.NTPSynchronized",
        "synchronized": None,
        "error": "timedated unavailable",
    })
    monkeypatch.setattr(w.time, "time", lambda: 1000.0)
    monkeypatch.setattr(w.time, "monotonic", lambda: 500.0)
    findings = w.Findings()

    w.check_clock({}, findings, {})

    blocked = {row["key"]: row for row in findings.blocked()}
    assert set(blocked) == {"clock_ntp_unverifiable"}
    assert "timedated unavailable" in blocked["clock_ntp_unverifiable"]["summary"]


def test_clock_guard_does_not_compare_across_monotonic_reset(monkeypatch):
    monkeypatch.setattr(w, "_ntp_sync_state", lambda: {
        "source": "timedatectl.NTPSynchronized",
        "synchronized": True,
    })
    monkeypatch.setattr(w.time, "time", lambda: 2000.0)
    monkeypatch.setattr(w.time, "monotonic", lambda: 10.0)
    state = {"clock_guard": {"wall_epoch": 1000.0, "monotonic_seconds": 500.0}}
    findings = w.Findings()
    tick = {}

    w.check_clock(state, findings, tick)

    assert findings.blocked() == []
    assert tick["clock"]["monotonic_reset"] is True
    assert "step_seconds" not in tick["clock"]


def test_timedatectl_allowlist_refuses_mutating_commands():
    with pytest.raises(RuntimeError, match="timedatectl command not allowed"):
        w.run(["timedatectl", "set-ntp", "true"])


def test_static_selfcheck_accepts_clock_guard():
    w.static_selfcheck()
