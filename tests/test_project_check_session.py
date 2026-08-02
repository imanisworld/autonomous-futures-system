from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ops import project_check_session as pcs


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "a.txt").write_text("1\n")
    subprocess.run(["git", "add", "a.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def test_session_start_report_shape(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    report = pcs.build_session_start_report(cwd=str(tmp_path), log_dir=tmp_path / "logs")
    assert report["repo"]["current_branch"] == "main"
    assert report["repo"]["head_sha"]
    assert report["worktree"]["current"] == str(tmp_path.resolve())
    assert report["branch_changed_during_check"] is False
    assert report["runtime_snapshot"]["evidence_epoch"] == "UNKNOWN"


def test_write_session_state_creates_file(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    log_dir = tmp_path / "logs"
    report = pcs.build_session_start_report(cwd=str(tmp_path), log_dir=log_dir)
    path = pcs.write_session_state(report, log_dir=log_dir)
    assert path.exists()
    state = json.loads(path.read_text())
    assert state["branch"] == "main"
    assert state["repo_root"] == str(tmp_path.resolve())


def test_precommit_passes_when_nothing_changed(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    log_dir = tmp_path / "logs"
    report = pcs.build_session_start_report(cwd=str(tmp_path), log_dir=log_dir)
    pcs.write_session_state(report, log_dir=log_dir)

    precommit = pcs.build_precommit_report(cwd=str(tmp_path), log_dir=log_dir)
    assert precommit["ok"] is True
    assert precommit["verdict"] == "PASS"
    assert precommit["failures"] == []


def test_precommit_fails_closed_without_session_state(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    precommit = pcs.build_precommit_report(cwd=str(tmp_path), log_dir=tmp_path / "logs")
    assert precommit["ok"] is False
    assert any("session-start state cannot be verified" in f for f in precommit["failures"])


def test_precommit_fails_closed_on_branch_drift(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    log_dir = tmp_path / "logs"
    report = pcs.build_session_start_report(cwd=str(tmp_path), log_dir=log_dir)
    pcs.write_session_state(report, log_dir=log_dir)

    subprocess.run(["git", "checkout", "-q", "-b", "other-branch"], cwd=tmp_path, check=True)
    precommit = pcs.build_precommit_report(cwd=str(tmp_path), log_dir=log_dir)
    assert precommit["ok"] is False
    assert any("branch differs from session-start branch unexpectedly" in f for f in precommit["failures"])


def test_precommit_fails_closed_on_head_moved(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    log_dir = tmp_path / "logs"
    report = pcs.build_session_start_report(cwd=str(tmp_path), log_dir=log_dir)
    pcs.write_session_state(report, log_dir=log_dir)

    (tmp_path / "b.txt").write_text("more\n")
    subprocess.run(["git", "add", "b.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "extra commit"], cwd=tmp_path, check=True)

    precommit = pcs.build_precommit_report(cwd=str(tmp_path), log_dir=log_dir)
    assert precommit["ok"] is False
    assert any("branch moved unexpectedly" in f for f in precommit["failures"])


def test_precommit_never_mutates_repo(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    log_dir = tmp_path / "logs"
    report = pcs.build_session_start_report(cwd=str(tmp_path), log_dir=log_dir)
    pcs.write_session_state(report, log_dir=log_dir)
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip()

    pcs.build_precommit_report(cwd=str(tmp_path), log_dir=log_dir)

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert head_before == head_after
    # only the tool's own state file under logs/ may be untracked; a.txt must be unmodified
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    assert "a.txt" not in status.stdout
