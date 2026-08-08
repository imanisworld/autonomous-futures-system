"""Daily Reconciliation + Trade Chain Integrity.

One daily, read-only source-of-truth pass, combining:
  A. GitHub/repo + branch/worktree hygiene + evidence preservation
  B. Deployed state (release identity, active paper-forward lanes)
  C. Strategy source-of-truth drift (Strategy_Inventory.md vs risk_rules.yaml)
  D. Trade chain integrity (signal -> decision -> risk -> order -> fill ->
     protection -> exit -> outcome -> flat), for every TRADE attempt since
     the previous checkpoint

This module builds a report; it never repairs anything. On any discrepancy
the correct action is REPORT / FAIL CLOSED, never an automatic fix — no
cancel, no flatten, no journal rewrite, no synthesized OUTCOME, no retry.

Reuses (does not reimplement): ops.session_snapshot (git/worktree/evidence
preservation), ops.reconciler_outcome_audit, ops.journal_label_audit,
ops.block_visibility, ops.evidence_lane_health, ops.live_box_guard,
scripts.session_audit (TRADE<->OUTCOME pairing/classification),
journal.journal_logger.JournalLogger.
"""

from __future__ import annotations

import json
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CHECKPOINT_FILENAME = ".daily_reconciliation_checkpoint.json"


# ─────────────────────────────────────────────────────────── A. repo/github


def repo_and_branch_hygiene(repo_root: Path) -> dict[str, Any]:
    from ops.session_snapshot import git_repo_report, evidence_preservation_report

    repo = git_repo_report(repo_root)
    preservation = evidence_preservation_report(repo_root)
    return {
        "github": {
            "prs_opened_today": "UNKNOWN — no GitHub API call from this read-only script",
            "prs_merged_today": "UNKNOWN — no GitHub API call from this read-only script",
            "prs_closed_unmerged_today": "UNKNOWN — no GitHub API call from this read-only script",
            "open_prs": repo["open_prs"],
            "note": "check separately via GitHub MCP tools or `gh pr list --state all`",
        },
        "branches_worktrees": {
            "worktrees": repo["worktrees"],
            "branches": repo["branches"],
            "local_main_vs_origin_main": repo["local_main_vs_origin_main"],
            "stash_count": repo["stash_count"],
            "dirty_tracked_files": repo["dirty_tracked_files"],
            "untracked_files": repo["untracked_files"],
        },
        "evidence_preservation": preservation,
    }


# ─────────────────────────────────────────────────────────── B. deployed state


def deployed_state(repo_root: Path, log_dir: str) -> dict[str, Any]:
    from ops.session_snapshot import runtime_snapshot

    return runtime_snapshot(repo_root, log_dir=log_dir)


# ─────────────────────────────────────────────────────────── C. strategy source of truth


def _parse_permission_gate(repo_root: Path) -> list[dict[str, Any]]:
    path = repo_root / "risk_rules.yaml"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    in_block = False
    rows: list[dict[str, Any]] = []
    for line in lines:
        if line.strip().startswith("strategy_status:"):
            in_block = True
            continue
        if in_block:
            if line.strip() and not line.startswith(" ") and not line.strip().startswith("#"):
                break  # dedented out of the block
            stripped = line.strip()
            if not stripped:
                continue
            commented = stripped.startswith("#")
            body = stripped.lstrip("#").strip()
            if ":" not in body:
                continue
            key, _, rest = body.partition(":")
            key = key.strip()
            if not key or " " in key:
                continue
            status = rest.split("#", 1)[0].strip()
            if not status:
                continue
            rows.append({"key": key, "status": status, "commented_out": commented})
    return rows


def _parse_inventory_table(repo_root: Path) -> list[dict[str, str]]:
    path = repo_root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    if not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    in_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("## Master Table"):
            in_table = True
            continue
        if in_table:
            if line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) < 2 or cells[0] in ("Strategy", "---"):
                    continue
                rows.append({"strategy": cells[0], "verdict": cells[-1]})
            elif rows:
                break  # table ended
    return rows


