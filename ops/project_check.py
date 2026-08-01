"""Three manually-invoked, read-only operator routines, composed from
existing ops/ and config tooling rather than duplicating it.

    python -m ops.project_check session-start
    python -m ops.project_check precommit
    python -m ops.project_check promotion --strategy <name> --candles <path> [...]
    python -m ops.project_check daily

No routine here ever commits, pushes, pulls, resets, rebases, checks out,
deletes a branch/worktree/stash/tag, edits risk_rules.yaml or any strategy/
execution/risk source file, cancels an order, flattens a position, or
repairs a journal. Every routine is read-only against the repo and the
journal, with one narrow exception: `session-start` writes its own small
state snapshot to an external cache directory (never inside the repo tree)
so that `precommit`, run later in the same session, has a baseline to
detect drift against. `git fetch` only happens when `--fetch` is passed
explicitly; every routine defaults to whatever remote-tracking refs are
already cached locally so it never surprises the caller with network I/O.

Ambiguous or unavailable data is always reported as the literal string
"UNKNOWN" (or "NOT_EVALUATED" where a check was skipped rather than
attempted) -- never inferred or defaulted to a value that looks like an
answer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, replace as dc_replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

UNKNOWN = "UNKNOWN"
NOT_EVALUATED = "NOT_EVALUATED"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ════════════════════════════════════════════════════════════════════════
# Git / worktree / branch primitives (shared by all three routines)
# ════════════════════════════════════════════════════════════════════════

def _git(repo_root: Path, *args: str, timeout: float = 20.0) -> tuple[int, str, str]:
    """Runs git and returns (returncode, stdout, stderr).

    stdout is only trailing-newline-stripped, never `.strip()`-ed: several
    callers (git status --porcelain in particular) depend on a leading
    space in the first line being meaningful (" M path" vs "M  path"); a
    blanket .strip() on the whole blob would eat exactly that character.
    """
    try:
        result = subprocess.run(
            ["git", *args], cwd=str(repo_root), capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.rstrip("\n"), result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"


def _git_text(repo_root: Path, *args: str) -> Optional[str]:
    code, out, _ = _git(repo_root, *args)
    return out.strip() if code == 0 else None


def find_repo_root(start: Path) -> Optional[Path]:
    text = _git_text(start, "rev-parse", "--show-toplevel")
    return Path(text).resolve() if text else None


def current_branch(repo_root: Path) -> str:
    branch = _git_text(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    return branch or UNKNOWN


def head_sha(repo_root: Path) -> str:
    return _git_text(repo_root, "rev-parse", "HEAD") or UNKNOWN


def upstream_of(repo_root: Path, branch: str) -> str:
    ref = _git_text(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{branch}@{{u}}")
    return ref or "NONE"


def status_porcelain(repo_root: Path) -> tuple[Optional[list[str]], Optional[str]]:
    code, out, err = _git(repo_root, "status", "--porcelain=v1")
    if code != 0:
        return None, err or "git status failed"
    return (out.splitlines() if out else []), None


def classify_status(lines: list[str]) -> dict[str, list[str]]:
    staged, dirty, untracked = [], [], []
    for line in lines:
        if len(line) < 4:
            continue
        index_status, worktree_status, path = line[0], line[1], line[3:]
        if index_status == "?" and worktree_status == "?":
            untracked.append(path)
            continue
        if index_status not in (" ", "?"):
            staged.append(path)
        if worktree_status not in (" ", "?"):
            dirty.append(path)
    return {
        "staged": sorted(set(staged)),
        "dirty_tracked": sorted(set(dirty)),
        "untracked": sorted(set(untracked)),
    }


def worktrees(repo_root: Path) -> list[dict[str, Any]]:
    code, out, _ = _git(repo_root, "worktree", "list", "--porcelain")
    if code != 0:
        return []
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in out.splitlines() + [""]:
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line.split(" ", 1)[1]
        elif line.startswith("HEAD "):
            current["head"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1].replace("refs/heads/", "")
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
    return entries


def stash_list(repo_root: Path) -> list[str]:
    code, out, _ = _git(repo_root, "stash", "list")
    return out.splitlines() if code == 0 and out else []


def archive_tags(repo_root: Path) -> list[str]:
    code, out, _ = _git(repo_root, "tag", "-l", "archive/*")
    return sorted(out.splitlines()) if code == 0 and out else []


def local_branches(repo_root: Path) -> list[dict[str, Any]]:
    code, out, _ = _git(
        repo_root, "for-each-ref",
        "--format=%(refname:short)\t%(upstream:short)\t%(upstream:track)",
        "refs/heads",
    )
    if code != 0:
        return []
    branches = []
    for line in out.splitlines():
        parts = line.split("\t")
        name = parts[0] if len(parts) > 0 else ""
        upstream = parts[1] if len(parts) > 1 else ""
        track = parts[2] if len(parts) > 2 else ""
        if name:
            branches.append({"name": name, "upstream": upstream or None, "track": track or None})
    return branches


def local_main_relationship(repo_root: Path) -> dict[str, Any]:
    has_local_main = _git_text(repo_root, "rev-parse", "--verify", "--quiet", "refs/heads/main") is not None
    has_remote_main = _git_text(repo_root, "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main") is not None
    if not has_local_main or not has_remote_main:
        return {"status": UNKNOWN, "ahead": None, "behind": None,
                "note": "local refs/heads/main and/or refs/remotes/origin/main not found"}
    code, out, _ = _git(repo_root, "rev-list", "--left-right", "--count", "origin/main...main")
    if code != 0 or not out:
        return {"status": UNKNOWN, "ahead": None, "behind": None, "note": "git rev-list failed"}
    parts = out.split()
    if len(parts) != 2:
        return {"status": UNKNOWN, "ahead": None, "behind": None, "note": f"unexpected rev-list output: {out!r}"}
    behind, ahead = int(parts[0]), int(parts[1])
    if ahead == 0 and behind == 0:
        status = "IN_SYNC"
    elif ahead > 0 and behind == 0:
        status = "AHEAD"
    elif ahead == 0 and behind > 0:
        status = "BEHIND"
    else:
        status = "DIVERGED"
    return {"status": status, "ahead": ahead, "behind": behind, "note": None}


def ahead_behind_upstream(repo_root: Path, branch: str) -> dict[str, Any]:
    upstream = upstream_of(repo_root, branch)
    if upstream in (UNKNOWN, "NONE"):
        return {"ahead": None, "behind": None, "note": "no upstream configured for current branch"}
    code, out, _ = _git(repo_root, "rev-list", "--left-right", "--count", f"{upstream}...{branch}")
    if code != 0 or not out:
        return {"ahead": None, "behind": None, "note": "git rev-list failed"}
    parts = out.split()
    if len(parts) != 2:
        return {"ahead": None, "behind": None, "note": f"unexpected rev-list output: {out!r}"}
    return {"behind": int(parts[0]), "ahead": int(parts[1]), "note": None}


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def gh_open_prs(repo_root: Path) -> Any:
    if not _gh_available():
        return UNKNOWN
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json",
             "number,title,headRefName,baseRefName,isDraft,updatedAt,createdAt", "--limit", "100"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return UNKNOWN
    if result.returncode != 0:
        return UNKNOWN
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return UNKNOWN


def gh_prs_all(repo_root: Path) -> Any:
    if not _gh_available():
        return UNKNOWN
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--json",
             "number,title,headRefName,baseRefName,state,isDraft,mergedAt,closedAt,createdAt,updatedAt",
             "--limit", "200"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=25,
        )
    except (OSError, subprocess.TimeoutExpired):
        return UNKNOWN
    if result.returncode != 0:
        return UNKNOWN
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return UNKNOWN


_SLUG_PREFIX_RE = re.compile(r"^(origin/)?(claude|codex|agent)/")


def _slug_words(short_name: str) -> set[str]:
    name = _SLUG_PREFIX_RE.sub("", short_name)
    name = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return {w for w in name.split("-") if len(w) > 2}


def evidence_preservation_candidates(
    repo_root: Path, *, open_pr_head_branches: Optional[set[str]] = None,
) -> dict[str, Any]:
    """Best-effort only: remote branches not merged into origin/main AND not
    the head of a currently-open PR (an open PR is itself a preservation
    mechanism -- this check is about *closed*-unmerged branches per the
    routine spec), with no archive/* tag whose name shares at least half
    its slug words. This is a heuristic proxy for "does an archive tag
    plausibly cover this branch" -- it never deletes anything and never
    creates a tag; branches it flags need a human to verify with
    `docs/BRANCH_ARCHIVE_INDEX.md`'s actual audit method (unique-commit
    diff + byte-diff) before any disposition decision.
    """
    code, out, err = _git(repo_root, "branch", "-r", "--no-merged", "origin/main", "--format=%(refname:short)")
    if code != 0:
        return {"checked": False, "note": f"git branch --no-merged failed: {err}", "candidates_missing_archive_tag": []}
    tags = archive_tags(repo_root)
    tag_words = {tag: _slug_words(tag.split("/", 1)[1]) if "/" in tag else set() for tag in tags}
    unmerged = sorted(b for b in out.splitlines() if b and not b.endswith("/HEAD"))

    excluded_open_pr: list[str] = []
    if open_pr_head_branches is not None:
        remaining = []
        for branch in unmerged:
            short = branch.split("/", 1)[1] if "/" in branch else branch
            if short in open_pr_head_branches:
                excluded_open_pr.append(branch)
            else:
                remaining.append(branch)
        unmerged_closed_or_unknown = remaining
    else:
        unmerged_closed_or_unknown = unmerged

    candidates = []
    for branch in unmerged_closed_or_unknown:
        short = branch.split("/", 1)[1] if "/" in branch else branch
        words = _slug_words(short)
        matched = None
        for tag, twords in tag_words.items():
            if words and twords and len(words & twords) >= max(1, len(words) // 2):
                matched = tag
                break
        if matched is None:
            candidates.append({"branch": branch, "matched_archive_tag": None})
    return {
        "checked": True,
        "note": ("heuristic slug match against archive/* tag names -- best-effort proxy, "
                 "not the authoritative unique-evidence audit; verify manually before any "
                 "branch deletion, per docs/BRANCH_ARCHIVE_INDEX.md"),
        "unmerged_remote_branch_count": len(unmerged),
        "open_pr_filter_applied": open_pr_head_branches is not None,
        "excluded_branches_with_open_pr": excluded_open_pr if open_pr_head_branches is not None else UNKNOWN,
        "candidates_missing_archive_tag": candidates,
    }


# ════════════════════════════════════════════════════════════════════════
# Runtime snapshot (shared by session-start and daily)
# ════════════════════════════════════════════════════════════════════════

def runtime_snapshot(repo_root: Path, *, log_dir: str = "logs") -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    try:
        from ops.live_box_guard import live_box_drift_report
        snapshot["live_box_drift"] = live_box_drift_report(repo_root=repo_root, log_dir=log_dir)
    except Exception as exc:  # defensive: this must never crash the caller's routine
        snapshot["live_box_drift"] = {"status": UNKNOWN, "error": f"{type(exc).__name__}: {exc}"}

    try:
        from config.settings import load_config
        config = load_config(str(repo_root / "risk_rules.yaml"))
        lanes: dict[str, Any] = {}
        for concept in config.enabled_concepts or []:
            if config.strategy_permission_gate_enabled:
                permission = config.strategy_status.get(concept, config.strategy_permission_default_status)
            else:
                permission = "GATE_DISABLED (strategy_permission_gate.enabled=false)"
            lanes[concept] = {
                "permission_status": permission,
                "entry_fill_model": config.entry_fill_model,
                "entry_tolerance_ticks_by_root": config.entry_tolerance_ticks_by_root,
            }
        snapshot["active_paper_forward_lanes"] = lanes
        snapshot["quantity_contract_caps"] = {
            "max_open_positions": config.max_open_positions,
            "sizing_rules": [asdict(r) for r in config.position_sizing.sizing_rules],
        }
        snapshot["config_source"] = "risk_rules.yaml (+ env overrides) via config.settings.load_config"
    except Exception as exc:
        snapshot["active_paper_forward_lanes"] = UNKNOWN
        snapshot["quantity_contract_caps"] = UNKNOWN
        snapshot["config_load_error"] = f"{type(exc).__name__}: {exc}"

    snapshot["intended_deployed_release_sha"] = UNKNOWN
    snapshot["evidence_epochs"] = UNKNOWN
    snapshot["note"] = (
        "intended_deployed_release_sha / evidence_epochs are box-local runtime "
        "facts (release_manifest.json identity, evidence-epoch markers) that do "
        "not exist in a plain repo checkout -- UNKNOWN here is expected off-box, "
        "not a failure."
    )
    return snapshot


# ════════════════════════════════════════════════════════════════════════
# ROUTINE 1a: session-start
# ════════════════════════════════════════════════════════════════════════

def session_start_report(repo_root: Path, *, fetch: bool = False, log_dir: str = "logs") -> dict[str, Any]:
    branch_before = current_branch(repo_root)

    if fetch:
        _git(repo_root, "fetch", "origin", "--prune", "--quiet")

    status_lines, status_err = status_porcelain(repo_root)
    changes = classify_status(status_lines or [])
    branches = local_branches(repo_root)
    open_prs = gh_open_prs(repo_root)
    open_pr_heads = {p.get("headRefName") for p in open_prs if p.get("headRefName")} if isinstance(open_prs, list) else None

    repo_section: dict[str, Any] = {
        "repo_root": str(repo_root),
        "current_branch": branch_before,
        "head_sha": head_sha(repo_root),
        "origin_main_sha": _git_text(repo_root, "rev-parse", "refs/remotes/origin/main") or UNKNOWN,
        "local_main_relationship": local_main_relationship(repo_root),
        "upstream": upstream_of(repo_root, branch_before),
        "current_worktree": str(repo_root),
        "worktrees": worktrees(repo_root),
        "status_read_error": status_err,
        "dirty_tracked_files": changes["dirty_tracked"],
        "staged_files": changes["staged"],
        "untracked_files": changes["untracked"],
        "branches_tracking_deleted_remotes": sorted(
            b["name"] for b in branches if b["track"] and "gone" in b["track"]
        ),
        "local_only_branches": sorted(b["name"] for b in branches if not b["upstream"]),
        "open_prs": open_prs,
        "evidence_preservation": evidence_preservation_candidates(repo_root, open_pr_head_branches=open_pr_heads),
        "archive_tags": archive_tags(repo_root),
        "stash_count": len(stash_list(repo_root)),
        "stash_labels": stash_list(repo_root),
        "remote_data_freshness": (
            "updated by this run (--fetch was passed)" if fetch
            else "cached remote-tracking refs from the last fetch (pass --fetch to update first)"
        ),
    }

    branch_after = current_branch(repo_root)
    repo_section["branch_changed_during_check"] = branch_after != branch_before
    if branch_after != branch_before:
        repo_section["branch_changed_note"] = f"{branch_before} -> {branch_after}"

    report = {
        "routine": "session-start",
        "generated_at": _now_iso(),
        "repo": repo_section,
        "runtime_snapshot": runtime_snapshot(repo_root, log_dir=log_dir),
    }
    report["overall_status"] = "ANOMALY" if repo_section["branch_changed_during_check"] else "OK"
    return report


# ════════════════════════════════════════════════════════════════════════
# ROUTINE 1b: precommit / prepush -- fail-closed drift check, READ ONLY
# ════════════════════════════════════════════════════════════════════════

def _state_dir() -> Path:
    override = os.getenv("PROJECT_CHECK_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "afs_project_check"


def _state_path(repo_root: Path) -> Path:
    key = hashlib.sha256(str(repo_root).encode("utf-8")).hexdigest()[:16]
    return _state_dir() / f"session_state_{key}.json"


def save_session_state(repo_root: Path, report: dict[str, Any]) -> Path:
    """Writes the session-start baseline to an external cache dir -- never
    inside the repo tree, so it can never show up as a dirty/untracked file
    in the repo's own git status."""
    state = {
        "repo_root": str(repo_root),
        "branch": report["repo"]["current_branch"],
        "worktree": report["repo"]["current_worktree"],
        "head_sha": report["repo"]["head_sha"],
        "recorded_at": report["generated_at"],
    }
    path = _state_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, sort_keys=True) + "\n")
    tmp.replace(path)
    return path


def load_session_state(repo_root: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(_state_path(repo_root).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def precommit_report(repo_root: Path) -> dict[str, Any]:
    branch = current_branch(repo_root)
    status_lines, status_err = status_porcelain(repo_root)
    changes = classify_status(status_lines or [])
    changed_files = sorted(set(changes["staged"]) | set(changes["dirty_tracked"]) | set(changes["untracked"]))

    reasons: list[str] = []
    if status_err:
        reasons.append(f"repository state is ambiguous: git status failed ({status_err})")
    if branch == UNKNOWN:
        reasons.append("repository state is ambiguous: could not resolve current branch")

    baseline = load_session_state(repo_root)
    if baseline is None:
        reasons.append("session-start state not found -- run `python -m ops.project_check session-start` "
                        "first in this worktree; baseline cannot be verified")
    else:
        if baseline.get("repo_root") != str(repo_root):
            reasons.append(f"session-start baseline was recorded for a different repo root "
                            f"({baseline.get('repo_root')!r} != {str(repo_root)!r})")
        if baseline.get("branch") != branch:
            reasons.append(f"branch differs from session-start baseline: "
                            f"{baseline.get('branch')!r} -> {branch!r}")
        if baseline.get("worktree") != str(repo_root):
            reasons.append(f"worktree differs from session-start baseline: "
                            f"{baseline.get('worktree')!r} -> {str(repo_root)!r}")

    owners: dict[str, list[str]] = defaultdict(list)
    for entry in worktrees(repo_root):
        wt_branch = entry.get("branch")
        if wt_branch:
            owners[wt_branch].append(entry.get("path", UNKNOWN))
    if branch in owners:
        other_owners = [p for p in owners[branch] if p != str(repo_root)]
        if other_owners:
            reasons.append(f"branch {branch!r} is checked out in another worktree too: {other_owners}")

    status = "FAIL_CLOSED" if reasons else "PASS"

    return {
        "routine": "precommit",
        "generated_at": _now_iso(),
        "status": status,
        "reasons": reasons,
        "repo_root": str(repo_root),
        "current_branch": branch,
        "current_head": head_sha(repo_root),
        "session_start_branch": (baseline or {}).get("branch", UNKNOWN),
        "session_start_worktree": (baseline or {}).get("worktree", UNKNOWN),
        "current_worktree": str(repo_root),
        "upstream": upstream_of(repo_root, branch),
        "ahead_behind_upstream": ahead_behind_upstream(repo_root, branch),
        "changed_files": changed_files,
        "staged_files": changes["staged"],
        "untracked_files": changes["untracked"],
    }


# ════════════════════════════════════════════════════════════════════════
# ROUTINE 2: Strategy Promotion Proof Gate
# ════════════════════════════════════════════════════════════════════════

_VERDICT_WORDS = (
    "VALIDATED", "PAPER PROOF", "PROMISING BUT UNPROVEN", "WAIT",
    "RESEARCH ONLY", "BROKEN", "RETIRE",
)


def _entries_from_dir(journal_dir: Path) -> list[dict[str, Any]]:
    from ops.proof_30_mnq import read_journal_entries
    return read_journal_entries(journal_dir)


def _decision_rows(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if e.get("type") not in ("OUTCOME", "READ_ERROR") and "decision" in e]


def _gate_attrition(decision_rows: list[dict[str, Any]]) -> dict[str, Any]:
    decision_counts: Counter = Counter()
    pre_risk: Counter = Counter()
    risk_rejections: Counter = Counter()
    for row in decision_rows:
        decision_counts[row.get("decision") or "UNLABELED"] += 1
        for gate in row.get("failed_gates") or []:
            pre_risk[str(gate)] += 1
        risk_check = row.get("risk_check") or {}
        if risk_check.get("result") == "REJECTED" and risk_check.get("failed_rule"):
            risk_rejections[str(risk_check["failed_rule"])] += 1
    return {
        "decision_type_counts": dict(decision_counts),
        "pre_risk_engine_gate_rejections": dict(pre_risk.most_common()),
        "risk_engine_rejections_by_rule": dict(risk_rejections.most_common()),
        "note": ("pre_risk_engine_gate_rejections comes from each decision row's `failed_gates` field "
                 "(market condition / trend / EMA / confluence / etc, whichever the strategy's detector "
                 "populates); risk_engine_rejections_by_rule comes from RiskEngine's own ordered "
                 "failed_rule -- both are read directly from the journal this replay run produced, "
                 "not recomputed."),
    }


def _execution_accounting(entries: list[dict[str, Any]], instruments: list[str]) -> dict[str, Any]:
    from ops.proof_30_mnq import pair_resolved_trades, classify_outcome  # noqa: F401 (classify used inside to_summary)

    per_instrument: dict[str, Any] = {}
    all_summaries: list[dict[str, Any]] = []
    unmatched_total = 0
    for instrument in instruments:
        resolved, unmatched_outcomes = pair_resolved_trades(entries, instrument=instrument, limit=1_000_000)
        summaries = [rt.to_summary() for rt in resolved]
        all_summaries.extend(summaries)
        unmatched_total += len(unmatched_outcomes)
        per_instrument[instrument] = {
            "resolved_count": len(resolved),
            "unmatched_outcomes_count": len(unmatched_outcomes),
            "category_counts": dict(Counter(s["category"] for s in summaries)),
        }

    approved_trade_rows = [
        e for e in entries
        if e.get("decision") == "TRADE" and (e.get("risk_check") or {}).get("result") == "APPROVED"
    ]
    entry_attempts = len(approved_trade_rows)
    category_totals = Counter(s["category"] for s in all_summaries)
    resolved_total = len(all_summaries)
    cancellations_or_no_fills = category_totals.get("cancelled_nofill", 0)
    ambiguous = category_totals.get("reconciler_touched", 0) + category_totals.get("other", 0)
    fills = resolved_total - cancellations_or_no_fills
    legitimately_open = entry_attempts - resolved_total

    identity_ok = unmatched_total == 0 and legitimately_open >= 0
    accounting = {
        "entry_attempts": entry_attempts,
        "fills": fills,
        "cancellations_or_known_no_fills": cancellations_or_no_fills,
        "ambiguous_or_reconciler_touched": ambiguous,
        "resolved_outcomes": resolved_total,
        "legitimately_open": legitimately_open if legitimately_open >= 0 else None,
        "unmatched_outcomes": unmatched_total,
        "per_instrument": per_instrument,
        "accounting_identity_check": (
            "PASS" if identity_ok else
            "MISMATCH -- see unmatched_outcomes / negative legitimately_open below; "
            "do not trust downstream performance numbers until reconciled"
        ),
    }
    return accounting, all_summaries


def _performance_from_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    resolved_pnl = [s for s in summaries if s["category"] in ("filled_win_loss", "breakeven") and s.get("pnl_dollars") is not None]
    if not resolved_pnl:
        return {"note": "no resolved win/loss/breakeven trades with pnl_dollars in this run"}
    pnls = [float(s["pnl_dollars"]) for s in resolved_pnl]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    def _by(key: str) -> dict[str, Any]:
        buckets: dict[str, list[float]] = defaultdict(list)
        for s in resolved_pnl:
            buckets[str(s.get(key) or UNKNOWN)].append(float(s["pnl_dollars"]))
        return {
            k: {"n": len(v), "sum_pnl_dollars": round(sum(v), 2), "win_rate": round(sum(1 for x in v if x > 0) / len(v), 4)}
            for k, v in buckets.items()
        }

    running, peak, max_dd = 0.0, 0.0, 0.0
    streak, worst_streak = 0, 0
    for p in pnls:
        running += p
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
        streak = streak + 1 if p < 0 else 0
        worst_streak = max(worst_streak, streak)

    top_winner = max(wins) if wins else 0.0
    return {
        "sample_size": len(pnls),
        "net_pnl_dollars": round(sum(pnls), 2),
        "expectancy_dollars": round(sum(pnls) / len(pnls), 4),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "max_drawdown_dollars": round(max_dd, 2),
        "worst_loss_streak": worst_streak,
        "top_winner_concentration": round(top_winner / gross_win, 4) if gross_win > 0 else None,
        "instrument_split": _by("instrument"),
        "direction_split": _by("direction"),
        "slippage_sensitivity": NOT_EVALUATED,
        "session_split": NOT_EVALUATED,
        "period_split_h1_h2": NOT_EVALUATED,
        "note": ("slippage_sensitivity / session_split / period_split require re-running this gate "
                 "with a shifted-tolerance config or a chronologically-split candle corpus; out of "
                 "scope for one invocation -- rerun with --candles split into two halves for H1/H2, "
                 "or with different ENTRY_SLIPPAGE_TOLERANCE_TICKS_* for slippage sensitivity."),
    }


def _classify_promotion(accounting: dict[str, Any], performance: dict[str, Any], baseline_note: str) -> dict[str, Any]:
    attempts = accounting["entry_attempts"]
    fills = accounting["fills"]

    tiers = {
        "research_result": baseline_note,
        "runtime_parity": (
            "PROVEN -- candidates reached RiskEngine and PaperBroker through the real "
            "ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker path"
            if attempts > 0 else
            "VACUOUS -- zero candidates ever reached RiskEngine; parity is unproven because "
            "nothing ran through the real path to prove or disprove"
        ),
        "paper_forward_evidence": (
            "NOT_ESTABLISHED_BY_THIS_RUN -- this gate proves runtime parity via replay, not "
            "live/demo paper-forward history; paper-forward evidence can only come from actual "
            "journal history accumulated on the running box"
        ),
    }

    if accounting["accounting_identity_check"] != "PASS":
        classification, reason = "UNSAFE", (
            "accounting identity violated (unmatched outcomes and/or negative open-position count) "
            "-- results are not trustworthy until the underlying journal/replay run is reconciled"
        )
    elif attempts == 0:
        classification, reason = "WAIT", "zero candidates reached RiskEngine through the real executable path"
    elif fills == 0:
        classification, reason = "BROKEN", (
            f"zero executable fills across {attempts} approved candidate(s) -- the real system "
            "rejected or failed to fill the entire population despite candidates existing "
            "(the Miyagi / 60M 3-2-2 failure pattern)"
        )
    elif "note" in performance and performance.get("sample_size") is None:
        classification, reason = "PROMISING BUT UNPROVEN", "no resolved win/loss/breakeven trades with pnl data to score"
    else:
        n = performance.get("sample_size", 0)
        pf = performance.get("profit_factor")
        expectancy = performance.get("expectancy_dollars")
        if n < 30:
            classification, reason = "PROMISING BUT UNPROVEN", f"resolved sample too small (n={n} < 30) to draw a conclusion"
        elif pf is None or expectancy is None or expectancy <= 0 or pf < 1.0:
            classification, reason = "BROKEN", f"negative or non-positive expectancy through the real path (pf={pf}, expectancy={expectancy})"
        else:
            classification, reason = "PROMISING BUT UNPROVEN", (
                f"n={n}, profit_factor={pf}, expectancy={expectancy} through the real executable path -- "
                "capped at PROMISING BUT UNPROVEN because VALIDATED requires actual paper-forward evidence, "
                "which this offline replay gate cannot establish"
            )

    return {"evidence_tiers": tiers, "classification": classification, "reason": reason}


def promotion_gate_report(
    repo_root: Path,
    *,
    strategy: str,
    candle_paths: list[str],
    baseline_evidence_path: Optional[str] = None,
    risk_rules_path: str = "risk_rules.yaml",
    replay_log_dir: Optional[str] = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "routine": "promotion",
        "generated_at": _now_iso(),
        "strategy": strategy,
        "candle_paths": candle_paths,
    }

    if not candle_paths:
        report["classification"] = {
            "classification": "WAIT",
            "reason": "no --candles corpus provided; this gate cannot drive the real "
                      "ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker path without one",
        }
        return report

    try:
        from config.settings import load_config
        base_config = load_config(str(repo_root / risk_rules_path))
    except Exception as exc:
        report["classification"] = {
            "classification": "UNSAFE",
            "reason": f"could not load production config ({type(exc).__name__}: {exc}) -- "
                      "refusing to fabricate a promotion verdict without the real config",
        }
        return report

    if strategy not in (base_config.enabled_concepts or []):
        report["identity_parity"] = {
            "candidate_identity_parity": "NOT_EVALUATED",
            "note": f"{strategy!r} is not in risk_rules.yaml enabled_concepts "
                    f"({base_config.enabled_concepts!r}) -- isolating it for this replay run anyway, "
                    "but note the strategy is not enabled in the reviewed production config as-is",
        }
    isolated_config = dc_replace(base_config, enabled_concepts=[strategy])

    import tempfile
    from replay.replay_engine import ReplayEngine

    log_dir = Path(replay_log_dir) if replay_log_dir else Path(tempfile.mkdtemp(prefix="afs_promotion_gate_"))
    log_dir.mkdir(parents=True, exist_ok=True)
    engine = ReplayEngine(config=isolated_config, log_dir=str(log_dir))

    try:
        if len(candle_paths) == 1:
            replay_result = engine.run(candle_paths[0])
        else:
            replay_result = engine.run_many(candle_paths)
        replay_dict = replay_result.to_dict()
    except Exception as exc:
        report["classification"] = {
            "classification": "UNSAFE",
            "reason": f"ReplayEngine raised {type(exc).__name__}: {exc} -- cannot prove this strategy "
                      "through the real path if the real path itself fails to run",
        }
        report["replay_log_dir"] = str(log_dir)
        return report

    entries = _entries_from_dir(log_dir)
    decision_rows = _decision_rows(entries)
    instruments = sorted({(e.get("instrument") or "").upper() for e in entries if e.get("instrument")} - {""})

    raw_candidate_count = sum(1 for e in decision_rows)
    report["identity_parity"] = report.get("identity_parity", {})
    report["identity_parity"].update({
        "raw_candidate_count": raw_candidate_count,
        "candles_processed": replay_dict.get("candles_processed"),
        "instruments_observed": instruments,
        "candidate_identity_parity": NOT_EVALUATED if not baseline_evidence_path else "SEE baseline_comparison",
        "direction_parity": NOT_EVALUATED if not baseline_evidence_path else "SEE baseline_comparison",
        "entry_stop_target_parity": NOT_EVALUATED if not baseline_evidence_path else "SEE baseline_comparison",
        "timeframe_parity": NOT_EVALUATED if not baseline_evidence_path else "SEE baseline_comparison",
        "causal_data_availability": (
            "ASSUMED_CAUSAL -- ReplayEngine consumes the supplied candle file(s) strictly "
            "chronologically; this gate does not independently re-verify that any HTF/context "
            "lookups the strategy's detector performs are themselves free of lookahead"
        ),
        "lookahead_or_partial_bar_dependency": NOT_EVALUATED,
        "baseline_evidence_path": baseline_evidence_path,
    })
    if baseline_evidence_path:
        report["identity_parity"]["baseline_comparison"] = _compare_to_baseline(baseline_evidence_path, decision_rows)

    report["gate_attrition"] = _gate_attrition(decision_rows)

    accounting, summaries = _execution_accounting(entries, instruments)
    report["execution"] = accounting

    performance = _performance_from_summaries(summaries)
    performance["replay_engine_reported"] = replay_dict
    report["performance"] = performance

    report["execution_context"] = {
        "entry_fill_model": isolated_config.entry_fill_model,
        "entry_tolerance_ticks_by_root": isolated_config.entry_tolerance_ticks_by_root,
        "max_open_positions": isolated_config.max_open_positions,
        "position_sizing": {
            "starting_balance": isolated_config.position_sizing.starting_balance,
            "sizing_rules": [asdict(r) for r in isolated_config.position_sizing.sizing_rules],
        },
        "max_daily_loss": isolated_config.max_daily_loss,
        "max_drawdown_percent": isolated_config.max_drawdown_percent,
        "circuit_breaker_losses": isolated_config.circuit_breaker_losses,
        "strategy_permission_gate_enabled": isolated_config.strategy_permission_gate_enabled,
        "strategy_permission_status": (
            isolated_config.strategy_status.get(strategy, isolated_config.strategy_permission_default_status)
            if isolated_config.strategy_permission_gate_enabled else "GATE_DISABLED"
        ),
    }

    baseline_note = (
        f"standalone baseline evidence supplied at {baseline_evidence_path}"
        if baseline_evidence_path else
        "no standalone baseline evidence supplied -- research-result tier not compared"
    )
    report["classification"] = _classify_promotion(accounting, performance, baseline_note)
    report["replay_log_dir"] = str(log_dir)
    return report


def _compare_to_baseline(baseline_path: str, decision_rows: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        raw = json.loads(Path(baseline_path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {"comparable": False, "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(raw, dict) or "trades" not in raw and "candidates" not in raw:
        return {
            "comparable": False,
            "note": "baseline file does not have a recognizable 'trades' or 'candidates' list; "
                    "this gate does not know this baseline's schema well enough to diff it "
                    "automatically -- compare manually",
        }
    baseline_list = raw.get("trades") or raw.get("candidates") or []
    return {
        "comparable": True,
        "baseline_candidate_count": len(baseline_list),
        "gate_candidate_count": len(decision_rows),
        "count_delta": len(decision_rows) - len(baseline_list),
        "note": "count-level comparison only -- this gate does not attempt per-candidate "
                "timestamp/id matching against an arbitrary baseline schema",
    }


# ════════════════════════════════════════════════════════════════════════
# ROUTINE 3: Daily Reconciliation + Trade Chain Integrity
# ════════════════════════════════════════════════════════════════════════

def _stale_pr_cutoff_days() -> int:
    return int(os.getenv("PROJECT_CHECK_STALE_PR_DAYS", "14"))


def github_repo_reconciliation(repo_root: Path, *, today: date) -> dict[str, Any]:
    all_prs = gh_prs_all(repo_root)
    if all_prs is UNKNOWN or not isinstance(all_prs, list):
        return {
            "checked": False,
            "note": "gh CLI not available or call failed -- PR-level reconciliation is UNKNOWN "
                    "in this environment; branch/worktree hygiene below is unaffected",
            "opened_today": UNKNOWN, "merged_today": UNKNOWN, "closed_unmerged_today": UNKNOWN,
            "open_prs": UNKNOWN, "stale_prs": UNKNOWN,
        }
    today_str = today.isoformat()
    stale_cutoff_days = _stale_pr_cutoff_days()
    opened_today = [p for p in all_prs if str(p.get("createdAt", "")).startswith(today_str)]
    merged_today = [p for p in all_prs if p.get("mergedAt") and str(p["mergedAt"]).startswith(today_str)]
    closed_unmerged_today = [
        p for p in all_prs
        if p.get("state") == "CLOSED" and not p.get("mergedAt")
        and str(p.get("closedAt", "")).startswith(today_str)
    ]
    open_prs = [p for p in all_prs if p.get("state") == "OPEN"]
    stale_prs = []
    for p in open_prs:
        updated = p.get("updatedAt")
        if not updated:
            continue
        try:
            age_days = (datetime.now(timezone.utc) - datetime.fromisoformat(updated.replace("Z", "+00:00"))).days
        except ValueError:
            continue
        if age_days >= stale_cutoff_days:
            stale_prs.append({"number": p.get("number"), "title": p.get("title"), "age_days": age_days})
    return {
        "checked": True,
        "opened_today": [p.get("number") for p in opened_today],
        "merged_today": [p.get("number") for p in merged_today],
        "closed_unmerged_today": [p.get("number") for p in closed_unmerged_today],
        "open_pr_count": len(open_prs),
        "open_pr_numbers": [p.get("number") for p in open_prs],
        "stale_pr_cutoff_days": stale_cutoff_days,
        "stale_prs": stale_prs,
    }


def branch_worktree_hygiene(repo_root: Path) -> dict[str, Any]:
    branches = local_branches(repo_root)
    code, remote_out, _ = _git(repo_root, "branch", "-r", "--format=%(refname:short)")
    remote_branches = sorted(b for b in remote_out.splitlines() if b and not b.endswith("/HEAD")) if code == 0 else []
    wts = worktrees(repo_root)
    dirty_worktrees = []
    for wt in wts:
        path = wt.get("path")
        if not path:
            continue
        lines, err = status_porcelain(Path(path))
        if err:
            dirty_worktrees.append({"path": path, "status": UNKNOWN, "error": err})
        elif lines:
            dirty_worktrees.append({"path": path, "dirty_entry_count": len(lines)})

    stale_merged = sorted(
        b["name"] for b in branches
        if b["name"] != "main"
        and _git(repo_root, "merge-base", "--is-ancestor", b["name"], "origin/main")[0] == 0
    )
    return {
        "stale_merged_local_branches": stale_merged,
        "active_worktrees": wts,
        "dirty_worktrees": dirty_worktrees,
        "branches_tracking_deleted_remotes": sorted(b["name"] for b in branches if b["track"] and "gone" in b["track"]),
        "local_only_branches": sorted(b["name"] for b in branches if not b["upstream"]),
        "local_main_relationship": local_main_relationship(repo_root),
        "remote_branches": remote_branches,
        "stash_count": len(stash_list(repo_root)),
    }


def strategy_inventory_table(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "## Master Table")
    except StopIteration:
        return []
    rows = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if cells[0] in ("Strategy", "") or all(set(c) <= {"-", " ", ":"} for c in cells):
            continue
        verdict_cell = cells[-1]
        verdict = next((w for w in _VERDICT_WORDS if w in verdict_cell.upper()), verdict_cell)
        rows.append({"strategy": cells[0], "verdict_raw": verdict_cell, "verdict": verdict})
    return rows


def _normalize_strategy_key(name: str) -> str:
    name = re.sub(r"\(.*?\)", "", name)
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def strategy_source_of_truth(repo_root: Path) -> dict[str, Any]:
    inventory_path = repo_root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    rows = strategy_inventory_table(inventory_path)
    try:
        from config.settings import load_config
        config = load_config(str(repo_root / "risk_rules.yaml"))
    except Exception as exc:
        return {"checked": False, "error": f"{type(exc).__name__}: {exc}", "inventory_rows": len(rows)}

    enabled = set(config.enabled_concepts or [])
    strategy_status = config.strategy_status or {}
    gate_on = config.strategy_permission_gate_enabled
    default_status = config.strategy_permission_default_status

    findings = []
    unmatched = []
    known_keys = {c: c for c in enabled | set(strategy_status)}
    for row in rows:
        key = _normalize_strategy_key(row["strategy"])
        concept = next(
            (c for c in known_keys if _normalize_strategy_key(c) == key
             or key in _normalize_strategy_key(c) or _normalize_strategy_key(c) in key),
            None,
        )
        if concept is None:
            unmatched.append(row["strategy"])
            continue
        runtime_enabled = concept in enabled
        runtime_permission = strategy_status.get(concept, default_status) if gate_on else "GATE_DISABLED"
        verdict = row["verdict"]
        if verdict in ("VALIDATED", "PAPER PROOF") and not runtime_enabled:
            findings.append({
                "strategy": row["strategy"], "matched_concept": concept, "severity": "warning",
                "issue": f"inventory verdict is {verdict!r} but {concept!r} is not in enabled_concepts",
            })
        if verdict in ("BROKEN", "RETIRE") and runtime_enabled:
            findings.append({
                "strategy": row["strategy"], "matched_concept": concept, "severity": "error",
                "issue": f"inventory verdict is {verdict!r} but {concept!r} is still in enabled_concepts",
            })
        if runtime_enabled and runtime_permission == "PAPER_ELIGIBLE" and verdict not in ("VALIDATED", "PAPER PROOF"):
            findings.append({
                "strategy": row["strategy"], "matched_concept": concept, "severity": "warning",
                "issue": f"runtime holds {concept!r} PAPER_ELIGIBLE but inventory verdict is only {verdict!r}",
            })

    return {
        "checked": True,
        "inventory_path": str(inventory_path),
        "inventory_row_count": len(rows),
        "unmatched_inventory_rows": unmatched,
        "note_on_matching": (
            "best-effort name normalization between Strategy_Inventory.md rows and "
            "risk_rules.yaml enabled_concepts/strategy_status keys -- an unmatched row is "
            "NOT reported as drift, only as unverifiable by this heuristic"
        ),
        "drift_findings": findings,
    }


def trade_chain_integrity(repo_root: Path, journal_dir: Path, day: date) -> dict[str, Any]:
    from ops.proof_30_mnq import read_journal_entries

    all_entries = read_journal_entries(journal_dir, through_date=day.isoformat())
    day_file = f"journal_{day.isoformat()}.jsonl"
    day_entries = [e for e in all_entries if Path(e.get("_path", "")).name == day_file]

    if not day_entries:
        return {
            "date": day.isoformat(),
            "overall": "PASS",
            "note": f"no journal entries found for {day_file} under {journal_dir} -- nothing to reconcile "
                    "(this is expected on a day the system did not run, e.g. a non-trading day)",
        }

    instruments = sorted({(e.get("instrument") or "").upper() for e in day_entries if e.get("instrument")} - {""})
    accounting, summaries = _execution_accounting(day_entries, instruments)

    sub_audits: dict[str, Any] = {}
    try:
        from ops.journal_label_audit import build_audit as label_build_audit
        sub_audits["journal_label_audit"] = label_build_audit(journal_dir=journal_dir)
    except Exception as exc:
        sub_audits["journal_label_audit"] = {"error": f"{type(exc).__name__}: {exc}"}

    try:
        from ops.audit_plain_cancelled import build_audit as cancelled_build_audit
        sub_audits["audit_plain_cancelled"] = cancelled_build_audit(journal_dir)
    except Exception as exc:
        sub_audits["audit_plain_cancelled"] = {"error": f"{type(exc).__name__}: {exc}"}

    try:
        from ops.reconciler_outcome_audit import build_audit_report
        overrides_doc = repo_root / "docs" / "proof-operator-overrides.md"
        sub_audits["reconciler_outcome_audit"] = build_audit_report(
            journal_dir=journal_dir,
            overrides_doc=overrides_doc if overrides_doc.exists() else None,
            from_date=day.isoformat(), to_date=day.isoformat(),
        )
    except Exception as exc:
        sub_audits["reconciler_outcome_audit"] = {"error": f"{type(exc).__name__}: {exc}"}

    try:
        from ops.strategy_intent_audit import build_audit as intent_build_audit
        sub_audits["strategy_intent_audit"] = intent_build_audit(journal_dir=journal_dir)
    except Exception as exc:
        sub_audits["strategy_intent_audit"] = {"error": f"{type(exc).__name__}: {exc}"}

    label_issues = sub_audits.get("journal_label_audit", {}).get("issues", []) if isinstance(sub_audits.get("journal_label_audit"), dict) else []
    label_errors = [i for i in label_issues if i.get("severity") == "error"]

    reasons: list[str] = []
    if accounting["unmatched_outcomes"] > 0:
        reasons.append(f"{accounting['unmatched_outcomes']} unmatched outcome row(s) (OUTCOME with no paired TRADE this day)")
    if accounting["legitimately_open"] is None:
        reasons.append("more resolved outcomes than approved TRADE rows this day -- accounting cannot balance")
    if label_errors:
        reasons.append(f"{len(label_errors)} error-severity journal_label_audit issue(s)")

    overall = "FAIL" if reasons else "PASS"

    result = {
        "date": day.isoformat(),
        "overall": overall,
        "reasons": reasons,
        "attempts": accounting["entry_attempts"],
        "fills": accounting["fills"],
        "no_fills": accounting["cancellations_or_known_no_fills"],
        "resolved": accounting["resolved_outcomes"],
        "legitimate_opens": accounting["legitimately_open"],
        "orphans_unmatched_outcomes": accounting["unmatched_outcomes"],
        "accounting_identity_check": accounting["accounting_identity_check"],
        "broker_journal_parity": NOT_EVALUATED,
        "stale_working_orders": NOT_EVALUATED,
        "duplicate_order_identities": NOT_EVALUATED,
        "sub_audits": sub_audits,
        "note": (
            "broker_journal_parity / stale_working_orders / duplicate_order_identities are "
            "NOT_EVALUATED: they require a live broker read (this routine is offline/read-only "
            "against the journal only) or a confirmed journal order-id schema this gate does not "
            "assume without verifying against the live box"
        ),
    }
    return result


def daily_report(repo_root: Path, *, log_dir: str = "logs", day: Optional[date] = None) -> dict[str, Any]:
    day = day or datetime.now(timezone.utc).date()
    journal_dir = Path(log_dir)
    if not journal_dir.is_absolute():
        journal_dir = repo_root / journal_dir

    report: dict[str, Any] = {
        "routine": "daily",
        "generated_at": _now_iso(),
        "date": day.isoformat(),
    }

    report["github_repo_reconciliation"] = github_repo_reconciliation(repo_root, today=day)
    report["branch_worktree_hygiene"] = branch_worktree_hygiene(repo_root)
    open_prs = gh_open_prs(repo_root)
    open_pr_heads = {p.get("headRefName") for p in open_prs if p.get("headRefName")} if isinstance(open_prs, list) else None
    report["evidence_preservation"] = evidence_preservation_candidates(repo_root, open_pr_head_branches=open_pr_heads)
    report["deployed_state"] = runtime_snapshot(repo_root, log_dir=log_dir)
    report["strategy_source_of_truth"] = strategy_source_of_truth(repo_root)

    try:
        report["automation_evidence"] = _automation_evidence(repo_root, log_dir)
    except Exception as exc:
        report["automation_evidence"] = {"error": f"{type(exc).__name__}: {exc}"}

    try:
        report["trade_chain_integrity"] = trade_chain_integrity(repo_root, journal_dir, day)
    except Exception as exc:
        report["trade_chain_integrity"] = {"date": day.isoformat(), "overall": UNKNOWN, "error": f"{type(exc).__name__}: {exc}"}

    blockers = []
    for candidate in report["evidence_preservation"].get("candidates_missing_archive_tag", []):
        blockers.append(f"BLOCKER: branch {candidate['branch']} has no matching archive tag (verify manually)")
    if report["trade_chain_integrity"].get("overall") == "FAIL":
        blockers.extend(report["trade_chain_integrity"].get("reasons", []))
    for finding in report["strategy_source_of_truth"].get("drift_findings", []):
        if finding.get("severity") == "error":
            blockers.append(f"strategy inventory drift: {finding['issue']}")

    report["overall_status"] = "BLOCKED" if blockers else "OK"
    report["blockers"] = blockers
    return report


def _automation_evidence(repo_root: Path, log_dir: str) -> dict[str, Any]:
    from ops.automation_evidence import automation_evidence_status
    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = repo_root / log_path
    return automation_evidence_status(log_path)


# ════════════════════════════════════════════════════════════════════════
# Human-readable rendering
# ════════════════════════════════════════════════════════════════════════

def _print_human(routine: str, report: dict[str, Any]) -> None:
    if routine == "daily":
        tc = report.get("trade_chain_integrity", {})
        if tc.get("overall") == "PASS":
            print("TRADE CHAIN: PASS")
            print(f"{tc.get('attempts', 0)} attempts")
            print(f"{tc.get('fills', 0)} fills")
            print(f"{tc.get('no_fills', 0)} no-fills")
            print(f"{tc.get('resolved', 0)} resolved")
            print(f"{tc.get('legitimate_opens', 0)} legitimate opens")
            print(f"{tc.get('orphans_unmatched_outcomes', 0)} orphans")
            print("broker/journal parity: NOT_EVALUATED (offline, journal-only)")
            print()
        print(f"OVERALL: {report['overall_status']}")
        if report.get("blockers"):
            print("BLOCKERS:")
            for b in report["blockers"]:
                print(f"  - {b}")
        print()
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


# ════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ops.project_check",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root", default=None, help="defaults to the repo containing this file")
    sub = parser.add_subparsers(dest="routine", required=True)

    p_start = sub.add_parser("session-start", help="git/worktree/branch hygiene + runtime posture snapshot")
    p_start.add_argument("--fetch", action="store_true", help="git fetch origin --prune before reporting (off by default)")
    p_start.add_argument("--log-dir", default="logs")
    p_start.add_argument("--json", action="store_true")
    p_start.add_argument("--no-save-state", action="store_true", help="don't write the precommit baseline")

    p_pre = sub.add_parser("precommit", help="read-only fail-closed drift check vs the session-start baseline")
    p_pre.add_argument("--json", action="store_true")

    p_promo = sub.add_parser("promotion", help="strategy promotion proof gate (real executable path)")
    p_promo.add_argument("--strategy", required=True, help="enabled_concepts name to isolate and run")
    p_promo.add_argument("--candles", nargs="*", default=[], help="one or more chronological candle JSONL files")
    p_promo.add_argument("--baseline-evidence", default=None, help="optional prior standalone evidence JSON for count-level parity")
    p_promo.add_argument("--risk-rules", default="risk_rules.yaml")
    p_promo.add_argument("--replay-log-dir", default=None, help="defaults to a fresh temp dir; never the live logs/ dir")
    p_promo.add_argument("--out", default=None)
    p_promo.add_argument("--json", action="store_true")

    p_daily = sub.add_parser("daily", help="daily reconciliation + trade chain integrity")
    p_daily.add_argument("--log-dir", default="logs")
    p_daily.add_argument("--date", type=date.fromisoformat, default=None)
    p_daily.add_argument("--out", default=None)
    p_daily.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else (find_repo_root(REPO_ROOT) or REPO_ROOT)

    if args.routine == "session-start":
        report = session_start_report(repo_root, fetch=args.fetch, log_dir=args.log_dir)
        if not args.no_save_state:
            state_path = save_session_state(repo_root, report)
            report["session_state_written_to"] = str(state_path)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            _print_human("session-start", report)
        return 1 if report["overall_status"] != "OK" else 0

    if args.routine == "precommit":
        report = precommit_report(repo_root)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            _print_human("precommit", report)
        return 1 if report["status"] != "PASS" else 0

    if args.routine == "promotion":
        report = promotion_gate_report(
            repo_root, strategy=args.strategy, candle_paths=args.candles,
            baseline_evidence_path=args.baseline_evidence, risk_rules_path=args.risk_rules,
            replay_log_dir=args.replay_log_dir,
        )
        if args.out:
            Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            _print_human("promotion", report)
        classification = report.get("classification", {}).get("classification")
        return 0 if classification in ("VALIDATED", "PROMISING BUT UNPROVEN") else 1

    if args.routine == "daily":
        report = daily_report(repo_root, log_dir=args.log_dir, day=args.date)
        if args.out:
            Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            _print_human("daily", report)
        return 1 if report["overall_status"] != "OK" else 0

    parser.error(f"unknown routine {args.routine!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
