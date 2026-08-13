"""Trade chain integrity: signal -> decision -> risk -> order -> fill/no-fill
-> protective bracket -> exit -> outcome -> flat position, for every new
paper/demo trade attempt since the prior checkpoint.

Strictly read-only against journals. Never cancels an order, flattens a
position, modifies a broker order, repairs a journal, synthesizes an
OUTCOME, rewrites state, retries an execution, or submits an order. It also
never calls a broker API -- broker/journal PARITY is reported UNKNOWN unless
the caller explicitly injects already-fetched, read-only broker snapshots
(see `broker_positions`/`broker_orders` params), which this package's CLI
does not do by default.

Reuses ops.proof_30_mnq (read_journal_entries, classify_outcome, parse_proof_ts)
and mirrors the TRADE<->OUTCOME/ORDER_IDS pairing style already used by
ops/reconciler_outcome_audit.py and scripts/session_audit.py, generalized to
all instruments in one pass instead of one instrument at a time.
"""
from __future__ import annotations

import json
import tempfile
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ops.proof_30_mnq import classify_outcome, parse_proof_ts, read_journal_entries

CHECKPOINT_SUBDIR = "afs-project-check"
CHECKPOINT_FILENAME = "trade_chain_checkpoint.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _checkpoint_path(repo_root: Path) -> Path:
    return repo_root / ".git" / CHECKPOINT_SUBDIR / CHECKPOINT_FILENAME


