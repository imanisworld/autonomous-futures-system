from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.project_check import gitutil


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def test_run_git_rejects_unlisted_subcommand(repo: Path) -> None:
    with pytest.raises(ValueError):
        gitutil.run_git(["commit", "-m", "nope"], cwd=repo)


def test_run_git_rejects_mutating_subcommands_even_if_spelled_out(repo: Path) -> None:
    for banned in ("push", "pull", "reset", "rebase", "checkout", "merge", "cherry-pick", "clean"):
        with pytest.raises(ValueError):
            gitutil.run_git([banned], cwd=repo)


@pytest.mark.parametrize(
    "args",
    (
        ["worktree", "remove", "other"],
        ["worktree", "prune"],
        ["stash", "drop"],
        ["tag", "-d", "archive/example"],
        ["branch", "-D", "example"],
    ),
)
def test_run_git_rejects_mutating_shapes_inside_mixed_command_families(
    repo: Path,
    args: list[str],
) -> None:
    with pytest.raises(ValueError):
        gitutil.run_git(args, cwd=repo)


def test_run_git_accepts_only_exact_worktree_list_shape(repo: Path) -> None:
    out, error = gitutil.run_git(["worktree", "list", "--porcelain"], cwd=repo)
    assert error is None
    assert out is not None and f"worktree {repo}" in out


def test_repo_root_and_branch(repo: Path) -> None:
    assert gitutil.repo_root(repo) == repo.resolve()
    assert gitutil.current_branch(repo) == "main"
    assert gitutil.head_sha(repo) is not None


def test_status_porcelain_reports_staged_dirty_untracked(repo: Path) -> None:
    (repo / "a.txt").write_text("changed\n")
    (repo / "untracked.txt").write_text("new\n")
    status = gitutil.status_porcelain(repo)
    assert "a.txt" in status["dirty_tracked"]
    assert "untracked.txt" in status["untracked"]
    assert status["staged"] == []

    _git(repo, "add", "untracked.txt")
    status2 = gitutil.status_porcelain(repo)
    assert "untracked.txt" in status2["staged"]


def test_main_sync_state_unknown_without_remote(repo: Path) -> None:
    state = gitutil.main_sync_state(repo)
    assert state["state"] == "UNKNOWN"
    assert state["local_main_branch"] == "main"
    assert state["remote_ref"] is None


