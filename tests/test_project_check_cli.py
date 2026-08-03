"""Tests for ops.project_check — the `python -m ops.project_check <cmd>` CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ops import project_check


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(root), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "initial commit")
    return root


def test_parser_requires_a_subcommand() -> None:
    parser = project_check.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_promotion_requires_strategy_flag() -> None:
    parser = project_check.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["promotion"])


def test_session_start_then_precommit_exit_codes(repo: Path, capsys) -> None:
    exit_code = project_check.main(["session-start", "--repo-root", str(repo), "--no-fetch"])
    assert exit_code == 0
    capsys.readouterr()

    exit_code = project_check.main(["precommit", "--repo-root", str(repo)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "PRECOMMIT: PASS" in out


def test_precommit_without_session_start_fails_closed_with_nonzero_exit(repo: Path, capsys) -> None:
    exit_code = project_check.main(["precommit", "--repo-root", str(repo)])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "PRECOMMIT: FAIL_CLOSED" in out


def test_session_start_json_output_is_valid_json(repo: Path, capsys) -> None:
    project_check.main(["session-start", "--repo-root", str(repo), "--no-fetch", "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["mode"] == "session-start"
    assert parsed["branch"] == "main"


def test_daily_json_output_is_valid_json(repo: Path, capsys) -> None:
    (repo / "docs" / "strategy-rules").mkdir(parents=True)
    (repo / "docs" / "strategy-rules" / "Strategy_Inventory.md").write_text("# empty\n", encoding="utf-8")
    (repo / "risk_rules.yaml").write_text("version: test\n", encoding="utf-8")
    journal_dir = repo / "logs"
    journal_dir.mkdir()

    project_check.main([
        "daily", "--repo-root", str(repo), "--journal-dir", str(journal_dir),
        "--today", "2026-08-01", "--from-date", "2026-08-01",
        "--no-fetch", "--no-checkpoint", "--json",
    ])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["today"] == "2026-08-01"
    assert "trade_chain" in parsed


def test_promotion_summary_output(repo: Path, capsys) -> None:
    (repo / "docs" / "strategy-rules").mkdir(parents=True)
    (repo / "docs" / "strategy-rules" / "Strategy_Inventory.md").write_text("# empty\n", encoding="utf-8")
    (repo / "risk_rules.yaml").write_text("version: test\n", encoding="utf-8")
    journal_dir = repo / "logs"
    journal_dir.mkdir()

    exit_code = project_check.main([
        "promotion", "--strategy", "orb_breakout", "--repo-root", str(repo),
        "--journal-dir", str(journal_dir), "--to-date", "2026-08-01",
    ])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "STRATEGY PROMOTION PROOF GATE: orb_breakout" in out
    assert "never VALIDATED automatically" in out


def test_no_subcommand_never_mutates_repo(repo: Path, capsys) -> None:
    before = _git(repo, "rev-parse", "HEAD")
    project_check.main(["precommit", "--repo-root", str(repo)])
    project_check.main(["session-start", "--repo-root", str(repo), "--no-fetch"])
    capsys.readouterr()
    assert _git(repo, "rev-parse", "HEAD") == before
    assert _git(repo, "status", "--porcelain") == ""
