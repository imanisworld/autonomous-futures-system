"""Daily Reconciliation + Trade Chain Integrity -- read only.

One daily source-of-truth pass combining PR/branch/worktree hygiene,
evidence preservation, deployed-state tracking, strategy-status
reconciliation, and trade-chain integrity. Deliberately not a fourth
routine -- it is folded in here per the operator's instruction.

Reuses (does not re-derive):
  - ops.session_safety for git/worktree/branch/gh reads and the runtime
    snapshot (live-box guard, active strategy lanes)
  - ops.strategy_intent_audit for journal candidate-audit rows
  - ops.trade_chain_audit for the trade-chain integrity pass

This module never edits docs/config, creates/deletes archive tags, repairs
a journal, cancels an order, flattens a position, or merges/deploys
anything. Every discrepancy is reported only.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ops import trade_chain_audit
from ops.session_safety import (
    UNKNOWN,
    _archive_tags,
    _branch_sync_status,
    _branches_tracking_deleted_remotes,
    _dirty_staged_untracked,
    _gh_json,
    _git,
    _git_lines,
    _local_only_branches,
    _repo_root,
    _runtime_snapshot,
    _stash_list,
    _unmerged_remote_branches_without_archive,
    _worktrees,
)

STALE_PR_DAYS = 7


# ------------------------------------------------------------------ github


def _all_prs(root: Path) -> tuple[list[dict[str, Any]] | None, str | None]:
    data, err = _gh_json(
        root,
        "pr",
        "list",
        "--state",
        "all",
        "--limit",
        "200",
        "--json",
        "number,title,headRefName,state,createdAt,mergedAt,closedAt,updatedAt,url,isDraft",
    )
    return data, err


def _github_section(root: Path, *, today: date) -> dict[str, Any]:
    prs, err = _all_prs(root)
    if prs is None:
        return {
            "available": False,
            "error": err,
            "opened_today": [],
            "merged_today": [],
            "closed_unmerged_today": [],
            "open_prs": [],
            "stale_prs": [],
        }
    today_str = today.isoformat()
    opened_today = [p for p in prs if str(p.get("createdAt") or "").startswith(today_str)]
    merged_today = [p for p in prs if str(p.get("mergedAt") or "").startswith(today_str)]
    closed_unmerged_today = [
        p
        for p in prs
        if str(p.get("closedAt") or "").startswith(today_str) and not p.get("mergedAt")
    ]
    open_prs = [p for p in prs if p.get("state") == "OPEN"]
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_PR_DAYS)
    stale_prs = []
    for p in open_prs:
        updated = p.get("updatedAt")
        if not updated:
            continue
        try:
            dt = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt < stale_cutoff:
            stale_prs.append(p)
    return {
        "available": True,
        "error": None,
        "opened_today": opened_today,
        "merged_today": merged_today,
        "closed_unmerged_today": closed_unmerged_today,
        "open_prs": open_prs,
        "stale_prs": stale_prs,
        "stale_threshold_days": STALE_PR_DAYS,
    }


# ------------------------------------------------------------- branch/repo


def _branches_section(root: Path) -> dict[str, Any]:
    worktrees = _worktrees(root)
    dirty_worktrees = []
    for wt in worktrees:
        path = wt.get("path")
        if not path:
            continue
        dsu = _dirty_staged_untracked(Path(path))
        if dsu["dirty_tracked"] or dsu["staged"] or dsu["untracked"]:
            dirty_worktrees.append({"path": path, **dsu})
    local_main_status, local_main_detail = _branch_sync_status(root, "main", "origin/main")
    remote_branches = set(
        line.replace("origin/", "", 1)
        for line in _git_lines(root, "branch", "-r", "--format=%(refname:short)")
        if line.startswith("origin/") and not line.endswith("/HEAD")
    )
    local_branches = set(
        _git_lines(root, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    )
    unexpected_remote = sorted(remote_branches - local_branches)
    return {
        "stale_merged_branches": _stale_merged_branches(root),
        "active_worktrees": worktrees,
        "dirty_worktrees": dirty_worktrees,
        "branches_tracking_deleted_remotes": _branches_tracking_deleted_remotes(root),
        "local_only_branches": _local_only_branches(root),
        "local_main_status": local_main_status,
        "local_main_detail": local_main_detail,
        "unexpected_remote_branches_not_local": unexpected_remote,
        "stash_count": len(_stash_list(root)),
        "stash_list": _stash_list(root),
    }


def _stale_merged_branches(root: Path) -> list[str]:
    """Local branches already merged into origin/main -- candidates for cleanup,
    never deleted here."""
    raw = _git(root, "branch", "--merged", "origin/main", "--format=%(refname:short)")
    if raw is None:
        return []
    return [line for line in raw.splitlines() if line.strip() and line.strip() not in ("main",)]


# ---------------------------------------------------------- evidence pres.


def _evidence_preservation_section(root: Path) -> dict[str, Any]:
    flagged = _unmerged_remote_branches_without_archive(root, root / "docs" / "BRANCH_ARCHIVE_INDEX.md")
    return {
        "closed_unmerged_branches_missing_archive_tag": flagged,
        "blocker_count": len(flagged),
        "archive_tags": _archive_tags(root),
        "note": (
            "Heuristic: a remote branch not an ancestor of origin/main and not listed "
            "in docs/BRANCH_ARCHIVE_INDEX.md or matched by an archive/* tag name. "
            "Never deletes a branch or creates a tag -- flags for human triage only."
        ),
    }


# ----------------------------------------------------------------- deployed


def _deployed_state_section(root: Path) -> dict[str, Any]:
    snapshot = _runtime_snapshot(root)
    guard = snapshot["live_box_guard"]
    return {
        "known_deployed_sha": snapshot["intended_deployed_release_sha"],
        "deployed_sha_matches_intended": (
            guard["status"] == "ok" if snapshot["intended_deployed_release_sha"] != UNKNOWN else UNKNOWN
        ),
        "active_evidence_epoch": snapshot["evidence_epoch"],
        "active_paper_forward_strategies": snapshot["active_paper_forward_strategy_lanes"],
        "entry_model_per_lane": snapshot["execution_mode_per_lane"],
        "effective_tolerance_per_lane": snapshot["effective_entry_tolerance"],
        "quantity_contract_cap": snapshot["quantity_contract_cap"],
        "runtime_mode_note": snapshot["runtime_config_vs_evidence_assumptions"],
        "live_box_guard_status": guard["status"],
        "live_box_guard_summary": guard["summary"],
    }


# ------------------------------------------------------- strategy-of-truth


_TABLE_ROW_RE = re.compile(r"^\|\s*(?P<strategy>[^|]+?)\s*\|.*\|\s*\*\*(?P<verdict>[A-Z][A-Z \-]+)\*\*\s*\|\s*$")
_ACTIVE_VERDICTS = {"VALIDATED", "PAPER PROOF"}
_INACTIVE_VERDICTS = {"BROKEN", "OVERFIT", "RETIRE", "WAIT", "UNSAFE"}


def _parse_strategy_inventory(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _TABLE_ROW_RE.match(line.strip())
        if not match:
            continue
        strategy = match.group("strategy").strip()
        if strategy.lower() in ("strategy", "---", ""):
            continue
        rows.append({"strategy": strategy, "verdict": match.group("verdict").strip()})
    return rows


def _slug(text: str) -> str:
    text = re.sub(r"\([^)]*\)", "", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _strategy_source_of_truth_section(root: Path, active_lanes: dict[str, Any]) -> dict[str, Any]:
    inventory_path = root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    rows = _parse_strategy_inventory(inventory_path)
    active_concepts: set[str] = set()
    if active_lanes.get("available"):
        for concepts in (active_lanes.get("active_lanes_by_instrument") or {}).values():
            active_concepts.update(concepts)

    drift: list[dict[str, str]] = []
    for row in rows:
        slug = _slug(row["strategy"])
        concept_active = any(
            slug in _slug(concept) or _slug(concept) in slug for concept in active_concepts
        )
        verdict = row["verdict"]
        if verdict in _ACTIVE_VERDICTS and not concept_active:
            drift.append({
                **row,
                "issue": "described as active/validated in Strategy_Inventory.md but no matching "
                "concept is enabled for any instrument in risk_rules.yaml (heuristic name match)",
            })
        elif verdict in _INACTIVE_VERDICTS and concept_active:
            drift.append({
                **row,
                "issue": "described as BROKEN/OVERFIT/RETIRE/WAIT/UNSAFE but a matching concept "
                "IS currently enabled for at least one instrument (heuristic name match)",
            })

    return {
        "inventory_path": str(inventory_path) if inventory_path.exists() else UNKNOWN,
        "inventory_rows_parsed": len(rows),
        "active_concepts_from_risk_rules": sorted(active_concepts),
        "flagged_drift": drift,
        "note": (
            "Name matching between Strategy_Inventory.md rows and risk_rules.yaml concept "
            "keys is a best-effort slug/substring heuristic, not an exact mapping -- treat "
            "flagged_drift as leads for a human to confirm, not settled findings."
        ),
    }


# --------------------------------------------------------------------- main


def build_daily_reconciliation(
    *,
    repo_root: str | Path | None = None,
    journal_dir: str | Path = "logs",
    since_date: str | None = None,
    status_url: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve() if repo_root else _repo_root()
    today = today or date.today()

    github = _github_section(root, today=today)
    branches = _branches_section(root)
    evidence = _evidence_preservation_section(root)
    deployed = _deployed_state_section(root)
    runtime_snapshot = _runtime_snapshot(root)
    strategy_truth = _strategy_source_of_truth_section(
        root, runtime_snapshot["active_paper_forward_strategy_lanes"]
    )
    trade_chain = trade_chain_audit.build_trade_chain_report(
        journal_dir=journal_dir,
        since_date=since_date,
        status_url=status_url,
    )

    return {
        "read_only": True,
        "generated_for_date": today.isoformat(),
        "repo_root": str(root),
        "github": github,
        "branches_worktrees": branches,
        "evidence_preservation": evidence,
        "deployed_state": deployed,
        "strategy_source_of_truth": strategy_truth,
        "trade_chain_integrity": trade_chain,
        "no_automatic_action": (
            "Read-only pass. Never deletes a branch/worktree, creates/deletes an archive "
            "tag, edits docs/config, repairs a journal, cancels an order, flattens a "
            "position, or merges/deploys anything."
        ),
    }


def format_daily_reconciliation(report: dict[str, Any]) -> str:
    lines = ["DAILY RECONCILIATION + TRADE CHAIN INTEGRITY", f"date: {report['generated_for_date']}", ""]

    gh = report["github"]
    lines.append("== GITHUB ==")
    if gh["available"]:
        lines += [
            f"opened today: {len(gh['opened_today'])}",
            f"merged today: {len(gh['merged_today'])}",
            f"closed-unmerged today: {len(gh['closed_unmerged_today'])}",
            f"open PRs: {len(gh['open_prs'])}",
            f"stale PRs (> {gh['stale_threshold_days']}d untouched): {len(gh['stale_prs'])}",
        ]
    else:
        lines.append(f"unavailable: {gh['error']}")
    lines.append("")

    br = report["branches_worktrees"]
    lines += [
        "== BRANCHES / WORKTREES ==",
        f"stale merged branches: {br['stale_merged_branches']}",
        f"active worktrees: {len(br['active_worktrees'])}",
        f"dirty worktrees: {len(br['dirty_worktrees'])}",
        f"branches tracking deleted remotes: {br['branches_tracking_deleted_remotes']}",
        f"local-only branches: {br['local_only_branches']}",
        f"local main vs origin/main: {br['local_main_status']}",
        f"unexpected remote branches (no local copy): {br['unexpected_remote_branches_not_local']}",
        f"stash count: {br['stash_count']}",
        "",
    ]

    ev = report["evidence_preservation"]
    lines += [
        "== EVIDENCE PRESERVATION ==",
        f"BLOCKER count (unique evidence, no archive tag): {ev['blocker_count']}",
    ]
    for item in ev["closed_unmerged_branches_missing_archive_tag"]:
        lines.append(f"  - BLOCKER: {item['branch']}")
    lines.append("")

    dep = report["deployed_state"]
    lines += [
        "== DEPLOYED STATE ==",
        f"known deployed sha: {dep['known_deployed_sha']}",
        f"matches intended: {dep['deployed_sha_matches_intended']}",
        f"active paper-forward strategies: {dep['active_paper_forward_strategies']}",
        f"entry model per lane: {dep['entry_model_per_lane']}",
        f"effective tolerance per lane: {dep['effective_tolerance_per_lane']}",
        f"quantity/contract cap: {dep['quantity_contract_cap']}",
        f"live box guard: {dep['live_box_guard_status']} -- {dep['live_box_guard_summary']}",
        "",
    ]

    st = report["strategy_source_of_truth"]
    lines += [
        "== STRATEGY SOURCE OF TRUTH ==",
        f"inventory rows parsed: {st['inventory_rows_parsed']}",
        f"flagged drift: {len(st['flagged_drift'])}",
    ]
    for item in st["flagged_drift"]:
        lines.append(f"  - {item['strategy']} ({item['verdict']}): {item['issue']}")
    lines.append("")

    lines.append("== TRADE CHAIN INTEGRITY ==")
    lines.append(trade_chain_audit.format_trade_chain_report(report["trade_chain_integrity"]))
    return "\n".join(lines)
