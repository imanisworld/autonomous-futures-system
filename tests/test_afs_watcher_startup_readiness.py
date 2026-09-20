"""Regression for reboot/deploy startup race between watcher and uvicorn."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

WATCHER_DIR = Path(__file__).parent.parent / "ops" / "afs_watcher"


def _load_watcher():
    if str(WATCHER_DIR) not in sys.path:
        sys.path.insert(0, str(WATCHER_DIR))
    spec = importlib.util.spec_from_file_location(
        "afs_watcher_startup_readiness", WATCHER_DIR / "watcher.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load_watcher()


def test_startup_readiness_waits_through_transient_connection_refusal(monkeypatch):
    calls = iter([
        (None, "connection refused"),
        (None, "connection refused"),
        ({"ok": True}, None),
    ])
    clock = {"t": 0.0}
    sleeps = []

    monkeypatch.setattr(w, "http_get_json", lambda path, timeout=2: next(calls))
    monkeypatch.setattr(w.time, "monotonic", lambda: clock["t"])

    def sleep(seconds):
        sleeps.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(w.time, "sleep", sleep)

    assert w.wait_for_status_api_ready(grace_seconds=5, poll_seconds=1) is True
    assert len(sleeps) == 2


def test_startup_readiness_timeout_does_not_convert_outage_to_success(monkeypatch):
    clock = {"t": 0.0}

    def monotonic():
        return clock["t"]

    def sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(
        w,
        "http_get_json",
        lambda path, timeout=2: (None, "connection refused"),
    )
    monkeypatch.setattr(w.time, "monotonic", monotonic)
    monkeypatch.setattr(w.time, "sleep", sleep)

    assert w.wait_for_status_api_ready(grace_seconds=3, poll_seconds=1) is False
    assert clock["t"] >= 3



def test_main_uses_startup_readiness_before_first_normal_tick(monkeypatch):
    called = {"ready": 0, "tick": 0}

    monkeypatch.setattr(w, "DEPLOY_PINS", {"ok": "yes"})
    monkeypatch.setattr(w, "RELEASE_DIR", Path("/tmp/fake-release"))
    monkeypatch.setattr(w, "RELEASE_SHA", "a" * 40)
    monkeypatch.setattr(w, "static_selfcheck", lambda: None)
    monkeypatch.setattr(w, "_ensure_dirs", lambda: None)
    monkeypatch.setattr(w.os, "open", lambda *a, **k: 123)
    monkeypatch.setattr(w.fcntl, "flock", lambda *a, **k: None)
    monkeypatch.setattr(w, "load_state", lambda: {})
    monkeypatch.setattr(w, "log", lambda _msg: None)

    def ready():
        called["ready"] += 1
        return True

    def tick(_state):
        called["tick"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(w, "wait_for_status_api_ready", ready)
    monkeypatch.setattr(w, "tick_once", tick)

    import pytest
    with pytest.raises(KeyboardInterrupt):
        w.main([])

    assert called == {"ready": 1, "tick": 1}


def test_main_once_mode_does_not_wait_for_status_api(monkeypatch):
    called = {"ready": 0, "tick": 0}

    monkeypatch.setattr(w, "DEPLOY_PINS", {"ok": "yes"})
    monkeypatch.setattr(w, "RELEASE_DIR", Path("/tmp/fake-release"))
    monkeypatch.setattr(w, "RELEASE_SHA", "a" * 40)
    monkeypatch.setattr(w, "static_selfcheck", lambda: None)
    monkeypatch.setattr(w, "_ensure_dirs", lambda: None)
    monkeypatch.setattr(w.os, "open", lambda *a, **k: 123)
    monkeypatch.setattr(w.fcntl, "flock", lambda *a, **k: None)
    monkeypatch.setattr(w, "load_state", lambda: {})
    monkeypatch.setattr(w, "log", lambda _msg: None)

    monkeypatch.setattr(
        w,
        "wait_for_status_api_ready",
        lambda: called.__setitem__("ready", called["ready"] + 1),
    )

    def tick(_state):
        called["tick"] += 1
        return {"verdict": "OK", "open_blockers": [], "findings": []}

    monkeypatch.setattr(w, "tick_once", tick)

    assert w.main(["--once"]) == 0
    assert called == {"ready": 0, "tick": 1}
