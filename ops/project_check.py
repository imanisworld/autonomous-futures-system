"""Manually-invoked repo/process safety routines for the autonomous futures system.

Three routines, one entry point (``python -m ops.project_check <subcommand>``):

  session-start   -- git/worktree/branch report + best-effort runtime snapshot;
                      writes a local checkpoint used by ``precommit``.
  precommit       -- read-only, fail-closed diff against the session-start
                      checkpoint. Never mutates git state.
  promotion       -- strategy promotion proof-gate report (--strategy NAME).
                      Refuses to call a strategy VALIDATED on research/backtest
                      numbers alone; requires real paper-forward journal fills.
  daily           -- daily repo/process reconciliation + trade-chain integrity
                      (PR/branch/evidence hygiene, deployed-state drift,
                      strategy-inventory-vs-runtime drift, trade-chain audit).

Every subcommand is read-only against git, the broker, and the journal: none
of them commit, push, pull, fetch by default, reset, rebase, checkout, switch,
cherry-pick, delete branches/worktrees, drop stashes, create/delete tags,
cancel orders, flatten positions, or repair the journal. The only writes this
module performs are its own local checkpoint files under ``logs/`` (gitignored):
``.project_check_session.json`` and ``.project_check_daily_checkpoint.json``.

This module composes existing read-only machinery rather than duplicating it:
  - ops.live_box_guard.live_box_drift_report / ops.release_manifest.build_release_manifest
    for deployed-state / runtime-override snapshots.
  - ops.evidence_readiness.build_evidence_readiness for the evidence window.
  - adaptive.journal_reader.JournalReader for identity-joined (paper_order_id)
    trade/decision records -- the same join adaptive's committee agents use,
    not a fresh FIFO pairing.

All subprocess calls to git pass argument lists (never a shell string), so
there is no bash/zsh word-splitting hazard regardless of the invoking shell.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SESSION_CHECKPOINT_PATH = ROOT / "logs" / ".project_check_session.json"
DAILY_CHECKPOINT_PATH = ROOT / "logs" / ".project_check_daily_checkpoint.json"

SENSITIVE_PATH_MARKERS = (
    ".env", "id_rsa", "id_ed25519", ".pem", "hetzner-futures", "credentials",
)

STALE_PR_DAYS = 14


# ─────────────────────────────────────────── git helpers (read-only only) ──

def _git(root: Path, *args: str, timeout: float = 15.0) -> Optional[str]:
    """Run a read-only git subprocess. Returns stripped stdout, or None on any failure."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _git_ok(root: Path, *args: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _git_lines(root: Path, *args: str) -> list[str]:
    out = _git(root, *args)
    if not out:
        return []
    return [line for line in out.split("\n") if line.strip()]


def _git_status_porcelain_raw(root: Path) -> str:
    """`git status --porcelain` with only the trailing newline removed.

    Porcelain status lines carry meaning in LEADING whitespace (e.g. " M path"
    -- unmodified index, modified worktree). A plain `.strip()` on the whole
    blob corrupts exactly the first line whenever it starts with a space, so
    this must never route through the general-purpose `_git()` helper.
    """
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root), capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.rstrip("\n")


def _status_breakdown(root: Path) -> tuple[list[str], list[str], list[str]]:
    """Return (dirty_tracked, staged, untracked) from `git status --porcelain`."""
    out = _git_status_porcelain_raw(root)
    dirty: list[str] = []
    staged: list[str] = []
    untracked: list[str] = []
    for line in out.split("\n"):
        if not line.strip():
            continue
        code, path = line[:2], line[3:]
        if code.startswith("??"):
            untracked.append(path)
            continue
        if code[0] not in (" ",):
            staged.append(path)
        if code[1] not in (" ",):
            dirty.append(path)
    return dirty, staged, untracked


def _upstream_ahead_behind(root: Path, branch: Optional[str]) -> tuple[Optional[str], Optional[int], Optional[int]]:
    upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not upstream:
        return None, None, None
    counts = _git(root, "rev-list", "--left-right", "--count", f"{upstream}...HEAD")
    if not counts:
        return upstream, None, None
    parts = counts.split()
    if len(parts) != 2:
        return upstream, None, None
    behind_str, ahead_str = parts
    try:
        return upstream, int(ahead_str), int(behind_str)
    except ValueError:
        return upstream, None, None


def _local_main_relationship(root: Path) -> dict[str, Any]:
    local_main = _git(root, "rev-parse", "refs/heads/main")
    origin_main = _git(root, "rev-parse", "origin/main")
    if origin_main is None:
        return {"status": "UNKNOWN", "reason": "origin/main not resolvable locally"}
    if local_main is None:
        return {"status": "UNKNOWN", "reason": "no local 'main' branch"}
    if local_main == origin_main:
        return {"status": "IN_SYNC", "local_main_sha": local_main, "origin_main_sha": origin_main}
    counts = _git(root, "rev-list", "--left-right", "--count", "origin/main...refs/heads/main")
    if not counts:
        return {"status": "UNKNOWN", "reason": "rev-list failed"}
    parts = counts.split()
    if len(parts) != 2:
        return {"status": "UNKNOWN", "reason": "unexpected rev-list output"}
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return {"status": "UNKNOWN", "reason": "non-integer rev-list output"}
    if ahead and behind:
        status = "DIVERGED"
    elif ahead:
        status = "AHEAD"
    elif behind:
        status = "BEHIND"
    else:
        status = "IN_SYNC"
    return {
        "status": status,
        "ahead": ahead,
        "behind": behind,
        "local_main_sha": local_main,
        "origin_main_sha": origin_main,
    }


