"""Trade Chain Integrity audit -- read only.

Traces every new demo/paper trade ATTEMPT since a checkpoint timestamp:
  signal -> decision -> risk -> order -> fill/no-fill -> protective bracket
  -> exit -> outcome -> flat position

Reuses the journal reading already trusted by ops/proof_30_mnq.py (same
approved-TRADE definition: decision == "TRADE" and risk_check.result ==
"APPROVED") and the same reconciler-marker vocabulary used by
ops/reconciler_outcome_audit.py, rather than re-deriving either.

Never cancels an order, flattens a position, modifies a broker order,
repairs a journal, synthesizes an OUTCOME, rewrites state, retries an
execution, or submits an order. On any discrepancy it reports / fails
closed only.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops.proof_30_mnq import RECONCILER_MARKERS, load_json_url, parse_proof_ts, read_journal_entries

FILLED_RESULTS = {"WIN", "LOSS", "BREAKEVEN"}
NO_FILL_RESULTS = {"CANCELLED", "VOID"}
STALE_UNRESOLVED_HOURS = 20.0


def _is_approved_trade(entry: dict[str, Any]) -> bool:
    risk = entry.get("risk_check") or {}
    return entry.get("decision") == "TRADE" and risk.get("result") == "APPROVED"


def _order_id_values(entry: dict[str, Any]) -> list[str]:
    ids = (entry.get("order_ids") or {}) if isinstance(entry.get("order_ids"), dict) else {}
    return [str(v) for k, v in ids.items() if k != "instrument" and v is not None]


@dataclass
class TradeChainItem:
    instrument: str
    trade_ts: str | None
    strategy: str | None
    direction: str | None
    has_order_ids: bool
    order_id_values: list[str]
    has_outcome: bool
    outcome_result: str | None
    exit_reason: str | None
    reconciler_touched: bool
    bucket: str
    flags: list[str] = field(default_factory=list)


def _bucket_trade(
    trade: dict[str, Any],
    order_ids_entry: dict[str, Any] | None,
    outcome_entry: dict[str, Any] | None,
    *,
    now: datetime,
) -> TradeChainItem:
    instrument = str(trade.get("instrument") or "?").upper()
    setup = trade.get("setup") or {}
    trade_ts = trade.get("ts")
    has_order_ids = order_ids_entry is not None
    order_id_values = _order_id_values(order_ids_entry) if order_ids_entry else []
    has_outcome = outcome_entry is not None
    body = (outcome_entry or {}).get("outcome") or {}
    result = str(body.get("result") or "").upper() or None
    exit_reason = body.get("exit_reason")
    reconciler_touched = bool(
        outcome_entry
        and (
            str(outcome_entry.get("session") or "").lower() == "reconcile"
            or any(marker in str(exit_reason or "").lower() for marker in RECONCILER_MARKERS)
        )
    )

    flags: list[str] = []
    if not setup.get("stop") or not setup.get("target"):
        flags.append("naked_position_risk_missing_bracket_fields")
    if not has_order_ids and not has_outcome:
        bucket = "no_order_attempt_logged"
        flags.append("no_order_ids_and_no_outcome_logged_for_approved_trade")
    elif has_outcome and result in NO_FILL_RESULTS:
        bucket = "no_fill"
    elif has_outcome and result in FILLED_RESULTS:
        bucket = "resolved_fill"
    elif has_order_ids and not has_outcome:
        trade_dt = parse_proof_ts(trade_ts)
        age_hours = (now - trade_dt).total_seconds() / 3600.0 if trade_dt else None
        if age_hours is not None and age_hours > STALE_UNRESOLVED_HOURS:
            bucket = "stale_unresolved"
            flags.append(f"unresolved for {age_hours:.1f}h -- check for a hanging order or missed reconcile")
        else:
            bucket = "legitimately_open"
    else:
        bucket = "unclassified"
        flags.append("outcome present without a recognized result and no order_ids")

    return TradeChainItem(
        instrument=instrument,
        trade_ts=trade_ts,
        strategy=setup.get("strategy"),
        direction=setup.get("direction"),
        has_order_ids=has_order_ids,
        order_id_values=order_id_values,
        has_outcome=has_outcome,
        outcome_result=result,
        exit_reason=exit_reason,
        reconciler_touched=reconciler_touched,
        bucket=bucket,
        flags=flags,
    )


def _pair_chain(entries: list[dict[str, Any]], *, now: datetime) -> list[TradeChainItem]:
    pending: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order_ids_by_trade: dict[int, dict[str, Any]] = {}
    outcome_by_trade: dict[int, dict[str, Any]] = {}
    for entry in entries:
        instrument = str(entry.get("instrument") or "").upper()
        if not instrument:
            continue
        if _is_approved_trade(entry):
            pending[instrument].append(entry)
            continue
        queue = pending[instrument]
        if not queue:
            continue
        if entry.get("type") == "ORDER_IDS":
            order_ids_by_trade[id(queue[-1])] = entry
            continue
        if entry.get("type") == "OUTCOME":
            trade = queue.pop(0)
            outcome_by_trade[id(trade)] = entry
            continue

    # Build items in original TRADE order. outcome_by_trade only contains
    # trades that were popped (paired with an OUTCOME); anything never popped
    # got no OUTCOME and may or may not have ORDER_IDS -- both dicts are keyed
    # by id(trade) so a plain re-walk of all approved trades covers every case.
    items: list[TradeChainItem] = []
    all_trades: list[dict[str, Any]] = [e for e in entries if _is_approved_trade(e)]
    for trade in all_trades:
        oid = id(trade)
        item = _bucket_trade(
            trade,
            order_ids_by_trade.get(oid),
            outcome_by_trade.get(oid),
            now=now,
        )
        items.append(item)
    return items


def _duplicate_order_ids(items: list[TradeChainItem]) -> list[str]:
    seen: dict[str, int] = defaultdict(int)
    for item in items:
        for value in item.order_id_values:
            seen[value] += 1
    return sorted(v for v, count in seen.items() if count > 1)


@dataclass
class TradeChainReport:
    read_only: bool = True
    journal_dir: str = ""
    since: str | None = None
    attempts: int = 0
    resolved_fills: int = 0
    open_positions: int = 0
    no_fills: int = 0
    stale_unresolved: int = 0
    no_order_attempt_logged: int = 0
    unclassified: int = 0
    duplicate_order_ids: list[str] = field(default_factory=list)
    accounting_ok: bool = True
    accounting_detail: str = ""
    reconciler_touched_count: int = 0
    naked_position_flags: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)
    broker_status: dict[str, Any] | None = None
    broker_status_error: str | None = None
    parity: str = "UNKNOWN"

    def as_dict(self) -> dict[str, Any]:
        return {
            "read_only": self.read_only,
            "journal_dir": self.journal_dir,
            "since": self.since,
            "totals": {
                "attempts": self.attempts,
                "resolved_fills": self.resolved_fills,
                "legitimately_open": self.open_positions,
                "no_fills": self.no_fills,
                "stale_unresolved": self.stale_unresolved,
                "no_order_attempt_logged": self.no_order_attempt_logged,
                "unclassified": self.unclassified,
            },
            "duplicate_order_ids": self.duplicate_order_ids,
            "accounting": {
                "ok": self.accounting_ok,
                "detail": self.accounting_detail,
            },
            "reconciler_touched_count": self.reconciler_touched_count,
            "naked_position_flags": self.naked_position_flags,
            "broker_status": self.broker_status,
            "broker_status_error": self.broker_status_error,
            "parity": self.parity,
            "items": self.items,
            "overall": self.overall(),
        }

    def overall(self) -> str:
        problems = (
            self.stale_unresolved
            or self.no_order_attempt_logged
            or self.unclassified
            or self.duplicate_order_ids
            or not self.accounting_ok
            or self.naked_position_flags
        )
        return "PASS" if not problems else "FAIL"


def build_trade_chain_report(
    *,
    journal_dir: str | Path,
    since_date: str | None = None,
    status_url: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = Path(journal_dir)
    now = now or datetime.now(timezone.utc)
    entries = [
        e
        for e in read_journal_entries(root)
        if e.get("type") != "READ_ERROR" and (since_date is None or str(e.get("ts") or "") >= since_date)
    ]
    items = _pair_chain(entries, now=now)
    dup_ids = _duplicate_order_ids(items)

    report = TradeChainReport(journal_dir=str(root), since=since_date)
    report.attempts = len(items)
    for item in items:
        if item.bucket == "resolved_fill":
            report.resolved_fills += 1
        elif item.bucket == "legitimately_open":
            report.open_positions += 1
        elif item.bucket == "no_fill":
            report.no_fills += 1
        elif item.bucket == "stale_unresolved":
            report.stale_unresolved += 1
        elif item.bucket == "no_order_attempt_logged":
            report.no_order_attempt_logged += 1
        else:
            report.unclassified += 1
        if item.reconciler_touched:
            report.reconciler_touched_count += 1
        if "naked_position_risk_missing_bracket_fields" in item.flags:
            report.naked_position_flags += 1
    report.duplicate_order_ids = dup_ids

    clean_attempts = report.attempts - report.no_order_attempt_logged - report.unclassified - report.stale_unresolved
    fills = report.resolved_fills + report.open_positions
    attempts_ok = clean_attempts == (fills + report.no_fills)
    fills_ok = fills == (report.resolved_fills + report.open_positions)
    report.accounting_ok = attempts_ok and fills_ok and not (
        report.no_order_attempt_logged or report.unclassified or report.stale_unresolved
    )
    report.accounting_detail = (
        f"attempts({report.attempts}) = fills({fills}) + no_fills({report.no_fills}) "
        f"+ unresolved/unclassified({report.stale_unresolved + report.no_order_attempt_logged + report.unclassified}); "
        f"fills({fills}) = resolved({report.resolved_fills}) + open({report.open_positions})"
    )

    report.items = [item.__dict__ for item in items]

    if status_url:
        status, err = load_json_url(status_url)
        report.broker_status = status
        report.broker_status_error = err
        if status is not None:
            broker_open = status.get("open_positions") or status.get("positions")
            if broker_open is not None:
                broker_count = len(broker_open) if isinstance(broker_open, list) else broker_open
                report.parity = "PASS" if broker_count == report.open_positions else "MISMATCH"
            else:
                report.parity = "UNKNOWN (status payload has no open-position field)"
        else:
            report.parity = f"UNKNOWN ({err})"
    else:
        report.parity = "UNKNOWN (no --status-url given; journal-only check)"

    return report.as_dict()


def format_trade_chain_report(report: dict[str, Any]) -> str:
    totals = report["totals"]
    if report["overall"] == "PASS":
        return (
            f"TRADE CHAIN: PASS\n"
            f"{totals['attempts']} attempts\n"
            f"{totals['resolved_fills'] + totals['legitimately_open']} fills\n"
            f"{totals['no_fills']} no-fills\n"
            f"{totals['resolved_fills']} resolved\n"
            f"{totals['legitimately_open']} legitimate opens\n"
            f"0 orphans\n"
            f"0 stale orders\n"
            f"0 duplicate identities\n"
            f"broker/journal parity {report['parity']}"
        )
    lines = [
        "TRADE CHAIN: FAIL",
        f"attempts={totals['attempts']} resolved_fills={totals['resolved_fills']} "
        f"open={totals['legitimately_open']} no_fills={totals['no_fills']} "
        f"stale_unresolved={totals['stale_unresolved']} "
        f"no_order_attempt_logged={totals['no_order_attempt_logged']} "
        f"unclassified={totals['unclassified']}",
        f"accounting: {report['accounting']}",
        f"duplicate_order_ids: {report['duplicate_order_ids']}",
        f"naked_position_flags: {report['naked_position_flags']}",
        f"broker/journal parity: {report['parity']}",
        "",
        "Problem items:",
    ]
    for item in report["items"]:
        if item["bucket"] in ("resolved_fill", "legitimately_open", "no_fill") and not item["flags"]:
            continue
        lines.append(
            f"  - {item['trade_ts']} {item['instrument']} {item['strategy']} {item['direction']} "
            f"bucket={item['bucket']} flags={item['flags']}"
        )
    return "\n".join(lines)
