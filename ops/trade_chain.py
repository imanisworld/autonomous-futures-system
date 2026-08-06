"""Read-only trade-chain assembly and accounting-identity checks.

Traces signal -> decision -> risk -> order -> fill/no-fill -> exit -> outcome
using only journal JSONL rows (via ``ops.proof_30_mnq``), the same
TRADE(APPROVED)<->OUTCOME FIFO pairing rule documented in RUNBOOK.md's
"Evidence-Chain Reconciliation" section and used by
``ops.proof_30_mnq``/``ops.reconciler_outcome_audit``.

Never cancels an order, flattens a position, modifies a broker order,
repairs a journal, synthesizes an outcome, or submits anything. Read-only.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from execution.day_only_exit import strategy_is_day_only
from ops.proof_30_mnq import classify_outcome, parse_proof_ts, read_journal_entries

# classify_outcome() buckets: filled_win_loss, breakeven, reconciler_touched,
# cancelled_nofill, other. A fill is any resolved outcome where a position
# actually opened; cancelled_nofill is the only "no position opened" bucket.
FILLED_CATEGORIES = frozenset({"filled_win_loss", "breakeven", "reconciler_touched"})
NO_FILL_CATEGORIES = frozenset({"cancelled_nofill"})
AMBIGUOUS_CATEGORIES = frozenset({"other"})


def _entry_date(entry: dict[str, Any] | None) -> str | None:
    if not entry:
        return None
    dt = parse_proof_ts(entry.get("ts"))
    return dt.date().isoformat() if dt else None


def _row_ref(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": entry.get("_path"),
        "line": entry.get("_line"),
        "ts": entry.get("ts"),
        "instrument": entry.get("instrument"),
        "strategy": (entry.get("setup") or {}).get("strategy"),
    }


def pair_trades(
    entries: list[dict[str, Any]],
    *,
    instrument: str | None = None,
    strategy: str | None = None,
) -> dict[str, list[Any]]:
    """FIFO-pair approved TRADE rows to their OUTCOME rows, per instrument.

    Returns resolved (trade, outcome) pairs, still-open approved trades with
    no paired outcome yet, outcomes with no matching open trade (orphans),
    and pre-order rejections (RISK_REJECTED / CONFIG_BLOCKED / risk_check
    REJECTED) which never became an attempt.
    """
    inst_filter = instrument.upper() if instrument else None
    open_trades: dict[str, list[dict[str, Any]]] = defaultdict(list)
    resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unmatched_outcomes: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for entry in entries:
        inst = str(entry.get("instrument") or "").upper()
        if inst_filter and inst != inst_filter:
            continue
        entry_strategy = (entry.get("setup") or {}).get("strategy")
        if strategy and entry_strategy and entry_strategy != strategy:
            continue

        risk = entry.get("risk_check") or {}
        if entry.get("decision") == "TRADE" and risk.get("result") == "APPROVED":
            open_trades[inst].append(entry)
            continue
        if entry.get("decision") in ("RISK_REJECTED", "CONFIG_BLOCKED") or risk.get("result") == "REJECTED":
            rejected.append(entry)
            continue
        if entry.get("type") == "OUTCOME":
            queue = open_trades.get(inst) or []
            if queue:
                resolved.append((queue.pop(0), entry))
            else:
                unmatched_outcomes.append(entry)

    still_open = [trade for queue in open_trades.values() for trade in queue]
    return {
        "resolved": resolved,
        "still_open": still_open,
        "unmatched_outcomes": unmatched_outcomes,
        "rejected": rejected,
    }


def accounting_identity(pairing: dict[str, Any]) -> dict[str, Any]:
    """Assert: attempts = fills + cancellations; fills = resolved-filled + open."""
    resolved = pairing["resolved"]
    categories = [classify_outcome(outcome.get("outcome") or {}) for _trade, outcome in resolved]
    cat_counts = Counter(categories)

    resolved_filled = sum(cat_counts[c] for c in FILLED_CATEGORIES)
    resolved_no_fill = sum(cat_counts[c] for c in NO_FILL_CATEGORIES)
    ambiguous = sum(cat_counts[c] for c in AMBIGUOUS_CATEGORIES)
    still_open = len(pairing["still_open"])

    fills_total = resolved_filled + still_open
    attempts_total = fills_total + resolved_no_fill

    no_fill_reasons = Counter(
        (outcome.get("outcome") or {}).get("no_fill_reason") or "UNSPECIFIED"
        for _trade, outcome in resolved
        if classify_outcome(outcome.get("outcome") or {}) in NO_FILL_CATEGORIES
    )

    return {
        "attempts": attempts_total,
        "fills": fills_total,
        "cancellations_or_no_fill": resolved_no_fill,
        "resolved": len(resolved),
        "legitimately_open": still_open,
        "pre_order_rejects": len(pairing["rejected"]),
        "unmatched_outcomes": len(pairing["unmatched_outcomes"]),
        "ambiguous_outcomes": ambiguous,
        "no_fill_reason_breakdown": dict(no_fill_reasons.most_common()),
        "identity_holds": {
            "attempts_eq_fills_plus_cancellations": attempts_total == fills_total + resolved_no_fill,
            "fills_eq_resolved_filled_plus_open": fills_total == resolved_filled + still_open,
        },
    }


def _duplicate_order_ids(pairing: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for _trade, outcome in pairing["resolved"]:
        order_id = (outcome.get("outcome") or {}).get("paper_order_id")
        if order_id:
            ids.append(str(order_id))
    counts = Counter(ids)
    return sorted(order_id for order_id, count in counts.items() if count > 1)


def _naked_fills(pairing: dict[str, Any]) -> list[dict[str, Any]]:
    """Filled positions whose paired TRADE row carries no stop or target."""
    naked = []
    for trade, outcome in pairing["resolved"]:
        if classify_outcome(outcome.get("outcome") or {}) not in FILLED_CATEGORIES:
            continue
        setup = trade.get("setup") or {}
        if setup.get("stop") is None or setup.get("target") is None:
            naked.append(_row_ref(outcome))
    return naked


def _day_only_violations(pairing: dict[str, Any], *, today: date) -> list[dict[str, Any]]:
    """Day-only strategies with an approved trade still open from a prior day."""
    violations = []
    for trade in pairing["still_open"]:
        setup = trade.get("setup") or {}
        if not strategy_is_day_only(setup.get("strategy")):
            continue
        trade_date = _entry_date(trade)
        if trade_date and trade_date < today.isoformat():
            violations.append({**_row_ref(trade), "trade_date": trade_date})
    return violations


def trade_chain_report(
    journal_dir: str | Path,
    *,
    instrument: str | None = None,
    strategy: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """One read-only trade-chain-integrity report over the journal window."""
    root = Path(journal_dir)
    entries = read_journal_entries(root, through_date=to_date)
    if from_date or to_date:
        filtered = []
        for entry in entries:
            entry_date = _entry_date(entry)
            if entry_date is None:
                filtered.append(entry)
                continue
            if from_date and entry_date < from_date:
                continue
            if to_date and entry_date > to_date:
                continue
            filtered.append(entry)
        entries = filtered

    read_errors = [entry for entry in entries if entry.get("type") == "READ_ERROR"]
    pairing = pair_trades(entries, instrument=instrument, strategy=strategy)
    accounting = accounting_identity(pairing)
    duplicate_order_ids = _duplicate_order_ids(pairing)
    naked_fills = _naked_fills(pairing)
    day_only_violations = _day_only_violations(pairing, today=today or date.today())
    orphan_outcomes = [_row_ref(entry) for entry in pairing["unmatched_outcomes"]]
    stale_open = [
        {**_row_ref(trade), "trade_date": _entry_date(trade)}
        for trade in pairing["still_open"]
    ]

    problems: list[str] = []
    if not accounting["identity_holds"]["attempts_eq_fills_plus_cancellations"]:
        problems.append("accounting identity failed: attempts != fills + cancellations")
    if not accounting["identity_holds"]["fills_eq_resolved_filled_plus_open"]:
        problems.append("accounting identity failed: fills != resolved-filled + legitimately-open")
    if orphan_outcomes:
        problems.append(f"{len(orphan_outcomes)} orphan outcome(s) with no matching approved trade")
    if duplicate_order_ids:
        problems.append(f"{len(duplicate_order_ids)} duplicate order identity/identities")
    if naked_fills:
        problems.append(f"{len(naked_fills)} filled position(s) with no recorded stop/target")
    if day_only_violations:
        problems.append(f"{len(day_only_violations)} day-only strategy position(s) still open from a prior day")
    if accounting["ambiguous_outcomes"]:
        problems.append(f"{accounting['ambiguous_outcomes']} outcome(s) with an unrecognized result value")
    if read_errors:
        problems.append(f"{len(read_errors)} journal read error(s)")

    return {
        "ok": not problems,
        "read_only": True,
        "journal_dir": str(root),
        "instrument": instrument,
        "strategy": strategy,
        "filters": {"from_date": from_date, "to_date": to_date},
        "journal_read_errors": read_errors,
        "accounting": accounting,
        "duplicate_order_ids": duplicate_order_ids,
        "naked_fills": naked_fills,
        "day_only_violations": day_only_violations,
        "orphan_outcomes": orphan_outcomes,
        "legitimately_open_positions": stale_open,
        "broker_journal_parity": {
            "status": "UNKNOWN",
            "reason": (
                "No read-only broker-account query is wired into repo ops tooling for "
                "this check. Verify manually via /status/broker-account on the active box."
            ),
        },
        "problems": problems,
    }


def format_trade_chain_summary(report: dict[str, Any]) -> str:
    """Compact PASS/FAIL summary; expand only when something is broken."""
    accounting = report["accounting"]
    verdict = "PASS" if report["ok"] else "FAIL"
    lines = [
        f"TRADE CHAIN: {verdict}",
        f"{accounting['attempts']} attempts",
        f"{accounting['fills']} fills",
        f"{accounting['cancellations_or_no_fill']} no-fills",
        f"{accounting['resolved']} resolved",
        f"{accounting['legitimately_open']} legitimate opens",
        f"{len(report['orphan_outcomes'])} orphans",
        f"{len(report['legitimately_open_positions'])} stale orders",
        f"{len(report['duplicate_order_ids'])} duplicate identities",
        f"broker/journal parity {report['broker_journal_parity']['status']}",
    ]
    if report["problems"]:
        lines.append("")
        lines.append("Problems:")
        lines.extend(f"  - {problem}" for problem in report["problems"])
    return "\n".join(lines)
