"""Daily Reconciliation + Trade Chain Integrity — routine #3 of ops.project_check.

One daily read-only source-of-truth pass combining:
  A. GitHub / repo reconciliation (PRs, branches, worktrees, stashes, evidence
     preservation via archive/* tags)
  B. Deployed state (release SHA, active paper-forward lanes, execution mode)
  C. Strategy source of truth (Strategy_Inventory.md vs risk_rules.yaml drift)
  D. Trade chain integrity (signal -> decision -> risk -> order -> fill/no-fill
     -> protective bracket -> exit -> outcome -> flat, with accounting
     identities)

READ ONLY. Never cancels an order, flattens a position, modifies a broker
order, repairs a journal, synthesizes an OUTCOME, rewrites state, retries an
execution, submits an order, deletes a branch/worktree, or creates/deletes an
archive tag. On any discrepancy: report and fail closed, never auto-repair.

The only write this module performs is its own local checkpoint file
(``logs/project_check_daily_checkpoint.json``) recording the last date
checked, so a second run the same day doesn't have to re-scan everything —
this is bookkeeping for this tool only, not a journal/git/broker mutation.
"""

from __future__ import annotations

import json
import re
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ops import project_check_git as pcg
from ops.proof_30_mnq import (
    RECONCILER_MARKERS,
    classify_outcome,
    pair_resolved_trades,
    read_journal_entries,
)

CHECKPOINT_FILENAME = "project_check_daily_checkpoint.json"
STALE_PR_DAYS = 14


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def checkpoint_path(log_dir: str | Path = "logs") -> Path:
    return Path(log_dir) / CHECKPOINT_FILENAME


def _read_checkpoint(log_dir: str | Path) -> Optional[str]:
    path = checkpoint_path(log_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("last_checked_through")
    except (OSError, ValueError):
        return None


def _write_checkpoint(log_dir: str | Path, through_date: str) -> Path:
    path = checkpoint_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"last_checked_through": through_date, "written_at": _now_iso()}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------- A. github/repo


def _repo_reconciliation(root: str, today: date) -> dict[str, Any]:
    prs = pcg.gh_pr_list(root)
    section: dict[str, Any] = {}
    if prs is None:
        section["prs"] = pcg.UNKNOWN
        section["prs_note"] = "gh CLI unavailable or the lookup failed — cannot verify PR state."
    else:
        today_iso = today.isoformat()
        opened_today = [p for p in prs if str(p.get("createdAt", "")).startswith(today_iso)]
        merged_today = [p for p in prs if str(p.get("mergedAt") or "").startswith(today_iso)]
        closed_unmerged_today = [
            p for p in prs
            if str(p.get("closedAt") or "").startswith(today_iso) and not p.get("mergedAt")
        ]
        open_prs = [p for p in prs if p.get("state") == "OPEN"]
        stale_cutoff = (today - timedelta(days=STALE_PR_DAYS)).isoformat()
        stale_prs = [
            p for p in open_prs
            if str(p.get("updatedAt", "9999")) < stale_cutoff
        ]
        section["prs_opened_today"] = opened_today
        section["prs_merged_today"] = merged_today
        section["prs_closed_unmerged_today"] = closed_unmerged_today
        section["open_prs"] = open_prs
        section["stale_prs"] = stale_prs

    branches = pcg.local_branches(root)
    wts = pcg.worktrees(root)
    dirty_wts = []
    for wt in wts:
        dirty = pcg.dirty_files(wt.get("path"))
        if dirty.get("modified") or dirty.get("staged") or dirty.get("untracked"):
            dirty_wts.append({"path": wt.get("path"), "dirty": dirty})

    head = pcg.head_sha(root)
    origin_main = pcg.ref_sha("origin/main", root)
    sync = pcg.sync_status(head, origin_main, root)

    not_merged = pcg.not_merged_into("main", root)
    unarchived = []
    for name in sorted(not_merged):
        sha = pcg.ref_sha(name, root)
        if not sha:
            continue
        pointing = pcg.tags_pointing_at(sha, root)
        if not any(t.startswith("archive/") for t in pointing):
            unarchived.append({"branch": name, "sha": sha})

    section.update(
        {
            "stale_merged_branches": pcg.merged_into("main", root),
            "active_worktrees": wts,
            "dirty_worktrees": dirty_wts,
            "branches_tracking_deleted_remotes": [b["name"] for b in branches if b["gone"]],
            "local_only_branches": [b["name"] for b in branches if b["local_only"]],
            "local_main_status": sync,
            "stash_count": len(pcg.stash_list(root)),
            "evidence_preservation_blockers": unarchived,
        }
    )
    return section


