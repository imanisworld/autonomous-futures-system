from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from scripts.options_212r_prospective_collect import _load_journal

UTC = timezone.utc


def test_journal_recovers_earliest_arm_and_terminal_state(tmp_path: Path):
    path = tmp_path / "evidence.jsonl"
    observation = {"setup_fingerprint": "fp1"}
    rows = [
        {"record_type": "ARMED", "setup_id": "abc", "observed_at": "2026-09-18T15:04:00+00:00", "observation": observation},
        {"record_type": "ARMED", "setup_id": "abc", "observed_at": "2026-09-18T15:03:00+00:00", "observation": observation},
        {"record_type": "RESOLUTION", "setup_id": "abc", "observed_at": "2026-09-18T15:10:30+00:00", "observation": observation},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    armed, terminal, fingerprints = _load_journal(path)
    assert armed["abc"] == datetime(2026, 9, 18, 15, 3, tzinfo=UTC)
    assert terminal == {"abc"}
    assert fingerprints == {"abc": "fp1"}


def test_malformed_journal_fails_closed(tmp_path: Path):
    path = tmp_path / "evidence.jsonl"
    path.write_text("not-json\n")
    with pytest.raises(RuntimeError, match="journal_invalid_json"):
        _load_journal(path)


def test_collector_has_no_execution_risk_broker_or_notification_imports():
    source = Path("scripts/options_212r_prospective_collect.py").read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ("execution", "risk", "webhook", "notifications", "broker")
    assert not [name for name in imported if name.startswith(forbidden)]


def test_pure_observer_has_no_network_or_storage_imports():
    source = Path("alert_ranker/options_212r_prospective.py").read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name.startswith(("httpx", "requests", "sqlite3", "execution", "risk", "broker")) for name in imported)


def test_journal_source_revision_fails_closed(tmp_path: Path):
    path = tmp_path / "evidence.jsonl"
    rows = [
        {"record_type": "ARMED", "setup_id": "abc", "observed_at": "2026-09-18T15:01:00+00:00", "observation": {"setup_fingerprint": "fp1"}},
        {"record_type": "RESOLUTION", "setup_id": "abc", "observed_at": "2026-09-18T15:10:00+00:00", "observation": {"setup_fingerprint": "fp2"}},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(RuntimeError, match="journal_setup_fingerprint_drift"):
        _load_journal(path)
