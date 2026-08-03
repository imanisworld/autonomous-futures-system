"""Routine 3: Daily Reconciliation + Trade Chain Integrity.

One daily read-only source-of-truth pass, combining PR hygiene, branch/
worktree hygiene, evidence preservation, deployed-state tracking,
strategy-status reconciliation, and actual paper/demo trade-chain integrity
in a single routine (deliberately not a fourth one).

Reuses, rather than duplicates:
- ``ops.repo_state`` for all git/PR introspection.
- ``ops.session_snapshot.runtime_snapshot`` for deployed-state/paper-lane
  reporting (same function the session-start routine uses).
- ``ops.strategy_inventory`` for Strategy_Inventory.md parsing.
- ``ops.trade_chain`` for the accounting-identity trade-chain pass.
- ``ops.reconciler_outcome_audit`` for reconciler-touched-outcome auditing
  against documented operator overrides.

READ ONLY end to end. This routine never cancels an order, flattens a
position, modifies a broker order, repairs a journal, synthesizes an
OUTCOME, rewrites trading state, retries an execution, submits an order,
edits docs/config, or creates/deletes an archive tag. On any discrepancy it
reports and fails closed, nothing more.
"""

from __future__ import annotations

import json
import re
from datetime import date as date_cls
from pathlib import Path
from typing import Any

from ops import repo_state as rs
from ops import strategy_inventory
from ops import trade_chain
from ops.session_snapshot import runtime_snapshot

CHECKPOINT_FILENAME = "ops_daily_checkpoint.json"
STALE_PR_DAYS = 14

# decision_output.schema.json's strategy enum plus names seen live in
# risk_rules.yaml's strategy_permission_gate/enabled_concepts comments as of
# this routine's authorship. Used only to widen the source-of-truth diff
# beyond whatever risk_rules.yaml currently mentions; anything actually
# configured is still read live from risk_rules.yaml, never hardcoded.
KNOWN_STRATEGY_NAMES = {
    "orb_reclaim", "orb_rejection", "orb_breakout", "orb_false_break_fade",
    "vwap_reclaim", "vwap_hold", "vwap_rejection", "pdh_reclaim", "pdl_reclaim",
    "continuation_pullback", "strat_212", "strat_122", "strat_122_observed",
    "strat_122_pullback", "strat_inside_break", "strat_outside_continuation",
    "strat_4hr_retrigger", "strat_4hr_retrigger_observed", "strat_322_first_live",
    "ema_pullback_trend", "gap_fill", "ovn_high_sweep_reclaim",
}

_FILL_MODEL_TOKENS = ("ioc_limit", "ioc_close", "stop_market", "market")


def _checkpoint_path(root: Path) -> Path | None:
    admin_dir = rs.git_dir(root)
    return admin_dir / CHECKPOINT_FILENAME if admin_dir else None


def _load_checkpoint(root: Path) -> dict[str, Any] | None:
    path = _checkpoint_path(root)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_checkpoint(root: Path, *, to_date: str) -> bool:
    path = _checkpoint_path(root)
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_to_date": to_date}), encoding="utf-8")
        return True
    except OSError:
        return False


def _github_repo_report(root: Path, *, today: str) -> dict[str, Any]:
    all_prs = rs.pull_requests(root, state="all", limit=200)
    if not all_prs["available"]:
        return {"available": False, "reason": all_prs["reason"]}

    def _on(pr: dict[str, Any], field: str) -> bool:
        value = pr.get(field)
        return bool(value) and str(value).startswith(today)

    opened_today = [pr for pr in all_prs["prs"] if _on(pr, "createdAt")]
    merged_today = [pr for pr in all_prs["prs"] if _on(pr, "mergedAt")]
    closed_unmerged_today = [
        pr for pr in all_prs["prs"] if _on(pr, "closedAt") and not pr.get("mergedAt")
    ]
    open_prs = [pr for pr in all_prs["prs"] if pr.get("state") == "OPEN"]
    stale_prs = [
        pr for pr in open_prs
        if pr.get("updatedAt") and pr["updatedAt"][:10] < _days_ago(today, STALE_PR_DAYS)
    ]
    return {
        "available": True,
        "opened_today": opened_today,
        "merged_today": merged_today,
        "closed_unmerged_today": closed_unmerged_today,
        "open_prs": open_prs,
        "stale_prs_over_14d_idle": stale_prs,
    }