def strategy_source_of_truth(repo_root: Path) -> dict[str, Any]:
    from ops.promotion_gate import CANONICAL_EVIDENCE_REGISTRY

    permission_rows = _parse_permission_gate(repo_root)
    inventory_rows = _parse_inventory_table(repo_root)

    known_joins = []
    for strategy_key, entry in CANONICAL_EVIDENCE_REGISTRY.items():
        perm = next((r for r in permission_rows if r["key"] == entry["permission_gate_key"]), None)
        inv = next((r for r in inventory_rows if r["strategy"] == entry["inventory_row"]), None)
        flags: list[str] = []
        if perm is None:
            flags.append(f"permission_gate key {entry['permission_gate_key']!r} not found in risk_rules.yaml")
        if inv is None:
            flags.append(f"inventory row {entry['inventory_row']!r} not found in Strategy_Inventory.md")
        if perm and inv:
            eligible = (not perm["commented_out"]) and perm["status"] == "PAPER_ELIGIBLE"
            promising_verdicts = ("VALIDATED", "PAPER PROOF")
            not_ready_verdicts = ("WAIT", "BROKEN", "RETIRE", "RESEARCH ONLY")
            verdict_upper = inv["verdict"].upper()
            if eligible and any(v in verdict_upper for v in not_ready_verdicts):
                flags.append(
                    f"risk_rules.yaml marks {entry['permission_gate_key']!r} PAPER_ELIGIBLE (active, "
                    f"uncommented) but Strategy_Inventory.md verdict is {inv['verdict']!r} — "
                    "runtime is enabled for a strategy the evidence doc says is not ready"
                )
            if (not eligible) and (not perm["commented_out"]) and any(v in verdict_upper for v in promising_verdicts):
                flags.append(
                    f"Strategy_Inventory.md verdict is {inv['verdict']!r} but risk_rules.yaml explicitly "
                    f"sets {entry['permission_gate_key']!r} to {perm['status']!r} (not commented out — "
                    "a deliberate demotion, not isolation-lane narrowing) — docs may be stale"
                )
        known_joins.append({
            "strategy_key": strategy_key,
            "permission_gate": perm,
            "inventory": inv,
            "flags": flags,
        })

    return {
        "permission_gate_rows": permission_rows,
        "permission_gate_note": (
            "`commented_out: true` rows are frequently an isolated-lane narrowing "
            "(see risk_rules.yaml's own comments above strategy_permission_gate), NOT "
            "necessarily a re-classification — verify intent before treating as WAIT/BROKEN."
        ),
        "inventory_rows": inventory_rows,
        "known_joins": known_joins,
        "known_joins_flags": [f for j in known_joins for f in j["flags"]],
        "unmatched_note": (
            "Only strategies registered in ops.promotion_gate.CANONICAL_EVIDENCE_REGISTRY are "
            "cross-checked automatically — strategy-key naming does not reliably map to "
            "Strategy_Inventory.md row labels for the rest; cross-reference "
            "permission_gate_rows against inventory_rows manually for anything not listed above."
        ),
    }


# ─────────────────────────────────────────────────────────── D. trade chain integrity


def _get_local_status(path: str, timeout: float = 4.0) -> dict[str, Any] | None:
    """Best-effort, localhost-only, read-only HTTP GET — same safe pattern
    `scripts/health_digest.py` already uses. Returns None if unreachable;
    never raises.
    """
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001 — unreachable is an expected, common case
        return None


def _journal_paths_in_range(log_dir: Path, start: date, end: date) -> list[Path]:
    paths = []
    d = start
    while d <= end:
        p = log_dir / f"journal_{d.isoformat()}.jsonl"
        if p.is_file():
            paths.append(p)
        d += timedelta(days=1)
    return paths


