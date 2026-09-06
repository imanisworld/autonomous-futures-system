"""Regressions for preservation reporting that must never fail open.

A failing read-only git listing used to be indistinguishable from an empty
one: `git stash list`, `git tag -l archive/*` and `for-each-ref refs/heads/`
each returned `[]` on error, so session-start and daily reconciliation would
print "stashes: 0 / archive tags: 0 / local-only branches: 0" for a repository
whose stashes, archive tags and branches had simply not been enumerated. In a
preservation routine that reads as a clean bill of health.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.project_check import gitutil
from ops.project_check.daily import _overall_blockers, _repo_hygiene
from ops.project_check.session import build_session_start_report
from scripts.project_check import _archive_proof, _count, _preservation_headline


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


def _break(monkeypatch, *leading: str) -> None:
    """Make exactly the listings whose first arg is in `leading` fail."""
    real = gitutil.run_git

    def patched(args, *, cwd, timeout=gitutil.DEFAULT_TIMEOUT_S):
        if args and args[0] in leading and (args[0] != "for-each-ref" or "refs/heads/" in args):
            return None, "simulated git failure"
        return real(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(gitutil, "run_git", patched)


@pytest.mark.parametrize(
    ("subcommand", "inventory", "items_key"),
    [
        ("stash", gitutil.stash_inventory, "stashes"),
        ("tag", gitutil.archive_tag_inventory, "tags"),
        ("for-each-ref", gitutil.local_branch_inventory, "branches"),
    ],
)
def test_failed_listing_is_unknown_not_empty(repo: Path, monkeypatch, subcommand, inventory, items_key) -> None:
    assert inventory(repo)["checked"] is True
    _break(monkeypatch, subcommand)
    result = inventory(repo)
    assert result["checked"] is False
    assert "simulated git failure" in result["reason"]
    assert result[items_key] == []


def test_empty_listing_stays_checked(repo: Path) -> None:
    # A repository with no stashes must not look like a failed enumeration.
    result = gitutil.stash_inventory(repo)
    assert result == {"checked": True, "reason": None, "stashes": []}


def test_session_start_reports_unknown_counts_when_enumeration_fails(repo: Path, monkeypatch) -> None:
    _break(monkeypatch, "stash", "tag", "for-each-ref")
    reported = build_session_start_report(cwd=repo)["repo"]
    assert reported["stash_count"] is None
    assert reported["stash_enumeration"]["checked"] is False
    assert reported["archive_tag_enumeration"]["checked"] is False
    assert reported["local_branch_enumeration"]["checked"] is False
    # The counts a reader would otherwise take as "nothing to preserve".
    assert _count(reported["stash_count"]) == "UNKNOWN"


def test_session_start_counts_are_known_on_a_healthy_repo(repo: Path) -> None:
    reported = build_session_start_report(cwd=repo)["repo"]
    assert reported["stash_count"] == 0
    assert reported["stash_enumeration"]["checked"] is True
    assert reported["archive_tag_enumeration"]["checked"] is True
    assert reported["local_branch_enumeration"]["checked"] is True


def test_daily_escalates_unverifiable_enumeration(repo: Path, monkeypatch) -> None:
    assert _repo_hygiene(repo)["unverified_enumerations"] == []
    _break(monkeypatch, "stash")
    hygiene = _repo_hygiene(repo)
    assert any("stash inventory" in reason for reason in hygiene["unverified_enumerations"])
    blockers = _overall_blockers(
        hygiene=hygiene,
        runtime={"live_box_drift": {"status": "ok"}, "risk_rules_load_error": None},
        strategy_drift={"checked": True, "drift_findings": []},
        trade_chain={"status": "PASS"},
    )
    assert any(b["code"] == "REPO_PRESERVATION_UNVERIFIED" for b in blockers)


def test_daily_has_no_preservation_blocker_when_everything_enumerates(repo: Path) -> None:
    blockers = _overall_blockers(
        hygiene=_repo_hygiene(repo),
        runtime={"live_box_drift": {"status": "ok"}, "risk_rules_load_error": None},
        strategy_drift={"checked": True, "drift_findings": []},
        trade_chain={"status": "PASS"},
    )
    assert [b["code"] for b in blockers] == []


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


def _row(repo: Path) -> dict:
    report = gitutil.unmerged_remote_branches_missing_archive_tag(repo)
    return next(r for r in report["branches"] if r["ref"] == "refs/heads/feature/evidence")


def test_unenumerable_archive_inventory_blocks_any_preservation_verdict(evidence_repo: Path, monkeypatch) -> None:
    _git(evidence_repo, "tag", "archive/feature-evidence-2026-09-01")
    assert _row(evidence_repo)["classification"] == "ARCHIVED / PRESERVED"
    _break(monkeypatch, "tag")
    report = gitutil.unmerged_remote_branches_missing_archive_tag(evidence_repo)
    assert report["archive_tag_enumeration"] == {
        "checked": False, "reason": "simulated git failure", "tag_count": None,
    }
    row = next(r for r in report["branches"] if r["ref"] == "refs/heads/feature/evidence")
    assert row["classification"] == "UNKNOWN"
    assert "archive tag inventory could not be enumerated" in row["reason"]
    assert row["archive_tag_exact_match"] is None


@pytest.mark.parametrize(
    ("tag_target", "expected_match", "expected_classification"),
    [
        (None, True, "ARCHIVED / PRESERVED"),
        ("main", False, "UNARCHIVED UNIQUE EVIDENCE — BLOCKER"),
    ],
)
def test_archive_tag_exact_match_distinguishes_existence_from_preservation(
    evidence_repo: Path, tag_target, expected_match, expected_classification
) -> None:
    tag = "archive/feature-evidence-2026-09-01"
    _git(evidence_repo, "tag", *( [tag] if tag_target is None else [tag, tag_target]))
    row = _row(evidence_repo)
    assert row["archive_tag_exact_match"] is expected_match
    assert row["classification"] == expected_classification


def test_archive_proof_line_shows_sha_evidence_and_mismatch() -> None:
    proof = _archive_proof(
        {
            "tip_sha": "a" * 40,
            "preserved_by": [],
            "matching_archive_tags": [
                {"tag": "archive/feature-evidence-2026-09-01", "sha": "b" * 40, "exact_tip_match": False}
            ],
        }
    )
    assert "tip aaaaaaaaaaaa" in proof
    assert "archive/feature-evidence-2026-09-01=bbbbbbbbbbbb MISMATCH" in proof
    assert "preserved_by: nothing" in proof


def test_preservation_headline_never_reports_zero_blockers_when_unchecked() -> None:
    unchecked = _preservation_headline({"checked": False, "reason": "boom", "flagged": [], "unknown": []})
    assert "UNKNOWN" in unchecked and "0" not in unchecked
    checked = _preservation_headline({"checked": True, "flagged": [{}], "unknown": []})
    assert "unpreserved branch blockers: 1; unknown: 0" in checked
