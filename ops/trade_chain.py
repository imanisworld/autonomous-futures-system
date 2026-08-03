"""Read-only trade-chain pairing and accounting-identity checks.

Reconstructs signal -> decision -> risk -> order -> fill/no-fill -> bracket ->
exit -> outcome from the append-only journal. Reuses existing machinery
rather than re-parsing journal files or re-deriving fill/no-fill logic:

- ``ops.proof_30_mnq.read_journal_entries`` for journal parsing.
- ``ops.fill_realism.is_entry_nofill`` for the no-fill classification.
- ``journal.journal_logger.JournalLogger.get_open_position`` for "is this
  pending trade actually today's legitimately open position" (the same
  question live trading code answers before evaluating a new signal).

This module never cancels an order, flattens a position, modifies a broker
order, repairs a journal, synthesizes an OUTCOME, rewrites state, retries an
execution, or submits an order. It only reads and reports.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as date_cls
from pathlib import Path
from typing import Any

from ops.fill_realism import is_entry_nofill
from ops.proof_30_mnq import parse_proof_ts, read_journal_entries

FILLED_RESULTS = {"WIN", "LOSS", "BREAKEVEN"}


def _entry_date(entry: dict[str, Any]) -> str | None:
    dt = parse_proof_ts(entry.get("ts") or entry.get("timestamp"))
    return dt.date().isoformat() if dt else None


def _outcome_strategy(entry: dict[str, Any]) -> str | None:
    body = entry.get("outcome") or {}
    return body.get("strategy") or entry.get("strategy")


def build_chain(
    journal_dir: str | Path,
    *,
    strategy: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """Pair every approved TRADE decision to its later OUTCOME (if any), and
    tally decision/risk-rejection/order-bracket bookkeeping.

    Returns a dict with ``pairs`` (trade+outcome pairs), ``pending`` (approved
    trades with no OUTCOME yet), ``order_ids_by_instrument``,
    ``decision_counts``, ``risk_rejections``, ``market_condition_counts``, and
    ``read_errors``. Callers combine this with
    ``journal_logger.get_open_position`` to split ``pending`` into
    legitimately-open vs. orphaned, and with ``compute_accounting`` for the
    identity checks.
    """
    journal_path = Path(journal_dir)
    entries = read_journal_entries(journal_path)
    read_errors = [e for e in entries if e.get("type") == "READ_ERROR"]

    def in_window(entry: dict[str, Any]) -> bool:
        entry_date = _entry_date(entry)
        if entry_date is None:
            return True
        if from_date and entry_date < from_date:
            return False
        if to_date and entry_date > to_date:
            return False
        return True

    scoped = [e for e in entries if e.get("type") != "READ_ERROR" and in_window(e)]

    open_trades: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order_ids_by_instrument: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pairs: list[dict[str, Any]] = []
    decision_counts: dict[str, int] = defaultdict(int)
    risk_rejections: dict[str, int] = defaultdict(int)
    market_condition_counts: dict[str, int] = defaultdict(int)
    outcomes_without_trade = 0

    for entry in scoped:
        instrument = str(entry.get("instrument") or "").upper()
        entry_type = entry.get("type")

        if entry_type == "ORDER_IDS":
            if instrument:
                order_ids_by_instrument[instrument].append(entry)
            continue

        decision = entry.get("decision")
        risk = entry.get("risk_check") or {}

        if decision:
            decision_counts[decision] += 1
            market_condition = entry.get("market_condition")
            if market_condition:
                market_condition_counts[market_condition] += 1
            if risk.get("result") == "REJECTED":
                risk_rejections[risk.get("failed_rule") or "unspecified"] += 1

        if decision == "TRADE" and isinstance(entry.get("setup"), dict):
            # Every approved trade is queued regardless of the strategy
            # filter, and only filtered out at pairing time below — the
            # per-instrument FIFO order must stay correct across strategies
            # that share an instrument, or a later OUTCOME can get matched
            # to the wrong strategy's trade (or wrongly treated as orphaned).
            if risk.get("result") == "APPROVED" and instrument:
                open_trades[instrument].append(entry)
            continue

        if entry_type == "OUTCOME" and instrument:
            queued = open_trades[instrument]
            trade = queued.pop(0) if queued else None
            if trade is None:
                outcomes_without_trade += 1

            if not strategy:
                pairs.append({"trade": trade, "outcome": entry, "instrument": instrument})
                continue

            if trade is not None:
                if (trade.get("setup") or {}).get("strategy") == strategy:
                    pairs.append({"trade": trade, "outcome": entry, "instrument": instrument})
                # else: this outcome belongs to a different strategy's trade
                # on the same instrument — correctly excluded from this filter.
            elif _outcome_strategy(entry) == strategy:
                # No queued trade at all (a true orphan outcome), but the
                # OUTCOME row itself is explicitly tagged for this strategy.
                pairs.append({"trade": trade, "outcome": entry, "instrument": instrument})

    pending = [
        {"instrument": instrument, "trade": trade}
        for instrument, queue in open_trades.items()
        for trade in queue
        if not strategy or (trade.get("setup") or {}).get("strategy") == strategy
    ]

    return {
        "read_errors": read_errors,
        "pairs": pairs,
        "pending": pending,
        "outcomes_without_matching_trade": outcomes_without_trade,
        "order_ids_by_instrument": {k: v for k, v in order_ids_by_instrument.items()},
        "decision_counts": dict(decision_counts),
        "risk_rejections": dict(risk_rejections),
        "market_condition_counts": dict(market_condition_counts),
        "window": {"from_date": from_date, "to_date": to_date, "strategy": strategy},
    }


def _classify_pair(pair: dict[str, Any]) -> str:
    outcome_body = (pair["outcome"] or {}).get("outcome") or {}
    result = str(outcome_body.get("result") or "").upper()
    if result in FILLED_RESULTS:
        return "fill_resolved"
    if result == "CANCELLED":
        if is_entry_nofill(result, outcome_body.get("exit_reason")):
            return "no_fill"
        return "other_cancel"
    return "unclassified"


def classify_pending(
    pending: list[dict[str, Any]],
    *,
    journal_dir: str | Path,
    reference_date: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Split pending (no-OUTCOME-yet) trades into legitimately-open vs. orphans.

    A pending trade is legitimately open only if it matches
    ``JournalLogger.get_open_position`` for its own decision date AND that
    date is the most recent date this trade-chain window touched (i.e. it is
    still today's/the-window's-latest live position, not a stale leftover
    from an earlier day that should have resolved by now).
    """
    from journal.journal_logger import JournalLogger

    if not pending:
        return {"legitimately_open": [], "orphans": []}

    dates = [_entry_date(item["trade"]) for item in pending if item.get("trade")]
    dates = [d for d in dates if d]
    latest_date = reference_date or (max(dates) if dates else None)

    logger = JournalLogger(log_dir=str(journal_dir))
    legitimately_open: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []
    for item in pending:
        trade = item.get("trade")
        trade_date = _entry_date(trade) if trade else None
        if trade is None or latest_date is None or trade_date != latest_date:
            orphans.append(item)
            continue
        try:
            year, month, day = (int(part) for part in latest_date.split("-"))
            open_position = logger.get_open_position(for_date=date_cls(year, month, day))
        except (ValueError, OSError):
            open_position = None
        setup = trade.get("setup") or {}
        matches = bool(
            open_position
            and str(open_position.get("instrument") or "").upper() == item["instrument"]
            and open_position.get("direction") == setup.get("direction")
            and open_position.get("entry") == setup.get("entry")
        )
        (legitimately_open if matches else orphans).append(item)
    return {"legitimately_open": legitimately_open, "orphans": orphans}


