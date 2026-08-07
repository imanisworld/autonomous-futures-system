"""Daily Reconciliation + Trade Chain Integrity — ops.project_check's `daily` subcommand.

One daily read-only source-of-truth pass, combining PR hygiene, branch/
worktree hygiene, evidence preservation, deployed-state tracking, strategy-
status reconciliation, and actual paper/demo trade-chain integrity — not a
fourth routine, a composition of the same helpers session-start/promotion
already use.

This routine is READ ONLY and must NEVER cancel an order, flatten a
position, modify a broker order, repair a journal, synthesize an OUTCOME,
rewrite state, retry an execution, or submit an order. On any discrepancy it
reports / fails closed — nothing more. It writes exactly one local
bookkeeping file (the "since prior checkpoint" timestamp, under
logs/.project_check/, already gitignored) — no journal, config, or git state
is ever touched.

Trade-chain accounting reuses ops.proof_30_mnq's trade pairing and outcome
classification — the same trusted logic ops.project_check_promotion uses —
rather than re-deriving fill/no-fill/orphan logic a third time in this repo.

Known, explicitly-flagged limitation: this tool has no broker/live-account
access (no SSH, no broker API call) — broker-side parity fields
(flat_state_parity, stale_working_orders) report UNKNOWN, never a guessed
PASS, matching PR #371's system-status-snapshot precedent for the same gap.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops import proof_30_mnq as p30
from ops import project_check_git as pcg
from ops import project_check_runtime as pcr

UNKNOWN = "UNKNOWN"
STALE_PR_DAYS = 14
STATE_DIR_NAME = "logs/.project_check"
DAILY_CHECKPOINT_FILENAME = "daily_checkpoint.json"


def _journal_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    return Path(os.getenv("LOG_DIR", "logs"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today_utc_str() -> str:
    return _now().date().isoformat()


def _checkpoint_path(root: Path) -> Path:
    return root / STATE_DIR_NAME / DAILY_CHECKPOINT_FILENAME


def load_checkpoint(root: Path) -> str | None:
    path = _checkpoint_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("last_run_ts")


def write_checkpoint(root: Path, ts: str) -> Path:
    path = _checkpoint_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_run_ts": ts}, indent=2) + "\n", encoding="utf-8")
    return path


# ── A/B: GitHub + branch/worktree hygiene ───────────────────────────────

def github_repo_reconciliation(root: Path) -> dict[str, Any]:
    today = _today_utc_str()
    open_data = pcg.list_prs(root, state="open")
    all_data = pcg.list_prs(root, state="all", limit=200)

    if not all_data["available"]:
        return {
            "available": False,
            "detail": all_data["detail"],
            "opened_today": UNKNOWN,
            "merged_today": UNKNOWN,
            "closed_unmerged_today": UNKNOWN,
            "open_prs": open_data["prs"] if open_data["available"] else UNKNOWN,
            "stale_open_prs": UNKNOWN,
        }

    def _date(v: str | None) -> str | None:
        if not v:
            return None
        return v[:10]

    opened_today = [pr for pr in all_data["prs"] if _date(pr.get("createdAt")) == today]
    merged_today = [pr for pr in all_data["prs"] if _date(pr.get("mergedAt")) == today]
    closed_unmerged_today = [
        pr for pr in all_data["prs"] if _date(pr.get("closedAt")) == today and not pr.get("mergedAt")
    ]
    stale_open = []
    for pr in open_data["prs"] if open_data["available"] else []:
        updated = pr.get("updatedAt")
        if not updated:
            continue
        try:
            updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError:
            continue
        if (_now() - updated_dt).days >= STALE_PR_DAYS:
            stale_open.append({"number": pr.get("number"), "title": pr.get("title"), "updated_at": updated})

    return {
        "available": True,
        "opened_today": [{"number": p.get("number"), "title": p.get("title")} for p in opened_today],
        "merged_today": [{"number": p.get("number"), "title": p.get("title")} for p in merged_today],
        "closed_unmerged_today": [{"number": p.get("number"), "title": p.get("title")} for p in closed_unmerged_today],
        "open_prs": [{"number": p.get("number"), "title": p.get("title")} for p in (open_data["prs"] if open_data["available"] else [])],
        "stale_open_prs": stale_open,
        "closed_unmerged_all_time": [
            {"number": p.get("number"), "title": p.get("title"), "headRefName": p.get("headRefName")}
            for p in all_data["prs"]
            if p.get("closedAt") and not p.get("mergedAt")
        ],
    }


def branch_worktree_hygiene(root: Path) -> dict[str, Any]:
    status = pcg.porcelain_status(root)
    wts = pcg.worktrees(root)
    local_bs = pcg.local_branches(root)
    return {
        "current_branch": pcg.current_branch(root) or UNKNOWN,
        "local_main_relationship": pcg.sync_status(root, "origin/main", "main"),
        "worktrees": wts,
        "dirty_worktrees": [w["path"] for w in wts if w.get("path") and _worktree_is_dirty(Path(w["path"]))],
        "branches_tracking_deleted_remotes": [b["branch"] for b in local_bs if b["tracking_gone"]],
        "local_only_branches": [b["branch"] for b in local_bs if b["local_only"]],
        "stash_count": len(pcg.stash_list(root)),
        "current_repo_dirty": bool(status["dirty"] or status["staged"] or status["untracked"]),
    }


def _worktree_is_dirty(path: Path) -> bool:
    if not path.exists():
        return False
    status = pcg.porcelain_status(path)
    return bool(status.get("dirty") or status.get("staged") or status.get("untracked"))


# ── C: evidence preservation ─────────────────────────────────────────────

def evidence_preservation(root: Path, github: dict[str, Any]) -> dict[str, Any]:
    if not github.get("available"):
        return {"available": False, "detail": "PR data unavailable; cannot evaluate", "blockers": []}
    tags = pcg.archive_tags(root)
    closed_unmerged = github.get("closed_unmerged_all_time", [])
    blockers = pcg.find_unpreserved_closed_branches(root, closed_unmerged, tags)
    return {"available": True, "archive_tags": tags, "blockers": blockers}


# ── D: deployed state ─────────────────────────────────────────────────────

def deployed_state_snapshot(config: Any) -> dict[str, Any]:
    identity = pcr.intended_release_identity()
    return {
        "intended_release": identity,
        "current_known_deployed_sha": UNKNOWN,
        "current_known_deployed_sha_detail": (
            "Not observable from a repo checkout — this offline tool does not SSH or call "
            "external services. Run ops/live_box_guard.py ON the deployed box, or read its "
            "/status/system-snapshot endpoint if reachable, for the actual observed identity."
        ),
        "active_paper_forward_lanes": pcr.active_lanes(config),
        "strategy_permission_gate": pcr.strategy_permission_snapshot(config),
        "enabled_concepts": pcr.enabled_concepts_snapshot(config),
    }


# ── E: strategy source of truth ───────────────────────────────────────────

_PAPER_LIKE_VERDICTS = {"VALIDATED", "PAPER PROOF"}
_NOT_PAPER_READY_VERDICTS = {"WAIT", "BROKEN", "RETIRE", "RESEARCH ONLY", "OVERFIT"}


def _normalize(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def strategy_source_of_truth(config: Any, inventory_path: Path) -> dict[str, Any]:
    inventory = pcr.parse_strategy_inventory(inventory_path)
    if not inventory["available"]:
        return {"available": False, "detail": inventory.get("detail"), "conflicts": []}

    status = pcr.strategy_permission_snapshot(config)
    strategy_status = status.get("strategy_status") if isinstance(status.get("strategy_status"), dict) else {}
    normalized_status = {_normalize(k): (k, v) for k, v in strategy_status.items()}

    conflicts: list[dict[str, Any]] = []
    for row in inventory["rows"]:
        verdict = row["verdict"].upper()
        norm = _normalize(row["strategy"])
        match = next((v for k, v in normalized_status.items() if k in norm or norm in k), None)
        if match is None:
            continue
        config_key, config_value = match
        is_not_ready_verdict = any(bad in verdict for bad in _NOT_PAPER_READY_VERDICTS)
        if is_not_ready_verdict and config_value == "PAPER_ELIGIBLE":
            conflicts.append(
                {
                    "strategy_inventory_row": row["strategy"],
                    "documented_verdict": row["verdict"],
                    "config_key": config_key,
                    "configured_status": config_value,
                    "issue": "documented as not paper-ready but strategy_permission_gate allows PAPER_ELIGIBLE",
                }
            )
    return {"available": True, "rows_checked": len(inventory["rows"]), "conflicts": conflicts}


# ── F: trade chain integrity ──────────────────────────────────────────────

def trade_chain_integrity(entries: list[dict[str, Any]], since: datetime | None) -> dict[str, Any]:
    filtered = entries
    if since is not None:
        filtered = [e for e in entries if (ts := p30.parse_proof_ts(e.get("ts"))) is not None and ts >= since]

    instruments = sorted({(e.get("instrument") or "").upper() for e in filtered if e.get("instrument")})

    total_attempts = 0
    total_resolved = 0
    total_open = 0
    total_orphans = 0
    all_summaries: list[dict[str, Any]] = []
    per_instrument: dict[str, Any] = {}

    for inst in instruments:
        resolved, unmatched = p30.pair_resolved_trades(filtered, instrument=inst, limit=100_000)
        attempts = sum(
            1
            for e in filtered
            if (e.get("instrument") or "").upper() == inst
            and e.get("decision") == "TRADE"
            and (e.get("risk_check") or {}).get("result") == "APPROVED"
        )
        summaries = [r.to_summary() for r in resolved]
        all_summaries.extend(summaries)
        open_count = max(attempts - len(resolved), 0)
        total_attempts += attempts
        total_resolved += len(resolved)
        total_open += open_count
        total_orphans += len(unmatched)
        per_instrument[inst] = {
            "attempts": attempts,
            "resolved": len(resolved),
            "legitimately_open": open_count,
            "orphan_outcomes": len(unmatched),
        }

    order_ids = [
        e.get("paper_order_id")
        for e in filtered
        if e.get("decision") == "TRADE" and e.get("paper_order_id")
    ]
    order_id_counts = Counter(order_ids)
    duplicate_order_ids = {oid: n for oid, n in order_id_counts.items() if n > 1}

    category_counts = Counter(s["category"] for s in all_summaries)
    fills = category_counts.get("filled_win_loss", 0) + category_counts.get("breakeven", 0)
    cancellations_no_fill = category_counts.get("cancelled_nofill", 0)
    reconciler_touched = category_counts.get("reconciler_touched", 0)
    other = category_counts.get("other", 0)

    computed_resolved_sum = fills + cancellations_no_fill + reconciler_touched + other
    accounting_ok = computed_resolved_sum == total_resolved and total_attempts >= total_resolved

    issues: list[str] = []
    if not accounting_ok:
        issues.append(
            f"accounting mismatch: fills({fills}) + cancellations({cancellations_no_fill}) + "
            f"reconciler_touched({reconciler_touched}) + other({other}) = {computed_resolved_sum} "
            f"!= resolved_total({total_resolved}), or attempts < resolved"
        )
    if total_orphans:
        issues.append(f"{total_orphans} orphan OUTCOME row(s) with no matching TRADE attempt")
    if duplicate_order_ids:
        issues.append(f"{len(duplicate_order_ids)} duplicate paper_order_id value(s): {duplicate_order_ids}")
    if reconciler_touched:
        issues.append(f"{reconciler_touched} reconciler-touched outcome(s) need manual broker-verified classification")

    passed = not issues
    return {
        "status": "PASS" if passed else "FAIL",
        "attempts": total_attempts,
        "fills": fills,
        "no_fills": cancellations_no_fill,
        "resolved": total_resolved,
        "legitimate_opens": total_open,
        "orphans": total_orphans,
        "stale_orders": UNKNOWN,
        "stale_orders_detail": "no broker/order-age source available to this offline tool",
        "duplicate_identities": len(duplicate_order_ids),
        "duplicate_identity_detail": duplicate_order_ids or None,
        "broker_journal_parity": UNKNOWN,
        "broker_journal_parity_detail": "no broker API access from this offline tool; run health_digest.py/live_box_guard.py on the box for broker-side flatness",
        "reconciler_touched_needing_manual_verification": reconciler_touched,
        "per_instrument": per_instrument,
        "issues": issues,
    }


def format_trade_chain_summary(tc: dict[str, Any]) -> str:
    if tc["status"] == "PASS":
        return "\n".join(
            [
                "TRADE CHAIN: PASS",
                f"{tc['attempts']} attempts",
                f"{tc['fills']} fills",
                f"{tc['no_fills']} no-fills",
                f"{tc['resolved']} resolved",
                f"{tc['legitimate_opens']} legitimate opens",
                f"{tc['orphans']} orphans",
                f"stale orders: {tc['stale_orders']}",
                f"{tc['duplicate_identities']} duplicate identities",
                f"broker/journal parity: {tc['broker_journal_parity']}",
            ]
        )
    lines = ["TRADE CHAIN: FAIL"]
    lines.extend(f"  - {issue}" for issue in tc["issues"])
    return "\n".join(lines)


# ── orchestration ─────────────────────────────────────────────────────────

def build_daily_report(root: Path, *, journal_dir: Path, since_override: str | None) -> dict[str, Any]:
    github = github_repo_reconciliation(root)
    branches = branch_worktree_hygiene(root)
    evidence = evidence_preservation(root, github)

    config, config_error = None, None
    try:
        from config.settings import load_config

        config = load_config()
    except Exception as exc:  # noqa: BLE001
        config_error = str(exc)

    deployed = deployed_state_snapshot(config)
    inventory_path = root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    source_of_truth = strategy_source_of_truth(config, inventory_path)

    if since_override:
        since_dt = p30.parse_proof_ts(since_override)
    else:
        checkpoint = load_checkpoint(root)
        since_dt = p30.parse_proof_ts(checkpoint) if checkpoint else datetime.combine(
            _now().date(), datetime.min.time(), tzinfo=timezone.utc
        )

    entries = p30.read_journal_entries(journal_dir) if journal_dir.exists() else []
    trade_chain = trade_chain_integrity(entries, since_dt)

    return {
        "routine": "daily",
        "read_only": True,
        "generated_at": _now().isoformat(),
        "since": since_dt.isoformat() if since_dt else None,
        "github_repo_reconciliation": github,
        "branch_worktree_hygiene": branches,
        "evidence_preservation": evidence,
        "deployed_state": deployed,
        "deployed_state_config_load_error": config_error,
        "strategy_source_of_truth": source_of_truth,
        "trade_chain_integrity": trade_chain,
    }


def cmd_daily(args: argparse.Namespace) -> int:
    root = pcg.find_repo_root()
    if root is None:
        print("FAIL CLOSED: could not resolve a git repo root from the current directory.")
        return 2
    journal_dir = _journal_dir(args.journal_dir)
    report = build_daily_report(root, journal_dir=journal_dir, since_override=args.since)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print()
    print(format_trade_chain_summary(report["trade_chain_integrity"]))

    blockers = report["evidence_preservation"].get("blockers") or []
    if blockers:
        print(f"\nEVIDENCE PRESERVATION BLOCKER: {len(blockers)} closed-unmerged branch(es) with unique commits and no archive tag.")
    conflicts = report["strategy_source_of_truth"].get("conflicts") or []
    if conflicts:
        print(f"\nSTRATEGY SOURCE-OF-TRUTH CONFLICT: {len(conflicts)} strategy(s) documented as not-paper-ready but configured PAPER_ELIGIBLE.")

    if not args.no_checkpoint_update:
        path = write_checkpoint(root, report["generated_at"])
        print(f"\ncheckpoint updated: {path}")

    ok = (
        report["trade_chain_integrity"]["status"] == "PASS"
        and not blockers
        and not conflicts
    )
    return 0 if ok else 1