def _days_ago(today_iso: str, days: int) -> str:
    year, month, day = (int(part) for part in today_iso.split("-"))
    from datetime import timedelta

    return (date_cls(year, month, day) - timedelta(days=days)).isoformat()


def _branch_worktree_report(root: Path) -> dict[str, Any]:
    return {
        "worktrees": rs.worktrees(root),
        "dirty_worktrees": [w["path"] for w in rs.worktrees(root) if w.get("dirty")],
        "branches_tracking_deleted_remotes": [b["name"] for b in rs.local_branches(root) if b["gone"]],
        "local_only_branches": [
            b["name"] for b in rs.local_branches(root) if b["upstream"] is None and b["name"] != "main"
        ],
        "main_sync_state": rs.main_sync_state(root),
        "stashes": rs.stashes(root),
    }


def _evidence_preservation_report(root: Path) -> dict[str, Any]:
    evidence = rs.unmerged_branch_evidence(root)
    index_path = root / "docs" / "BRANCH_ARCHIVE_INDEX.md"
    documented_tags: set[str] = set()
    if index_path.exists():
        try:
            text = index_path.read_text(encoding="utf-8")
            documented_tags = set(re.findall(r"archive/[a-zA-Z0-9._-]+", text))
        except OSError:
            pass
    actual_tags = set(evidence["archive_tags"])
    return {
        **evidence,
        "branch_archive_index_path": str(index_path),
        "branch_archive_index_found": index_path.exists(),
        "tags_undocumented_in_index": sorted(actual_tags - documented_tags),
        "tags_documented_but_missing_on_origin": sorted(documented_tags - actual_tags),
    }


def _strategy_source_of_truth_report(root: Path) -> dict[str, Any]:
    inventory_path = root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    rows = strategy_inventory.load_master_table(inventory_path)

    try:
        from config.settings import load_config

        config = load_config(str(root / "risk_rules.yaml"))
        config_ok = True
        config_error = None
    except Exception as exc:
        config = None
        config_ok = False
        config_error = f"{type(exc).__name__}: {exc}"

    if not config_ok:
        return {
            "inventory_found": inventory_path.exists(),
            "config_ok": False,
            "config_error": config_error,
            "drift": [],
        }

    names = set(config.strategy_status) | set(config.enabled_concepts) | KNOWN_STRATEGY_NAMES
    drift: list[dict[str, Any]] = []
    missing_from_inventory: list[str] = []

    for name in sorted(names):
        matches = strategy_inventory.match_strategy_rows(name, rows)
        status = config.strategy_status.get(name, config.strategy_permission_default_status)
        enabled = name in config.enabled_concepts
        reachable = status == "PAPER_ELIGIBLE" and enabled

        if not matches:
            if status == "PAPER_ELIGIBLE" or enabled:
                missing_from_inventory.append(name)
            continue

        for row in matches:
            verdict = row["verdict_normalized"]
            described_active = verdict in {"PAPER PROOF", "VALIDATED"}
            described_weak = verdict in {"WAIT", "BROKEN", "OVERFIT", "RETIRE", "RESEARCH ONLY"}

            if described_active and not reachable:
                drift.append({
                    "strategy": name, "inventory_row": row["strategy"], "verdict": verdict,
                    "runtime_status": status, "enabled_concept": enabled,
                    "issue": "described as active/paper-proof in inventory but runtime is not reachable "
                             "(fail-closed) — see strategy_status/enabled_concepts",
                })
            if described_weak and reachable:
                drift.append({
                    "strategy": name, "inventory_row": row["strategy"], "verdict": verdict,
                    "runtime_status": status, "enabled_concept": enabled,
                    "issue": f"inventory verdict is {verdict!r} but risk_rules.yaml marks this "
                             "strategy PAPER_ELIGIBLE and enabled",
                })

            honest_fills_text = row.get("honest_fills", "").lower()
            mentioned_models = [tok for tok in _FILL_MODEL_TOKENS if tok in honest_fills_text]
            if mentioned_models and config.entry_fill_model not in mentioned_models:
                drift.append({
                    "strategy": name, "inventory_row": row["strategy"], "verdict": verdict,
                    "issue": f"inventory 'Honest fills' column mentions {mentioned_models}, "
                             f"active runtime entry_fill_model is {config.entry_fill_model!r}",
                })

    return {
        "inventory_found": inventory_path.exists(),
        "config_ok": True,
        "matched_strategy_count": len(names),
        "drift": drift,
        "missing_from_inventory": missing_from_inventory,
    }


