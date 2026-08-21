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

    active_verdicts = {"VALIDATED", "PAPER PROOF", "PROMISING BUT UNPROVEN"}
    inactive_verdicts = {"BROKEN", "RETIRE", "WAIT", "RESEARCH ONLY"}

    findings: list[dict[str, Any]] = []
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
        verdict_says_active = any(v in verdict for v in active_verdicts)
        verdict_says_inactive = any(v in verdict for v in inactive_verdicts)

        if verdict_says_active and not is_active_in_config:
            findings.append(
                {
                    "strategy": row["name"],
                    "concept_key": concept,
                    "match_kind": match_kind,
                    "inventory_verdict": row.get("verdict"),
                    "issue": "described as active/promising in Strategy_Inventory.md but not "
                    "paper-eligible/enabled for any instrument in the current risk_rules.yaml",
                }
            )
        elif verdict_says_inactive and is_active_in_config:
            findings.append(
                {
                    "strategy": row["name"],
                    "concept_key": concept,
                    "match_kind": match_kind,
                    "inventory_verdict": row.get("verdict"),
                    "issue": "described as BROKEN/RETIRE/WAIT/RESEARCH ONLY in Strategy_Inventory.md but "
                    "IS paper-eligible/enabled for at least one instrument in the current risk_rules.yaml",
                }
            )

    return {
        "checked": True,
        "reason": None,
        "inventory_path": str(inventory_path),
        "inventory_row_count": len(rows),
        "drift_findings": findings,
        "unmatched_inventory_rows": unmatched,
        "note": (
            "Name matching is best-effort (confirmed aliases + heuristic substring fallback); "
            "unmatched rows are listed, never silently dropped or guessed."
        ),
    }


def _repo_hygiene(root: Path) -> dict[str, Any]:
    main_sync = gitutil.main_sync_state(root)
    status = gitutil.status_porcelain(root)
    all_worktrees = [w.as_dict() for w in gitutil.worktrees(root)]
    for w in all_worktrees:
        w["dirty_status"] = gitutil.worktree_dirty(w["path"])
    stashes = gitutil.stash_list(root)
    prs = gitutil.open_prs(root)
    branches_tracking_deleted_remotes = [b for b in gitutil.local_branches(root) if b["tracking_deleted_remote"]]
    local_only_branches = [b for b in gitutil.local_branches(root) if b["local_only"]]
    closed_unmerged = gitutil.unmerged_remote_branches_missing_archive_tag(root)
    archive_tags = gitutil.archive_tags(root)

    return {
        "current_branch": gitutil.current_branch(root),
        "local_main_relationship": main_sync,
        "dirty_tracked_files": status.get("dirty_tracked", []),
        "staged_files": status.get("staged", []),
        "untracked_files": status.get("untracked", []),
        "worktrees": all_worktrees,
        "stash_count": len(stashes),
        "stashes": stashes,
        "open_prs": prs,
        "branches_tracking_deleted_remotes": branches_tracking_deleted_remotes,
        "local_only_branches": local_only_branches,
        "evidence_preservation": {
            "closed_unmerged_branches_missing_archive_tag": closed_unmerged,
            "archive_tags": archive_tags,
            "note": (
                "This never creates or deletes an archive tag and never deletes a branch. "
                "A flagged branch here is a BLOCKER for cleanup, not an instruction to act."
            ),
        },
    }


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
        runtime=runtime,
    )

    return {
        # Mirrors the trade-chain result -- a daily report is not "ok" if the
        # trade-chain check FAILed, even though every field above rendered
        # successfully. An API/import consumer reading only "ok" must not be
        # able to mistake a FAIL for a clean run.
        "ok": trade_chain.get("status") == "PASS",
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
