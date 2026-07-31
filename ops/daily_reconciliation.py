"""Read-only Daily Reconciliation + Trade Chain Integrity.

One daily source-of-truth pass combining PR/branch/worktree hygiene,
evidence preservation, deployed-state tracking, strategy-status
reconciliation, and actual paper/demo trade-chain integrity for the
window since the prior checkpoint (default: since the start of today,
UTC).

This composes existing read-only machinery only:
``ops.repo_hygiene`` (git/GitHub hygiene), ``ops.live_box_guard``
(deployed-state drift), ``ops.evidence_lane_health`` (active lanes),
``ops.build_honest_baseline`` / ``ops.reconciler_outcome_audit`` /
``ops.audit_plain_cancelled`` / ``ops.proof_30_mnq`` (trade-chain truth).
It never cancels an order, flattens a position, modifies a broker order,
repairs a journal, synthesizes an OUTCOME, or retries anything. On any
discrepancy it reports; it never fixes.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops import repo_hygiene
from ops.audit_plain_cancelled import build_audit as build_cancelled_audit
from ops.build_honest_baseline import INSTRUMENTS, build_baseline
from ops.evidence_lane_health import build_snapshot as evidence_lane_snapshot
from ops.live_box_guard import live_box_drift_report
from ops.proof_30_mnq import read_journal_entries
from ops.reconciler_outcome_audit import build_audit_report as build_reconciler_audit

STALE_PR_DAYS = 14


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _default_checkpoint(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _ts_on_or_after(value: str | None, checkpoint_iso: str) -> bool:
    return bool(value) and str(value) >= checkpoint_iso


# ------------------------------------------------------------------
# A. GitHub / repo reconciliation
# ------------------------------------------------------------------

def _pr_age_days(pr: dict[str, Any], now: datetime) -> int:
    created = pr.get("createdAt")
    if not created:
        return 0
    try:
        created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except ValueError:
        return 0
    return (now - created_dt).days


def _github_reconciliation(repo_root: Path, today_iso: str) -> dict[str, Any]:
    open_prs = repo_hygiene.gh_pr_list(repo_root, state="open")
    all_recent = repo_hygiene.gh_pr_list(repo_root, state="all", limit=100)
    if not open_prs["available"]:
        return {
            "available": False,
            "reason": open_prs["reason"],
            "opened_today": "UNKNOWN",
            "merged_today": "UNKNOWN",
            "closed_unmerged_today": "UNKNOWN",
            "open_prs": "UNKNOWN",
            "stale_open_prs": "UNKNOWN",
        }
    prs = all_recent["prs"] if all_recent["available"] else []
    opened_today = [p for p in prs if str(p.get("createdAt") or "")[:10] == today_iso]
    merged_today = [p for p in prs if str(p.get("mergedAt") or "")[:10] == today_iso]
    closed_unmerged_today = [
        p for p in prs
        if str(p.get("closedAt") or "")[:10] == today_iso and not p.get("mergedAt")
    ]
    now = _now()
    stale = [p for p in open_prs["prs"] if _pr_age_days(p, now) >= STALE_PR_DAYS]
    return {
        "available": True,
        "opened_today": opened_today,
        "merged_today": merged_today,
        "closed_unmerged_today": closed_unmerged_today,
        "open_prs": open_prs["prs"],
        "open_pr_count": len(open_prs["prs"]),
        "stale_open_prs": stale,
        "stale_threshold_days": STALE_PR_DAYS,
    }


def _branch_worktree_hygiene(repo_root: Path) -> dict[str, Any]:
    main_sync = repo_hygiene.main_sync_status(repo_root)
    wts = repo_hygiene.worktrees(repo_root)
    return {
        "stale_merged_local_branches": repo_hygiene.merged_local_branches(repo_root),
        "active_worktrees": wts,
        "dirty_worktrees": [w for w in wts if w.get("dirty")],
        "branches_tracking_deleted_remotes": repo_hygiene.branches_tracking_deleted_remotes(repo_root),
        "local_only_branches": repo_hygiene.local_only_branches(repo_root),
        "main_sync": main_sync,
        "stash_count": len(repo_hygiene.stashes(repo_root)),
    }


def _evidence_preservation(repo_root: Path, closed_unmerged_prs: Any) -> dict[str, Any]:
    if not isinstance(closed_unmerged_prs, list):
        return {"available": False, "reason": "closed-unmerged PR list unavailable (gh CLI missing?)", "blockers": []}
    blockers = []
    checked = []
    for pr in closed_unmerged_prs:
        ref = pr.get("headRefName")
        if not ref:
            continue
        evidence = repo_hygiene.branch_unique_vs_main(repo_root, f"origin/{ref}")
        checked.append({"pr": pr.get("number"), "branch": ref, **evidence})
        if evidence.get("resolvable") and evidence.get("unique_commit_count", 0) > 0 and not evidence.get("archive_tags"):
            blockers.append({
                "pr": pr.get("number"),
                "branch": ref,
                "unique_commit_count": evidence["unique_commit_count"],
                "reason": "unique commits with no archive/* tag preserving the tip",
            })
    return {"available": True, "checked": checked, "blockers": blockers}


# ------------------------------------------------------------------
# B. Deployed state
# ------------------------------------------------------------------

def _deployed_state(repo_root: Path, log_dir: Path) -> dict[str, Any]:
    drift = live_box_drift_report(repo_root=repo_root, log_dir=log_dir)
    try:
        lanes = evidence_lane_snapshot(log_dir)
    except Exception as exc:  # pragma: no cover - defensive
        lanes = {"error": str(exc)}
    return {
        "deployed_sha": drift.get("commit") or "UNKNOWN",
        "deployed_sha_matches_intended_release": drift.get("ok"),
        "risk_rules_sha256": drift.get("risk_rules_sha256") or "UNKNOWN",
        "active_runtime_overrides": drift.get("active_runtime_overrides") or [],
        "unpinned_runtime_overrides": drift.get("unpinned_runtime_overrides") or [],
        "runtime_evidence_source": drift.get("runtime_evidence_source") or "UNKNOWN",
        "active_paper_forward_lanes": [
            {"instrument": lane["instrument"], "lane": lane["lane"], "mode": lane["mode"], "status": lane["status"]}
            for lane in lanes.get("lanes", [])
        ] if isinstance(lanes, dict) and "lanes" in lanes else "UNKNOWN",
        "live_box_drift": drift,
    }


# ------------------------------------------------------------------
# C. Strategy source of truth
# ------------------------------------------------------------------

_VERDICT_RE = re.compile(r"\*\*([A-Z ]+)\*\*")


def _parse_strategy_inventory(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "Strategy" in line or set(line.strip()) <= {"|", "-", " "}:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        name = cells[0]
        verdict_cell = cells[-1]
        match = _VERDICT_RE.search(verdict_cell)
        verdict = match.group(1).strip() if match else verdict_cell
        if not name or name.lower() in ("strategy",):
            continue
        rows.append({"strategy": name, "verdict_raw": verdict_cell, "verdict": verdict})
    return rows


def _strategy_source_of_truth(inventory_path: Path, deployed_state: dict[str, Any]) -> dict[str, Any]:
    rows = _parse_strategy_inventory(inventory_path)
    lanes = deployed_state.get("active_paper_forward_lanes")
    flags: list[str] = []
    if isinstance(lanes, list):
        active_lane_names = {lane["lane"] for lane in lanes}
        for row in rows:
            if row["verdict"] in ("BROKEN", "RETIRE", "WAIT") and row["strategy"].lower().replace(" ", "_") in active_lane_names:
                flags.append(
                    f"{row['strategy']} is classified {row['verdict']!r} in Strategy_Inventory.md but "
                    "appears in an active paper-forward lane -- verify fail-closed state"
                )
    return {
        "inventory_path": str(inventory_path),
        "inventory_found": inventory_path.exists(),
        "rows": rows,
        "drift_flags": flags,
        "note": (
            "Verdict/lane-name matching is a heuristic (name normalization only); "
            "treat drift_flags as REVIEW candidates, not confirmed mismatches."
        ),
    }


# ------------------------------------------------------------------
# D. Trade chain integrity
# ------------------------------------------------------------------

def _approved_decision_counts(entries: list[dict[str, Any]], checkpoint_iso: str) -> dict[str, int]:
    counts = {inst: 0 for inst in INSTRUMENTS}
    for entry in entries:
        inst = str(entry.get("instrument") or "").upper()
        if inst not in counts:
            continue
        if entry.get("decision") != "TRADE":
            continue
        if (entry.get("risk_check") or {}).get("result") != "APPROVED":
            continue
        if not _ts_on_or_after(entry.get("ts"), checkpoint_iso):
            continue
        counts[inst] += 1
    return counts


def _duplicate_order_ids(entries: list[dict[str, Any]], checkpoint_iso: str) -> list[str]:
    seen: Counter[str] = Counter()
    for entry in entries:
        if not _ts_on_or_after(entry.get("ts"), checkpoint_iso):
            continue
        order_ids = entry.get("order_ids")
        if isinstance(order_ids, (list, tuple)):
            for oid in order_ids:
                if oid:
                    seen[str(oid)] += 1
        elif isinstance(order_ids, str) and order_ids:
            seen[order_ids] += 1
    return [oid for oid, count in seen.items() if count > 1]


def _trade_chain_integrity(journal_dir: Path, checkpoint_iso: str, overrides_doc: Path | None) -> dict[str, Any]:
    entries = read_journal_entries(journal_dir)
    baseline = build_baseline(journal_dir)
    reconciler_audit = build_reconciler_audit(journal_dir=journal_dir, overrides_doc=overrides_doc)
    cancelled_audit = build_cancelled_audit(journal_dir)
    approved_counts = _approved_decision_counts(entries, checkpoint_iso)
    duplicate_orders = _duplicate_order_ids(entries, checkpoint_iso)

    per_instrument: dict[str, Any] = {}
    total = {"attempts": 0, "fills": 0, "cancellations": 0, "rejects_unresolved": 0, "resolved": 0, "legitimately_open": 0}
    orphans_all: list[dict[str, Any]] = []
    unaudited_all: list[dict[str, Any]] = []
    suspects_all: list[dict[str, Any]] = []

    for inst in INSTRUMENTS:
        inst_report = baseline["instruments"][inst]
        window_rows = [r for r in inst_report["trades"] if _ts_on_or_after(r.get("trade_ts"), checkpoint_iso)]
        window_orphans = [
            o for o in inst_report["unmatched_outcomes"]
            if _ts_on_or_after(o.get("ts"), checkpoint_iso)
        ]
        filled = sum(1 for r in window_rows if r["category"] in ("filled_win_loss", "breakeven"))
        cancellations = sum(1 for r in window_rows if r["category"] == "cancelled_nofill")
        rejects_unresolved = sum(1 for r in window_rows if r["category"] in ("reconciler_touched", "unresolved_excluded", "other"))
        resolved = len(window_rows)
        attempts = approved_counts.get(inst, 0)
        legitimately_open = max(0, attempts - resolved)

        inst_unaudited = [
            item for item in reconciler_audit["unaudited"]
            if str(item.get("instrument") or "").upper() == inst
            and _ts_on_or_after(item.get("outcome_ts"), checkpoint_iso)
        ]
        inst_suspects = [
            row for row in cancelled_audit.get(inst, {}).get("suspect_rows", [])
            if _ts_on_or_after(row.get("outcome_ts"), checkpoint_iso)
        ]

        per_instrument[inst] = {
            "attempts": attempts,
            "fills": filled,
            "cancellations": cancellations,
            "rejects_unresolved": rejects_unresolved,
            "resolved": resolved,
            "legitimately_open": legitimately_open,
            "orphan_outcomes": window_orphans,
            "unaudited_reconciler_rows": inst_unaudited,
            "mislabeled_fill_suspects": inst_suspects,
            "accounting_identity_holds": attempts == (filled + cancellations + rejects_unresolved + legitimately_open),
        }
        for key in total:
            total[key] += per_instrument[inst][key]
        orphans_all.extend(window_orphans)
        unaudited_all.extend(inst_unaudited)
        suspects_all.extend(inst_suspects)

    stale_working_orders = "UNKNOWN (no live broker/order-state read in this pass; see /futures-execution-safety-audit)"

    passed = (
        not orphans_all
        and not unaudited_all
        and not suspects_all
        and not duplicate_orders
        and all(v["accounting_identity_holds"] for v in per_instrument.values())
    )

    return {
        "checkpoint": checkpoint_iso,
        "verdict": "PASS" if passed else "REVIEW",
        "summary_line": (
            f"TRADE CHAIN: {'PASS' if passed else 'REVIEW'}\n"
            f"{total['attempts']} attempts\n"
            f"{total['fills']} fills\n"
            f"{total['cancellations']} no-fills\n"
            f"{total['resolved']} resolved\n"
            f"{total['legitimately_open']} legitimate opens\n"
            f"{len(orphans_all)} orphans\n"
            f"duplicate order identities: {len(duplicate_orders)}\n"
            f"unaudited reconciler-touched rows: {len(unaudited_all)}\n"
            f"mislabeled-fill suspects: {len(suspects_all)}\n"
            f"stale working orders: {stale_working_orders}\n"
            f"broker/journal parity: NOT CHECKED (no live broker read in this read-only journal pass)"
        ),
        "totals": total,
        "per_instrument": per_instrument,
        "duplicate_order_identities": duplicate_orders,
        "stale_working_orders": stale_working_orders,
        "broker_journal_parity": (
            "NOT CHECKED: this routine reads the journal only. For live broker-state parity "
            "run /futures-execution-safety-audit or /futures-deployment-safety-audit."
        ),
    }


# ------------------------------------------------------------------
# Orchestration
# ------------------------------------------------------------------

def build_daily_report(
    *,
    repo_root: str | Path,
    journal_dir: str | Path,
    log_dir: str | Path,
    checkpoint: datetime | None = None,
    overrides_doc: str | Path | None = None,
    strategy_inventory_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    journal_path = Path(journal_dir)
    log_path = Path(log_dir)
    now = _now()
    checkpoint_dt = checkpoint or _default_checkpoint(now)
    checkpoint_iso = checkpoint_dt.isoformat()
    today_iso = now.date().isoformat()
    inventory_path = Path(strategy_inventory_path) if strategy_inventory_path else root / "docs" / "strategy-rules" / "Strategy_Inventory.md"

    github = _github_reconciliation(root, today_iso)
    branches = _branch_worktree_hygiene(root)
    evidence_preservation = _evidence_preservation(root, github.get("closed_unmerged_today"))
    deployed_state = _deployed_state(root, log_path)
    strategy_truth = _strategy_source_of_truth(inventory_path, deployed_state)
    trade_chain = _trade_chain_integrity(
        journal_path, checkpoint_iso, Path(overrides_doc) if overrides_doc else root / "docs" / "proof-operator-overrides.md"
    )

    return {
        "routine": "daily",
        "generated_at": now.isoformat(),
        "checkpoint": checkpoint_iso,
        "github": github,
        "branches_worktrees": branches,
        "evidence_preservation": evidence_preservation,
        "deployed_state": deployed_state,
        "strategy_source_of_truth": strategy_truth,
        "trade_chain_integrity": trade_chain,
    }