def _reconciler_audit_summary(root: Path, journal_dir: Path) -> dict[str, Any]:
    from ops.reconciler_outcome_audit import build_audit_report

    overrides_doc = root / "docs" / "proof-operator-overrides.md"
    report = build_audit_report(
        journal_dir=journal_dir,
        overrides_doc=overrides_doc if overrides_doc.exists() else None,
    )
    return {
        "total_touched": report["summary"]["total_touched"],
        "unaudited": report["summary"]["unaudited"],
        "unaudited_rows": report["unaudited"],
    }


def build_daily_report(
    *,
    repo_root: str | Path | None = None,
    journal_dir: str | Path | None = None,
    today: str | None = None,
    from_date: str | None = None,
    do_fetch: bool = True,
    save_checkpoint: bool = True,
) -> dict[str, Any]:
    root = rs.find_repo_root(repo_root)
    if root is None:
        return {"ok": False, "status": "error", "summary": "Not inside a git repository."}

    if today is None:
        today = date_cls.today().isoformat()
    journal_path = Path(journal_dir) if journal_dir else root / "logs"

    fetched = rs.fetch_remote(root, tags=True) if do_fetch else False

    checkpoint = _load_checkpoint(root)
    effective_from_date = from_date or (checkpoint or {}).get("last_to_date") or today

    github = _github_repo_report(root, today=today)
    branches_worktrees = _branch_worktree_report(root)
    evidence = _evidence_preservation_report(root)
    deployed = runtime_snapshot(root)
    strategy_drift = _strategy_source_of_truth_report(root)
    chain = trade_chain.build_report(journal_path, from_date=effective_from_date, to_date=today)
    reconciler = _reconciler_audit_summary(root, journal_path)

    if save_checkpoint:
        _save_checkpoint(root, to_date=today)

    blockers = list(evidence.get("blockers", []))
    if strategy_drift.get("drift"):
        blockers.extend(dict.fromkeys(f"strategy-inventory-drift:{d['strategy']}" for d in strategy_drift["drift"]))
    if reconciler["unaudited"]:
        blockers.append(f"reconciler-touched-unaudited:{reconciler['unaudited']}")
    if not chain["ok"]:
        blockers.append("trade-chain-integrity-fail")

    ok = not blockers
    return {
        "ok": ok,
        "status": "PASS" if ok else "FAIL_CLOSED",
        "repo_root": str(root),
        "today": today,
        "window": {"from_date": effective_from_date, "to_date": today},
        "fetched_before_check": fetched,
        "github": github,
        "branches_worktrees": branches_worktrees,
        "evidence_preservation": evidence,
        "deployed_state": deployed,
        "strategy_source_of_truth": strategy_drift,
        "reconciler_touched_outcomes": reconciler,
        "trade_chain": chain,
        "trade_chain_summary_line": trade_chain.format_summary_line(chain),
        "blockers": blockers,
    }
