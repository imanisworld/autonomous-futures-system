from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ops.project_check import main


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "a.txt").write_text("1\n")
    subprocess.run(["git", "add", "a.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def test_session_start_then_precommit_pass(tmp_path: Path, capsys) -> None:
    _init_repo(tmp_path)
    log_dir = tmp_path / "logs"

    rc = main(["session-start", "--cwd", str(tmp_path), "--log-dir", str(log_dir)])
    assert rc == 0
    assert (log_dir / "project_check_session_state.json").exists()

    rc = main(["precommit", "--cwd", str(tmp_path), "--log-dir", str(log_dir)])
    assert rc == 0


def test_precommit_without_session_start_fails_closed(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    rc = main(["precommit", "--cwd", str(tmp_path), "--log-dir", str(tmp_path / "logs")])
    assert rc == 1


def test_json_flag_accepted_after_subcommand(tmp_path: Path, capsys) -> None:
    _init_repo(tmp_path)
    log_dir = tmp_path / "logs"
    rc = main(["session-start", "--cwd", str(tmp_path), "--log-dir", str(log_dir), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["repo"]["current_branch"] == "main"


def test_daily_subcommand_runs_and_returns_pass_exit_code(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    rc = main(["daily", "--cwd", str(tmp_path), "--log-dir", str(log_dir), "--no-checkpoint-update"])
    assert rc == 0  # empty journal window -> trade chain trivially passes


def test_promotion_subcommand_requires_strategy_flag(tmp_path: Path) -> None:
    try:
        main(["promotion", "--cwd", str(tmp_path)])
        assert False, "expected SystemExit for missing --strategy"
    except SystemExit as exc:
        assert exc.code != 0
