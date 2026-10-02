"""Where the observation Discord status state file is written."""
from __future__ import annotations

from pathlib import Path

from notifications.observation_status import _state_path

STATE = "discord_observation_status.json"


def _clear(monkeypatch):
    for name in ("DISCORD_OBSERVATION_STATUS_STATE", "LOG_DIR", "AFS_SHARED_DIR"):
        monkeypatch.delenv(name, raising=False)


def test_explicit_state_path_wins(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("DISCORD_OBSERVATION_STATUS_STATE", str(tmp_path / "x.json"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "journal"))
    assert _state_path() == tmp_path / "x.json"


def test_log_dir_used_beside_the_journal(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("AFS_SHARED_DIR", str(tmp_path / "shared"))
    assert _state_path() == tmp_path / "journal" / STATE


def test_shared_dir_used_when_log_dir_unset(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("AFS_SHARED_DIR", str(tmp_path / "shared"))
    assert _state_path() == tmp_path / "shared" / "logs" / STATE


def test_blank_values_are_ignored(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DISCORD_OBSERVATION_STATUS_STATE", "  ")
    monkeypatch.setenv("LOG_DIR", " ")
    assert _state_path() == Path("logs") / STATE
