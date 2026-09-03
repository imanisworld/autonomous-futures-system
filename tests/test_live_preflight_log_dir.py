"""Durable-artifact path contract for the live preflight state file.

Releases are immutable trees that may contain a real ``logs/`` directory, so a
cwd-relative default path would write the preflight artifact into the release
instead of ``LOG_DIR`` (the shared directory the flatness cron and the evidence
lanes read). These tests pin the contract: the writer and reader resolve the
artifact under ``LOG_DIR`` at call time, and never touch ``<cwd>/logs``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from execution import live_preflight

REPO = Path(__file__).resolve().parents[1]
# Runtime packages that execute inside the immutable release with cwd = release root.
RUNTIME_PACKAGES = ("execution", "webhook")
_RELATIVE_LOGS_LITERAL = re.compile(r"""Path\(\s*["']logs["']|["']logs/""")
def test_default_state_path_resolves_under_log_dir(monkeypatch, tmp_path):
    shared = tmp_path / "shared-logs"
    monkeypatch.setenv("LOG_DIR", str(shared))

    assert live_preflight.default_state_path() == shared / live_preflight.STATE_FILENAME
    assert live_preflight._state_path() == shared / live_preflight.STATE_FILENAME


def test_default_state_path_falls_back_to_relative_logs_without_log_dir(monkeypatch):
    monkeypatch.delenv("LOG_DIR", raising=False)

    assert live_preflight.default_state_path() == Path("logs") / live_preflight.STATE_FILENAME


def test_log_dir_is_read_at_call_time_not_import_time(monkeypatch, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("LOG_DIR", str(first))
    assert live_preflight.default_state_path().parent == first
    monkeypatch.setenv("LOG_DIR", str(second))
    assert live_preflight.default_state_path().parent == second


def test_save_state_writes_to_log_dir_not_release_cwd(monkeypatch, tmp_path):
    release_root = tmp_path / "release"
    (release_root / "logs").mkdir(parents=True)  # immutable release ships a real logs/
    shared = tmp_path / "shared-logs"
    monkeypatch.chdir(release_root)
    monkeypatch.setenv("LOG_DIR", str(shared))

    state = live_preflight.LivePreflightState(date=live_preflight._today(), last_result=True)
    live_preflight.save_state(state)

    artifact = shared / live_preflight.STATE_FILENAME
    assert artifact.exists()
    assert json.loads(artifact.read_text())["last_result"] is True
    assert list((release_root / "logs").iterdir()) == []


def test_load_state_reads_from_log_dir(monkeypatch, tmp_path):
    shared = tmp_path / "shared-logs"
    shared.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_DIR", str(shared))
    (shared / live_preflight.STATE_FILENAME).write_text(
        json.dumps(
            {
                "date": live_preflight._today(),
                "armed": True,
                "armed_at": "2026-01-01T00:00:00+00:00",
                "armed_by": "test",
                "last_preflight_at": "2026-01-01T00:00:00+00:00",
                "last_result": True,
                "checks": [],
            }
        )
    )

    state = live_preflight.load_state()

    assert state.armed is True
    assert state.armed_by == "test"


def test_runtime_packages_have_no_cwd_relative_logs_literals():
    """Guard: no futures-bot runtime module may default a durable artifact to ``logs/``.

    Durable artifacts must resolve through ``LOG_DIR`` so an immutable release
    with a real ``logs/`` directory cannot silently absorb them.
    """
    offenders: list[str] = []
    for package in RUNTIME_PACKAGES:
        for path in sorted((REPO / package).rglob("*.py")):
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if not _RELATIVE_LOGS_LITERAL.search(line):
                    continue
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {stripped}")
    assert offenders == [], "cwd-relative logs/ literals in runtime packages:\n" + "\n".join(offenders)
