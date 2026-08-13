"""Manual, read-only ownership preflight for research and promotion work.

Run before beginning a new research pass or preparing a promotion:

    python -m ops.project_check research
    python -m ops.project_check promotion

The check answers three narrow repository questions:

* Does this worktree already contain staged or untracked evidence?
* Does Git's worktree registry give a branch to more than one worktree?
* Does the local ``origin/main`` ref still match the remote, and does HEAD
  contain that verified base?

It deliberately does not replace ``ops.live_box_guard``, the deploy lock,
the behavior-neutral release gate, strategy evidence gates, or journal
reconciliation. Those guards protect different boundaries. This module does
not fetch, write a checkpoint, modify refs, or touch runtime/broker state.
Every subprocess is an explicitly allowlisted read-only Git command.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


_READ_ONLY_GIT_SUBCOMMANDS = frozenset(
    {"ls-remote", "merge-base", "rev-parse", "status", "worktree"}
)


@dataclass(frozen=True)
class GitResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _git(root: Path, *args: str, timeout: float = 8.0) -> GitResult:
    """Run an allowlisted read-only Git command without raising."""
    if not args or args[0] not in _READ_ONLY_GIT_SUBCOMMANDS:
        raise ValueError(f"refusing non-read-only git subcommand: {args[:1]!r}")
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GitResult(-1, stderr=str(exc))
    return GitResult(
        result.returncode,
        stdout=result.stdout.rstrip("\n"),
        stderr=result.stderr.rstrip("\n"),
    )


def discover_repo_root(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    result = _git(candidate, "rev-parse", "--show-toplevel")
    if result.returncode != 0 or not result.stdout:
        raise ValueError(f"not a Git worktree: {candidate}")
    return Path(result.stdout).resolve()


def _parse_status_z(raw: str) -> dict[str, list[str]]:
    """Parse ``git status --porcelain=v1 -z`` without losing odd paths."""
    staged: list[str] = []
    untracked: list[str] = []
    records = raw.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            continue
        state, path = record[:2], record[3:]
        if state == "??":
            untracked.append(path)
            continue
        if state[0] not in {" ", "?"}:
            staged.append(path)
        # In -z mode a rename/copy has a second NUL-delimited source path.
        if "R" in state or "C" in state:
            index += 1
    return {"staged": sorted(staged), "untracked": sorted(untracked)}


def current_worktree_evidence(root: Path) -> dict[str, Any]:
    """Return only staged/untracked evidence from ``root`` itself."""
    result = _git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    if result.returncode != 0:
        return {
            "ok": False,
            "staged": [],
            "untracked": [],
            "error": result.stderr or "git status failed",
        }
    evidence = _parse_status_z(result.stdout)
    return {"ok": True, **evidence}


def _parse_worktrees(raw: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in raw.splitlines():
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            if current:
                entries.append(current)
            current = {"path": value, "prunable": False}
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "HEAD":
            current["head"] = value
        elif key == "detached":
            current["detached"] = True
        elif key == "prunable":
            current["prunable"] = True
            current["prunable_reason"] = value
    if current:
        entries.append(current)
    return entries


def registered_worktrees(root: Path) -> dict[str, Any]:
    result = _git(root, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        return {
            "ok": False,
            "worktrees": [],
            "error": result.stderr or "git worktree list failed",
        }
    return {"ok": True, "worktrees": _parse_worktrees(result.stdout)}


def duplicate_branch_owners(
    worktrees: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return branches assigned to multiple distinct registered paths."""
    owners: dict[str, set[str]] = defaultdict(set)
    prunable_paths: set[str] = set()
    for worktree in worktrees:
        branch = worktree.get("branch")
        path = worktree.get("path")
        if not branch or not path:
            continue
        resolved = str(Path(path).resolve())
        owners[str(branch)].add(resolved)
        if worktree.get("prunable"):
            prunable_paths.add(resolved)
    return [
        {
            "branch": branch,
            "paths": sorted(paths),
            "includes_prunable_registration": bool(paths & prunable_paths),
        }
        for branch, paths in sorted(owners.items())
        if len(paths) > 1
    ]


def worktree_ownership(root: Path) -> dict[str, Any]:
    registry = registered_worktrees(root)
    branch_result = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    top_result = _git(root, "rev-parse", "--show-toplevel")
    if not registry["ok"] or branch_result.returncode != 0 or top_result.returncode != 0:
        errors = [registry.get("error")]
        if branch_result.returncode != 0:
            errors.append(branch_result.stderr or "cannot resolve current branch")
        if top_result.returncode != 0:
            errors.append(top_result.stderr or "cannot resolve current worktree")
        return {
            "ok": False,
            "current_branch": None,
            "current_worktree": None,
            "duplicates": [],
            "error": "; ".join(error for error in errors if error),
        }

    current_branch = branch_result.stdout
    current_path = str(Path(top_result.stdout).resolve())
    worktrees = registry["worktrees"]
    duplicates = duplicate_branch_owners(worktrees)
    current_registration = next(
        (
            item
            for item in worktrees
            if item.get("path")
            and str(Path(item["path"]).resolve()) == current_path
        ),
        None,
    )
    errors: list[str] = []
    if current_registration is None:
        errors.append("current worktree is absent from `git worktree list`")
    elif current_branch != "HEAD" and current_registration.get("branch") != current_branch:
        errors.append(
            "current worktree registration does not own the checked-out branch"
        )

    return {
        "ok": not errors,
        "current_branch": current_branch,
        "current_worktree": current_path,
        "duplicates": duplicates,
        "worktrees": worktrees,
        "error": "; ".join(errors) if errors else None,
    }


