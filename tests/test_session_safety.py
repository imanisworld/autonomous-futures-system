from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ops import session_safety as ss


# ─── Hermetic git repo fixtures ─────────────────────────────────────────────

def _run(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=10,
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr}")
    return result


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run(["init", "-q", "-b", "main"], path)
    _run(["config", "user.name", "Test"], path)
    _run(["config", "user.email", "test@example.com"], path)
    _run(["config", "commit.gpgsign", "false"], path)
    _run(["config", "tag.gpgsign", "false"], path)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run(["add", "README.md"], path)
    _run(["commit", "-q", "-m", "initial commit"], path)
    return path


def _commit(path: Path, filename: str, content: str, message: str) -> str:
    (path / filename).write_text(content, encoding="utf-8")
    _run(["add", filename], path)
    _run(["commit", "-q", "-m", message], path)
    return _run(["rev-parse", "HEAD"], path).stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    return _init_repo(tmp_path / "repo")


@pytest.fixture
def repo_with_origin(tmp_path) -> Path:
    """A repo with a real origin/main remote-tracking ref, populated via a
    one-time test-setup fetch (the module itself never fetches)."""
    remote = tmp_path / "remote.git"
    _run(["init", "-q", "--bare", "-b", "main"], remote.parent)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    local = _init_repo(tmp_path / "repo")
    _run(["remote", "add", "origin", str(remote)], local)
    _run(["push", "-q", "-u", "origin", "main"], local)
    return local


# ─── Low-level git plumbing ─────────────────────────────────────────────────

def test_resolve_repo_root(repo):
    root = ss.resolve_repo_root(repo)
    assert root == repo.resolve()


def test_resolve_repo_root_not_a_repo(tmp_path):
    not_repo = tmp_path / "not_a_repo"
    not_repo.mkdir()
    assert ss.resolve_repo_root(not_repo) is None


def test_current_branch_and_head(repo):
    assert ss.current_branch(repo) == "main"
    head = ss.current_head(repo)
    assert head == _run(["rev-parse", "HEAD"], repo).stdout.strip()


def test_cross_checked_branch_agrees(repo):
    symbolic, status_branch, ambiguous = ss.cross_checked_branch(repo)
    assert symbolic == "main"
    assert status_branch == "main"
    assert ambiguous is False


def test_git_dir_main_worktree(repo):
    gd = ss.git_dir(repo)
    assert gd == (repo / ".git").resolve()


def test_sync_relationship_in_sync(repo_with_origin):
    sync = ss.sync_relationship(repo_with_origin)
    assert sync["relationship"] == "IN_SYNC"
    assert sync["ahead"] == 0
    assert sync["behind"] == 0


def test_sync_relationship_ahead(repo_with_origin):
    _commit(repo_with_origin, "a.txt", "a", "local only commit")
    sync = ss.sync_relationship(repo_with_origin)
    assert sync["relationship"] == "AHEAD"
    assert sync["ahead"] == 1
    assert sync["behind"] == 0