def _list_worktrees(root: Path) -> list[dict[str, Any]]:
    out = _git(root, "worktree", "list", "--porcelain") or ""
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in out.split("\n"):
        if not line.strip():
            if current:
                worktrees.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].replace("refs/heads/", "")
        elif line == "detached":
            current["branch"] = None
            current["detached"] = True
        elif line == "bare":
            current["bare"] = True
        elif line.startswith("locked"):
            current["locked"] = True
    if current:
        worktrees.append(current)

    for wt in worktrees:
        path = Path(wt.get("path", ""))
        if path.exists():
            status = _git_status_porcelain_raw(path)
            wt["dirty"] = bool(status)
            wt["dirty_files"] = [line for line in status.split("\n") if line.strip()]
        else:
            wt["dirty"] = None
            wt["dirty_files"] = []
            wt["missing"] = True
    return worktrees


def _local_branches(root: Path) -> list[dict[str, Any]]:
    fmt = "%(refname:short)%09%(upstream:short)%09%(upstream:track)%09%(objectname)"
    out = _git(root, "for-each-ref", f"--format={fmt}", "refs/heads/") or ""
    branches: list[dict[str, Any]] = []
    for line in out.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        name = parts[0] if len(parts) > 0 else ""
        upstream = parts[1] if len(parts) > 1 else ""
        track = parts[2] if len(parts) > 2 else ""
        sha = parts[3] if len(parts) > 3 else ""
        branches.append({
            "name": name,
            "sha": sha,
            "upstream": upstream or None,
            "tracking_deleted_remote": "gone" in track,
            "local_only": not upstream,
        })
    return branches


def _archive_tags(root: Path) -> list[str]:
    return _git_lines(root, "tag", "-l", "archive/*")


def _stash_list(root: Path) -> list[str]:
    return _git_lines(root, "stash", "list")


def _unmerged_branches_missing_archive(
    root: Path, branches: list[dict[str, Any]], archive_tags: list[str],
    *, current_branch: Optional[str] = None,
) -> dict[str, Any]:
    """Best-effort, LOCAL-ONLY proxy for 'closed-unmerged branches with unique
    evidence but no archive tag'. Cannot see GitHub's closed/merged state
    directly (that needs `gh`); this only sees local git history vs origin/main.

    Excludes the currently checked-out branch: this check is about ABANDONED
    work that could be lost, and the active branch is (by definition) not
    abandoned -- flagging it would just be noise on every invocation.
    """
    if _git(root, "rev-parse", "origin/main") is None:
        return {"status": "UNKNOWN", "reason": "origin/main not resolvable locally"}

    archive_tag_shas: set[str] = set()
    for tag in archive_tags:
        sha = _git(root, "rev-list", "-n", "1", tag)
        if sha:
            archive_tag_shas.add(sha)

    merged = set(_git_lines(
        root, "for-each-ref", "--merged=origin/main", "--format=%(refname:short)", "refs/heads/",
    ))

    results = []
    for b in branches:
        name = b["name"]
        if name == "main" or name == current_branch or name in merged:
            continue
        count_str = _git(root, "rev-list", "--count", f"origin/main..{name}")
        try:
            unique_commits = int(count_str) if count_str is not None else None
        except ValueError:
            unique_commits = None
        if unique_commits is None:
            flag = "UNKNOWN"
        else:
            protected = b["sha"] in archive_tag_shas
            has_unique = unique_commits > 0
            flag = "BLOCKER" if (has_unique and not protected) else "OK"
        results.append({
            "branch": name,
            "unique_commits_vs_origin_main": unique_commits,
            "archive_tag_protects_tip": b["sha"] in archive_tag_shas,
            "flag": flag,
        })
    return {"status": "OK", "branches": results}