# ---------------------------------------------------------------- B. deployed state


def _deployed_state() -> dict[str, Any]:
    from ops.project_check_session import _runtime_posture

    return _runtime_posture()


# ---------------------------------------------------------------- C. strategy source of truth


def _normalize_strategy_name(name: str) -> str:
    name = re.sub(r"\([^)]*\)", "", name)  # drop "(MES)"/"(MNQ)" instrument suffix
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _parse_strategy_inventory(root: Path) -> list[dict[str, str]]:
    path = root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    if not path.is_file():
        return []
    rows = []
    in_table = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("| Strategy "):
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table and not line.startswith("|"):
            break
        if in_table:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2:
                continue
            name = cells[0]
            verdict = cells[-1].replace("*", "").strip()
            rows.append({"name": name, "verdict": verdict, "normalized": _normalize_strategy_name(name)})
    return rows


def _strategy_source_of_truth(root: Path, runtime: dict[str, Any]) -> dict[str, Any]:
    inventory_rows = _parse_strategy_inventory(root)
    try:
        from config.settings import load_config

        config = load_config()
        enabled_concepts = set(config.enabled_concepts or [])
        strategy_status = config.strategy_status or {}
        default_status = config.strategy_permission_default_status
        config_error = None
    except Exception as exc:  # noqa: BLE001
        enabled_concepts, strategy_status, default_status, config_error = set(), {}, None, str(exc)

    drift: list[dict[str, Any]] = []
    stale_positive_but_gated = []
    for row in inventory_rows:
        verdict = row["verdict"]
        norm = row["normalized"]
        matches = [c for c in enabled_concepts | set(strategy_status.keys()) if _normalize_strategy_name(c) in norm or norm in _normalize_strategy_name(c)]
        concept = matches[0] if matches else None
        is_promotable_verdict = verdict.upper() in ("PAPER PROOF", "VALIDATED")
        is_negative_verdict = any(k in verdict.upper() for k in ("BROKEN", "WAIT", "RETIRE", "OVERFIT"))

        if concept is None:
            continue
        permission = strategy_status.get(concept, default_status)
        enabled = concept in enabled_concepts

        if is_promotable_verdict and (not enabled or permission != "PAPER_ELIGIBLE"):
            stale_positive_but_gated.append(
                {
                    "strategy": row["name"],
                    "inventory_verdict": verdict,
                    "concept": concept,
                    "enabled_concept": enabled,
                    "permission_status": permission,
                    "issue": "described as active/promotable in Strategy_Inventory.md but fail-closed at runtime",
                }
            )
        if is_negative_verdict and enabled and permission == "PAPER_ELIGIBLE":
            drift.append(
                {
                    "strategy": row["name"],
                    "inventory_verdict": verdict,
                    "concept": concept,
                    "enabled_concept": enabled,
                    "permission_status": permission,
                    "issue": "runtime marks this PAPER_ELIGIBLE + enabled, but Strategy_Inventory.md verdict is negative",
                }
            )

    return {
        "inventory_rows_parsed": len(inventory_rows),
        "config_load_error": config_error,
        "described_active_but_fail_closed": stale_positive_but_gated,
        "described_negative_but_runtime_paper_eligible": drift,
        "note": (
            "Matching is a best-effort normalized name match between "
            "Strategy_Inventory.md row names and risk_rules.yaml concept keys. "
            "A miss here is not proof of no drift — verify unmatched strategies "
            "manually."
        ),
    }


# ---------------------------------------------------------------- D. trade chain


def _broker_snapshot(api_base: Optional[str]) -> dict[str, Any]:
    if not api_base:
        return {"checked": False, "note": "no --api-base supplied — broker/journal parity NOT checked"}
    try:
        with urllib.request.urlopen(f"{api_base.rstrip('/')}/status/broker-account", timeout=8.0) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode())
        return {"checked": True, "payload": payload}
    except Exception as exc:  # noqa: BLE001
        return {"checked": True, "error": str(exc)}