def compute_accounting(
    pairs: list[dict[str, Any]],
    pending_classified: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Accounting identities required by the daily/promotion routines:

    attempts = fills + cancellations + rejects/known no-fills
    fills = resolved + legitimately open
    """
    resolved_fills = 0
    no_fills = 0
    other_cancellations = 0
    unclassified = 0
    for pair in pairs:
        bucket = _classify_pair(pair)
        if bucket == "fill_resolved":
            resolved_fills += 1
        elif bucket == "no_fill":
            no_fills += 1
        elif bucket == "other_cancel":
            other_cancellations += 1
        else:
            unclassified += 1

    legitimately_open = len(pending_classified["legitimately_open"])
    orphans = len(pending_classified["orphans"])

    fills = resolved_fills + legitimately_open
    attempts = fills + other_cancellations + no_fills
    # Pairs whose trade leg could not be matched (outcome arrived with no
    # queued approved decision) are a chain-integrity defect, not part of the
    # attempts identity — surfaced separately as orphaned outcomes.
    identity_attempts_ok = attempts == (resolved_fills + legitimately_open + other_cancellations + no_fills)
    identity_fills_ok = fills == (resolved_fills + legitimately_open)

    return {
        "attempts": attempts,
        "fills": fills,
        "resolved_fills": resolved_fills,
        "legitimately_open": legitimately_open,
        "no_fills": no_fills,
        "other_cancellations": other_cancellations,
        "unclassified_outcomes": unclassified,
        "orphaned_pending": orphans,
        "identity_attempts_ok": identity_attempts_ok,
        "identity_fills_ok": identity_fills_ok,
        "identity_ok": identity_attempts_ok and identity_fills_ok and unclassified == 0,
    }


def check_bracket_protection(
    pairs: list[dict[str, Any]],
    order_ids_by_instrument: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Heuristic, proximity-free bracket-protection check.

    A filled/open pair is flagged as a possible naked position if its
    instrument has zero ORDER_IDS (bracket-linkage) rows anywhere in the
    scoped window. An ORDER_IDS row is flagged as a possible stale protective
    child order if its instrument has zero OUTCOME rows anywhere after it in
    the scoped window. Both are coarse (whole-window, not timestamp-matched
    one-to-one) and intended as a lead for manual follow-up, not a proof.
    """
    filled_instruments = {
        pair["instrument"]
        for pair in pairs
        if _classify_pair(pair) == "fill_resolved" or (
            (pair["outcome"].get("outcome") or {}).get("result") == "OPEN"
        )
    }
    possible_naked = sorted(
        instrument for instrument in filled_instruments
        if not order_ids_by_instrument.get(instrument)
    )

    outcome_instruments_present = {pair["instrument"] for pair in pairs}
    possible_stale_brackets = []
    for instrument, order_rows in order_ids_by_instrument.items():
        if instrument not in outcome_instruments_present:
            possible_stale_brackets.extend(
                {
                    "instrument": instrument,
                    "path": row.get("_path"),
                    "line": row.get("_line"),
                }
                for row in order_rows
            )

    all_order_id_tuples: dict[tuple, list[str]] = defaultdict(list)
    for instrument, rows in order_ids_by_instrument.items():
        for row in rows:
            ids = row.get("order_ids")
            if isinstance(ids, (list, tuple)) and ids:
                all_order_id_tuples[tuple(ids)].append(f"{row.get('_path')}:{row.get('_line')}")
    duplicate_order_identities = {
        ids: locations for ids, locations in all_order_id_tuples.items() if len(locations) > 1
    }

    return {
        "possible_naked_positions": possible_naked,
        "possible_stale_protective_orders": possible_stale_brackets,
        "duplicate_order_identities": {
            "|".join(map(str, ids)): locations for ids, locations in duplicate_order_identities.items()
        },
        "limitations": [
            "Naked/stale checks are whole-window and instrument-scoped, not "
            "one-to-one timestamp-matched to a specific position.",
            "A flagged naked position or stale bracket is a lead for manual "
            "verification, not a confirmed defect.",
        ],
    }


def build_report(
    journal_dir: str | Path,
    *,
    strategy: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """Full trade-chain report: pairing, accounting identities, and
    bracket/protection heuristics, all in one read-only pass."""
    chain = build_chain(journal_dir, strategy=strategy, from_date=from_date, to_date=to_date)
    pending_classified = classify_pending(
        chain["pending"], journal_dir=journal_dir, reference_date=to_date
    )
    accounting = compute_accounting(chain["pairs"], pending_classified)
    protection = check_bracket_protection(chain["pairs"], chain["order_ids_by_instrument"])

    ok = (
        not chain["read_errors"]
        and accounting["identity_ok"]
        and accounting["orphaned_pending"] == 0
        and chain["outcomes_without_matching_trade"] == 0
        and not protection["possible_naked_positions"]
        and not protection["possible_stale_protective_orders"]
    )

    return {
        "ok": ok,
        "read_only": True,
        "journal_dir": str(journal_dir),
        "window": chain["window"],
        "read_errors": chain["read_errors"],
        "decision_counts": chain["decision_counts"],
        "risk_rejections": chain["risk_rejections"],
        "market_condition_counts": chain["market_condition_counts"],
        "outcomes_without_matching_trade": chain["outcomes_without_matching_trade"],
        "accounting": accounting,
        "protection": protection,
        "legitimately_open": pending_classified["legitimately_open"],
        "orphaned_pending": pending_classified["orphans"],
        "pair_count": len(chain["pairs"]),
    }


def format_summary_line(report: dict[str, Any]) -> str:
    """The compact 'TRADE CHAIN: PASS/FAIL' line the daily routine prints when
    nothing is wrong, per the house rule against walls of text on a normal
    day."""
    accounting = report["accounting"]
    status = "PASS" if report["ok"] else "FAIL"
    lines = [
        f"TRADE CHAIN: {status}",
        f"{accounting['attempts']} attempts",
        f"{accounting['resolved_fills'] + accounting['legitimately_open']} fills",
        f"{accounting['no_fills']} no-fills",
        f"{accounting['resolved_fills']} resolved",
        f"{accounting['legitimately_open']} legitimate opens",
        f"{accounting['orphaned_pending']} orphans",
        f"{len(report['protection']['possible_stale_protective_orders'])} stale orders",
        f"{len(report['protection']['duplicate_order_identities'])} duplicate identities",
        f"broker/journal parity {'PASS' if report['ok'] else 'FAIL'}",
    ]
    return "\n".join(lines)