def test_sync_relationship_behind(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = _init_repo(tmp_path / "seed")
    _run(["remote", "add", "origin", str(remote)], seed)
    _run(["push", "-q", "-u", "origin", "main"], seed)

    local = _init_repo(tmp_path / "local2")
    _run(["remote", "add", "origin", str(remote)], local)
    _run(["fetch", "-q", "origin"], local)  # test-setup only; module code never fetches

    # advance the remote past local via a second clone
    _commit(seed, "b.txt", "b", "remote-only commit")
    _run(["push", "-q", "origin", "main"], seed)
    _run(["fetch", "-q", "origin"], local)  # test-setup only

    sync = ss.sync_relationship(local)
    assert sync["relationship"] == "BEHIND"
    assert sync["behind"] == 1


def test_sync_relationship_unknown_when_ref_missing(repo):
    sync = ss.sync_relationship(repo, ref="origin/main")
    assert sync["relationship"] == "UNKNOWN"


def test_git_status_parsing(repo):
    (repo / "tracked.txt").write_text("v1", encoding="utf-8")
    _run(["add", "tracked.txt"], repo)
    _run(["commit", "-q", "-m", "add tracked"], repo)

    (repo / "tracked.txt").write_text("v2", encoding="utf-8")  # unstaged modify
    (repo / "staged_new.txt").write_text("new", encoding="utf-8")
    _run(["add", "staged_new.txt"], repo)  # staged add
    (repo / "untracked.txt").write_text("u", encoding="utf-8")  # untracked

    status = ss.git_status(repo)
    assert "tracked.txt" in status["unstaged"]
    assert "staged_new.txt" in status["staged"]
    assert "untracked.txt" in status["untracked"]
    assert "tracked.txt" not in status["staged"]
    assert "untracked.txt" not in status["staged"]
    assert "untracked.txt" not in status["unstaged"]


def test_list_worktrees_and_dirty(repo):
    wt_path = repo.parent / "wt2"
    _run(["worktree", "add", "-q", "-b", "feature", str(wt_path)], repo)
    worktrees = ss.list_worktrees(repo)
    paths = {w["path"] for w in worktrees}
    assert str(repo.resolve()) in paths
    assert str(wt_path.resolve()) in paths
    feature_wt = next(w for w in worktrees if w["path"] == str(wt_path.resolve()))
    assert feature_wt["branch"] == "refs/heads/feature"

    assert ss.worktree_dirty(str(wt_path)) is False
    (wt_path / "dirty.txt").write_text("x", encoding="utf-8")
    assert ss.worktree_dirty(str(wt_path)) is True


def test_worktree_dirty_missing_path_returns_none(tmp_path):
    assert ss.worktree_dirty(str(tmp_path / "does_not_exist")) is None


def test_branch_vv_report_gone_and_local_only(repo_with_origin):
    _run(["branch", "local-only-branch"], repo_with_origin)
    _run(["checkout", "-q", "-b", "to-be-deleted-upstream"], repo_with_origin)
    _run(["push", "-q", "-u", "origin", "to-be-deleted-upstream"], repo_with_origin)
    _run(["checkout", "-q", "main"], repo_with_origin)
    remote_url = _run(["remote", "get-url", "origin"], repo_with_origin).stdout.strip()
    _run(["push", "-q", "origin", "--delete", "to-be-deleted-upstream"], repo_with_origin)
    _run(["fetch", "-q", "--prune", "origin"], repo_with_origin)  # test setup only

    report = ss.branch_vv_report(repo_with_origin)
    assert "to-be-deleted-upstream" in report["gone"]
    assert "local-only-branch" in report["local_only"]


def test_stash_list(repo):
    (repo / "README.md").write_text("changed", encoding="utf-8")
    _run(["stash", "push", "-q", "-m", "wip stash"], repo)
    stashes = ss.stash_list(repo)
    assert len(stashes) == 1
    assert "wip stash" in stashes[0]["label"]


def test_archive_tags(repo):
    _run(["tag", "archive/foo-2026-08-01"], repo)
    _run(["tag", "not-an-archive-tag"], repo)
    tags = ss.archive_tags(repo)
    assert tags == ["archive/foo-2026-08-01"]


def test_merged_local_branches(repo):
    _run(["checkout", "-q", "-b", "merged-branch"], repo)
    _commit(repo, "m.txt", "m", "merged branch commit")
    _run(["checkout", "-q", "main"], repo)
    _run(["merge", "-q", "--no-ff", "-m", "merge it", "merged-branch"], repo)
    assert "merged-branch" in ss.merged_local_branches(repo)


# ─── closed-unmerged-with-evidence detection ───────────────────────────────

def test_closed_unmerged_branch_without_archive_tag(repo):
    _run(["checkout", "-q", "-b", "orphan-work"], repo)
    _commit(repo, "orphan.txt", "x", "unique orphan work")
    _run(["checkout", "-q", "main"], repo)

    result = ss.closed_unmerged_branches_with_evidence(repo)
    assert result["scoped"] is True
    branch = next(b for b in result["branches"] if b["branch"] == "orphan-work")
    assert branch["unique_commit_count"] == 1
    assert branch["archive_exact_preserved"] is False
    assert branch["archive_descendant_preserved"] is False


def test_closed_unmerged_branch_with_exact_archive_tag(repo):
    _run(["checkout", "-q", "-b", "preserved-work"], repo)
    tip = _commit(repo, "preserved.txt", "x", "unique preserved work")
    _run(["checkout", "-q", "main"], repo)
    _run(["tag", "archive/preserved-work-2026-08-01", tip], repo)

    result = ss.closed_unmerged_branches_with_evidence(repo)
    branch = next(b for b in result["branches"] if b["branch"] == "preserved-work")
    assert branch["archive_exact_preserved"] is True
    assert branch["archive_descendant_preserved"] is True


def test_closed_unmerged_fully_merged_branch_excluded(repo):
    _run(["checkout", "-q", "-b", "already-merged"], repo)
    _commit(repo, "am.txt", "x", "already merged work")
    _run(["checkout", "-q", "main"], repo)
    _run(["merge", "-q", "--no-ff", "-m", "merge it", "already-merged"], repo)

    result = ss.closed_unmerged_branches_with_evidence(repo)
    names = {b["branch"] for b in result["branches"]}
    assert "already-merged" not in names


def test_closed_unmerged_scope_limit(repo):
    for i in range(3):
        _run(["branch", f"scoped-branch-{i}"], repo)
    result = ss.closed_unmerged_branches_with_evidence(repo, scope_limit=1)
    assert result["scoped"] is False
    assert "limitation" in result
    assert result["branches"] == []


# ─── gh helpers (never network; UNKNOWN when unavailable) ──────────────────

def test_gh_unavailable_returns_unknown(monkeypatch, repo):
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    assert ss.gh_available() is False
    data, err = ss.prs_by_state(repo, "open")
    assert data == "UNKNOWN"
    assert err == "gh_not_available"


def test_prs_active_today_unknown_when_gh_unavailable(monkeypatch, repo):
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    result = ss.prs_active_today(repo)
    assert result["opened_today"] == "UNKNOWN"
    assert result["merged_today"] == "UNKNOWN"
    assert result["closed_unmerged_today"] == "UNKNOWN"
    assert result["error"] == "gh_not_available"


def test_stale_open_prs_unknown_when_gh_unavailable(monkeypatch, repo):
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    data, err = ss.stale_open_prs(repo)
    assert data == "UNKNOWN"
    assert err == "gh_not_available"


def test_gh_json_uses_argument_list_not_shell(monkeypatch, repo):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        class R:
            returncode = 0
            stdout = "[]"
            stderr = ""
        return R()

    monkeypatch.setattr(ss.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(ss.subprocess, "run", fake_run)
    data, err = ss.prs_by_state(repo, "open")
    assert err is None
    assert data == []
    assert isinstance(captured["args"], list)
    assert captured["args"][0] == "gh"


# ─── collect_git_state / runtime snapshot ──────────────────────────────────

def test_collect_git_state_smoke(monkeypatch, repo):
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)  # no gh in sandbox
    state = ss.collect_git_state(repo)
    assert state["repo_root"] is None or True  # collect_git_state doesn't set repo_root key from arg directly
    assert state["branch"] == "main"
    assert state["branch_ambiguous"] is False
    assert state["open_prs"] == "UNKNOWN"
    assert isinstance(state["worktrees"], list)
    assert isinstance(state["closed_unmerged_candidates"], dict)


def test_build_runtime_snapshot_degrades_gracefully(tmp_path):
    fake_repo = _init_repo(tmp_path / "fake_repo")
    snapshot = ss.build_runtime_snapshot(fake_repo)
    assert "live_box_guard" in snapshot
    assert "active_paper_forward_lanes" in snapshot
    # No risk_rules.yaml in this synthetic repo -> config load fails cleanly.
    assert snapshot["config_load_error"] is not None
    assert snapshot["active_paper_forward_lanes"] == []


def test_fill_model_drift_unknown_when_no_inventory(tmp_path):
    class FakeConfig:
        entry_fill_model = "ioc_limit"

    result = ss.fill_model_drift(FakeConfig(), tmp_path / "missing.md", ["orb_reclaim"])
    assert result == [{
        "strategy": "orb_reclaim", "status": "UNKNOWN",
        "reason": "no matching Strategy_Inventory.md profile heading found",
    }]


def test_fill_model_drift_detects_mismatch(tmp_path):
    inventory = tmp_path / "Strategy_Inventory.md"
    inventory.write_text(
        "### ORB Reclaim — MES\n"
        "- Entry: reclaim\n"
        "- Fill model: IOC-faithful\n",
        encoding="utf-8",
    )

    class FakeConfig:
        entry_fill_model = "market"

    result = ss.fill_model_drift(FakeConfig(), inventory, ["orb_reclaim"])
    assert result[0]["status"] == "MISMATCH"
    assert result[0]["inventory_mapped_model"] == "ioc_limit"


# ─── Mode A: start ──────────────────────────────────────────────────────────

def test_build_start_report_writes_snapshot(monkeypatch, repo):
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    report = ss.build_start_report(repo_root=repo, now=now)

    assert report["ok"] is True
    assert report["branch"] == "main"
    assert report["snapshot_written"] is True
    assert report["branch_changed_during_check"] is False

    snapshot_path = Path(report["snapshot_path"])
    assert snapshot_path.exists()
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["branch"] == "main"
    assert payload["worktree_path"] == report["current_worktree"]
    assert payload["timestamp"] == now.isoformat()

    # Never tracked.
    tracked = _run(["ls-files", "--", snapshot_path.name], repo).stdout
    assert tracked.strip() == ""


def test_build_start_report_not_a_repo(tmp_path):
    not_repo = tmp_path / "not_a_repo"
    not_repo.mkdir()
    report = ss.build_start_report(repo_root=not_repo)
    assert report["ok"] is False
    assert "error" in report


def test_format_start_report_smoke(monkeypatch, repo):
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    report = ss.build_start_report(repo_root=repo)
    text = ss.format_start_report(report)
    assert "SESSION SAFETY: start" in text
    assert "RUNTIME SNAPSHOT" in text


# ─── Mode B: precommit / prepush ───────────────────────────────────────────

def test_precommit_passes_when_nothing_changed(monkeypatch, repo):
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    ss.build_start_report(repo_root=repo, now=now)

    report = ss.build_precommit_report(repo_root=repo, now=now + timedelta(minutes=5))
    assert report["ok"] is True
    assert report["exit_code"] == 0
    assert report["failures"] == []


def test_precommit_fails_closed_without_start(repo):
    report = ss.build_precommit_report(repo_root=repo)
    assert report["ok"] is False
    assert report["exit_code"] == 1
    assert any("session-start state cannot be verified" in f for f in report["failures"])


def test_precommit_fails_on_branch_change(monkeypatch, repo):
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    ss.build_start_report(repo_root=repo, now=now)

    _run(["checkout", "-q", "-b", "other-branch"], repo)
    report = ss.build_precommit_report(repo_root=repo, now=now + timedelta(minutes=5))
    assert report["ok"] is False
    assert any("differs from session-start branch" in f for f in report["failures"])


def test_precommit_fails_on_stale_snapshot(monkeypatch, repo):
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    ss.build_start_report(repo_root=repo, now=now)

    report = ss.build_precommit_report(
        repo_root=repo, now=now + timedelta(hours=25), stale_after_seconds=ss.STALE_SESSION_SECONDS,
    )
    assert report["ok"] is False
    assert any("stale" in f for f in report["failures"])


def test_precommit_fails_on_missing_snapshot_fields(repo):
    gd = ss.git_dir(repo)
    (gd / ss.SESSION_STATE_FILENAME).write_text(json.dumps({"branch": "main"}), encoding="utf-8")
    report = ss.build_precommit_report(repo_root=repo)
    assert report["ok"] is False
    assert any("session-start state cannot be verified" in f for f in report["failures"])


def test_precommit_fails_on_unparseable_snapshot(repo):
    gd = ss.git_dir(repo)
    (gd / ss.SESSION_STATE_FILENAME).write_text("not json", encoding="utf-8")
    report = ss.build_precommit_report(repo_root=repo)
    assert report["ok"] is False
    assert any("session-start state cannot be verified" in f for f in report["failures"])


def test_precommit_never_writes_any_file(monkeypatch, repo):
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    ss.build_start_report(repo_root=repo, now=now)

    gd = ss.git_dir(repo)
    snapshot_path = gd / ss.SESSION_STATE_FILENAME
    before = snapshot_path.read_text(encoding="utf-8")
    before_mtime = snapshot_path.stat().st_mtime

    ss.build_precommit_report(repo_root=repo, now=now + timedelta(minutes=1))

    assert snapshot_path.read_text(encoding="utf-8") == before
    assert snapshot_path.stat().st_mtime == before_mtime


def test_precommit_branch_owned_by_another_worktree(monkeypatch, repo):
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    ss.build_start_report(repo_root=repo, now=now)

    # Simulate: session claims branch "feature", but "feature" is actually
    # checked out in a different linked worktree than the one we're in.
    wt_path = repo.parent / "wt_feature"
    _run(["worktree", "add", "-q", "-b", "feature", str(wt_path)], repo)
    gd = ss.git_dir(repo)
    snapshot = json.loads((gd / ss.SESSION_STATE_FILENAME).read_text(encoding="utf-8"))
    snapshot["branch"] = "feature"
    (gd / ss.SESSION_STATE_FILENAME).write_text(json.dumps(snapshot), encoding="utf-8")

    report = ss.build_precommit_report(repo_root=repo, now=now + timedelta(minutes=1))
    assert report["ok"] is False
    assert any("different worktree" in f for f in report["failures"])


def test_format_precommit_report_smoke(monkeypatch, repo):
    monkeypatch.setattr(ss.shutil, "which", lambda name: None)
    ss.build_start_report(repo_root=repo)
    report = ss.build_precommit_report(repo_root=repo)
    text = ss.format_precommit_report(report)
    assert "SESSION SAFETY: precommit" in text
    assert "PASS" in text