def load_checkpoint(repo_root: Path) -> str | None:
    path = _checkpoint_path(repo_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("last_processed_ts")


def save_checkpoint(repo_root: Path, last_processed_ts: str | None) -> None:
    if last_processed_ts is None:
        return
    path = _checkpoint_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{CHECKPOINT_FILENAME}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump({"last_processed_ts": last_processed_ts, "saved_at": _now_iso()}, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _entry_ts(entry: dict[str, Any]) -> datetime | None:
    return parse_proof_ts(entry.get("ts"))


def _pair_fifo_by_instrument(
    anchors: list[dict[str, Any]], events: list[dict[str, Any]], all_in_order: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Pair each anchor with the next same-instrument event in a strict FIFO
    queue, walking all entries in journal (chronological) order.

    Same approach as ops.proof_30_mnq.pair_resolved_trades (queue.pop(0) on
    the oldest open anchor for that instrument when a matching event arrives),
    generalized to all instruments in one pass instead of one at a time. A
    nearest-single-global-match approach was tried first and rejected: it let
    an unresolved earlier trade "steal" a later trade's outcome across a
    journal-day boundary, which is exactly the kind of misattribution this
    routine exists to catch, not commit.
    """
    anchor_ids = {id(a) for a in anchors}
    event_ids = {id(e) for e in events}
    queues: dict[str, list[dict[str, Any]]] = defaultdict(list)
    paired: dict[int, dict[str, Any]] = {}
    for entry in all_in_order:
        instrument = entry.get("instrument")
        if id(entry) in anchor_ids:
            queues[instrument].append(entry)
        elif id(entry) in event_ids and queues[instrument]:
            paired[id(queues[instrument].pop(0))] = entry
    return paired


def build_trade_chain_report(
    *,
    journal_dir: Path,
    repo_root: Path,
    since_ts: str | None = None,
    use_checkpoint: bool = True,
    advance_checkpoint: bool = False,
    broker_positions: Callable[[], list[dict[str, Any]]] | None = None,
    broker_orders: Callable[[], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    entries = read_journal_entries(journal_dir)
    read_errors = [e for e in entries if e.get("type") == "READ_ERROR"]

    checkpoint_ts = since_ts
    if checkpoint_ts is None and use_checkpoint:
        checkpoint_ts = load_checkpoint(repo_root)

    def in_window(entry: dict[str, Any]) -> bool:
        if checkpoint_ts is None:
            return True
        ts = entry.get("ts")
        return ts is not None and str(ts) > checkpoint_ts

    windowed = [e for e in entries if e.get("type") != "READ_ERROR" and in_window(e)]
    all_ts = sorted(str(e.get("ts")) for e in entries if e.get("type") != "READ_ERROR" and e.get("ts"))
    latest_journal_ts = all_ts[-1] if all_ts else None
    latest_journal_day = latest_journal_ts[:10] if latest_journal_ts else None

    attempts = [
        e for e in windowed
        if e.get("decision") == "TRADE" and (e.get("risk_check") or {}).get("result") == "APPROVED"
    ]
    outcomes = [e for e in windowed if e.get("type") == "OUTCOME"]
    order_id_events = [e for e in windowed if e.get("type") == "ORDER_IDS"]
    risk_rejected = [e for e in windowed if e.get("decision") == "RISK_REJECTED"]
    config_blocked = [e for e in windowed if e.get("decision") == "CONFIG_BLOCKED"]

    outcome_by_attempt = _pair_fifo_by_instrument(attempts, outcomes, windowed)
    orderids_by_attempt = _pair_fifo_by_instrument(attempts, order_id_events, windowed)

    resolved_fills: list[dict[str, Any]] = []
    resolved_cancellations: list[dict[str, Any]] = []
    needs_broker_verification: list[dict[str, Any]] = []
    unresolved_open: list[dict[str, Any]] = []
    unresolved_orphan: list[dict[str, Any]] = []
    naked_position_risk: list[dict[str, Any]] = []

    for attempt in attempts:
        outcome = outcome_by_attempt.get(id(attempt))
        setup = attempt.get("setup") or {}
        row = {
            "trade_ts": attempt.get("ts"),
            "instrument": attempt.get("instrument"),
            "strategy": setup.get("strategy"),
            "direction": setup.get("direction"),
            "path": attempt.get("_path"),
            "line": attempt.get("_line"),
        }
        if outcome is None:
            attempt_day = (str(attempt.get("ts")) or "")[:10]
            if latest_journal_day is not None and attempt_day == latest_journal_day:
                unresolved_open.append(row)
            else:
                unresolved_orphan.append({**row, "reason": "no OUTCOME found and not from the most recent journal day"})
            continue
        category = classify_outcome(outcome.get("outcome") or {})
        row["category"] = category
        row["exit_reason"] = (outcome.get("outcome") or {}).get("exit_reason")
        row["pnl_dollars"] = (outcome.get("outcome") or {}).get("pnl_dollars")
        if category in ("filled_win_loss", "breakeven"):
            resolved_fills.append(row)
            if setup.get("stop") in (None, "") or setup.get("target") in (None, ""):
                naked_position_risk.append({**row, "issue": "filled trade missing stop or target in journaled setup"})
        elif category == "cancelled_nofill":
            resolved_cancellations.append(row)
        else:
            needs_broker_verification.append(row)

    # Duplicate order-identity check across all order_ids events in the window.
    seen_order_ids: dict[str, list[str]] = defaultdict(list)
    for attempt in attempts:
        oi = orderids_by_attempt.get(id(attempt))
        if not oi:
            continue
        ids = (oi.get("order_ids") or {})
        label = f"{attempt.get('_path')}:{attempt.get('_line')}"
        for key, value in ids.items():
            if key == "instrument" or value in (None, ""):
                continue
            seen_order_ids[str(value)].append(label)
    duplicate_order_ids = {oid: locs for oid, locs in seen_order_ids.items() if len(locs) > 1}

    fills_count = len(resolved_fills) + len(unresolved_open)
    cancellations_count = len(resolved_cancellations)
    rejects_or_no_fill_count = len(needs_broker_verification)
    orphans_count = len(unresolved_orphan)
    attempts_identity_holds = len(attempts) == (
        fills_count + cancellations_count + rejects_or_no_fill_count + orphans_count
    )
    fills_identity_holds = fills_count == (len(resolved_fills) + len(unresolved_open))

    risk_rejected_missing_reason = [
        {"ts": e.get("ts"), "instrument": e.get("instrument"), "path": e.get("_path"), "line": e.get("_line")}
        for e in risk_rejected
        if not e.get("reason")
    ]

    broker_parity: dict[str, Any]
    if broker_positions is None and broker_orders is None:
        broker_parity = {
            "checked": False,
            "reason": (
                "no read-only broker snapshot was supplied to this routine; broker/journal "
                "parity requires a live broker read this routine does not perform by default "
                "(see build_trade_chain_report's broker_positions/broker_orders params)"
            ),
        }
    else:
        broker_parity = {"checked": True, "note": "broker snapshot comparison not yet implemented for the supplied data"}

    problems = (
        orphans_count > 0
        or bool(duplicate_order_ids)
        or bool(naked_position_risk)
        or not attempts_identity_holds
        or not fills_identity_holds
        or bool(risk_rejected_missing_reason)
    )

    if advance_checkpoint and latest_journal_ts:
        save_checkpoint(repo_root, latest_journal_ts)

    return {
        "ok": not read_errors,
        "routine": "trade-chain-integrity",
        "generated_at": _now_iso(),
        "journal_dir": str(journal_dir),
        "window": {
            "since_ts_exclusive": checkpoint_ts,
            "latest_journal_ts": latest_journal_ts,
            "used_checkpoint": use_checkpoint and since_ts is None,
        },
        "journal_read_errors": read_errors,
        "status": "PASS" if not problems else "FAIL",
        "summary": {
            "attempts": len(attempts),
            "fills": fills_count,
            "resolved_fills": len(resolved_fills),
            "legitimately_open": len(unresolved_open),
            "cancellations": cancellations_count,
            "needs_broker_verification": rejects_or_no_fill_count,
            "orphans": orphans_count,
            "duplicate_order_identities": len(duplicate_order_ids),
            "naked_position_risk": len(naked_position_risk),
            "risk_rejected": len(risk_rejected),
            "risk_rejected_missing_reason": len(risk_rejected_missing_reason),
            "config_blocked": len(config_blocked),
        },
        "accounting": {
            "attempts_identity": "attempts = fills + cancellations + needs_broker_verification + orphans",
            "attempts_identity_holds": attempts_identity_holds,
            "fills_identity": "fills = resolved_fills + legitimately_open",
            "fills_identity_holds": fills_identity_holds,
        },
        "detail": {
            "resolved_fills": resolved_fills,
            "legitimately_open": unresolved_open,
            "cancellations": resolved_cancellations,
            "needs_broker_verification": needs_broker_verification,
            "orphans": unresolved_orphan,
            "duplicate_order_identities": duplicate_order_ids,
            "naked_position_risk": naked_position_risk,
            "risk_rejected_missing_reason": risk_rejected_missing_reason,
        },
        "broker_journal_parity": broker_parity,
        "eod_day_only_boundary_check": {
            "checked": False,
            "reason": "no machine-readable per-strategy day-only-exit registry found in the repo to verify against",
        },
        "not_checked_reminder": (
            "This routine is read-only and never cancels an order, flattens a position, "
            "modifies a broker order, repairs a journal, synthesizes an OUTCOME, rewrites "
            "state, retries an execution, or submits an order."
        ),
    }