def _sha_from_ls_remote(output: str) -> str | None:
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == "refs/heads/main":
            return fields[0]
    return None


def origin_main_assumption(root: Path) -> dict[str, Any]:
    """Verify local origin/main against the remote without updating refs."""
    local = _git(
        root,
        "rev-parse",
        "--verify",
        "refs/remotes/origin/main^{commit}",
    )
    remote = _git(root, "ls-remote", "--heads", "origin", "refs/heads/main")
    local_sha = local.stdout if local.returncode == 0 and local.stdout else None
    remote_sha = _sha_from_ls_remote(remote.stdout) if remote.returncode == 0 else None

    if local_sha is None:
        freshness = "MISSING_LOCAL_REF"
        detail = "local refs/remotes/origin/main is missing"
    elif remote.returncode != 0:
        freshness = "UNVERIFIED"
        detail = remote.stderr or "origin/main could not be read from origin"
    elif remote_sha is None:
        freshness = "MISSING_REMOTE_REF"
        detail = "origin did not advertise refs/heads/main"
    elif local_sha != remote_sha:
        freshness = "STALE"
        detail = "local origin/main differs from the branch currently advertised by origin"
    else:
        freshness = "CURRENT"
        detail = "local origin/main matches origin"

    head_contains_base: bool | None = None
    ancestry_detail = "not checked because origin/main was not verified current"
    if freshness == "CURRENT":
        ancestry = _git(
            root,
            "merge-base",
            "--is-ancestor",
            "refs/remotes/origin/main",
            "HEAD",
        )
        if ancestry.returncode == 0:
            head_contains_base = True
            ancestry_detail = "HEAD contains the verified origin/main"
        elif ancestry.returncode == 1:
            head_contains_base = False
            ancestry_detail = "HEAD does not contain the verified origin/main"
        else:
            ancestry_detail = ancestry.stderr or "branch ancestry could not be verified"

    return {
        "freshness": freshness,
        "local_sha": local_sha,
        "remote_sha": remote_sha,
        "detail": detail,
        "head_contains_verified_base": head_contains_base,
        "ancestry_detail": ancestry_detail,
    }


def project_check_report(
    purpose: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    if purpose not in {"research", "promotion"}:
        raise ValueError(f"unsupported purpose: {purpose!r}")
    repo_root = discover_repo_root(root)
    evidence = current_worktree_evidence(repo_root)
    ownership = worktree_ownership(repo_root)
    base = origin_main_assumption(repo_root)

    blockers: list[str] = []
    if not evidence["ok"]:
        blockers.append(f"current-worktree evidence could not be inspected: {evidence['error']}")
    else:
        if evidence["staged"]:
            blockers.append(
                "current worktree already has staged evidence: "
                + ", ".join(evidence["staged"])
            )
        if evidence["untracked"]:
            blockers.append(
                "current worktree already has untracked evidence: "
                + ", ".join(evidence["untracked"])
            )

    if not ownership["ok"]:
        blockers.append(f"worktree ownership could not be verified: {ownership['error']}")
    for duplicate in ownership["duplicates"]:
        blockers.append(
            f"branch {duplicate['branch']!r} is registered to multiple worktrees: "
            + ", ".join(duplicate["paths"])
        )

    if base["freshness"] != "CURRENT":
        blockers.append(
            f"origin/main assumption is {base['freshness']}: {base['detail']}; "
            "refresh origin/main explicitly, then rerun this check"
        )
    elif base["head_contains_verified_base"] is not True:
        blockers.append(base["ancestry_detail"])

    return {
        "routine": "project-check",
        "purpose": purpose,
        "read_only": True,
        "bookkeeping_writes": [],
        "repo_root": str(repo_root),
        "ok": not blockers,
        "blockers": blockers,
        "current_worktree_evidence": evidence,
        "worktree_ownership": ownership,
        "origin_main": base,
    }


def format_report(report: dict[str, Any]) -> str:
    state = "PASS" if report["ok"] else "BLOCKED"
    evidence = report["current_worktree_evidence"]
    ownership = report["worktree_ownership"]
    base = report["origin_main"]
    local_sha = (base["local_sha"] or "unknown")[:12]
    remote_sha = (base["remote_sha"] or "unknown")[:12]
    lines = [
        f"PROJECT CHECK -- {report['purpose'].upper()} -- {state}",
        f"repo: {report['repo_root']}",
        "mode: read-only (no local bookkeeping writes)",
        f"current worktree: {ownership.get('current_worktree') or 'unknown'}",
        f"current branch: {ownership.get('current_branch') or 'unknown'}",
        f"staged evidence: {evidence.get('staged') or 'none'}",
        f"untracked evidence: {evidence.get('untracked') or 'none'}",
        f"duplicate branch owners: {len(ownership.get('duplicates') or [])}",
        f"origin/main: {base['freshness']} (local={local_sha}, remote={remote_sha})",
        f"HEAD contains verified base: {base['head_contains_verified_base']}",
    ]
    if report["blockers"]:
        lines.append("blockers:")
        lines.extend(f"  - {reason}" for reason in report["blockers"])
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a manual, read-only ownership preflight before new work."
    )
    parser.add_argument("purpose", choices=("research", "promotion"))
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    try:
        report = project_check_report(args.purpose)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else format_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
