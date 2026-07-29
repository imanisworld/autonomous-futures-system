"""tests/test_project_check.py

Proves ops.project_check's session-start -> precommit continuity contract:
precommit fails closed with no checkpoint, passes when nothing changed
about the session's identity, and fails closed the moment the branch or
worktree looks different from what session-start recorded. Also proves
precommit never runs a git write command.
"""

from __future__ import annotations

import io
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

from ops import project_check


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-q", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "a.txt").write_text("1\n")
    subprocess.check_call(["git", "add", "a.txt"], cwd=repo)
    subprocess.check_call(["git", "commit", "-q", "-m", "init"], cwd=repo)
    return repo


def _run(monkeypatch, repo: Path, argv: list[str]) -> tuple[int, str]:
    monkeypatch.chdir(repo)
    parser = project_check.build_parser()
    args = parser.parse_args(argv)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = args.func(args)
    return code, buf.getvalue()


def test_precommit_fails_closed_without_a_checkpoint(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    code, out = _run(monkeypatch, repo, ["precommit"])
    assert code == 1
    assert "FAIL CLOSED" in out
    assert "session-start state cannot be verified" in out


def test_precommit_passes_when_session_identity_is_unchanged(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    code, _ = _run(monkeypatch, repo, ["session-start", "--no-gh"])
    assert code == 0
    code, out = _run(monkeypatch, repo, ["precommit"])
    assert code == 0
    assert "PASS" in out


def test_precommit_fails_closed_on_unexpected_branch_switch(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    _run(monkeypatch, repo, ["session-start", "--no-gh"])
    subprocess.check_call(["git", "checkout", "-q", "-b", "other-branch"], cwd=repo)
    code, out = _run(monkeypatch, repo, ["precommit"])
    assert code == 1
    assert "branch differs from session-start branch unexpectedly" in out


def test_precommit_never_runs_a_git_write_command(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    _run(monkeypatch, repo, ["session-start", "--no-gh"])
    before_sha = _git(repo, "rev-parse", "HEAD")
    before_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")

    calls: list[list[str]] = []
    real_run = subprocess.run

    def _spy(cmd, *a, **kw):
        calls.append(list(cmd))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(project_check.repo_state.subprocess, "run", _spy)
    _run(monkeypatch, repo, ["precommit"])

    # "branch"/"stash"/"tag" have both read forms (listing) and write forms
    # (create/delete/move) -- only the unconditionally-mutating subcommands
    # are checked by bare presence; the rest are checked for a mutating flag.
    always_unsafe = {"commit", "push", "pull", "reset", "rebase", "checkout", "switch", "merge", "cherry-pick"}
    mutating_flags = {"-d", "-D", "-m", "-M", "-c", "--delete", "--move", "pop", "drop", "apply", "add", "remove", "prune"}
    for call in calls:
        if call[0] != "git":
            continue
        subcommand, rest = call[1], call[2:]
        assert subcommand not in always_unsafe, f"precommit ran a git write command: {call}"
        if subcommand in ("branch", "stash", "tag", "worktree"):
            assert not (set(rest) & mutating_flags), f"precommit ran a mutating {subcommand} command: {call}"

    assert _git(repo, "rev-parse", "HEAD") == before_sha
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == before_branch


def test_session_start_writes_a_gitignored_checkpoint(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    (repo / ".gitignore").write_text(".claude/*\n")
    subprocess.check_call(["git", "add", ".gitignore"], cwd=repo)
    subprocess.check_call(["git", "commit", "-q", "-m", "gitignore"], cwd=repo)

    _run(monkeypatch, repo, ["session-start", "--no-gh"])
    assert (repo / ".claude" / "project_check_state.json").exists()
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True)
    assert ".claude" not in status


def test_promotion_subcommand_runs_end_to_end(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "logs").mkdir()
    code, out = _run(monkeypatch, repo, ["promotion", "--strategy", "orb_breakout", "--log-dir", "logs"])
    assert code == 0
    assert "STRATEGY PROMOTION PROOF GATE" in out
    assert "never emits VALIDATED" in out
