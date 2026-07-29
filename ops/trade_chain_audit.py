"""Read-only trade-chain integrity tracer: signal -> decision -> risk -> order
-> fill/no-fill -> protective bracket -> exit -> outcome -> flat.

Reuses the journal readers and outcome classification already trusted by the
proof/baseline tooling (``ops.proof_30_mnq``) instead of re-parsing journals
with new logic. This module only reads ``journal_*.jsonl`` files (and, for
context, an evidence-lane state file if present); it never repairs a
journal, cancels an order, flattens a position, or contacts a broker.

Where a check needs data this repo cannot safely obtain read-only (live
broker position/order state), the result is explicitly marked
``NOT_VERIFIED`` rather than assumed to pass.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ops.proof_30_mnq import classify_outcome, read_journal_entries

DECISION_TYPES = {"TRADE", "NO_TRADE", "RISK_REJECTED", "CONFIG_BLOCKED", "DONE_FOR_DAY", "WAIT"}
FILLED_CATEGORIES = {"filled_win_loss", "breakeven"}
NO_FILL_CATEGORIES = {"cancelled_nofill"}
NEEDS_REVIEW_CATEGORIES = {"reconciler_touched", "other"}


def _pair_trades_to_followups(entries: list[dict[str, Any]]) -> tuple[dict[int, dict], dict[int, dict]]:
    """Greedily pair each TRADE with its nearest later same-instrument OUTCOME
    and ORDER_IDS row. Mirrors ``scripts/session_audit.py``'s pairing, kept
    local here so this module has no import-time dependency on a script."""
    trades = [e for e in entries if e.get("decision") == "TRADE"]
    outcomes = [e for e in entries if e.get("type") == "OUTCOME"]
    order_ids_rows = [e for e in entries if e.get("type") == "ORDER_IDS"]

    def pair(events: list[dict[str, Any]]) -> dict[int, dict]:
        used: set[int] = set()
        paired: dict[int, dict] = {}
        for t in sorted(trades, key=lambda e: e.get("ts") or ""):
            best = None
            for ev in events:
                if id(ev) in used:
                    continue
                if ev.get("instrument") != t.get("instrument"):
                    continue
                if (ev.get("ts") or "") < (t.get("ts") or ""):
                    continue
                if best is None or (ev["ts"] or "") < (best["ts"] or ""):
                    best = ev
            if best is not None:
                used.add(id(best))
                paired[id(t)] = best
        return paired

    return pair(outcomes), pair(order_ids_rows)


def _row_id(entry: dict[str, Any]) -> str:
    return f"{entry.get('_path')}:{entry.get('_line')}"


def _order_identity(order_ids: dict[str, Any] | None) -> tuple | None:
    if not isinstance(order_ids, dict) or not order_ids:
        return None
    return tuple(sorted((str(k), str(v)) for k, v in order_ids.items() if k != "instrument"))


def build_signal_decision_section(entries: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = [e for e in entries if e.get("decision") in DECISION_TYPES]
    missing_reason = []
    for e in decisions:
        decision = e.get("decision")
        if decision in ("NO_TRADE", "RISK_REJECTED", "CONFIG_BLOCKED"):
            has_reason = bool(e.get("reason") or e.get("failed_gates") or e.get("config_block"))
            if not has_reason:
                missing_reason.append(_row_id(e))
    missing_attribution = []
    for e in decisions:
        if e.get("decision") != "TRADE":
            continue
        setup = e.get("setup") or {}
        if not (setup.get("strategy") and e.get("instrument") and setup.get("direction")):
            missing_attribution.append(_row_id(e))
    return {
        "total_decisions": len(decisions),
        "by_decision_type": dict(Counter(e.get("decision") for e in decisions)),
        "rejected_without_reason": missing_reason,
        "trades_missing_attribution": missing_attribution,
    }


def build_order_section(
    trades: list[dict[str, Any]],
    outcome_by_trade: dict[int, dict],
    order_ids_by_trade: dict[int, dict],
) -> dict[str, Any]:
    identities: dict[tuple, list[str]] = defaultdict(list)
    no_order_ids: list[str] = []
    unresolved: list[dict[str, Any]] = []
    resolved_labels = Counter()

    for t in trades:
        oid_row = order_ids_by_trade.get(id(t))
        identity = _order_identity((oid_row or {}).get("order_ids"))
        if identity is None:
            no_order_ids.append(_row_id(t))
        else:
            identities[identity].append(_row_id(t))

        outcome_row = outcome_by_trade.get(id(t))
        if outcome_row is None:
            unresolved.append(t)
        else:
            category = classify_outcome(outcome_row.get("outcome") or {})
            resolved_labels[category] += 1

    duplicate_identities = {k: v for k, v in identities.items() if len(v) > 1}
    return {
        "order_attempts": len(trades),
        "no_order_ids_logged": no_order_ids,
        "duplicate_order_identities": [
            {"order_ids": dict(k), "rows": v} for k, v in duplicate_identities.items()
        ],
        "unresolved_trades": unresolved,
        "resolved_by_category": dict(resolved_labels),
    }


def _lane_state_paths(log_dir: Path) -> list[Path]:
    from execution.mes_trend_consolidation_break_evidence import state_path as mes_state_path
    from execution.mnq_strat_evidence import LANES, state_path as mnq_state_path

    return [mes_state_path(log_dir), *(mnq_state_path(log_dir, lane) for lane in LANES)]


def _any_open_position(log_dir: Path, instrument: str) -> bool:
    import json

    for path in _lane_state_paths(log_dir):
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(state, dict):
            continue
        position = state.get("position")
        if isinstance(position, dict) and str(position.get("instrument") or "").upper() == instrument.upper():
            return True
    return False


def classify_unresolved(
    unresolved_trades: list[dict[str, Any]], log_dir: Path | None
) -> tuple[list[str], list[str]]:
    """Split unresolved (no OUTCOME yet) trades into legitimate-open vs orphan
    rows, using the same per-lane ``state.json`` the live engine itself reads.

    Best-effort only: this matches on instrument, not on the specific trade,
    so it cannot prove *which* open trade a position belongs to when more
    than one is unresolved on the same instrument -- it can only say whether
    an open position exists at all. ``log_dir=None`` (state files
    unavailable) reports every unresolved trade as an orphan rather than
    guessing.
    """
    if log_dir is None:
        return [], [_row_id(t) for t in unresolved_trades]
    legitimate, orphan = [], []
    for t in unresolved_trades:
        instrument = str(t.get("instrument") or "")
        if instrument and _any_open_position(log_dir, instrument):
            legitimate.append(_row_id(t))
        else:
            orphan.append(_row_id(t))
    return legitimate, orphan


def build_fill_section(trades: list[dict[str, Any]], outcome_by_trade: dict[int, dict]) -> dict[str, Any]:
    incomplete_brackets = []
    for t in trades:
        setup = t.get("setup") or {}
        if setup.get("entry") is None or setup.get("stop") is None or setup.get("target") is None:
            incomplete_brackets.append(_row_id(t))
    return {
        "trades_with_incomplete_bracket": incomplete_brackets,
        "entry_execution_model_recorded": "NOT_VERIFIED — journal TRADE/OUTCOME rows do not carry an "
        "entry-execution-model field; cross-check ops.session_snapshot.entry_execution_mode() "
        "for the code/env default separately",
        "effective_tolerance_recorded": "NOT_VERIFIED — see ops.session_snapshot.entry_tolerance_by_instrument() "
        "for the code/env default; journal rows do not log the tolerance used per-fill",
    }


def build_protection_section(trades: list[dict[str, Any]], order_ids_by_trade: dict[int, dict]) -> dict[str, Any]:
    filled_without_order_ids = [
        _row_id(t) for t in trades if id(t) not in order_ids_by_trade
    ]
    return {
        "filled_trades_without_logged_order_ids": filled_without_order_ids,
        "note": "a filled trade with no ORDER_IDS row has no recorded proof a protective "
        "bracket was ever submitted for it -- flagged, not assumed naked or assumed protected",
        "stale_child_orders": "NOT_VERIFIED — requires a live broker order-list read, out of "
        "scope for a journal-only, read-only audit",
    }


def build_accounting_section(
    order_section: dict[str, Any], legitimate_open: list[str], orphan: list[str]
) -> dict[str, Any]:
    attempts = order_section["order_attempts"]
    resolved = order_section["resolved_by_category"]
    fills = resolved.get("filled_win_loss", 0) + resolved.get("breakeven", 0)
    cancellations = resolved.get("cancelled_nofill", 0)
    needs_review = sum(resolved.get(c, 0) for c in NEEDS_REVIEW_CATEGORIES)
    opens = len(legitimate_open)
    orphans = len(orphan)
    identity_ok = attempts == (fills + cancellations + needs_review + opens + orphans)
    return {
        "attempts": attempts,
        "fills": fills,
        "cancellations": cancellations,
        "needs_broker_review": needs_review,
        "legitimately_open": opens,
        "orphans": orphans,
        "identity_attempts_eq_fills_plus_cancellations_plus_rejects_plus_opens": identity_ok,
    }


def audit_trade_chain(
    journal_dir: str | Path,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return a full trade-chain integrity report over the given journal window.

    ``state_dir`` defaults to ``journal_dir`` (production layout: lane
    ``state.json`` files live alongside ``journal_*.jsonl`` in ``logs/``).
    Pass ``state_dir=None`` explicitly only if state files are known to be
    unavailable; every unresolved trade is then reported as an orphan rather
    than assumed open.
    """
    journal_dir = Path(journal_dir)
    entries = read_journal_entries(journal_dir)
    if from_date or to_date:
        entries = [
            e for e in entries
            if not (from_date and str(e.get("ts") or "") < from_date)
            and not (to_date and str(e.get("ts") or "") > to_date + "~")
        ]
    read_errors = [e for e in entries if e.get("type") == "READ_ERROR"]
    trades = [e for e in entries if e.get("decision") == "TRADE"]
    outcome_by_trade, order_ids_by_trade = _pair_trades_to_followups(entries)

    signal_decision = build_signal_decision_section(entries)
    order = build_order_section(trades, outcome_by_trade, order_ids_by_trade)
    fill = build_fill_section(trades, outcome_by_trade)
    protection = build_protection_section(trades, order_ids_by_trade)

    effective_state_dir = journal_dir if state_dir is None else Path(state_dir) if state_dir else None
    legitimate_open, orphan = classify_unresolved(order["unresolved_trades"], effective_state_dir)
    accounting = build_accounting_section(order, legitimate_open, orphan)

    problems = []
    if read_errors:
        problems.append(f"{len(read_errors)} corrupt/unparseable journal row(s)")
    if signal_decision["rejected_without_reason"]:
        problems.append(f"{len(signal_decision['rejected_without_reason'])} rejected decision(s) missing a reason")
    if order["duplicate_order_identities"]:
        problems.append(f"{len(order['duplicate_order_identities'])} duplicate order identity group(s)")
    if fill["trades_with_incomplete_bracket"]:
        problems.append(f"{len(fill['trades_with_incomplete_bracket'])} trade(s) with an incomplete stop/target bracket")
    if orphan:
        problems.append(f"{len(orphan)} orphan fill(s) with no OUTCOME and no matching open lane state")
    if not accounting["identity_attempts_eq_fills_plus_cancellations_plus_rejects_plus_opens"]:
        problems.append("accounting identity mismatch: attempts != fills + cancellations + rejects/no-fills + opens")

    return {
        "read_only": True,
        "journal_dir": str(journal_dir),
        "filters": {"from_date": from_date, "to_date": to_date},
        "journal_read_errors": len(read_errors),
        "signal_decision": signal_decision,
        "order": {k: v for k, v in order.items() if k != "unresolved_trades"},
        "fill": fill,
        "protection": protection,
        "accounting": accounting,
        "orphans_pending_outcome": orphan,
        "legitimately_open": legitimate_open,
        "duplicate_order_identities": order["duplicate_order_identities"],
        "problems": problems,
        "pass": not problems,
    }


def format_compact(report: dict[str, Any]) -> str:
    """Render the "PASS" compact form the spec requires when nothing is broken;
    expand automatically the moment ``report["pass"]`` is False."""
    acc = report["accounting"]
    if report["pass"]:
        return (
            "TRADE CHAIN: PASS\n"
            f"{acc['attempts']} attempts\n"
            f"{acc['fills']} fills\n"
            f"{acc['cancellations']} no-fills\n"
            f"{acc['fills']} resolved\n"
            f"{acc['legitimately_open']} legitimate opens\n"
            f"{acc['orphans']} orphans\n"
            "0 duplicate identities\n"
            "stale orders: NOT_VERIFIED (no live broker order-list read; journal-only audit)\n"
            "broker/journal parity: NOT_VERIFIED (no live broker read; journal-internal checks only)"
        )
    lines = ["TRADE CHAIN: FAIL", f"{len(report['problems'])} problem(s):"]
    lines.extend(f"  - {p}" for p in report["problems"])
    if report["orphans_pending_outcome"]:
        lines.append(f"orphans/unresolved: {report['orphans_pending_outcome']}")
    if report["duplicate_order_identities"]:
        lines.append(f"duplicate order identities: {report['duplicate_order_identities']}")
    return "\n".join(lines)
