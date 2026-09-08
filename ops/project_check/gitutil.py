"""Read-only git/gh helpers shared by ops.project_check routines.

Every git call here goes through ``run_git``, which only accepts subcommands
in ``_ALLOWED_GIT_SUBCOMMANDS`` -- a defense-in-depth allowlist so a coding
mistake elsewhere in this package cannot accidentally commit, push, pull,
reset, rebase, checkout, delete a branch/worktree, drop a stash, or
create/delete a tag. All subprocess calls pass an argument list (never a
shell string), so there is no shell word-splitting to reason about even
under zsh.

``gh pr list`` (read-only) is used best-effort for PR status; if the ``gh``
binary is not on PATH, or the call fails/times out, PR data is reported as
unavailable rather than guessed.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_S = 6.0


@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _is_read_only_git_command(args: list[str]) -> bool:
    """Accept only command shapes used by this package's read-only calls.

    Several Git command families mix inspection and mutation. In particular,
    allowing the top-level ``worktree``, ``branch``, ``stash``, or ``tag``
    name would also admit remove/prune/delete operations. Keep those families
    pinned to their exact inspection forms.
    """
    if not args:
        return False
    command = args[0]
    if command in {"rev-parse", "status", "for-each-ref", "rev-list", "merge-base"}:
        return True
    if command == "diff":
        prefix = ["--no-ext-diff", "--no-textconv"]
        return (
            args[1:4] in ([*prefix, "--quiet"], [*prefix, "--name-only"])
            or args[1:5] == [*prefix, "--no-color", "--patch"]
        ) and not any(arg.startswith(("--output", "--ext-diff", "--textconv")) for arg in args[4:])
    if command == "patch-id":
        # Hashes a diff supplied on stdin; touches no ref, index or working tree.
        return args == ["patch-id", "--stable"]
    if command == "ls-remote":
        return args == ["ls-remote", "--heads", "origin", "refs/heads/main"]
    if command == "worktree":
        return args == ["worktree", "list", "--porcelain"]
    if command == "stash":
        return args == ["stash", "list"]
    if command == "tag":
        return args == ["tag", "-l", "archive/*"]
    if command == "branch":
        return (
            len(args) == 3
            and args[1] == "-r"
            and args[2].startswith("--format=")
        ) or (
            len(args) == 5
            and args[1:3] == ["-r", "--merged"]
            and args[4].startswith("--format=")
        )
    return False


def run_git_result(
    args: list[str], *, cwd: Path, timeout: float = DEFAULT_TIMEOUT_S, stdin_text: str | None = None
) -> GitCommandResult:
    """Run one exact read-only Git command and retain its return code."""
    if not _is_read_only_git_command(args):
        raise ValueError(f"git command shape not in read-only allowlist: {args!r}")
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", *args],
            cwd=str(cwd),
            input=stdin_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GitCommandResult(-1, stderr=str(exc))
    return GitCommandResult(
        result.returncode,
        stdout=result.stdout,
        stderr=(result.stderr or "").strip(),
    )


def run_git(args: list[str], *, cwd: Path, timeout: float = DEFAULT_TIMEOUT_S) -> tuple[str | None, str | None]:
    """Run a read-only git subcommand. Returns (stdout, error) -- never raises
    on a git-level failure (nonzero exit, timeout, missing binary)."""
    result = run_git_result(args, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        return None, result.stderr or f"git {' '.join(args)} exited {result.returncode}"
    return result.stdout, None


def repo_root(cwd: Path) -> Path | None:
    out, _err = run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    return Path(out.strip()) if out else None


def git_dir(root: Path) -> Path:
    """Keep bookkeeping per worktree, including when .git is a gitfile."""
    if not (root / ".git").is_file():
        return root / ".git"
    out, error = run_git(["rev-parse", "--absolute-git-dir"], cwd=root)
    if not out:
        raise OSError(error or "could not resolve worktree git directory")
    return Path(out.strip())


def current_branch(root: Path) -> str | None:
    out, _err = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    value = out.strip() if out else None
    return value if value and value != "HEAD" else (value or None)


def is_detached_head(root: Path) -> bool:
    out, _err = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    return (out or "").strip() == "HEAD"


def head_sha(root: Path) -> str | None:
    out, _err = run_git(["rev-parse", "HEAD"], cwd=root)
    return out.strip() if out else None


def upstream_of(root: Path, branch: str | None = None) -> str | None:
    ref = f"{branch}@{{upstream}}" if branch else "@{upstream}"
    out, _err = run_git(["rev-parse", "--abbrev-ref", ref], cwd=root)
    return out.strip() if out else None


def local_main_branch(root: Path) -> str | None:
    """Best-effort local default-branch name: prefer 'main', fall back to 'master'."""
    for candidate in ("main", "master"):
        out, _err = run_git(["rev-parse", "--verify", "--quiet", candidate], cwd=root)
        if out:
            return candidate
    return None


def remote_default_ref(root: Path, main_branch: str | None) -> str | None:
    """Best-effort 'origin/<main>' ref name, using only already-known remote-tracking refs (no fetch)."""
    candidates = [f"origin/{main_branch}"] if main_branch else []
    candidates += ["origin/main", "origin/master"]
    for ref in dict.fromkeys(candidates):
        out, _err = run_git(["rev-parse", "--verify", "--quiet", ref], cwd=root)
        if out:
            return ref
    return None


def ref_sha(root: Path, ref: str) -> str | None:
    out, _err = run_git(["rev-parse", ref], cwd=root)
    return out.strip() if out else None


def main_sync_state(root: Path) -> dict[str, Any]:
    """IN_SYNC / AHEAD / BEHIND / DIVERGED / UNKNOWN for local main vs origin/main.

    Deliberately does not fetch -- this reflects remote-tracking refs as of the
    last fetch (by this or another tool/operator), not live remote truth. Callers
    that need freshness should fetch separately and re-run.
    """
    main_branch = local_main_branch(root)
    if main_branch is None:
        return {
            "state": "UNKNOWN",
            "reason": "no local 'main' or 'master' branch ref found",
            "local_main_branch": None,
            "remote_ref": None,
            "ahead": None,
            "behind": None,
        }
    remote_ref = remote_default_ref(root, main_branch)
    if remote_ref is None:
        return {
            "state": "UNKNOWN",
            "reason": "no origin/main or origin/master remote-tracking ref found locally "
            "(never fetched in this clone, or remote branch renamed)",
            "local_main_branch": main_branch,
            "remote_ref": None,
            "ahead": None,
            "behind": None,
        }
    out, err = run_git(
        ["rev-list", "--left-right", "--count", f"{remote_ref}...{main_branch}"], cwd=root
    )
    if not out:
        return {
            "state": "UNKNOWN",
            "reason": err or "rev-list failed",
            "local_main_branch": main_branch,
            "remote_ref": remote_ref,
            "ahead": None,
            "behind": None,
        }
    parts = out.strip().split()
    if len(parts) != 2:
        return {
            "state": "UNKNOWN",
            "reason": f"unexpected rev-list output: {out!r}",
            "local_main_branch": main_branch,
            "remote_ref": remote_ref,
            "ahead": None,
            "behind": None,
        }
    behind, ahead = int(parts[0]), int(parts[1])
    if behind == 0 and ahead == 0:
        state = "IN_SYNC"
    elif behind == 0 and ahead > 0:
        state = "AHEAD"
    elif behind > 0 and ahead == 0:
        state = "BEHIND"
    else:
        state = "DIVERGED"
    return {
        "state": state,
        "reason": None,
        "local_main_branch": main_branch,
        "remote_ref": remote_ref,
        "ahead": ahead,
        "behind": behind,
        "as_of": "last local fetch of the remote-tracking ref (this tool does not fetch)",
    }


def status_porcelain(root: Path) -> dict[str, Any]:
    """Split `git status --porcelain=v1` into staged / unstaged (dirty tracked) / untracked."""
    out, err = run_git(["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=root)
    staged: list[str] = []
    dirty_tracked: list[str] = []
    untracked: list[str] = []
    if out is None:
        return {"staged": [], "dirty_tracked": [], "untracked": [], "error": err}
    records = iter(out.split("\0"))
    for line in records:
        if not line:
            continue
        code = line[:2]
        path = line[3:]
        if code == "??":
            untracked.append(path)
            continue
        index_state, worktree_state = code[0], code[1]
        if "R" in code or "C" in code:
            next(records, None)  # -z emits the rename/copy source separately.
        if index_state not in (" ", "?"):
            staged.append(path)
        if worktree_state not in (" ", "?"):
            dirty_tracked.append(path)
    return {"staged": staged, "dirty_tracked": dirty_tracked, "untracked": untracked, "error": None}


@dataclass(frozen=True)
class Worktree:
    path: str
    head: str | None
    branch: str | None
    bare: bool
    detached: bool
    locked: bool
    prunable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "head": self.head,
            "branch": self.branch,
            "bare": self.bare,
            "detached": self.detached,
            "locked": self.locked,
            "prunable": self.prunable,
        }


def worktrees(root: Path) -> list[Worktree]:
    out, _err = run_git(["worktree", "list", "--porcelain"], cwd=root)
    if not out:
        return []
    entries: list[Worktree] = []
    cur: dict[str, Any] = {}

    def flush() -> None:
        if cur.get("path"):
            entries.append(
                Worktree(
                    path=cur["path"],
                    head=cur.get("head"),
                    branch=cur.get("branch"),
                    bare=cur.get("bare", False),
                    detached=cur.get("detached", False),
                    locked=cur.get("locked", False),
                    prunable=cur.get("prunable", False),
                )
            )

    for line in out.splitlines():
        if not line.strip():
            flush()
            cur.clear()
            continue
        if line.startswith("worktree "):
            cur["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "bare":
            cur["bare"] = True
        elif line == "detached":
            cur["detached"] = True
        elif line.startswith("locked"):
            cur["locked"] = True
        elif line.startswith("prunable"):
            cur["prunable"] = True
    flush()
    return entries


def worktree_dirty(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_dir():
        return {"checked": False, "reason": "path not found"}
    actual_root = repo_root(p)
    if actual_root is None or actual_root.resolve() != p.resolve():
        return {"checked": False, "reason": "path is not the registered worktree root"}
    status = status_porcelain(p)
    if status.get("error"):
        return {"checked": False, "reason": status["error"]}
    dirty = bool(status["staged"] or status["dirty_tracked"] or status["untracked"])
    return {
        "checked": True,
        "dirty": dirty,
        "staged_count": len(status["staged"]),
        "dirty_tracked_count": len(status["dirty_tracked"]),
        "untracked_count": len(status["untracked"]),
        "staged": status["staged"],
        "dirty_tracked": status["dirty_tracked"],
        "untracked": status["untracked"],
    }


def worktree_inventory(root: Path) -> list[dict[str, Any]]:
    entries = []
    for worktree in worktrees(root):
        row = worktree.as_dict()
        row["dirty_status"] = worktree_dirty(worktree.path)
        if row["dirty_status"]["checked"] and head_sha(Path(worktree.path)) != worktree.head:
            row["dirty_status"] = {"checked": False, "reason": "HEAD changed during inspection"}
        entries.append(row)
    return entries


def _listing(root: Path, args: list[str]) -> tuple[list[str] | None, str | None]:
    """Split a read-only listing into lines, keeping "command failed" distinct
    from "nothing to list".

    Collapsing the two is a fail-open in a preservation routine: a stash, an
    archive tag, or a local branch that could not be enumerated would otherwise
    be reported identically to one that does not exist.
    """
    out, error = run_git(args, cwd=root)
    if out is None:
        return None, error or f"git {' '.join(args)} failed"
    return [line for line in out.splitlines() if line.strip()], None


def stash_inventory(root: Path) -> dict[str, Any]:
    """Stash listing that reports UNKNOWN rather than an empty list on failure."""
    lines, error = _listing(root, ["stash", "list"])
    if lines is None:
        return {"checked": False, "reason": error, "stashes": []}
    entries = []
    for line in lines:
        ref, _, message = line.partition(": ")
        entries.append({"ref": ref, "message": message})
    return {"checked": True, "reason": None, "stashes": entries}


def stash_list(root: Path) -> list[dict[str, Any]]:
    return stash_inventory(root)["stashes"]


def archive_tag_inventory(root: Path) -> dict[str, Any]:
    """Archive-tag listing with each tag dereferenced to its commit.

    ``sha`` is the dereferenced commit (annotated or lightweight); a tag that
    cannot be dereferenced keeps ``sha`` None so callers can refuse to treat it
    as proof that a tip is preserved.
    """
    lines, error = _listing(root, ["tag", "-l", "archive/*"])
    if lines is None:
        return {"checked": False, "reason": error, "tags": []}
    tags = []
    for name in lines:
        name = name.strip()
        sha = ref_sha(root, f"refs/tags/{name}^{{commit}}")
        tags.append({"tag": name, "sha": sha, "object_sha": ref_sha(root, f"refs/tags/{name}")})
    return {"checked": True, "reason": None, "tags": tags}


def archive_tags(root: Path) -> list[dict[str, Any]]:
    return archive_tag_inventory(root)["tags"]


def local_branch_inventory(root: Path) -> dict[str, Any]:
    """Local-branch listing that reports UNKNOWN rather than an empty list on failure.

    ``local_only`` and ``tracking_deleted_remote`` are descriptive facts only;
    neither is ever a disposability signal on its own.
    """
    lines, error = _listing(
        root,
        [
            "for-each-ref",
            "--format=%(refname:short)\t%(upstream:short)\t%(upstream:track)\t%(objectname)",
            "refs/heads/",
        ],
    )
    if lines is None:
        return {"checked": False, "reason": error, "branches": []}
    branches = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        name, upstream, track, sha = parts
        branches.append(
            {
                "branch": name,
                "upstream": upstream or None,
                "tracking_deleted_remote": track == "[gone]",
                "local_only": upstream == "",
                "sha": sha,
            }
        )
    return {"checked": True, "reason": None, "branches": branches}


def local_branches(root: Path) -> list[dict[str, Any]]:
    return local_branch_inventory(root)["branches"]


def remote_branches(root: Path) -> list[str]:
    out, _err = run_git(["branch", "-r", "--format=%(refname:short)"], cwd=root)
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip() and "->" not in line]


def merged_remote_branches(root: Path, into_ref: str) -> set[str]:
    out, _err = run_git(["branch", "-r", "--merged", into_ref, "--format=%(refname:short)"], cwd=root)
    if not out:
        return set()
    return {line.strip() for line in out.splitlines() if line.strip() and "->" not in line}


def unique_commit_count(root: Path, base_ref: str, branch_ref: str) -> int | None:
    out, _err = run_git(["rev-list", "--count", f"{base_ref}..{branch_ref}"], cwd=root)
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def _is_ancestor(root: Path, tip: str, target: str) -> bool | None:
    result = run_git_result(["merge-base", "--is-ancestor", tip, target], cwd=root)
    return result.returncode == 0 if result.returncode in (0, 1) else None


def _content_preserved(root: Path, tip: str, target: str, base_ref: str) -> bool | None:
    """Compare every path changed by the branch, including deletes and modes.

    Unrelated main changes do not defeat a squash match. A differing path is
    not proof of loss: a merged PR can also preserve it in main's history.
    """
    base, error = run_git(["merge-base", base_ref, tip], cwd=root)
    if error or not base:
        return None
    paths, error = run_git(
        ["diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", base.strip(), tip, "--"],
        cwd=root,
    )
    if error or paths is None:
        return None
    changed = [p for p in paths.split("\0") if p]
    if not changed:
        return True
    result = run_git_result(
        ["diff", "--no-ext-diff", "--no-textconv", "--quiet", tip, target, "--", *changed], cwd=root,
    )
    return result.returncode == 0 if result.returncode in (0, 1) else None


def _patch_identity(root: Path, diff_args: list[str]) -> str | None:
    """Normalized identity of one diff, or None when it cannot be computed.

    ``git patch-id --stable`` ignores hunk offsets and blob hashes, so the same
    change rebased onto a different base still hashes the same.
    """
    patch, error = run_git(diff_args, cwd=root)
    if error or not patch or not patch.strip():
        return None
    result = run_git_result(["patch-id", "--stable"], cwd=root, stdin_text=patch)
    if result.returncode != 0:
        return None
    fields = result.stdout.split("\n", 1)[0].split()
    return fields[0] if fields else None


def _patch_preserved_at_merge(root: Path, tip: str, merged: str, base_ref: str) -> bool | None:
    """Did the merge commit apply exactly this branch's patch?

    File-content equality is the wrong test for a squash merge whose branch was
    cut from an older base: another PR touching the same paths can land in
    between, so the merged content legitimately differs from the branch tip
    while the branch's own change is fully preserved. Compare the patches
    instead. A conflict-resolved or partially-applied merge does not match, and
    is left unproven rather than called preserved.
    """
    base, error = run_git(["merge-base", base_ref, tip], cwd=root)
    if error or not base:
        return None
    paths, error = run_git(
        ["diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", base.strip(), tip, "--"],
        cwd=root,
    )
    if error or paths is None:
        return None
    changed = [p for p in paths.split("\0") if p]
    if not changed:
        return True
    parent = ref_sha(root, f"{merged}^")
    if parent is None:
        return None
    patch_prefix = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--patch"]
    branch_id = _patch_identity(root, [*patch_prefix, base.strip(), tip, "--", *changed])
    merge_id = _patch_identity(root, [*patch_prefix, parent, merged, "--", *changed])
    if branch_id is None or merge_id is None:
        return None
    return branch_id == merge_id


def _content_twin_on_other_origin_branch(
    root: Path,
    tip: str,
    base_ref: str,
    tips: dict[str, str],
    own_ref: str,
    remote_ref: str | None,
) -> tuple[str | None, bool]:
    """First pushed origin branch whose content already holds this tip's changes.

    A branch that was rebased or re-created under a second name carries the same
    work at a different SHA, so ``--contains`` reachability never finds it even
    though the work is fully pushed. Compare content as a last resort, before
    concluding a tip is unarchived unique evidence.

    Returns ``(matching ref, some_comparison_failed)``. A failed comparison is
    never reported as "no twin found": the caller must leave preservation
    unproven rather than escalate on an unfinished check.
    """
    comparison_failed = False
    for ref in sorted(tips):
        if not ref.startswith("refs/remotes/origin/") or ref == own_ref or ref.endswith("/HEAD"):
            continue
        if remote_ref and ref == f"refs/remotes/{remote_ref}":
            continue  # main is already covered by the caller's own comparison
        match = _content_preserved(root, tip, tips[ref], base_ref)
        if match is True:
            return ref, comparison_failed
        if match is None:
            comparison_failed = True
    return None, comparison_failed


def unmerged_remote_branches_missing_archive_tag(root: Path) -> dict[str, Any]:
    """Preservation of committed local AND origin branch tips (legacy API name).

    Ancestry counts are never treated as unpushed-work proof after a squash.
    Dirty files, worktree ownership and stashes remain outside tip-preservation
    conclusions. No result here authorizes cleanup.
    """
    remote_ref = remote_default_ref(root, local_main_branch(root))
    main_tip = ref_sha(root, remote_ref) if remote_ref else None
    refs, error = run_git(
        ["for-each-ref", "--format=%(refname)\t%(objectname)", "refs/heads/", "refs/remotes/origin/"],
        cwd=root,
    )
    if refs is None:
        return {"checked": False, "reason": error, "flagged": [], "branches": [], "unknown": []}
    tips = dict(line.split("\t", 1) for line in refs.splitlines())
    archive_inventory = archive_tag_inventory(root)
    tags = archive_inventory["tags"]
    owners = worktrees(root)
    prs = None  # One bounded, read-only PR query, only if Git alone is insufficient.
    rows = []
    for ref, tip in tips.items():
        local = ref.startswith("refs/heads/")
        name = ref.removeprefix("refs/heads/") if local else ref.removeprefix("refs/remotes/origin/")
        if ref == f"refs/remotes/{remote_ref}" or name == "HEAD":
            continue
        origin_tip = tips.get(f"refs/remotes/origin/{name}")
        matching_tags = [t for t in tags if re.fullmatch(
            rf"archive/{re.escape(name.replace('/', '-'))}(?:-pr\d+)?-\d{{4}}-\d{{2}}-\d{{2}}", t["tag"]
        )]
        row = {
            "branch": name if local else f"origin/{name}", "ref": ref, "tip_sha": tip,
            "classification": "UNKNOWN", "reason": "preservation not verified",
            "worktree_owners": [w.path for w in owners if w.branch == name or (
                w.branch and w.branch.casefold() == name.casefold() and w.head == tip
            )],
            "ahead_of_origin_branch": unique_commit_count(root, origin_tip, tip) if local and origin_tip else None,
            "matching_archive_tags": [{**t, "exact_tip_match": t["sha"] == tip} for t in matching_tags],
            # None: no name-matching tag (or the tag inventory is unknown).
            # False: a name-matching archive tag exists but its dereferenced
            # commit is NOT this tip -- existence alone never proves preservation.
            "archive_tag_exact_match": (
                None if not archive_inventory["checked"] or not matching_tags
                else any(t["sha"] == tip for t in matching_tags)
            ),
            "preserved_by": [], "pr_status": "NOT_QUERIED",
        }
        contains, contains_error = run_git(
            ["for-each-ref", f"--contains={tip}", "--format=%(refname)", "refs/remotes/origin/", "refs/tags/archive/"],
            cwd=root,
        )
        containing = set((contains or "").splitlines())
        ancestry = _is_ancestor(root, tip, main_tip) if main_tip else None
        main_contains = f"refs/remotes/{remote_ref}" in containing if remote_ref else None
        archives = [f"refs/tags/{t['tag']}" for t in tags if t["sha"] and f"refs/tags/{t['tag']}" in containing]
        other_origins = sorted(r for r in containing if r.startswith("refs/remotes/origin/") and r != ref and not r.endswith("/HEAD"))
        if contains_error or (main_tip and (ancestry is None or ancestry != main_contains)):
            row["reason"] = contains_error or "independent main ancestry checks failed or disagree"
        elif ancestry:
            row.update(classification="REDUNDANT", reason="tip is reachable from main", preserved_by=[remote_ref])
        elif not archive_inventory["checked"]:
            row["reason"] = f"archive tag inventory could not be enumerated: {archive_inventory['reason']}"
        elif any(r.startswith("refs/tags/archive/") and r not in archives for r in containing):
            row["reason"] = "archive inventory could not be verified against containing refs"
        elif archives or other_origins:
            row.update(classification="ARCHIVED / PRESERVED", reason="tip is reachable from an archive or another origin ref", preserved_by=archives + other_origins)
        elif not main_tip:
            row["reason"] = "no main ref available for content comparison"
        elif any(t["sha"] is None for t in tags):
            row["reason"] = "an archive tag could not be dereferenced to a commit"
        else:
            if prs is None:
                prs = open_prs(root, state="all")
            pr = pr_status_for_branch(root, name, prs=prs, tip_sha=tip)
            row["pr_status"] = pr["status"]
            row["pr"] = pr.get("pr")
            equivalent = _content_preserved(root, tip, main_tip, main_tip)
            row["content_equivalent_on_main"] = equivalent
            if pr["status"] == "MERGED":
                merged = (pr.get("pr", {}).get("mergeCommit") or {}).get("oid")
                merge_reachable = _is_ancestor(root, merged, main_tip) if merged else None
                historical = _content_preserved(root, tip, merged, main_tip) if merge_reachable else None
                row["content_preserved_at_merge"] = historical
                # Only when content equality is inconclusive: a squash merge of a
                # branch cut from an older base preserves the work as a patch, not
                # as identical file content.
                patch_match = (
                    _patch_preserved_at_merge(root, tip, merged, main_tip)
                    if merge_reachable is True and equivalent is not True and historical is not True
                    else None
                )
                row["patch_preserved_at_merge"] = patch_match
                if merge_reachable is True and (equivalent is True or historical is True):
                    row.update(classification="REDUNDANT", reason="matching MERGED PR and content preserved on main", preserved_by=[merged])
                elif merge_reachable is True and patch_match is True:
                    row.update(classification="REDUNDANT", reason="matching MERGED PR whose merge commit applied this branch's exact patch", preserved_by=[merged])
                else:
                    row["reason"] = "MERGED PR alone is insufficient; main/content preservation is unverified"
            elif pr["status"] == "UNKNOWN":
                row["reason"] = pr.get("reason") or "PR state is unavailable or ambiguous"
            elif equivalent is True:
                row.update(classification="REDUNDANT", reason="all branch-changed paths equal main", preserved_by=[main_tip])
            elif equivalent is False and pr["status"] in {"CLOSED", "NO_PR_FOUND"}:
                # Last resort before escalating: the same work may already be
                # pushed under another branch name at a different SHA.
                twin, twin_comparison_failed = _content_twin_on_other_origin_branch(
                    root, tip, main_tip, tips, ref, remote_ref
                )
                row["content_twin_origin_ref"] = twin
                if twin:
                    row.update(classification="ARCHIVED / PRESERVED", reason="all branch-changed paths equal another origin branch", preserved_by=[twin])
                elif twin_comparison_failed:
                    row["reason"] = "content comparison against another origin branch could not be completed; preservation unverified"
                else:
                    row.update(classification="UNARCHIVED UNIQUE EVIDENCE — BLOCKER", reason="tip has no preserving ref and changed paths differ from main and from every other origin branch; preserve evidence or review supersession before cleanup")
            else:
                row["reason"] = "active PR or content comparison requires review"
        if ref_sha(root, ref) != tip:
            row.update(classification="UNKNOWN", reason="branch tip changed during inspection")
        row["worktree_ownership_checked"] = bool(owners)
        row["cleanup_blocked"] = not owners or bool(row["worktree_owners"]) or row["classification"] not in {"REDUNDANT", "ARCHIVED / PRESERVED"}
        rows.append(row)
    return {
        "checked": True, "reason": None, "remote_ref": remote_ref, "branches": rows,
        "archive_tag_enumeration": {
            "checked": archive_inventory["checked"], "reason": archive_inventory["reason"],
            "tag_count": len(tags) if archive_inventory["checked"] else None,
        },
        "flagged": [r for r in rows if r["classification"] == "UNARCHIVED UNIQUE EVIDENCE — BLOCKER"],
        "unknown": [r for r in rows if r["classification"] == "UNKNOWN"],
        "note": "Committed tips only, using cached Git refs. Dirty/untracked files and stashes are NOT covered; no cleanup is authorized.",
    }


def gh_available() -> bool:
    return shutil.which("gh") is not None


def open_prs(root: Path, *, timeout: float = 10.0, state: str = "open") -> dict[str, Any]:
    """Best-effort `gh pr list` (read-only). Reports unavailable rather than guessing."""
    if not gh_available():
        return {"available": False, "reason": "gh CLI not found on PATH", "prs": []}
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                state,
                "--json",
                "number,title,headRefName,headRefOid,baseRefName,isCrossRepository,mergeCommit,state,url,isDraft,updatedAt",
                "--limit",
                "1000" if state == "all" else "50",
            ],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": f"gh pr list failed: {exc}", "prs": []}
    if result.returncode != 0:
        return {
            "available": False,
            "reason": (result.stderr or "").strip() or f"gh exited {result.returncode}",
            "prs": [],
        }
    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"available": False, "reason": f"could not parse gh output: {exc}", "prs": []}
    if not isinstance(prs, list) or not all(isinstance(pr, dict) for pr in prs):
        return {"available": False, "reason": "unexpected gh output shape", "prs": []}
    return {"available": True, "reason": None, "prs": prs, "complete": len(prs) < (1000 if state == "all" else 50)}


def pr_status_for_branch(
    root: Path, branch: str, *, timeout: float = 10.0,
    prs: dict | None = None, tip_sha: str | None = None,
) -> dict[str, Any]:
    """Use only an unambiguous PR for the exact tip; branch names can be reused."""
    if prs is None:
        prs = open_prs(root, timeout=timeout, state="all")
    if not prs.get("available"):
        return {"available": False, "status": "UNKNOWN", "reason": prs.get("reason")}
    rows = [pr for pr in prs["prs"] if pr.get("headRefName") == branch and not pr.get("isCrossRepository")]
    # Replacement PRs can merge the exact head of a closed original under a
    # different branch name. Verify by SHA, never by title or name similarity.
    replacements = [pr for pr in prs["prs"] if tip_sha and pr.get("headRefOid") == tip_sha
                    and pr.get("state") == "MERGED" and pr.get("baseRefName") == "main"
                    and pr.get("headRefName") != branch and not pr.get("isCrossRepository")]
    if len(replacements) == 1:
        return {"available": True, "status": "MERGED", "pr": replacements[0]}
    matches = [pr for pr in rows if pr.get("headRefOid") == tip_sha] if tip_sha else rows
    if len(matches) == 1:
        return {"available": True, "status": matches[0].get("state", "UNKNOWN"), "pr": matches[0]}
    if rows or not prs.get("complete"):
        return {"available": True, "status": "UNKNOWN", "reason": "PR heads disagree with this tip, are ambiguous, or the PR inventory is incomplete"}
    return {"available": True, "status": "NO_PR_FOUND"}