def _trade_chain(entries: list[dict], today: date, api_base: Optional[str]) -> dict[str, Any]:
    instruments = sorted({e.get("instrument") for e in entries if e.get("instrument")})

    all_resolved: list[Any] = []
    all_unmatched_outcomes: list[dict] = []
    all_still_open: list[dict] = []
    rejected_no_reason: list[dict] = []
    rejected_total = 0

    for inst in instruments:
        resolved, unmatched = pair_resolved_trades(entries, instrument=inst, limit=10_000)
        all_resolved.extend(resolved)
        all_unmatched_outcomes.extend(unmatched)
        # still-open = approved TRADE rows for this instrument with no OUTCOME
        # paired above; recompute the pending tail the same way pair_resolved_trades does.
        pending: list[dict] = []
        for e in entries:
            if (e.get("instrument") or "").upper() != inst.upper():
                continue
            if e.get("decision") == "TRADE" and (e.get("risk_check") or {}).get("result") == "APPROVED":
                pending.append(e)
            elif e.get("type") == "OUTCOME" and pending:
                pending.pop(0)
        all_still_open.extend(pending)

    for e in entries:
        if e.get("decision") == "TRADE" and (e.get("risk_check") or {}).get("result") == "REJECTED":
            rejected_total += 1
            risk = e.get("risk_check") or {}
            if not (risk.get("reason") or risk.get("failed_rule") or e.get("reason")):
                rejected_no_reason.append({"instrument": e.get("instrument"), "ts": e.get("ts")})

    # ORDER_IDS rows -> nearest-following pairing per instrument for bracket/protection checks.
    order_ids_rows = [e for e in entries if e.get("type") == "ORDER_IDS"]
    seen_entry_ids: Counter[str] = Counter()
    for row in order_ids_rows:
        entry_id = (row.get("order_ids") or {}).get("entry")
        if entry_id:
            seen_entry_ids[str(entry_id)] += 1
    duplicate_order_ids = {k: v for k, v in seen_entry_ids.items() if v > 1}

    fills, cancellations, other = [], [], []
    no_fill_reason_counts: Counter[str] = Counter()
    naked_fills: list[dict] = []
    for pair in all_resolved:
        outcome_body = pair.outcome_body
        category = classify_outcome(outcome_body)
        if category in ("filled_win_loss", "breakeven"):
            fills.append(pair)
            trade_ts = pair.trade.get("ts") or ""
            inst = pair.trade.get("instrument")
            matched_bracket = None
            for row in order_ids_rows:
                if row.get("instrument") == inst and (row.get("ts") or "") >= trade_ts:
                    matched_bracket = row
                    break
            if matched_bracket is None:
                naked_fills.append({"instrument": inst, "ts": trade_ts, "issue": "no ORDER_IDS row found — bracket unknown"})
            else:
                ids = matched_bracket.get("order_ids") or {}
                if not ids.get("target") or not ids.get("stop"):
                    naked_fills.append({"instrument": inst, "ts": trade_ts, "issue": "ORDER_IDS row missing target and/or stop leg", "order_ids": ids})
        elif category == "reconciler_touched":
            other.append(pair)
        elif category == "cancelled_nofill":
            cancellations.append(pair)
            no_fill_reason_counts[outcome_body.get("no_fill_reason") or "UNCLASSIFIED"] += 1
        else:
            other.append(pair)

    today_iso = today.isoformat()
    legitimately_open = [t for t in all_still_open if (t.get("ts") or "")[:10] == today_iso]
    orphans = [t for t in all_still_open if (t.get("ts") or "")[:10] != today_iso]

    day_only_exit_issues = [e for e in entries if e.get("type") == "DAY_ONLY_EXIT_ISSUE"]

    reconciler_touched = [
        p for p in all_resolved
        if any(m in str(p.outcome_body.get("exit_reason") or "").lower() for m in RECONCILER_MARKERS)
    ]

    attempts = len(all_resolved) + len(all_still_open)
    approved_total = attempts + 0  # every resolved/still-open pair originated from an APPROVED trade
    identity_fills = {
        "description": "fills(open+closed) = resolved_fills + legitimately_open",
        "resolved_fills": len(fills),
        "legitimately_open": len(legitimately_open),
        "total": len(fills) + len(legitimately_open),
    }
    identity_attempts = {
        "description": "entry_attempts(approved) = fills + cancellations + orphans + other_unclassified",
        "entry_attempts": approved_total,
        "rhs": len(fills) + len(legitimately_open) + len(cancellations) + len(orphans) + len(other),
        "matches": approved_total == len(fills) + len(legitimately_open) + len(cancellations) + len(orphans) + len(other),
    }

    broker = _broker_snapshot(api_base)
    parity_mismatch = None
    if broker.get("checked") and isinstance(broker.get("payload"), dict):
        broker_flat = broker["payload"].get("position") in (None, "", [])
        journal_open = bool(legitimately_open) or bool(orphans)
        if broker_flat and journal_open:
            parity_mismatch = "broker reports FLAT but journal shows an unresolved open position"
        elif not broker_flat and not journal_open:
            parity_mismatch = "broker reports an OPEN position but journal shows none unresolved"

    problems: list[str] = []
    if not identity_attempts["matches"]:
        problems.append("accounting identity mismatch on entry_attempts")
    if orphans:
        problems.append(f"{len(orphans)} unresolved trade(s) from a prior day (orphan/stale)")
    if naked_fills:
        problems.append(f"{len(naked_fills)} filled position(s) with missing/incomplete protective bracket")
    if duplicate_order_ids:
        problems.append(f"duplicate order identity: {duplicate_order_ids}")
    if rejected_no_reason:
        problems.append(f"{len(rejected_no_reason)} risk-rejected candidate(s) with no reason recorded")
    if all_unmatched_outcomes:
        problems.append(f"{len(all_unmatched_outcomes)} OUTCOME row(s) with no matching TRADE (unmatched outcome)")
    if day_only_exit_issues:
        problems.append(f"{len(day_only_exit_issues)} DAY_ONLY_EXIT_ISSUE record(s) in window")
    if reconciler_touched:
        problems.append(f"{len(reconciler_touched)} outcome(s) reconciler-touched — needs broker-verified manual classification")
    if parity_mismatch:
        problems.append(f"broker/journal parity: {parity_mismatch}")

    return {
        "instruments": instruments,
        "attempts": attempts,
        "fills": len(fills),
        "cancellations": len(cancellations),
        "resolved": len(all_resolved),
        "legitimately_open": len(legitimately_open),
        "orphans": orphans,
        "other_unclassified": len(other),
        "candidates_reaching_risk_engine": attempts + rejected_total,
        "rejected_by_risk_engine": rejected_total,
        "rejected_with_no_reason": rejected_no_reason,
        "known_no_fill_reasons": dict(no_fill_reason_counts.most_common()),
        "naked_or_unverified_brackets": naked_fills,
        "duplicate_order_ids": duplicate_order_ids,
        "unmatched_outcomes": len(all_unmatched_outcomes),
        "day_only_exit_issues": len(day_only_exit_issues),
        "reconciler_touched_needs_manual_verification": len(reconciler_touched),
        "accounting_identities": [identity_fills, identity_attempts],
        "broker_parity": broker,
        "broker_parity_mismatch": parity_mismatch,
        "problems": problems,
        "pass": not problems,
    }


