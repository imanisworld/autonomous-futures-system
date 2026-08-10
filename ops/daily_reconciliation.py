"""Daily Reconciliation + Trade Chain Integrity.

One daily, read-only source-of-truth pass folding together:
  A) GitHub / repo / worktree hygiene + evidence preservation (ops.repo_state)
  B) deployed-state tracking (ops.live_box_guard.live_box_drift_report)
  C) strategy source-of-truth drift (Strategy_Inventory.md vs risk_rules.yaml)
  D) trade-chain integrity for the day (ops.trade_chain, journal-only + a
     best-effort, fail-soft local status-endpoint parity check)

Never mutates anything: no cancel/flatten/order-submit/journal-repair/tag
creation/branch deletion. On any discrepancy this module reports and fails
closed; it never resolves the discrepancy itself.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from ops import repo_state
from ops.live_box_guard import live_box_drift_report
from ops.trade_chain import (
    build_accounting_identity,
    duplicate_order_ids,
    pair_trades,
    read_entries,
)

CHECKPOINT_FILENAME = ".daily_reconciliation_checkpoint.json"
STRATEGY_INVENTORY_REL_PATH = "docs/strategy-rules/Strategy_Inventory.md"
DRIFT_VERDICTS_ENABLED_SHOULD_NOT_BE = {"WAIT", "BROKEN", "RETIRE", "RESEARCH ONLY"}
STATUS_BASE = "http://127.0.0.1:8000"


# ─────────────────────────────── A) repo/branch/PR hygiene ──────────────────

def _repo_root(repo_root: str | Path | None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    found = repo_state.repo_root_of(Path.cwd())
    return found or Path(__file__).resolve().parents[1]


def _pr_hygiene(root: Path) -> dict[str, Any]:
    all_prs = repo_state.gh_pr_list(root, state="all", limit=200)
    if all_prs is None:
        return {
            "opened_today": "UNKNOWN (gh unavailable or call failed)",
            "merged_today": "UNKNOWN (gh unavailable or call failed)",
            "closed_unmerged_today": "UNKNOWN (gh unavailable or call failed)",
            "open_prs": "UNKNOWN (gh unavailable or call failed)",
            "stale_prs": "UNKNOWN (gh unavailable or call failed)",
        }
    today = date.today().isoformat()
    opened_today = [pr for pr in all_prs if str(pr.get("createdAt", "")).startswith(today)]
    merged_today = [pr for pr in all_prs if str(pr.get("mergedAt") or "").startswith(today)]
    closed_unmerged_today = [
        pr for pr in all_prs
        if str(pr.get("closedAt") or "").startswith(today) and not pr.get("mergedAt")
    ]
    open_prs = [pr for pr in all_prs if pr.get("state") == "OPEN"]
    stale_cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    stale_prs = [pr for pr in open_prs if str(pr.get("updatedAt") or "") < stale_cutoff]
    return {
        "opened_today": [pr.get("number") for pr in opened_today],
        "merged_today": [pr.get("number") for pr in merged_today],
        "closed_unmerged_today": [pr.get("number") for pr in closed_unmerged_today],
        "open_prs": [pr.get("number") for pr in open_prs],
        "stale_prs_over_14d": [pr.get("number") for pr in stale_prs],
    }


def _evidence_preservation(snapshot: dict[str, Any]) -> dict[str, Any]:
    blockers = [
        finding for finding in snapshot["branches_missing_archive_tag"]
        if not finding["has_archive_tag"]
    ]
    return {
        "closed_unmerged_local_branches_checked": len(snapshot["branches_missing_archive_tag"]),
        "blockers_missing_archive_tag": blockers,
        "note": (
            "A branch here is NOT necessarily unique evidence -- confirm via "
            "docs/BRANCH_ARCHIVE_INDEX.md's disposition method (unique commits/files vs main, "
            "byte-diff) before tagging or deleting. This routine never creates a tag or deletes "
            "a branch; a BLOCKER here means 'review before deleting', not 'broken'."
        ),
    }


# ─────────────────────────────── C) strategy source of truth ────────────────

_VERDICT_RE = re.compile(r"\*\*([^*]+)\*\*\s*$")


def _parse_strategy_inventory(root: Path) -> list[dict[str, str]]:
    path = root / STRATEGY_INVENTORY_REL_PATH
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    in_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## Master Table"):
            in_table = True
            continue
        if in_table and stripped.startswith("## "):
            break
        if not in_table or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("Strategy", "---") or set(cells[0]) <= {"-"}:
            continue
        name = cells[0]
        verdict_cell = cells[-1]
        match = _VERDICT_RE.search(verdict_cell)
        verdict_text = match.group(1) if match else verdict_cell
        verdict_token = verdict_text.split("—")[0].split("-")[0].strip()
        rows.append({"display_name": name, "verdict_text": verdict_text, "verdict_token": verdict_token})
    return rows


def _slug_key(display_name: str) -> str:
    without_paren = re.sub(r"\([^)]*\)", "", display_name)
    return re.sub(r"[^a-z0-9]+", "_", without_paren.strip().lower()).strip("_")


def _strategy_source_of_truth(root: Path, rules: dict[str, Any]) -> dict[str, Any]:
    inventory_rows = _parse_strategy_inventory(root)
    gate = rules.get("strategy_permission_gate") or {}
    strategy_status = gate.get("strategy_status") or {}
    enabled_concepts = set((rules.get("strategy") or {}).get("enabled_concepts") or [])

    runtime_keys = set(strategy_status) | enabled_concepts
    findings: list[dict[str, Any]] = []
    matched_runtime_keys: set[str] = set()

    for row in inventory_rows:
        slug = _slug_key(row["display_name"])
        matched = [key for key in runtime_keys if key in slug or slug in key]
        if not matched:
            findings.append({
                "strategy": row["display_name"],
                "inventory_verdict": row["verdict_token"],
                "runtime_key": None,
                "issue": "no matching runtime key found in risk_rules.yaml (manual mapping needed)",
                "severity": "info",
            })
            continue
        matched_runtime_keys.update(matched)
        for key in matched:
            status = strategy_status.get(key, gate.get("default_status", "UNKNOWN"))
            is_live = key in enabled_concepts and status == "PAPER_ELIGIBLE"
            verdict = row["verdict_token"].upper()
            if is_live and any(bad in verdict for bad in DRIFT_VERDICTS_ENABLED_SHOULD_NOT_BE):
                findings.append({
                    "strategy": row["display_name"],
                    "inventory_verdict": row["verdict_token"],
                    "runtime_key": key,
                    "issue": (
                        f"enabled + PAPER_ELIGIBLE at runtime but Strategy_Inventory.md says "
                        f"{row['verdict_token']!r}"
                    ),
                    "severity": "blocker",
                })
            elif ("PAPER PROOF" in verdict or "VALIDATED" in verdict) and not is_live:
                findings.append({
                    "strategy": row["display_name"],
                    "inventory_verdict": row["verdict_token"],
                    "runtime_key": key,
                    "issue": (
                        f"inventory says {row['verdict_token']!r} but runtime status is "
                        f"{status!r} / enabled_concepts membership={key in enabled_concepts} "
                        "-- described as active but actually fail-closed"
                    ),
                    "severity": "warn",
                })

    missing_rows = sorted(key for key in runtime_keys if key not in matched_runtime_keys)
    if missing_rows:
        findings.append({
            "strategy": None,
            "inventory_verdict": None,
            "runtime_key": missing_rows,
            "issue": "runtime key(s) with no corresponding Strategy_Inventory.md row found",
            "severity": "info",
        })

    return {
        "inventory_path": str(root / STRATEGY_INVENTORY_REL_PATH),
        "inventory_present": bool(inventory_rows),
        "findings": findings,
        "blockers": [f for f in findings if f["severity"] == "blocker"],
    }


# ─────────────────────────────── D) trade chain integrity ───────────────────

def _read_checkpoint(root: Path, log_dir: str | Path) -> dict[str, Any] | None:
    path = _checkpoint_path(root, log_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _checkpoint_path(root: Path, log_dir: str | Path) -> Path:
    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = root / log_path
    return log_path / CHECKPOINT_FILENAME


def _write_checkpoint(root: Path, log_dir: str | Path, through_date: str) -> str | None:
    path = _checkpoint_path(root, log_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"through_date": through_date, "recorded_at": datetime.now(timezone.utc).isoformat()},
                        indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return str(path)
    except OSError:
        return None


def _local_status(path: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"{STATUS_BASE}{path}", timeout=4.0) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001 - best-effort, fail soft to UNKNOWN
        return None


def _trade_chain(root: Path, log_dir: str | Path, from_date: str, to_date: str) -> dict[str, Any]:
    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = root / log_path

    entries = read_entries(log_path, from_date=from_date, to_date=to_date)
    read_errors = [e for e in entries if e.get("type") == "READ_ERROR"]

    resolved, still_open, unmatched_outcomes = pair_trades(entries, lambda entry: True)
    identity = build_accounting_identity(resolved, still_open)
    dupes = duplicate_order_ids(entries)

    rejected = [e for e in entries if e.get("decision") == "RISK_REJECTED"]
    rejected_missing_reason = [e for e in rejected if not e.get("reason")]
    config_blocked = [e for e in entries if e.get("decision") == "CONFIG_BLOCKED"]
    config_blocked_missing_reason = [e for e in config_blocked if not e.get("config_block")]

    order_id_rows = [e for e in entries if e.get("type") == "ORDER_IDS"]
    approved_trade_count = sum(
        1 for e in entries
        if e.get("decision") == "TRADE" and (e.get("risk_check") or {}).get("result") == "APPROVED"
    )
    possible_naked_entries = max(0, approved_trade_count - len(order_id_rows))

    orphans = []
    for trade in still_open:
        trade_date = str(trade.get("ts") or "")[:10]
        if trade_date and trade_date < to_date:
            orphans.append({
                "ts": trade.get("ts"),
                "instrument": trade.get("instrument"),
                "strategy": (trade.get("setup") or {}).get("strategy"),
                "issue": f"still open from {trade_date}, before the scan's end date {to_date}",
            })

    broker_status = _local_status("/status/broker-account")
    today_status = _local_status("/status/today")
    parity = {"status": "UNKNOWN", "detail": "local status endpoint unreachable (service not running on this host, or not the deployed box)"}
    if broker_status is not None:
        broker_flat = broker_status.get("position") in (None, "", [])
        journal_open = len(still_open) > 0
        if broker_flat and journal_open:
            parity = {"status": "MISMATCH", "detail": "broker reports FLAT but journal shows an open position"}
        elif (not broker_flat) and not journal_open:
            parity = {"status": "MISMATCH", "detail": "broker reports an open position but journal shows none"}
        else:
            parity = {"status": "PASS", "detail": "broker/journal open-position state agrees"}

    anomalies: list[str] = []
    if read_errors:
        anomalies.append(f"{len(read_errors)} journal read error(s)")
    if not identity.ok:
        anomalies.append("accounting identity failed: attempts != resolved + legitimately_open")
    if dupes:
        anomalies.append(f"{len(dupes)} duplicate order identity(ies)")
    if rejected_missing_reason:
        anomalies.append(f"{len(rejected_missing_reason)} RISK_REJECTED row(s) missing a reason")
    if config_blocked_missing_reason:
        anomalies.append(f"{len(config_blocked_missing_reason)} CONFIG_BLOCKED row(s) missing config_block")
    if possible_naked_entries:
        anomalies.append(f"{possible_naked_entries} approved trade(s) with no matching ORDER_IDS row")
    if orphans:
        anomalies.append(f"{len(orphans)} orphaned open position(s) from a prior day")
    if unmatched_outcomes:
        anomalies.append(f"{len(unmatched_outcomes)} unmatched OUTCOME row(s)")
    if parity["status"] == "MISMATCH":
        anomalies.append(f"broker/journal parity: {parity['detail']}")

    verdict = "PASS" if not anomalies else "FAIL"
    summary = {
        "verdict": verdict,
        "attempts": identity.attempts,
        "fills": identity.fills,
        "cancellations_and_no_fills": identity.cancellations + identity.needs_manual_classification,
        "resolved": identity.resolved,
        "legitimate_opens": identity.legitimately_open,
        "orphans": len(orphans),
        "stale_or_naked_orders": possible_naked_entries,
        "duplicate_identities": len(dupes),
        "broker_journal_parity": parity["status"],
    }

    return {
        "window": {"from_date": from_date, "to_date": to_date},
        "summary": summary,
        "anomalies": anomalies,
        "accounting_identity": identity.as_dict(),
        "duplicate_order_ids": dupes,
        "rejected_missing_reason": rejected_missing_reason,
        "config_blocked_missing_reason": config_blocked_missing_reason,
        "orphaned_open_positions": orphans,
        "unmatched_outcomes_count": len(unmatched_outcomes),
        "broker_journal_parity": parity,
        "journal_read_errors": read_errors,
    }


# ─────────────────────────────── entry point ─────────────────────────────────

def _load_risk_rules(root: Path, risk_rules_path: str | Path) -> dict[str, Any]:
    path = Path(risk_rules_path)
    if not path.is_absolute():
        path = root / path
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def build_daily_reconciliation_report(
    *,
    repo_root: str | Path | None = None,
    log_dir: str | Path = "logs",
    risk_rules_path: str | Path = "risk_rules.yaml",
    base_branch: str = "main",
    remote: str = "origin",
    target_date: str | None = None,
    check_prs: bool = True,
) -> dict[str, Any]:
    root = _repo_root(repo_root)
    to_date = target_date or date.today().isoformat()

    checkpoint = _read_checkpoint(root, log_dir)
    from_date = (checkpoint or {}).get("through_date") or to_date

    snapshot = repo_state.repo_snapshot(root, base=base_branch, remote=remote)
    pr_hygiene = _pr_hygiene(root) if check_prs else {"skipped": True}
    evidence_preservation = _evidence_preservation(snapshot)

    deployed_state = live_box_drift_report(repo_root=root, risk_rules_path=risk_rules_path, log_dir=log_dir)

    rules = _load_risk_rules(root, risk_rules_path)
    strategy_drift = _strategy_source_of_truth(root, rules)

    trade_chain = _trade_chain(root, log_dir, from_date, to_date)

    blockers: list[str] = []
    for finding in evidence_preservation["blockers_missing_archive_tag"]:
        blockers.append(f"evidence preservation: {finding['branch']} has no archive/* tag")
    for finding in strategy_drift["blockers"]:
        blockers.append(f"strategy drift: {finding['strategy']} -- {finding['issue']}")
    if trade_chain["summary"]["verdict"] != "PASS":
        blockers.append("trade chain integrity: " + "; ".join(trade_chain["anomalies"]))
    if deployed_state["status"] == "error":
        blockers.append("deployed state: " + deployed_state["summary"])

    overall_verdict = "CLEAN" if not blockers else "BLOCKER"

    checkpoint_path = _write_checkpoint(root, log_dir, to_date)

    return {
        "routine": "daily-reconciliation-and-trade-chain-integrity",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_verdict": overall_verdict,
        "blockers": blockers,
        "github_repo_reconciliation": {
            "pr_hygiene": pr_hygiene,
            "branches_and_worktrees": {
                "stale_merged_branches": snapshot["local_only_branches"],
                "active_worktrees": snapshot["worktrees"],
                "branches_tracking_deleted_remotes": snapshot["branches_tracking_deleted_remotes"],
                "local_main_relationship": snapshot["local_main_relationship"],
                "stash_count": snapshot["stash_count"],
            },
            "evidence_preservation": evidence_preservation,
        },
        "deployed_state": deployed_state,
        "strategy_source_of_truth": strategy_drift,
        "trade_chain_integrity": trade_chain,
        "checkpoint_written": checkpoint_path,
        "never_mutates": (
            "This routine never cancels an order, flattens a position, modifies a broker order, "
            "repairs a journal, synthesizes an OUTCOME, retries an execution, submits an order, "
            "edits docs/config, or creates/deletes a git tag or branch."
        ),
    }


def format_trade_chain_line(trade_chain: dict[str, Any]) -> str:
    """The spec'd compact PASS line; callers should print full detail only on FAIL."""
    s = trade_chain["summary"]
    if s["verdict"] == "PASS":
        return (
            f"TRADE CHAIN: PASS\n"
            f"{s['attempts']} attempts\n"
            f"{s['fills']} fills\n"
            f"{s['cancellations_and_no_fills']} no-fills\n"
            f"{s['resolved']} resolved\n"
            f"{s['legitimate_opens']} legitimate opens\n"
            f"{s['orphans']} orphans\n"
            f"{s['stale_or_naked_orders']} stale orders\n"
            f"{s['duplicate_identities']} duplicate identities\n"
            f"broker/journal parity {s['broker_journal_parity']}"
        )
    lines = ["TRADE CHAIN: FAIL"] + [f"- {a}" for a in trade_chain["anomalies"]]
    return "\n".join(lines)