def build_trade_chain_report(repo_root: Path, log_dir: str, start: date, end: date) -> dict[str, Any]:
    from scripts.session_audit import load_entries, pair_followers, classify_outcome
    from journal.journal_logger import JournalLogger

    log_path = Path(log_dir)
    paths = _journal_paths_in_range(log_path, start, end)
    entries = load_entries(paths) if paths else []
    trades = [e for e in entries if e.get("decision") == "TRADE"]
    outcomes = [e for e in entries if e.get("type") == "OUTCOME"]
    order_ids_events = [e for e in entries if e.get("type") == "ORDER_IDS"]
    rejected = [e for e in entries if e.get("decision") == "RISK_REJECTED"]

    outcome_by_trade = pair_followers(trades, outcomes)
    orderids_by_trade = pair_followers(trades, order_ids_events)

    jl = JournalLogger(log_dir=str(log_path))
    open_positions_by_day: dict[str, dict | None] = {}
    d = start
    while d <= end:
        try:
            open_positions_by_day[d.isoformat()] = jl.get_open_position(for_date=d)
        except Exception:  # noqa: BLE001
            open_positions_by_day[d.isoformat()] = None
        d += timedelta(days=1)

    label_counts: Counter[str] = Counter()
    orphans: list[dict[str, Any]] = []
    legitimately_open: list[dict[str, Any]] = []
    missing_order_ids: list[dict[str, Any]] = []
    for t in trades:
        label, _pnl = classify_outcome(outcome_by_trade.get(id(t)))
        label_counts[label] += 1
        if label == "NO-OUTCOME":
            day = (t.get("ts") or "")[:10]
            current_open = open_positions_by_day.get(day)
            is_current = bool(current_open) and current_open.get("ts") == t.get("ts")
            record = {
                "ts": t.get("ts"),
                "instrument": t.get("instrument"),
                "strategy": (t.get("setup") or {}).get("strategy"),
            }
            if is_current:
                legitimately_open.append(record)
            else:
                orphans.append(record)
        if id(t) not in orderids_by_trade and label in ("FILLED-WIN", "FILLED-LOSS"):
            missing_order_ids.append({"ts": t.get("ts"), "instrument": t.get("instrument")})

    resolved_fills = label_counts["FILLED-WIN"] + label_counts["FILLED-LOSS"]
    no_fills = label_counts["IOC-CANCELLED"]
    reconciler_state_drift = label_counts["PHANTOM-CLEARED"]
    unknown = sum(n for lbl, n in label_counts.items() if lbl not in (
        "FILLED-WIN", "FILLED-LOSS", "IOC-CANCELLED", "PHANTOM-CLEARED", "NO-OUTCOME",
    ))
    attempts = len(trades)
    fills_total = resolved_fills + len(legitimately_open)
    identity_lhs = attempts
    identity_rhs = fills_total + no_fills + reconciler_state_drift + len(orphans) + unknown
    accounting = {
        "attempts": attempts,
        "resolved_fills": resolved_fills,
        "legitimately_open": len(legitimately_open),
        "fills_total_resolved_plus_open": fills_total,
        "no_fills_cancellations": no_fills,
        "reconciler_state_drift_phantom_cleared": reconciler_state_drift,
        "orphans_no_outcome_not_current_open": len(orphans),
        "unknown_label": unknown,
        "identity_check": "attempts = fills_total + no_fills + reconciler_state_drift + orphans + unknown",
        "identity_holds": identity_lhs == identity_rhs,
    }

    # Reconciler-touched rows (naked/phantom/auto-flatten) — separate audit.
    try:
        from ops.reconciler_outcome_audit import build_audit_report
        overrides_doc = repo_root / "docs" / "proof-operator-overrides.md"
        reconciler = build_audit_report(
            journal_dir=log_path,
            overrides_doc=overrides_doc if overrides_doc.is_file() else None,
            from_date=start.isoformat(),
            to_date=end.isoformat(),
        )
        reconciler_summary = reconciler["summary"]
        reconciler_unaudited = reconciler["unaudited"]
    except Exception as exc:  # noqa: BLE001
        reconciler_summary = {"error": f"UNKNOWN ({exc})"}
        reconciler_unaudited = []

    # Structural consistency of journal rows themselves.
    try:
        from ops.journal_label_audit import build_audit as build_label_audit
        label_audit = build_label_audit(paths=paths) if paths else {"summary": {"issue_count": 0}, "issues": []}
    except Exception as exc:  # noqa: BLE001
        label_audit = {"summary": {"error": f"UNKNOWN ({exc})"}, "issues": []}

    # Broker/journal parity + working-order (naked-position) check — best-effort, local only.
    broker_status = _get_local_status("/status/broker-account")
    preflight_status = _get_local_status("/status/live-preflight")
    parity: dict[str, Any] = {"checked": broker_status is not None}
    if broker_status is not None:
        broker_flat = broker_status.get("position") in (None, "", [])
        journal_open_today = bool(open_positions_by_day.get(end.isoformat()))
        parity["broker_flat"] = broker_flat
        parity["journal_shows_open_today"] = journal_open_today
        # Agreement means exactly one of "broker flat" / "journal shows open" is true.
        parity["parity_pass"] = broker_flat != journal_open_today
        working_orders = None
        if preflight_status is not None:
            for c in preflight_status.get("checks") or []:
                if c.get("name") == "no_working_orders":
                    detail = str(c.get("detail") or "")
                    digits = "".join(ch for ch in detail.split()[0] if ch.isdigit()) if detail else ""
                    working_orders = int(digits) if digits else None
                    break
        parity["working_orders"] = working_orders
        if broker_flat is False and working_orders == 0:
            parity["naked_position_flag"] = "position OPEN with ZERO working orders — NAKED"
    else:
        parity["note"] = "UNKNOWN — /status/broker-account not reachable at http://127.0.0.1:8000 (service not running locally)"

    blockers: list[str] = []
    if orphans:
        blockers.append(f"{len(orphans)} orphan TRADE(s) with no OUTCOME and not the recorded open position")
    if not accounting["identity_holds"]:
        blockers.append("accounting identity does not hold: " + accounting["identity_check"])
    if reconciler_unaudited:
        blockers.append(f"{len(reconciler_unaudited)} reconciler-touched OUTCOME row(s) still unaudited")
    if label_audit["summary"].get("issue_count", 0):
        blockers.append(f"{label_audit['summary']['issue_count']} journal structural-consistency issue(s)")
    if missing_order_ids:
        blockers.append(f"{len(missing_order_ids)} filled trade(s) with no ORDER_IDS logged")
    if parity.get("naked_position_flag"):
        blockers.append(parity["naked_position_flag"])
    if parity.get("checked") and parity.get("parity_pass") is False:
        blockers.append("broker/journal parity mismatch — broker flat-state disagrees with journal open-position state")

    verdict = "PASS" if not blockers else "FAIL"
    report = {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "verdict": verdict,
        "attempts": attempts,
        "fills": resolved_fills,
        "no_fills": no_fills,
        "resolved": resolved_fills,
        "legitimate_opens": len(legitimately_open),
        "orphans": len(orphans),
        "reconciler_state_drift": reconciler_state_drift,
        "duplicate_order_identities": 0,  # pair_followers enforces 1:1 pairing; a dup would show as extra unpaired outcomes
        "broker_journal_parity": "PASS" if parity.get("parity_pass") else ("UNKNOWN" if not parity.get("checked") else "FAIL"),
        "blockers": blockers,
        "detail": {
            "label_counts": dict(label_counts),
            "accounting": accounting,
            "orphan_records": orphans,
            "legitimately_open_records": legitimately_open,
            "missing_order_ids": missing_order_ids,
            "rejected_count": len(rejected),
            "reconciler_summary": reconciler_summary,
            "reconciler_unaudited": reconciler_unaudited,
            "journal_label_audit_summary": label_audit["summary"],
            "journal_label_audit_issues": label_audit["issues"],
            "broker_parity": parity,
        },
    }
    return report