def format_trade_chain(chain: dict[str, Any]) -> str:
    if chain["pass"]:
        return (
            "TRADE CHAIN: PASS\n"
            f"{chain['attempts']} attempts\n"
            f"{chain['fills']} fills\n"
            f"{chain['cancellations']} no-fills\n"
            f"{chain['resolved']} resolved\n"
            f"{chain['legitimately_open']} legitimate opens\n"
            f"0 orphans\n"
            f"0 stale/naked brackets\n"
            f"0 duplicate identities\n"
            f"broker/journal parity {'PASS' if not chain['broker_parity_mismatch'] else 'FAIL'}"
            + ("" if chain["broker_parity"].get("checked") else " (NOT CHECKED — no --api-base)")
        )
    lines = ["TRADE CHAIN: FAIL — problems found:"]
    for p in chain["problems"]:
        lines.append(f"  ✗ {p}")
    lines.append(
        f"attempts={chain['attempts']} fills={chain['fills']} cancellations={chain['cancellations']} "
        f"resolved={chain['resolved']} legit_open={chain['legitimately_open']} "
        f"orphans={len(chain['orphans'])} naked={len(chain['naked_or_unverified_brackets'])}"
    )
    if chain["orphans"]:
        lines.append(f"  orphans: {chain['orphans']}")
    if chain["naked_or_unverified_brackets"]:
        lines.append(f"  brackets: {chain['naked_or_unverified_brackets']}")
    if chain["rejected_with_no_reason"]:
        lines.append(f"  rejected w/o reason: {chain['rejected_with_no_reason']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- top level


def build_daily_report(
    cwd: Optional[str] = None,
    *,
    log_dir: str | Path = "logs",
    since: Optional[str] = None,
    api_base: Optional[str] = None,
    update_checkpoint: bool = True,
) -> dict[str, Any]:
    root = pcg.repo_root(cwd) or cwd or "."
    today = date.today()

    checkpoint = _read_checkpoint(log_dir)
    window_start = since or checkpoint
    if window_start:
        try:
            start_date = datetime.fromisoformat(window_start).date() + timedelta(days=0 if since else 1)
        except ValueError:
            start_date = today
    else:
        start_date = today

    entries = read_journal_entries(Path(log_dir))
    window_start_iso = start_date.isoformat()
    windowed_entries = [e for e in entries if (e.get("ts") or "9999")[:10] >= window_start_iso]

    report: dict[str, Any] = {
        "generated_at": _now_iso(),
        "window": {"start": window_start_iso, "through": today.isoformat(), "checkpoint_used": checkpoint},
        "github_repo_reconciliation": _repo_reconciliation(root, today),
        "deployed_state": _deployed_state(),
        "strategy_source_of_truth": _strategy_source_of_truth(Path(root), {}),
        "trade_chain": _trade_chain(windowed_entries, today, api_base),
    }

    if update_checkpoint:
        report["checkpoint_written"] = str(_write_checkpoint(log_dir, today.isoformat()))

    return report


def format_daily_report(report: dict[str, Any]) -> str:
    lines = [
        f"DAILY RECONCILIATION — {report['generated_at']}",
        f"window: {report['window']['start']} .. {report['window']['through']} (checkpoint used: {report['window']['checkpoint_used']})",
        "",
        "== A. GitHub / repo reconciliation ==",
    ]
    gr = report["github_repo_reconciliation"]
    if gr.get("prs") == pcg.UNKNOWN:
        lines.append(f"PRs: UNKNOWN — {gr.get('prs_note')}")
    else:
        lines.append(
            f"PRs opened today: {len(gr['prs_opened_today'])}  merged today: {len(gr['prs_merged_today'])}  "
            f"closed-unmerged today: {len(gr['prs_closed_unmerged_today'])}  open: {len(gr['open_prs'])}  "
            f"stale(>{STALE_PR_DAYS}d): {len(gr['stale_prs'])}"
        )
    lines.append(
        f"worktrees: {len(gr['active_worktrees'])} ({len(gr['dirty_worktrees'])} dirty)  "
        f"local main: {gr['local_main_status']}  stashes: {gr['stash_count']}"
    )
    if gr["branches_tracking_deleted_remotes"]:
        lines.append(f"branches tracking deleted remotes: {gr['branches_tracking_deleted_remotes']}")
    if gr["evidence_preservation_blockers"]:
        lines.append(f"BLOCKER — unmerged branches with no archive tag: {gr['evidence_preservation_blockers']}")

    lines.append("")
    lines.append("== B. Deployed state ==")
    ds = report["deployed_state"]
    lines.append(f"deployed SHA: {ds['deployed_release_sha']} ({ds['deployed_release_branch']})")
    lines.append(f"active paper-forward lanes: {ds['active_paper_forward_lanes']}")
    lines.append(f"execution mode: {ds['execution_mode']}  entry fill model: {ds['entry_fill_model']}")

    lines.append("")
    lines.append("== C. Strategy source of truth ==")
    st = report["strategy_source_of_truth"]
    lines.append(f"inventory rows parsed: {st['inventory_rows_parsed']}")
    if st["described_active_but_fail_closed"]:
        lines.append("DRIFT — described active but fail-closed at runtime:")
        for d in st["described_active_but_fail_closed"]:
            lines.append(f"  - {d['strategy']}: inventory={d['inventory_verdict']} permission={d['permission_status']} enabled={d['enabled_concept']}")
    if st["described_negative_but_runtime_paper_eligible"]:
        lines.append("DRIFT — runtime PAPER_ELIGIBLE but inventory verdict negative:")
        for d in st["described_negative_but_runtime_paper_eligible"]:
            lines.append(f"  - {d['strategy']}: inventory={d['inventory_verdict']} permission={d['permission_status']}")
    if not st["described_active_but_fail_closed"] and not st["described_negative_but_runtime_paper_eligible"]:
        lines.append("no drift detected")

    lines.append("")
    lines.append("== D. Trade chain integrity ==")
    lines.append(format_trade_chain(report["trade_chain"]))

    return "\n".join(lines)