def _pr_status_via_gh(root: Path) -> Any:
    """Best-effort PR lookup via the `gh` CLI. UNKNOWN (string) if unavailable --
    never invented. This is separate from this session's own GitHub MCP access,
    which is not available to a plain script invocation.
    """
    gh = shutil.which("gh")
    if not gh:
        return "UNKNOWN (gh CLI not found on PATH)"
    try:
        proc = subprocess.run(
            [gh, "pr", "list", "--state", "all", "--limit", "100",
             "--json", "number,title,url,state,headRefName,createdAt,mergedAt,closedAt,updatedAt"],
            cwd=str(root), capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"UNKNOWN (gh pr list raised {exc})"
    if proc.returncode != 0:
        return f"UNKNOWN (gh pr list exited {proc.returncode}: {proc.stderr.strip()[:200]})"
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return f"UNKNOWN (gh pr list produced invalid JSON: {exc})"


# ───────────────────────────────────────────── shared repo-state collector ──

def collect_git_state(root: Path, *, fetch: bool = False) -> dict[str, Any]:
    warnings: list[str] = []
    if fetch:
        try:
            proc = subprocess.run(
                ["git", "fetch", "origin", "main", "--quiet"],
                cwd=str(root), capture_output=True, text=True, timeout=30,
            )
            if proc.returncode != 0:
                warnings.append(f"git fetch origin main failed: {proc.stderr.strip()[:200]}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"git fetch origin main raised: {exc}")

    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    head_sha = _git(root, "rev-parse", "HEAD")
    current_worktree = _git(root, "rev-parse", "--show-toplevel")
    upstream, ahead, behind = _upstream_ahead_behind(root, branch)
    dirty, staged, untracked = _status_breakdown(root)
    branches = _local_branches(root)
    worktrees = _list_worktrees(root)
    archive_tags = _archive_tags(root)

    return {
        "repo_root": str(root),
        "current_branch": branch,
        "head_sha": head_sha,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "local_main_relationship": _local_main_relationship(root),
        "current_worktree": current_worktree,
        "worktrees": worktrees,
        "dirty_tracked_files": dirty,
        "staged_files": staged,
        "untracked_files": untracked,
        "branches_tracking_deleted_remotes": [b["name"] for b in branches if b["tracking_deleted_remote"]],
        "local_only_branches": [b["name"] for b in branches if b["local_only"]],
        "local_branches": branches,
        "archive_tags": archive_tags,
        "stash": _stash_list(root),
        "warnings": warnings,
        "fetched": fetch,
    }


# ───────────────────────────────────────────────────── runtime snapshot ──

def _load_risk_rules(root: Path) -> dict[str, Any]:
    import yaml
    path = root / "risk_rules.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _active_paper_forward_lanes(root: Path) -> dict[str, Any]:
    try:
        rules = _load_risk_rules(root)
    except (OSError, Exception) as exc:  # noqa: BLE001 - report, never raise
        return {"error": str(exc)}
    gate = rules.get("strategy_permission_gate") or {}
    statuses = gate.get("strategy_status") or {}
    instruments = (rules.get("instruments") or {}).get("allowed") or []
    lanes = []
    for name, status in statuses.items():
        if status != "PAPER_ELIGIBLE":
            continue
        tolerance = {"default": os.getenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS") or "UNKNOWN"}
        for inst in instruments:
            tolerance[inst] = os.getenv(f"ENTRY_SLIPPAGE_TOLERANCE_TICKS_{inst}") or "UNKNOWN"
        lanes.append({
            "strategy": name,
            "permission_status": status,
            "allowed_instruments": instruments,
            "entry_fill_model": os.getenv("ENTRY_FILL_MODEL") or "UNKNOWN",
            "tradovate_entry_execution_mode": os.getenv("TRADOVATE_ENTRY_EXECUTION_MODE") or "UNKNOWN",
            "entry_tolerance_ticks": tolerance,
            "exit_mode": os.getenv("EXIT_MODE") or "UNKNOWN",
            "quantity_contract_cap": os.getenv("MAX_CONTRACTS_HARD_CAP") or "UNKNOWN",
        })
    return {
        "gate_enabled": gate.get("enabled"),
        "default_status": gate.get("default_status", "UNKNOWN"),
        "paper_eligible_lanes": lanes,
        "note": (
            "Entry fill model / tolerance env vars are instrument-scoped, not "
            "per-strategy-lane in this codebase; reported per active lane x "
            "allowed instrument rather than invented as a per-lane value."
        ),
    }


def runtime_snapshot(root: Path) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    try:
        from ops.live_box_guard import live_box_drift_report
        snapshot["live_box_guard"] = live_box_drift_report(repo_root=root)
    except Exception as exc:  # noqa: BLE001 - never let a snapshot section crash the routine
        snapshot["live_box_guard"] = {"error": str(exc)}
    try:
        from ops.release_manifest import build_release_manifest
        snapshot["release_manifest"] = build_release_manifest(root)
    except Exception as exc:  # noqa: BLE001
        snapshot["release_manifest"] = {"error": str(exc)}
    snapshot["active_paper_forward_lanes"] = _active_paper_forward_lanes(root)
    snapshot["evidence_epoch"] = (
        "UNKNOWN -- no 'evidence epoch' concept exists in ops/*.py; nearest "
        "analogue is the rolling evidence-readiness window below."
    )
    try:
        from ops.evidence_readiness import build_evidence_readiness
        snapshot["evidence_readiness_window"] = build_evidence_readiness(log_dir=str(root / "logs"))
    except Exception as exc:  # noqa: BLE001
        snapshot["evidence_readiness_window"] = {"error": str(exc)}
    return snapshot


# ───────────────────────────────────────────────────────────── session-start ──

def _render_session_start(report: dict[str, Any]) -> str:
    repo = report["repo"]
    lines = [
        f"SESSION START  ({report['checked_at']})",
        f"repo_root: {repo['repo_root']}",
        f"branch: {repo['current_branch']}   head: {repo['head_sha']}",
        f"upstream: {repo['upstream']}  ahead={repo['ahead']} behind={repo['behind']}",
        f"local main relationship: {repo['local_main_relationship'].get('status')}",
        f"current worktree: {repo['current_worktree']}",
        f"worktrees: {len(repo['worktrees'])}",
        f"dirty tracked: {len(repo['dirty_tracked_files'])}  staged: {len(repo['staged_files'])}  "
        f"untracked: {len(repo['untracked_files'])}",
        f"branches tracking deleted remotes: {repo['branches_tracking_deleted_remotes']}",
        f"local-only branches: {repo['local_only_branches']}",
        f"archive/* tags: {len(repo['archive_tags'])}",
        f"stash entries: {len(repo['stash'])}",
        f"branch changed during check: {repo['branch_changed_during_check']}",
    ]
    if repo["warnings"]:
        lines.append(f"warnings: {repo['warnings']}")
    lanes = report["runtime_snapshot"].get("active_paper_forward_lanes", {})
    lines.append(f"active paper-forward lanes: {[l['strategy'] for l in lanes.get('paper_eligible_lanes', [])]}")
    lines.append(
        "(full runtime snapshot -- deployed SHA drift, evidence-readiness window, "
        "release manifest -- is in --json output)"
    )
    return "\n".join(lines)


def cmd_session_start(args: argparse.Namespace) -> int:
    root = ROOT
    branch_before = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    repo_state = collect_git_state(root, fetch=args.fetch)
    branch_after = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    repo_state["branch_changed_during_check"] = branch_before != branch_after

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "repo": repo_state,
        "open_prs": "UNKNOWN (pass --gh to attempt a `gh pr list` lookup)" if not args.gh else _pr_status_via_gh(root),
        "runtime_snapshot": runtime_snapshot(root),
    }

    checkpoint = {
        "checked_at": report["checked_at"],
        "repo_root": str(root),
        "branch": repo_state["current_branch"],
        "head_sha": repo_state["head_sha"],
        "worktree": repo_state["current_worktree"],
    }
    SESSION_CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_CHECKPOINT_PATH.write_text(json.dumps(checkpoint, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(_render_session_start(report))
    return 0


# ───────────────────────────────────────────────────────────────── precommit ──

def cmd_precommit(args: argparse.Namespace) -> int:
    root = ROOT
    problems: list[str] = []
    checkpoint: Optional[dict[str, Any]] = None

    if not SESSION_CHECKPOINT_PATH.exists():
        problems.append(
            "session-start state cannot be verified: no checkpoint found "
            f"at {SESSION_CHECKPOINT_PATH}; run `python -m ops.project_check session-start` first"
        )
    else:
        try:
            checkpoint = json.loads(SESSION_CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"session-start state cannot be verified: checkpoint unreadable ({exc})")

    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    head_sha = _git(root, "rev-parse", "HEAD")
    current_worktree = _git(root, "rev-parse", "--show-toplevel")
    dirty, staged, untracked = _status_breakdown(root)
    upstream, ahead, behind = _upstream_ahead_behind(root, branch)

    if branch is None or head_sha is None or current_worktree is None:
        problems.append("repository state is ambiguous: a read-only git query failed unexpectedly")

    if checkpoint:
        if checkpoint.get("repo_root") and checkpoint["repo_root"] != str(root):
            problems.append(
                "session-start state cannot be verified: checkpoint repo_root "
                f"({checkpoint['repo_root']}) does not match current repo root ({root})"
            )
        if checkpoint.get("branch") and branch and checkpoint["branch"] != branch:
            problems.append(
                f"branch differs from session-start branch unexpectedly: "
                f"was '{checkpoint['branch']}', now '{branch}'"
            )
        if checkpoint.get("worktree") and current_worktree and checkpoint["worktree"] != current_worktree:
            problems.append(
                f"worktree differs from session-start worktree unexpectedly: "
                f"was '{checkpoint['worktree']}', now '{current_worktree}'"
            )
        if (
            checkpoint.get("branch") == branch
            and checkpoint.get("head_sha") and head_sha
            and checkpoint["head_sha"] != head_sha
        ):
            if not _git_ok(root, "merge-base", "--is-ancestor", checkpoint["head_sha"], "HEAD"):
                problems.append(
                    "branch moved unexpectedly: current HEAD is not a fast-forward "
                    f"descendant of the session-start HEAD ({checkpoint['head_sha'][:12]} -> {(head_sha or '?')[:12]})"
                )

    if branch:
        worktrees = _list_worktrees(root)
        owners = [
            wt for wt in worktrees
            if wt.get("branch") == branch and Path(wt.get("path", "")) != Path(current_worktree or root)
        ]
        if owners:
            problems.append(
                f"intended branch '{branch}' is currently owned by another worktree: "
                f"{[w['path'] for w in owners]}"
            )

    newly_sensitive = [f for f in staged if any(marker in f for marker in SENSITIVE_PATH_MARKERS)]
    if newly_sensitive:
        problems.append(f"unexpected files changed: possible secret/key file staged: {newly_sensitive}")

    verdict = "FAIL" if problems else "PASS"
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "problems": problems,
        "repo_root": str(root),
        "current_branch": branch,
        "current_head": head_sha,
        "session_start_branch": (checkpoint or {}).get("branch"),
        "current_worktree": current_worktree,
        "session_start_worktree": (checkpoint or {}).get("worktree"),
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "changed_files": dirty,
        "staged_files": staged,
        "untracked_files": untracked,
        "read_only": True,
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(f"PRECOMMIT: {verdict}")
        print(f"branch: {branch} (session-start: {report['session_start_branch']})")
        print(f"worktree: {current_worktree} (session-start: {report['session_start_worktree']})")
        print(f"upstream: {upstream}  ahead={ahead} behind={behind}")
        print(f"changed: {len(dirty)}  staged: {len(staged)}  untracked: {len(untracked)}")
        for p in problems:
            print(f"  BLOCKER: {p}")
    return 1 if problems else 0


# ─────────────────────────────────────────────────────── promotion proof gate ──

def _strategy_runtime_status(root: Path, strategy: str) -> dict[str, Any]:
    try:
        rules = _load_risk_rules(root)
    except (OSError, Exception) as exc:  # noqa: BLE001
        return {"error": str(exc)}
    gate = rules.get("strategy_permission_gate") or {}
    statuses = gate.get("strategy_status") or {}
    default_status = gate.get("default_status", "UNKNOWN")
    permission_status = statuses.get(strategy, default_status)
    instruments = (rules.get("instruments") or {}).get("allowed") or []
    tolerance = {"default": os.getenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS") or "UNKNOWN"}
    for inst in instruments:
        tolerance[inst] = os.getenv(f"ENTRY_SLIPPAGE_TOLERANCE_TICKS_{inst}") or "UNKNOWN"
    return {
        "strategy": strategy,
        "gate_enabled": gate.get("enabled"),
        "permission_status": permission_status,
        "explicitly_configured": strategy in statuses,
        "allowed_instruments": instruments,
        "entry_fill_model": os.getenv("ENTRY_FILL_MODEL") or "UNKNOWN",
        "tradovate_entry_execution_mode": os.getenv("TRADOVATE_ENTRY_EXECUTION_MODE") or "UNKNOWN",
        "entry_slippage_tolerance_ticks": tolerance,
        "exit_mode": os.getenv("EXIT_MODE") or "UNKNOWN",
        "max_contracts_hard_cap": os.getenv("MAX_CONTRACTS_HARD_CAP") or "UNKNOWN",
    }


def _strategy_paper_forward_evidence(journal_dir: Path, strategy: str, days: int) -> dict[str, Any]:
    from adaptive.journal_reader import JournalReader

    reader = JournalReader(journal_dir)
    trades = [t for t in reader.read_trades(days=days) if t.strategy == strategy]
    decisions = [d for d in reader.read_decisions(days=days) if d.strategy == strategy]

    fills_resolved = [t for t in trades if t.result in ("WIN", "LOSS", "BREAKEVEN")]
    cancellations = [t for t in trades if t.result == "CANCELLED"]
    open_positions = [t for t in trades if t.result is None and not t.unjoinable_legacy]
    unjoinable = [t for t in trades if t.unjoinable_legacy]
    fills_total = len(fills_resolved) + len(open_positions)
    attempts = len(trades)

    wins = [t for t in fills_resolved if t.result == "WIN"]
    losses = [t for t in fills_resolved if t.result == "LOSS"]
    pnl = sum(float(t.pnl_dollars or 0.0) for t in fills_resolved)
    long_trades = sum(1 for t in trades if t.direction == "LONG")
    short_trades = sum(1 for t in trades if t.direction == "SHORT")
    instrument_split = dict(Counter(t.instrument for t in trades))
    rejects = [d for d in decisions if d.decision == "RISK_REJECTED"]

    return {
        "journal_dir": str(journal_dir),
        "days_scanned": days,
        "attempts": attempts,
        "risk_rejected": len(rejects),
        "fills_resolved": len(fills_resolved),
        "legitimately_open": len(open_positions),
        "cancellations_no_fill": len(cancellations),
        "unjoinable_needs_manual_review": len(unjoinable),
        "fills_total": fills_total,
        "accounting_identity_holds": attempts == (fills_total + len(cancellations) + len(unjoinable)),
        "net_pnl_dollars": round(pnl, 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(fills_resolved), 4) if fills_resolved else None,
        "long_short_split": {"LONG": long_trades, "SHORT": short_trades},
        "instrument_split": instrument_split,
        "zero_executable_fills": fills_total == 0,
    }


def _strategy_gate_attrition(journal_dir: Path, strategy: str, days: int) -> dict[str, Any]:
    from adaptive.journal_reader import JournalReader

    reader = JournalReader(journal_dir)
    decisions = [d for d in reader.read_decisions(days=days) if d.strategy == strategy]
    no_trade = [d for d in decisions if d.decision == "NO_TRADE"]
    gate_counts: Counter[str] = Counter()
    for d in no_trade:
        for gate in d.failed_gates:
            gate_counts[gate] += 1
    return {
        "source": "journal NO_TRADE.failed_gates -- paper-forward RUNTIME attrition, "
                  "not a controlled replay of one fixed historical candidate set",
        "no_trade_candidates_observed": len(no_trade),
        "gate_counts": dict(gate_counts.most_common()),
        "limitation": (
            "This counts gates that blocked a real formed setup during live/paper "
            "operation. It is NOT a candidate-set -> ReplayEngine -> DecisionEngine "
            "-> RiskEngine -> PaperBroker funnel over one historical population. "
            "For that, pass --evidence-json pointing at an existing "
            "scripts/*_canonical_evidence.py (or similar) results file."
        ),
    }


def _parse_inventory_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows = []
    in_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("| Strategy "):
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2:
                continue
            name_cell, verdict_cell = cells[0], cells[-1]
            match = re.search(r"\*\*([A-Z][A-Z0-9 ]*)\*\*", verdict_cell)
            verdict = match.group(1).strip() if match else verdict_cell.strip()
            rows.append({"name": name_cell, "verdict": verdict})
    return rows


def _lookup_inventory_verdict(root: Path, strategy: str) -> dict[str, Any]:
    path = root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    if not path.exists():
        return {"verdict": "UNKNOWN", "note": f"{path} not found"}
    key = re.sub(r"[^a-z0-9]", "", strategy.lower())
    for row in _parse_inventory_rows(path):
        norm = re.sub(r"[^a-z0-9]", "", row["name"].lower())
        if key and key in norm:
            return {"verdict": row["verdict"], "matched_row": row["name"], "source": str(path)}
    return {
        "verdict": "UNKNOWN",
        "note": (
            f"no row in {path} matched strategy name '{strategy}'; "
            "pass --research-verdict to supply it manually"
        ),
    }


def _compare_evidence_assumptions(runtime: dict[str, Any], evidence_json: Optional[dict[str, Any]]) -> dict[str, Any]:
    if evidence_json is None:
        return {"status": "UNKNOWN", "note": "no --evidence-json supplied"}
    assumptions = evidence_json.get("assumptions") or evidence_json.get("execution_context") or {}
    mismatches = []
    for key, runtime_key in (
        ("entry_fill_model", "entry_fill_model"),
        ("tradovate_entry_execution_mode", "tradovate_entry_execution_mode"),
        ("exit_mode", "exit_mode"),
    ):
        assumed = assumptions.get(key)
        actual = runtime.get(runtime_key)
        if assumed is not None and actual is not None and str(assumed) != str(actual):
            mismatches.append({"field": key, "evidence_assumed": assumed, "runtime_actual": actual})
    return {"status": "COMPARED", "assumptions": assumptions, "mismatches": mismatches}


def _classify_promotion(
    runtime: dict[str, Any],
    paper_evidence: dict[str, Any],
    research: dict[str, Any],
    parity: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []

    if runtime.get("permission_status") != "PAPER_ELIGIBLE":
        reasons.append(
            f"runtime permission_status={runtime.get('permission_status')!r} -- "
            "strategy_permission_gate blocks this strategy before PaperBroker; "
            "no promotion evidence can be considered executable yet"
        )
        return "WAIT", reasons

    if not paper_evidence.get("accounting_identity_holds"):
        reasons.append(
            "accounting identity failed: attempts != fills_total + cancellations + "
            "unjoinable rows -- journal data integrity issue, cannot certify from this data"
        )
        return "BROKEN", reasons

    research_verdict = research.get("verdict", "UNKNOWN")

    if paper_evidence.get("zero_executable_fills"):
        reasons.append("ZERO EXECUTABLE FILLS in the paper-forward journal for this strategy/window")
        if research_verdict in ("BROKEN", "RETIRE"):
            reasons.append(f"research/inventory verdict is already {research_verdict}")
            return "BROKEN", reasons
        return "PROMISING BUT UNPROVEN", reasons

    if parity.get("mismatches"):
        reasons.append(f"runtime parity defects vs evidence assumptions: {parity['mismatches']}")
        return "PROMISING BUT UNPROVEN", reasons

    if paper_evidence.get("net_pnl_dollars", 0) < 0:
        reasons.append(f"paper-forward net P&L is negative (${paper_evidence.get('net_pnl_dollars')})")
        return "BROKEN", reasons

    if paper_evidence.get("fills_resolved", 0) < 30:
        reasons.append(
            f"only {paper_evidence.get('fills_resolved')} resolved paper-forward fills -- "
            "below the 30-trade sample bar this repo's own Strategy_Inventory.md uses elsewhere"
        )
        return "PROMISING BUT UNPROVEN", reasons

    reasons.append(
        f"{paper_evidence.get('fills_resolved')} resolved paper-forward fills, positive net "
        "P&L, no accounting/parity defects found"
    )
    return "VALIDATED", reasons


def cmd_promotion(args: argparse.Namespace) -> int:
    root = ROOT
    strategy = args.strategy
    journal_dir = Path(args.journal_dir) if args.journal_dir else (root / "logs")
    days = args.days

    runtime = _strategy_runtime_status(root, strategy)
    paper_evidence = _strategy_paper_forward_evidence(journal_dir, strategy, days)
    gate_attrition = _strategy_gate_attrition(journal_dir, strategy, days)
    research = {"verdict": args.research_verdict} if args.research_verdict else _lookup_inventory_verdict(root, strategy)

    evidence_json = None
    if args.evidence_json:
        evidence_path = Path(args.evidence_json)
        try:
            evidence_json = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            evidence_json = {"error": f"could not read/parse {evidence_path}: {exc}"}

    parity = _compare_evidence_assumptions(runtime, evidence_json if isinstance(evidence_json, dict) and "error" not in evidence_json else None)
    classification, reasons = _classify_promotion(runtime, paper_evidence, research, parity)

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy,
        "classification": classification,
        "reasons": reasons,
        "research_result": research,
        "runtime_parity": {"runtime": runtime, "evidence_assumptions_comparison": parity,
                            "evidence_json_source": args.evidence_json},
        "identity_gate_attrition": gate_attrition,
        "paper_forward_evidence": paper_evidence,
        "raw_evidence_json": evidence_json,
        "rules": (
            "No rescue/tuning variant in this pass. No automatic runtime change, "
            "merge, or deploy. Legitimate account risk controls (risk_rules.yaml, "
            "RiskEngine) are never silently exempted to reproduce research numbers."
        ),
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(f"PROMOTION PROOF GATE -- {strategy}")
        print(f"CLASSIFICATION: {classification}")
        for r in reasons:
            print(f"  - {r}")
        print(f"research/inventory verdict: {research.get('verdict')}")
        print(
            f"runtime: permission_status={runtime.get('permission_status')} "
            f"entry_fill_model={runtime.get('entry_fill_model')} "
            f"exit_mode={runtime.get('exit_mode')}"
        )
        print(
            f"paper-forward: attempts={paper_evidence.get('attempts')} "
            f"fills_resolved={paper_evidence.get('fills_resolved')} "
            f"legitimately_open={paper_evidence.get('legitimately_open')} "
            f"no_fill={paper_evidence.get('cancellations_no_fill')} "
            f"net_pnl=${paper_evidence.get('net_pnl_dollars')} "
            f"accounting_identity_holds={paper_evidence.get('accounting_identity_holds')}"
        )
        print("(full report, including gate-attrition failed_gates counts, in --json output)")
    return 0


# ───────────────────────────────────────────────── daily reconciliation ──

def _load_daily_checkpoint() -> Optional[date]:
    if not DAILY_CHECKPOINT_PATH.exists():
        return None
    try:
        data = json.loads(DAILY_CHECKPOINT_PATH.read_text(encoding="utf-8"))
        return date.fromisoformat(data["last_run_date"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def _write_daily_checkpoint(today: date) -> None:
    DAILY_CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DAILY_CHECKPOINT_PATH.write_text(json.dumps({"last_run_date": today.isoformat()}, indent=2), encoding="utf-8")


def _github_daily_reconciliation(root: Path) -> dict[str, Any]:
    prs = _pr_status_via_gh(root)
    if not isinstance(prs, list):
        return {"prs": prs}
    today_iso = date.today().isoformat()
    opened_today = [p for p in prs if str(p.get("createdAt", "")).startswith(today_iso)]
    merged_today = [p for p in prs if p.get("mergedAt") and str(p["mergedAt"]).startswith(today_iso)]
    closed_unmerged_today = [
        p for p in prs
        if p.get("closedAt") and str(p["closedAt"]).startswith(today_iso) and not p.get("mergedAt")
    ]
    open_prs = [p for p in prs if p.get("state") == "OPEN"]
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_PR_DAYS)
    stale_prs = []
    for p in open_prs:
        updated = p.get("updatedAt")
        if not updated:
            continue
        try:
            updated_dt = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
        except ValueError:
            continue
        if updated_dt < stale_cutoff:
            stale_prs.append(p)
    return {
        "opened_today": [p.get("number") for p in opened_today],
        "merged_today": [p.get("number") for p in merged_today],
        "closed_unmerged_today": [p.get("number") for p in closed_unmerged_today],
        "open_prs": [p.get("number") for p in open_prs],
        f"stale_open_prs_over_{STALE_PR_DAYS}d": [p.get("number") for p in stale_prs],
    }


def _strategy_source_of_truth(root: Path) -> dict[str, Any]:
    try:
        rules = _load_risk_rules(root)
    except (OSError, Exception) as exc:  # noqa: BLE001
        return {"error": str(exc)}
    gate = rules.get("strategy_permission_gate") or {}
    statuses = gate.get("strategy_status") or {}
    default_status = gate.get("default_status", "UNKNOWN")
    inventory_path = root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    rows = _parse_inventory_rows(inventory_path)

    drift = []
    active_like = {"PAPER PROOF", "VALIDATED"}
    broken_like = {"BROKEN", "WAIT", "OVERFIT", "RETIRE"}
    for row in rows:
        key = re.sub(r"[^a-z0-9]", "", row["name"].lower())
        runtime_status = default_status
        matched_code_name = None
        for code_name, status in statuses.items():
            norm_code = re.sub(r"[^a-z0-9]", "", code_name.lower())
            if norm_code and (norm_code in key or key in norm_code):
                runtime_status = status
                matched_code_name = code_name
                break
        verdict = row["verdict"]
        if verdict in active_like and runtime_status != "PAPER_ELIGIBLE":
            drift.append({
                "strategy": row["name"], "matched_code_name": matched_code_name,
                "inventory_verdict": verdict, "runtime_status": runtime_status,
                "issue": "described as active/validated in inventory but runtime is not PAPER_ELIGIBLE",
            })
        if verdict in broken_like and runtime_status == "PAPER_ELIGIBLE":
            drift.append({
                "strategy": row["name"], "matched_code_name": matched_code_name,
                "inventory_verdict": verdict, "runtime_status": runtime_status,
                "issue": "runtime is PAPER_ELIGIBLE despite an inventory verdict of BROKEN/WAIT/OVERFIT/RETIRE",
            })
    return {
        "inventory_path": str(inventory_path),
        "rows_parsed": len(rows),
        "drift": drift,
    }


def _duplicate_order_ids(journal_dir: Path, days: int) -> int:
    from adaptive.journal_reader import JournalReader
    seen: Counter[str] = Counter()
    for offset in range(days):
        day = date.today() - timedelta(days=offset)
        path = journal_dir / f"journal_{day.isoformat()}.jsonl"
        if not path.exists():
            continue
        for raw in JournalReader._read_raw(path):  # noqa: SLF001 - read-only reuse, no public wrapper exists
            if raw.get("decision") == "TRADE" and raw.get("paper_order_id"):
                seen[raw["paper_order_id"]] += 1
    return sum(1 for count in seen.values() if count > 1)


def _trade_chain_report(journal_dir: Path, since: date) -> dict[str, Any]:
    from adaptive.journal_reader import JournalReader

    today = date.today()
    days = max((today - since).days + 1, 1)
    reader = JournalReader(journal_dir)
    trades = [t for t in reader.read_trades(days=days) if t.date >= since.isoformat()]
    decisions = [d for d in reader.read_decisions(days=days) if d.date >= since.isoformat()]

    fills_resolved = [t for t in trades if t.result in ("WIN", "LOSS", "BREAKEVEN")]
    cancellations = [t for t in trades if t.result == "CANCELLED"]
    open_positions = [t for t in trades if t.result is None and not t.unjoinable_legacy]
    unjoinable = [t for t in trades if t.unjoinable_legacy]
    fills_total = len(fills_resolved) + len(open_positions)
    attempts = len(trades)
    rejects = [d for d in decisions if d.decision == "RISK_REJECTED"]

    missing_attribution = [
        d for d in decisions
        if not d.strategy or d.strategy == "unknown" or not d.instrument or not d.direction
    ]
    missing_reject_reason = [
        d for d in decisions
        if d.decision == "RISK_REJECTED" and not d.risk_failed_rule and not d.reason
    ]
    naked = [t for t in (fills_resolved + open_positions) if t.stop is None or t.target is None]
    stale_open = [t for t in open_positions if t.date != today.isoformat()]
    duplicate_ids = _duplicate_order_ids(journal_dir, days)

    identity_ok = attempts == (fills_total + len(cancellations) + len(unjoinable))
    clean = (
        identity_ok and not unjoinable and not naked and not stale_open
        and not missing_attribution and not missing_reject_reason and duplicate_ids == 0
    )

    return {
        "since": since.isoformat(),
        "through": today.isoformat(),
        "attempts": attempts,
        "fills": fills_total,
        "resolved": len(fills_resolved),
        "legitimately_open": len(open_positions),
        "no_fills": len(cancellations),
        "risk_rejected": len(rejects),
        "orphans_unjoinable": len(unjoinable),
        "naked_positions": len(naked),
        "stale_open_positions": len(stale_open),
        "missing_attribution": len(missing_attribution),
        "missing_reject_reason": len(missing_reject_reason),
        "duplicate_order_identity": duplicate_ids,
        "accounting_identity_holds": identity_ok,
        "broker_journal_parity": (
            "UNKNOWN -- no live broker session available for an offline check. "
            "Run ops/reconciler_outcome_audit.py or /futures-cancelled-audit "
            "against the live box for authoritative broker/journal parity."
        ),
        "pass": clean,
    }


def _render_trade_chain(tc: dict[str, Any]) -> str:
    if tc["pass"]:
        return (
            "TRADE CHAIN: PASS\n"
            f"{tc['attempts']} attempts\n"
            f"{tc['fills']} fills ({tc['resolved']} resolved, {tc['legitimately_open']} legitimate opens)\n"
            f"{tc['no_fills']} no-fills\n"
            f"{tc['risk_rejected']} risk-rejected\n"
            "0 orphans\n0 stale orders\n0 duplicate identities\n"
            f"broker/journal parity: {tc['broker_journal_parity']}"
        )
    lines = ["TRADE CHAIN: FAIL / NEEDS REVIEW"]
    for key in (
        "attempts", "fills", "resolved", "legitimately_open", "no_fills", "risk_rejected",
        "orphans_unjoinable", "naked_positions", "stale_open_positions", "missing_attribution",
        "missing_reject_reason", "duplicate_order_identity", "accounting_identity_holds",
    ):
        lines.append(f"  {key}: {tc.get(key)}")
    lines.append(f"  broker/journal parity: {tc['broker_journal_parity']}")
    return "\n".join(lines)


def cmd_daily(args: argparse.Namespace) -> int:
    root = ROOT
    today = date.today()
    journal_dir = Path(args.journal_dir) if args.journal_dir else (root / "logs")
    prior = _load_daily_checkpoint()
    since = (prior + timedelta(days=1)) if prior else today

    git_state = collect_git_state(root, fetch=False)
    github = {"status": "skipped (--no-gh)"} if args.no_gh else _github_daily_reconciliation(root)
    evidence_preservation = _unmerged_branches_missing_archive(
        root, git_state["local_branches"], git_state["archive_tags"],
        current_branch=git_state["current_branch"],
    )
    deployed_state = runtime_snapshot(root)
    strategy_sot = _strategy_source_of_truth(root)
    trade_chain = _trade_chain_report(journal_dir, since)

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "reconciliation_window": {"since": since.isoformat(), "through": today.isoformat()},
        "github": github,
        "branches_worktrees": {
            "current_branch": git_state["current_branch"],
            "local_main_relationship": git_state["local_main_relationship"],
            "worktrees": git_state["worktrees"],
            "dirty_worktrees": [w["path"] for w in git_state["worktrees"] if w.get("dirty")],
            "branches_tracking_deleted_remotes": git_state["branches_tracking_deleted_remotes"],
            "local_only_branches": git_state["local_only_branches"],
            "stash_count": len(git_state["stash"]),
        },
        "evidence_preservation": evidence_preservation,
        "deployed_state": deployed_state,
        "strategy_source_of_truth": strategy_sot,
        "trade_chain": trade_chain,
    }

    if not args.dry_run:
        _write_daily_checkpoint(today)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(f"DAILY RECONCILIATION  ({report['checked_at']})  window since={since} through={today}")
        print(f"branch: {git_state['current_branch']}  "
              f"local main: {git_state['local_main_relationship'].get('status')}")
        print(f"dirty worktrees: {report['branches_worktrees']['dirty_worktrees']}")
        print(f"branches tracking deleted remotes: {git_state['branches_tracking_deleted_remotes']}")
        blockers = [b for b in evidence_preservation.get("branches", []) if b.get("flag") == "BLOCKER"]
        print(f"evidence-preservation BLOCKERs (unique commits, no archive tag): "
              f"{[b['branch'] for b in blockers]}")
        drift = strategy_sot.get("drift", [])
        print(f"strategy inventory/runtime drift: {len(drift)} row(s)" + (f" -> {drift}" if drift else ""))
        print()
        print(_render_trade_chain(trade_chain))
        print()
        print("(full GitHub/deployed-state/evidence detail in --json output)")
    return 0


# ──────────────────────────────────────────────────────────────────── CLI ──

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ops.project_check",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("session-start", help="Git/worktree/runtime snapshot; writes a local checkpoint.")
    p_start.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    p_start.add_argument("--fetch", action="store_true", help="Run `git fetch origin main` first (off by default).")
    p_start.add_argument("--gh", action="store_true", help="Attempt a `gh pr list` lookup (off by default).")
    p_start.set_defaults(func=cmd_session_start)

    p_pre = sub.add_parser("precommit", help="Read-only, fail-closed diff against the session-start checkpoint.")
    p_pre.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    p_pre.set_defaults(func=cmd_precommit)

    p_promo = sub.add_parser("promotion", help="Strategy promotion proof-gate report.")
    p_promo.add_argument("--strategy", required=True, help="Strategy code name, e.g. orb_breakout.")
    p_promo.add_argument("--journal-dir", default=None, help="Journal directory (default: <repo>/logs).")
    p_promo.add_argument("--days", type=int, default=30, help="Days of journal history to scan (default: 30).")
    p_promo.add_argument("--evidence-json", default=None, help="Path to an existing evidence/backtest results JSON.")
    p_promo.add_argument("--research-verdict", default=None, help="Override the Strategy_Inventory.md lookup.")
    p_promo.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    p_promo.set_defaults(func=cmd_promotion)

    p_daily = sub.add_parser("daily", help="Daily repo/process reconciliation + trade-chain integrity.")
    p_daily.add_argument("--journal-dir", default=None, help="Journal directory (default: <repo>/logs).")
    p_daily.add_argument("--no-gh", action="store_true", help="Skip the `gh pr list` lookup.")
    p_daily.add_argument("--dry-run", action="store_true", help="Do not update the daily checkpoint file.")
    p_daily.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    p_daily.set_defaults(func=cmd_daily)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