def test_main_sync_state_in_sync_ahead_behind(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare", "-b", "main")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True, capture_output=True)
    _git(clone, "config", "user.email", "test@example.com")
    _git(clone, "config", "user.name", "Test")
    (clone / "f.txt").write_text("1\n")
    _git(clone, "add", "f.txt")
    _git(clone, "commit", "-q", "-m", "c1")
    _git(clone, "push", "-q", "-u", "origin", "main")

    state = gitutil.main_sync_state(clone)
    assert state["state"] == "IN_SYNC"

    (clone / "f.txt").write_text("2\n")
    _git(clone, "add", "f.txt")
    _git(clone, "commit", "-q", "-m", "c2 local only")
    state_ahead = gitutil.main_sync_state(clone)
    assert state_ahead["state"] == "AHEAD"
    assert state_ahead["ahead"] == 1
    assert state_ahead["behind"] == 0


def test_worktrees_lists_current_worktree(repo: Path) -> None:
    wts = gitutil.worktrees(repo)
    assert len(wts) == 1
    assert Path(wts[0].path).resolve() == repo.resolve()
    assert wts[0].branch == "main"


def test_stash_list_empty_then_populated(repo: Path) -> None:
    assert gitutil.stash_list(repo) == []
    (repo / "a.txt").write_text("dirty\n")
    _git(repo, "stash", "push", "-m", "wip")
    stashes = gitutil.stash_list(repo)
    assert len(stashes) == 1
    assert "wip" in stashes[0]["message"]


def test_archive_tags_only_matches_archive_prefix(repo: Path) -> None:
    _git(repo, "tag", "-a", "archive/foo-2026-01-01", "-m", "archived")
    _git(repo, "tag", "-a", "v1.0.0", "-m", "release")
    tags = gitutil.archive_tags(repo)
    names = [t["tag"] for t in tags]
    assert "archive/foo-2026-01-01" in names
    assert "v1.0.0" not in names


def test_local_branches_reports_local_only(repo: Path) -> None:
    _git(repo, "branch", "feature/x")
    branches = gitutil.local_branches(repo)
    by_name = {b["branch"]: b for b in branches}
    assert by_name["feature/x"]["local_only"] is True
    assert by_name["main"]["local_only"] is True


def test_gh_available_reflects_path(monkeypatch) -> None:
    monkeypatch.setattr(gitutil.shutil, "which", lambda name: None)
    assert gitutil.gh_available() is False


def test_open_prs_unavailable_without_gh(monkeypatch, repo: Path) -> None:
    monkeypatch.setattr(gitutil.shutil, "which", lambda name: None)
    result = gitutil.open_prs(repo)
    assert result["available"] is False
    assert result["prs"] == []


def _sha(root: Path, ref: str = "HEAD") -> str:
    return subprocess.check_output(["git", "rev-parse", ref], cwd=root, text=True).strip()


@pytest.fixture
def evidence_repo(repo: Path, monkeypatch) -> Path:
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature/evidence")
    (repo / "a.txt").write_text("unique evidence\n")
    _git(repo, "commit", "-qam", "research evidence")
    monkeypatch.setattr(gitutil, "open_prs", lambda *a, **kw: {
        "available": True, "complete": True, "prs": [],
    })
    return repo


def _branch_report(repo: Path, ref: str = "refs/heads/feature/evidence") -> dict:
    report = gitutil.unmerged_remote_branches_missing_archive_tag(repo)
    return next(row for row in report["branches"] if row["ref"] == ref)


@pytest.mark.parametrize("annotated", [False, True])
def test_archive_preserves_exact_tip_with_dereferenced_sha(evidence_repo: Path, annotated: bool) -> None:
    tag = "archive/feature-evidence-2026-09-01"
    _git(evidence_repo, "tag", *(["-a", tag, "-m", "preserve"] if annotated else [tag]))
    row = _branch_report(evidence_repo)
    assert row["classification"] == "ARCHIVED / PRESERVED"
    archive = row["matching_archive_tags"][0]
    assert archive["sha"] == row["tip_sha"]
    assert archive["exact_tip_match"] is True
    assert (archive["object_sha"] != archive["sha"]) is annotated
    assert row["worktree_owners"] == [str(evidence_repo)]
    assert row["cleanup_blocked"] is True


def test_wrong_tip_archive_does_not_preserve_unique_evidence(evidence_repo: Path) -> None:
    _git(evidence_repo, "tag", "-a", "archive/feature-evidence-2026-09-01", "main", "-m", "old tip")
    row = _branch_report(evidence_repo)
    assert row["matching_archive_tags"][0]["exact_tip_match"] is False
    assert row["classification"] == "UNARCHIVED UNIQUE EVIDENCE — BLOCKER"


@pytest.mark.parametrize("gone_upstream", [False, True])
def test_local_only_or_deleted_remote_tip_preserved_on_other_origin(evidence_repo: Path, gone_upstream: bool) -> None:
    if gone_upstream:
        _git(evidence_repo, "remote", "add", "origin", str(evidence_repo.parent / "unused.git"))
        _git(evidence_repo, "config", "branch.feature/evidence.remote", "origin")
        _git(evidence_repo, "config", "branch.feature/evidence.merge", "refs/heads/deleted")
    _git(evidence_repo, "update-ref", "refs/remotes/origin/backup", "HEAD")
    row = _branch_report(evidence_repo)
    assert row["classification"] == "ARCHIVED / PRESERVED"
    assert "refs/remotes/origin/backup" in row["preserved_by"]
    assert row["ahead_of_origin_branch"] is None


@pytest.mark.parametrize("later_main_edit", [False, True])
def test_squash_merged_pr_preserves_content_without_tip_ancestry(evidence_repo: Path, monkeypatch, later_main_edit: bool) -> None:
    repo = evidence_repo
    tip = _sha(repo)
    _git(repo, "update-ref", "refs/remotes/origin/feature/evidence", tip)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--squash", "feature/evidence")
    _git(repo, "commit", "-qm", "squash merged PR")
    merge = _sha(repo)
    (repo / ("a.txt" if later_main_edit else "unrelated.txt")).write_text("later main work\n")
    _git(repo, "add", "--", "a.txt" if later_main_edit else "unrelated.txt")
    _git(repo, "commit", "-qm", "main advances")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    monkeypatch.setattr(gitutil, "open_prs", lambda *a, **kw: {
        "available": True, "complete": True,
        "prs": [{"state": "MERGED", "headRefName": "feature/evidence", "headRefOid": tip,
                 "mergeCommit": {"oid": merge}, "isCrossRepository": False}],
    })
    assert gitutil._is_ancestor(repo, tip, _sha(repo)) is False
    row = _branch_report(repo, "refs/remotes/origin/feature/evidence")
    assert row["classification"] == "REDUNDANT"
    assert row["content_preserved_at_merge"] is True
    assert row["content_equivalent_on_main"] is (not later_main_edit)
    # New local work after that PR is NOT protected by the old merged status.
    _git(repo, "checkout", "-q", "feature/evidence")
    (repo / "a.txt").write_text("unpushed followup\n")
    _git(repo, "commit", "-qam", "new unpushed evidence")
    row = _branch_report(repo)
    assert row["ahead_of_origin_branch"] == 1
    assert row["classification"] == "UNKNOWN"
    assert row["cleanup_blocked"] is True


def test_closed_unmerged_remote_without_preservation_is_blocker(evidence_repo: Path, monkeypatch) -> None:
    tip = _sha(evidence_repo)
    _git(evidence_repo, "update-ref", "refs/remotes/origin/feature/evidence", tip)
    monkeypatch.setattr(gitutil, "open_prs", lambda *a, **kw: {
        "available": True, "complete": True,
        "prs": [{"state": "CLOSED", "headRefName": "feature/evidence", "headRefOid": tip}],
    })
    row = _branch_report(evidence_repo, "refs/remotes/origin/feature/evidence")
    assert row["classification"] == "UNARCHIVED UNIQUE EVIDENCE — BLOCKER"


def test_pr_unavailable_or_disagreeing_checks_remain_unknown(evidence_repo: Path, monkeypatch) -> None:
    monkeypatch.setattr(gitutil, "open_prs", lambda *a, **kw: {"available": False, "reason": "offline"})
    assert _branch_report(evidence_repo)["classification"] == "UNKNOWN"
    monkeypatch.setattr(gitutil, "_is_ancestor", lambda *a: True)
    row = _branch_report(evidence_repo)
    assert row["classification"] == "UNKNOWN"
    assert "disagree" in row["reason"]


def test_worktree_inventory_reports_all_files_and_handles_git_discovery(repo: Path, tmp_path: Path) -> None:
    linked = tmp_path / "linked tree"
    _git(repo, "worktree", "add", "-b", "other", str(linked))
    (linked / "a.txt").write_text("dirty\n")
    (linked / "staged.txt").write_text("staged\n")
    _git(linked, "add", "staged.txt")
    (linked / "evidence").mkdir()
    name = 'evidence/space and\nnewline.txt'
    (linked / name).write_text("untracked\n")
    (linked / "evidence/second.txt").write_text("untracked\n")
    status = {x["path"]: x["dirty_status"] for x in gitutil.worktree_inventory(repo)}
    assert status[str(repo)]["dirty"] is False
    assert status[str(linked)]["dirty_tracked"] == ["a.txt"]
    assert status[str(linked)]["staged"] == ["staged.txt"]
    assert set(status[str(linked)]["untracked"]) == {name, "evidence/second.txt"}
    assert gitutil.worktree_dirty(str(linked / "evidence"))["checked"] is False
    assert gitutil.worktree_dirty(str(tmp_path / "missing"))["checked"] is False
    (linked / "a.txt").write_text("one\n")
    _git(linked, "mv", "a.txt", "renamed file.txt")
    assert "renamed file.txt" in gitutil.status_porcelain(linked)["staged"]
    assert set(gitutil.status_porcelain(linked)["untracked"]) == {name, "evidence/second.txt"}


def test_main_relationship_retains_both_sides_of_divergence(evidence_repo: Path) -> None:
    repo = evidence_repo
    _git(repo, "update-ref", "refs/remotes/origin/main", "feature/evidence")
    assert gitutil.main_sync_state(repo)["state"] == "BEHIND"
    _git(repo, "checkout", "-q", "main")
    (repo / "local.txt").write_text("local-only main\n")
    _git(repo, "add", "local.txt")
    _git(repo, "commit", "-qm", "local main divergence")
    state = gitutil.main_sync_state(repo)
    assert (state["state"], state["ahead"], state["behind"]) == ("DIVERGED", 1, 1)


def test_diff_inspection_cannot_write_output(repo: Path) -> None:
    with pytest.raises(ValueError):
        gitutil.run_git(["diff", "--no-ext-diff", "--no-textconv", "--quiet", "--output=unsafe", "HEAD"], cwd=repo)


def test_archive_can_preserve_older_remote_tip_without_claiming_exact_match(evidence_repo: Path) -> None:
    repo = evidence_repo
    original_tip = _sha(repo)
    (repo / "a.txt").write_text("later work\n")
    _git(repo, "commit", "-qam", "later")
    _git(repo, "tag", "-a", "archive/feature-evidence-2026-09-01", "-m", "wrong intended tip")
    _git(repo, "update-ref", "refs/remotes/origin/feature/evidence", original_tip)
    row = _branch_report(repo, "refs/remotes/origin/feature/evidence")
    assert row["classification"] == "ARCHIVED / PRESERVED"
    assert row["matching_archive_tags"][0]["exact_tip_match"] is False
    assert row["matching_archive_tags"][0]["sha"] == _sha(repo)


def test_incomplete_or_conflicting_pr_inventory_is_unknown(repo: Path) -> None:
    tip = _sha(repo)
    pr = {"headRefName": "feature", "headRefOid": tip, "state": "MERGED"}
    for prs in [
        {"available": True, "complete": False, "prs": []},
        {"available": True, "complete": True, "prs": [pr, {**pr, "state": "CLOSED"}]},
    ]:
        result = gitutil.pr_status_for_branch(repo, "feature", prs=prs, tip_sha=tip)
        assert result["status"] == "UNKNOWN"


def test_closed_original_preserved_by_exact_head_replacement_merge(evidence_repo: Path, monkeypatch) -> None:
    repo = evidence_repo
    tip = _sha(repo)
    _git(repo, "update-ref", "refs/remotes/origin/feature/evidence", tip)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--squash", "feature/evidence")
    _git(repo, "commit", "-qm", "replacement PR merged")
    merge = _sha(repo)
    (repo / "a.txt").write_text("main evolved after replacement\n")
    _git(repo, "commit", "-qam", "later main")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    monkeypatch.setattr(gitutil, "open_prs", lambda *a, **kw: {
        "available": True, "complete": True,
        "prs": [
            {"state": "CLOSED", "headRefName": "feature/evidence", "headRefOid": tip},
            {"state": "MERGED", "headRefName": "merge/replacement", "headRefOid": tip,
             "baseRefName": "main", "mergeCommit": {"oid": merge}},
        ],
    })
    row = _branch_report(repo, "refs/remotes/origin/feature/evidence")
    assert row["classification"] == "REDUNDANT"
    assert row["pr"]["headRefName"] == "merge/replacement"
    assert row["content_preserved_at_merge"] is True
