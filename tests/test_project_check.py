from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ops import project_check
from ops.live_box_guard import PROOF_CRITICAL_RUNTIME_OVERRIDES


@pytest.fixture(autouse=True)
def _isolate_runtime_env(tmp_path_factory, monkeypatch):
    for name in PROOF_CRITICAL_RUNTIME_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"EXPECTED_PROOF_{name}", raising=False)
    monkeypatch.delenv("ENABLE_MANUAL_EXECUTION_CONTROLS", raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-primary")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "test-rotation")
    git_path = shutil.which("git")
    binstub_dir = tmp_path_factory.mktemp("binstub")
    (binstub_dir / "git").symlink_to(git_path)
    monkeypatch.setenv("PATH", str(binstub_dir))


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-q", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "risk_rules.yaml").write_text("trading_mode:\n  live_trading_enabled: false\n", encoding="utf-8")
    (repo / "logs").mkdir()
    (repo / "docs" / "strategy-rules").mkdir(parents=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.check_call(["git", "add", "."], cwd=repo)
    subprocess.check_call(["git", "commit", "-q", "-m", "initial"], cwd=repo)
    return repo


def test_session_start_then_precommit_exit_codes(tmp_path, capsys):
    repo = _init_repo(tmp_path)

    code = project_check.main(["--repo-root", str(repo), "--log-dir", str(repo / "logs"), "session-start"])
    assert code == 0
    capsys.readouterr()

    code = project_check.main(["--repo-root", str(repo), "--log-dir", str(repo / "logs"), "precommit"])
    assert code == 0
    out = capsys.readouterr().out
    assert "PRECOMMIT | OK" in out


def test_precommit_exits_nonzero_without_session_start(tmp_path, capsys):
    repo = _init_repo(tmp_path)
    code = project_check.main(["--repo-root", str(repo), "--log-dir", str(repo / "logs"), "precommit"])
    assert code == 1
    out = capsys.readouterr().out
    assert "FAIL CLOSED" in out


def test_precommit_json_output_is_parseable(tmp_path, capsys):
    repo = _init_repo(tmp_path)
    project_check.main(["--repo-root", str(repo), "--log-dir", str(repo / "logs"), "session-start"])
    capsys.readouterr()
    project_check.main(["--repo-root", str(repo), "--log-dir", str(repo / "logs"), "--json", "precommit"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is True


def test_daily_runs_end_to_end(tmp_path, capsys):
    repo = _init_repo(tmp_path)
    code = project_check.main([
        "--repo-root", str(repo),
        "--journal-dir", str(repo / "logs"),
        "--log-dir", str(repo / "logs"),
        "daily",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "TRADE CHAIN: PASS" in out


def test_promotion_requires_strategy_flag(tmp_path):
    repo = _init_repo(tmp_path)
    with pytest.raises(SystemExit):
        project_check.main(["--repo-root", str(repo), "--journal-dir", str(repo / "logs"), "promotion"])


def test_promotion_runs_end_to_end(tmp_path, capsys):
    repo = _init_repo(tmp_path)
    code = project_check.main([
        "--repo-root", str(repo),
        "--journal-dir", str(repo / "logs"),
        "promotion", "--strategy", "orb_breakout",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "CLASSIFICATION: WAIT" in out
