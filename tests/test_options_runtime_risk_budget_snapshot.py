from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from options_manager.validation.portfolio_risk_gate import AGGREGATE_RISK_BUDGET_ENV
from scripts.options_runtime_risk_budget_snapshot import (
    build_budget_snapshot,
    snapshot_json,
)


def test_builds_minimal_canonical_snapshot_from_runtime_env_value():
    snapshot = build_budget_snapshot(
        {
            AGGREGATE_RISK_BUDGET_ENV: "900",
            "UNRELATED_SECRET": "must-not-appear",
        }
    )
    assert snapshot == {
        "schema_version": 1,
        "source_env_key": AGGREGATE_RISK_BUDGET_ENV,
        "max_aggregate_open_risk_dollars": 900.0,
    }
    encoded = snapshot_json(snapshot)
    assert "UNRELATED_SECRET" not in encoded
    assert "must-not-appear" not in encoded
    assert encoded == (
        '{"max_aggregate_open_risk_dollars":900.0,'
        '"schema_version":1,'
        '"source_env_key":"OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS"}\n'
    )


@pytest.mark.parametrize("value", ["", "nan", "inf", "-1", "0", "not-a-number"])
def test_missing_or_invalid_budget_fails_closed(value):
    with pytest.raises(ValueError):
        build_budget_snapshot({AGGREGATE_RISK_BUDGET_ENV: value})


def test_direct_help_works_without_pythonpath():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/options_runtime_risk_budget_snapshot.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "aggregate-risk budget" in result.stdout


def test_cli_emits_only_budget_snapshot_from_supplied_env_file(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"{AGGREGATE_RISK_BUDGET_ENV}=875\n"
        "SOME_SECRET=do-not-print\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/options_runtime_risk_budget_snapshot.py",
            "--env-file",
            str(env_file),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["max_aggregate_open_risk_dollars"] == 875.0
    assert payload["source_env_key"] == AGGREGATE_RISK_BUDGET_ENV
    assert "SOME_SECRET" not in result.stdout
    assert "do-not-print" not in result.stdout


def test_explicit_env_file_wins_over_inherited_process_value(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"{AGGREGATE_RISK_BUDGET_ENV}=875\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/options_runtime_risk_budget_snapshot.py",
            "--env-file",
            str(env_file),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={AGGREGATE_RISK_BUDGET_ENV: "9999"},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["max_aggregate_open_risk_dollars"] == 875.0


def test_missing_env_file_fails_closed(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "scripts/options_runtime_risk_budget_snapshot.py",
            "--env-file",
            str(tmp_path / "missing.env"),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={},
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "BLOCKED"
    assert payload["reason"] == "env file is unavailable"