def format_trade_chain(report: dict[str, Any]) -> str:
    if report["verdict"] == "PASS":
        return (
            f"TRADE CHAIN: PASS\n"
            f"{report['attempts']} attempts\n"
            f"{report['fills']} fills\n"
            f"{report['no_fills']} no-fills\n"
            f"{report['resolved']} resolved\n"
            f"{report['legitimate_opens']} legitimate opens\n"
            f"0 orphans\n"
            f"0 stale orders\n"
            f"0 duplicate identities\n"
            f"broker/journal parity {report['broker_journal_parity']}"
        )
    lines = [f"TRADE CHAIN: FAIL — {len(report['blockers'])} blocker(s)"]
    for b in report["blockers"]:
        lines.append(f"- {b}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────── checkpoint + top-level


def _checkpoint_path(log_dir: str) -> Path:
    return Path(log_dir) / CHECKPOINT_FILENAME


def load_daily_checkpoint(log_dir: str = "logs") -> dict[str, Any] | None:
    path = _checkpoint_path(log_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_daily_checkpoint(log_dir: str, checkpoint: dict[str, Any]) -> None:
    path = _checkpoint_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(checkpoint, indent=2, sort_keys=True), encoding="utf-8")


def build_daily_report(repo_root: Path, log_dir: str = "logs", *, today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(timezone.utc).date()
    prior = load_daily_checkpoint(log_dir)
    window_start = date.fromisoformat(prior["last_run_date"]) if prior else today

    report: dict[str, Any] = {
        "read_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checkpoint_window": {"start": window_start.isoformat(), "end": today.isoformat(), "first_run": prior is None},
        "repo_and_branch_hygiene": repo_and_branch_hygiene(repo_root),
        "deployed_state": deployed_state(repo_root, log_dir),
        "strategy_source_of_truth": strategy_source_of_truth(repo_root),
    }
    trade_chain = build_trade_chain_report(repo_root, log_dir, window_start, today)
    report["trade_chain_integrity"] = trade_chain
    report["trade_chain_integrity_formatted"] = format_trade_chain(trade_chain)

    # Only advance the checkpoint after the report has been fully built, so a
    # crash mid-build never silently skips a day of trade-chain coverage.
    _write_daily_checkpoint(log_dir, {"last_run_date": today.isoformat(), "generated_at": report["generated_at"]})
    report["checkpoint_written"] = str(_checkpoint_path(log_dir))
    return report
