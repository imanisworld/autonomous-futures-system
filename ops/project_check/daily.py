"""Routine 3: Daily Reconciliation + Trade Chain Integrity.

One daily read-only source-of-truth pass combining:
  A. GitHub/repo reconciliation (PRs, branches, worktrees, evidence preservation)
  B. Deployed state (reuses ops.project_check.runtime.runtime_snapshot)
  C. Strategy source of truth (Strategy_Inventory.md vs risk_rules.yaml drift)
  D. Trade chain integrity (reuses ops.project_check.trade_chain)

Never deletes a branch/worktree, never creates/deletes an archive tag, never
edits docs/config, never cancels an order or flattens a position. Everything
here is a report; any action implied by a finding is a separate, explicit
operator decision.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops.project_check import gitutil
from ops.project_check.runtime import runtime_snapshot
from ops.project_check.trade_chain import build_trade_chain_report

# Confident, explicitly-verified name -> risk_rules.yaml strategy-concept key
# mappings only. Anything not in this table is left to a best-effort fuzzy
# match (clearly labeled as such) or reported unmatched -- never guessed.
STRATEGY_NAME_ALIASES = {
    "orb reclaim": "orb_reclaim",
    "orb breakout": "orb_breakout",
    "orb rejection": "orb_rejection",
    "vwap reclaim": "vwap_reclaim",
    "vwap rejection": "vwap_rejection",
    "vwap hold": "vwap_hold",
    "pdh reclaim": "pdh_reclaim",
    "pdl reclaim": "pdl_reclaim",
    "4hr re-trigger": "strat_4hr_retrigger",
    "60m 3-2-2 first live": "strat_322_first_live",
}

_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(name: str) -> str:
    name = re.sub(r"\(.*?\)", "", name)  # drop "(MES)"/"(MNQ)" instrument suffixes
    name = re.sub(r"[^a-z0-9 -]", "", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def _parse_strategy_inventory(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not path.exists():
        return [], f"{path} not found"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], str(exc)

    rows: list[dict[str, Any]] = []
    in_master_table = False
    header_seen = False
    for line in lines:
        if line.startswith("## Master Table"):
            in_master_table = True
            header_seen = False
            continue
        if in_master_table and line.startswith("## "):
            break
        if not in_master_table:
            continue
        match = _TABLE_ROW_RE.match(line.strip())
        if not match:
            continue
        cells = [c.strip() for c in match.group(1).split("|")]
        if not header_seen:
            header_seen = True
            continue
        if not cells or set(cells[0]) <= {"-", " "}:
            continue
        name = cells[0].strip()
        verdict_cell = cells[-1] if cells else ""
        verdict_match = re.search(r"\*\*(.+?)\*\*", verdict_cell)
        verdict = verdict_match.group(1).strip() if verdict_match else verdict_cell.strip() or None
        if name:
            rows.append({"name": name, "verdict": verdict, "raw_verdict_cell": verdict_cell})
    return rows, None


def _strategy_source_of_truth(*, repo_root: Path, rules_active_lanes: dict[str, Any]) -> dict[str, Any]:
    inventory_path = repo_root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    rows, error = _parse_strategy_inventory(inventory_path)
    if error:
        return {"checked": False, "reason": error}

    lane_summary = (rules_active_lanes or {}).get("active_lane_summary") or {}
    active_concepts_any_instrument = {c for concepts in lane_summary.values() for c in concepts}

    # Evidence classification and runtime enablement are separate dimensions.
    # PROMISING does not mean a lane must be enabled, and WAIT does not prove
    # that a concept may never be active as a source for a derived evidence lane.
    # Only explicit unsafe/retired classifications are automatic blockers when
    # that exact executable concept is active.
    unsafe_active_verdicts = {"BROKEN", "RETIRE", "UNSAFE"}

    findings: list[dict[str, Any]] = []
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for row in rows:
        normalized = _normalize(row["name"])
        concept = STRATEGY_NAME_ALIASES.get(normalized)
        match_kind = "confirmed_alias"
        if concept is None:
            # Best-effort fuzzy fallback: does a known concept key literally
            # appear inside the normalized name, or vice versa?
            for key in STRATEGY_NAME_ALIASES.values():
                key_spaced = key.replace("_", " ")
                if key_spaced in normalized or normalized in key_spaced:
                    concept = key
                    match_kind = "heuristic_substring_match_confirm_manually"
                    break
        if concept is None:
            unmatched.append(row)
            continue

        is_active_in_config = concept in active_concepts_any_instrument
        verdict = (row.get("verdict") or "").upper()
        matched.append(
            {
                "strategy": row["name"],
                "concept_key": concept,
                "match_kind": match_kind,
                "inventory_verdict": row.get("verdict"),
                "configured_active": is_active_in_config,
            }
        )
        if is_active_in_config and any(v in verdict for v in unsafe_active_verdicts):
            findings.append(
                {
                    "strategy": row["name"],
                    "concept_key": concept,
                    "match_kind": match_kind,
                    "inventory_verdict": row.get("verdict"),
                    "issue": (
                        "explicitly classified BROKEN/RETIRE/UNSAFE in Strategy_Inventory.md "
                        "but the exact concept is paper-eligible/enabled for at least one instrument"
                    ),
                }
            )

    return {
        "checked": True,
        "reason": None,
        "inventory_path": str(inventory_path),
        "inventory_row_count": len(rows),
        "drift_findings": findings,
        "matched_inventory_rows": matched,
        "unmatched_inventory_rows": unmatched,
        "note": (
            "Evidence verdict and config enablement are reported separately. Only explicit "
            "BROKEN/RETIRE/UNSAFE + active exact-concept combinations are automatic drift findings. "
            "Name matching is best-effort; unmatched rows are listed, never guessed."
        ),
    }


def _repo_hygiene(root: Path) -> dict[str, Any]:
    main_sync = gitutil.main_sync_state(root)
    status = gitutil.status_porcelain(root)
    all_worktrees = gitutil.worktree_inventory(root)
    stash_inventory = gitutil.stash_inventory(root)
    stashes = stash_inventory["stashes"]
    prs = gitutil.open_prs(root)
    branch_inventory = gitutil.local_branch_inventory(root)
    branches_tracking_deleted_remotes = [b for b in branch_inventory["branches"] if b["tracking_deleted_remote"]]
    local_only_branches = [b for b in branch_inventory["branches"] if b["local_only"]]
    closed_unmerged = gitutil.unmerged_remote_branches_missing_archive_tag(root)
    archive_inventory = gitutil.archive_tag_inventory(root)

    # Any enumeration that failed leaves a preservation conclusion unproven, so
    # it is reported as UNKNOWN and escalated -- never as an empty/clean result.
    unverified: list[str] = []
    if not all_worktrees:
        unverified.append("worktree inventory could not be enumerated")
    if not stash_inventory["checked"]:
        unverified.append(f"stash inventory could not be enumerated: {stash_inventory['reason']}")
    if not branch_inventory["checked"]:
        unverified.append(f"local branch inventory could not be enumerated: {branch_inventory['reason']}")
    if not archive_inventory["checked"]:
        unverified.append(f"archive tag inventory could not be enumerated: {archive_inventory['reason']}")
    if not closed_unmerged.get("checked"):
        unverified.append(f"branch preservation check did not complete: {closed_unmerged.get('reason')}")

    return {
        "current_branch": gitutil.current_branch(root),
        "local_main_relationship": main_sync,
        "dirty_tracked_files": status.get("dirty_tracked", []),
        "staged_files": status.get("staged", []),
        "untracked_files": status.get("untracked", []),
        "worktrees": all_worktrees,
        "worktree_inventory_checked": bool(all_worktrees),
        "stash_count": len(stashes) if stash_inventory["checked"] else None,
        "stashes": stashes,
        "stash_enumeration": {"checked": stash_inventory["checked"], "reason": stash_inventory["reason"]},
        "stash_preservation_note": "Stashed work is outside all branch-cleanup conclusions; retain and review separately.",
        "open_prs": prs,
        "branches_tracking_deleted_remotes": branches_tracking_deleted_remotes,
        "local_only_branches": local_only_branches,
        "local_branch_enumeration": {"checked": branch_inventory["checked"], "reason": branch_inventory["reason"]},
        "local_branch_note": (
            "local-only or deleted-remote status is descriptive only; disposability is decided "
            "solely by the preservation classification."
        ),
        "unverified_enumerations": unverified,
        "evidence_preservation": {
            "closed_unmerged_branches_missing_archive_tag": closed_unmerged,
            "archive_tags": archive_inventory["tags"],
            "archive_tag_enumeration": {
                "checked": archive_inventory["checked"], "reason": archive_inventory["reason"],
            },
            "note": (
                "This never creates or deletes an archive tag and never deletes a branch. "
                "A flagged branch here is a BLOCKER for cleanup, not an instruction to act."
            ),
        },
    }


def _overall_blockers(
    *,
    hygiene: dict[str, Any],
    runtime: dict[str, Any],
    strategy_drift: dict[str, Any],
    trade_chain: dict[str, Any],
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []

    if trade_chain.get("status") != "PASS":
        blockers.append({"code": "TRADE_CHAIN_FAIL", "detail": "trade-chain integrity check failed"})

    drift = runtime.get("live_box_drift") or {}
    if str(drift.get("status") or "").lower() == "error":
        blockers.append(
            {
                "code": "RUNTIME_DRIFT_ERROR",
                "detail": str(drift.get("summary") or "live-box/runtime drift check returned error"),
            }
        )
    if runtime.get("risk_rules_load_error"):
        blockers.append(
            {"code": "RISK_RULES_UNVERIFIED", "detail": str(runtime["risk_rules_load_error"])}
        )

    if hygiene.get("unverified_enumerations"):
        blockers.append(
            {
                "code": "REPO_PRESERVATION_UNVERIFIED",
                "detail": "; ".join(hygiene["unverified_enumerations"]),
            }
        )

    if hygiene.get("dirty_tracked_files") or hygiene.get("staged_files"):
        blockers.append(
            {
                "code": "REPO_TRACKED_DIRTY",
                "detail": "tracked/staged repository changes are present during daily reconciliation",
            }
        )

    if not strategy_drift.get("checked"):
        blockers.append(
            {
                "code": "STRATEGY_SOURCE_UNVERIFIED",
                "detail": str(strategy_drift.get("reason") or "strategy inventory check was not completed"),
            }
        )
    elif strategy_drift.get("drift_findings"):
        blockers.append(
            {
                "code": "UNSAFE_STRATEGY_ACTIVE",
                "detail": (
                    f"{len(strategy_drift['drift_findings'])} active concept(s) carry an explicit "
                    "BROKEN/RETIRE/UNSAFE inventory classification"
                ),
            }
        )
    return blockers


def build_daily_report(
    *,
    repo_root: str | Path,
    journal_dir: str | Path = "logs",
    risk_rules_path: str | Path = "risk_rules.yaml",
    use_checkpoint: bool = True,
    advance_checkpoint: bool = False,
) -> dict[str, Any]:
    root = Path(repo_root)
    hygiene = _repo_hygiene(root)
    runtime = runtime_snapshot(repo_root=root, risk_rules_path=risk_rules_path)
    strategy_drift = _strategy_source_of_truth(repo_root=root, rules_active_lanes=runtime.get("active_lanes"))
    trade_chain = build_trade_chain_report(
        journal_dir=Path(journal_dir) if Path(journal_dir).is_absolute() else root / journal_dir,
        repo_root=root,
        use_checkpoint=use_checkpoint,
        advance_checkpoint=advance_checkpoint,
    )

    overall_blockers = _overall_blockers(
        hygiene=hygiene,
        runtime=runtime,
        strategy_drift=strategy_drift,
        trade_chain=trade_chain,
    )
    overall_status = "PASS" if not overall_blockers else "FAIL"

    return {
        "ok": overall_status == "PASS",
        "overall_status": overall_status,
        "overall_blockers": overall_blockers,
        "routine": "daily-reconciliation",
        "generated_at": _now_iso(),
        "repo_reconciliation": hygiene,
        "deployed_state": runtime,
        "strategy_source_of_truth": strategy_drift,
        "trade_chain": trade_chain,
        "forbidden_actions_reminder": (
            "Read-only: never cancels an order, flattens a position, modifies a broker "
            "order, repairs a journal, synthesizes an OUTCOME, deletes a branch/worktree, "
            "creates/deletes an archive tag, or edits docs/config."
        ),
    }
