"""Shared, read-only trade-chain primitives used by the Strategy Promotion Proof
Gate and Daily Reconciliation routines.

Builds on `ops.proof_30_mnq` (journal reading, TRADE<->OUTCOME pairing, outcome
classification) rather than re-implementing it. Everything here only *reads*
journal_*.jsonl files; nothing writes, repairs, cancels, or resolves anything.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ops.proof_30_mnq import ResolvedTrade, classify_outcome, read_journal_entries

FILL_CATEGORIES = {"filled_win_loss", "breakeven"}
NEEDS_MANUAL_CLASSIFICATION = {"reconciler_touched", "other"}


def approved_trade_entries(entries: list[dict[str, Any]], key_fn: Callable[[dict], bool]) -> list[dict[str, Any]]:
    """Journal TRADE rows that reached RiskEngine approval and match `key_fn`."""
    return [
        entry for entry in entries
        if entry.get("decision") == "TRADE"
        and (entry.get("risk_check") or {}).get("result") == "APPROVED"
        and key_fn(entry)
    ]


def pair_trades(
    entries: list[dict[str, Any]],
    key_fn: Callable[[dict], bool],
) -> tuple[list[ResolvedTrade], list[dict[str, Any]], list[dict[str, Any]]]:
    """Greedily pair approved TRADE rows matching `key_fn` with the next OUTCOME
    row for the same instrument, in file order (same rule the runtime/reconciler
    tooling uses — see RUNBOOK.md "Evidence-Chain Reconciliation").

    Returns (resolved_pairs, still_open_trades, unmatched_outcomes).
    """
    open_by_instrument: dict[str, list[dict[str, Any]]] = {}
    resolved: list[ResolvedTrade] = []
    unmatched_outcomes: list[dict[str, Any]] = []

    for entry in entries:
        instrument = str(entry.get("instrument") or "").upper()
        if (
            entry.get("decision") == "TRADE"
            and (entry.get("risk_check") or {}).get("result") == "APPROVED"
            and key_fn(entry)
        ):
            open_by_instrument.setdefault(instrument, []).append(entry)
            continue
        if entry.get("type") == "OUTCOME":
            queue = open_by_instrument.get(instrument)
            if queue:
                resolved.append(ResolvedTrade(queue.pop(0), entry))
            elif key_fn(entry):
                # Only surfaced as "unmatched" when the outcome itself matches
                # the same filter (e.g. carries a strategy tag) — an OUTCOME row
                # from an instrument/strategy with no open queue at all is not
                # this caller's concern.
                unmatched_outcomes.append(entry)

    still_open: list[dict[str, Any]] = [
        trade for queue in open_by_instrument.values() for trade in queue
    ]
    return resolved, still_open, unmatched_outcomes


def classify_pairs(resolved: list[ResolvedTrade]) -> Counter:
    return Counter(classify_outcome(trade.outcome_body) for trade in resolved)


@dataclass
class AccountingIdentity:
    attempts: int
    fills: int
    cancellations: int
    needs_manual_classification: int
    legitimately_open: int

    @property
    def resolved(self) -> int:
        return self.fills + self.cancellations + self.needs_manual_classification

    @property
    def ok(self) -> bool:
        return self.attempts == self.resolved + self.legitimately_open

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "fills": self.fills,
            "cancellations": self.cancellations,
            "needs_manual_classification": self.needs_manual_classification,
            "resolved": self.resolved,
            "legitimately_open": self.legitimately_open,
            "ok": self.ok,
            "identity": "attempts == (fills + cancellations + needs_manual_classification) + legitimately_open",
        }


def build_accounting_identity(
    resolved: list[ResolvedTrade],
    still_open: list[dict[str, Any]],
) -> AccountingIdentity:
    categories = classify_pairs(resolved)
    fills = categories.get("filled_win_loss", 0) + categories.get("breakeven", 0)
    cancellations = categories.get("cancelled_nofill", 0)
    needs_manual = categories.get("reconciler_touched", 0) + categories.get("other", 0)
    attempts = len(resolved) + len(still_open)
    return AccountingIdentity(
        attempts=attempts,
        fills=fills,
        cancellations=cancellations,
        needs_manual_classification=needs_manual,
        legitimately_open=len(still_open),
    )


def duplicate_order_ids(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ORDER_IDS rows whose order id appears more than once anywhere in the scan
    window — a duplicate order identity is never expected."""
    seen: Counter = Counter()
    rows_by_id: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        if entry.get("type") != "ORDER_IDS":
            continue
        ids = entry.get("order_ids") or {}
        for field, value in ids.items():
            if field == "instrument" or value in (None, ""):
                continue
            key = f"{field}:{value}"
            seen[key] += 1
            rows_by_id.setdefault(key, []).append(entry)
    return [
        {"order_id": key, "count": count, "rows": rows_by_id[key]}
        for key, count in seen.items() if count > 1
    ]


def gate_attrition(entries_for_strategy: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate `failed_gates` across every non-TRADE decision row that carries
    this strategy's formed setup, in the order the entries were read (which is
    the order the real DecisionEngine -> RiskEngine gates were evaluated)."""
    gate_counts: Counter = Counter()
    by_decision: Counter = Counter()
    for entry in entries_for_strategy:
        decision = entry.get("decision") or "UNKNOWN"
        by_decision[decision] += 1
        if decision == "TRADE":
            continue
        for gate in entry.get("failed_gates") or []:
            gate_counts[gate] += 1
    return {
        "candidate_count": len(entries_for_strategy),
        "by_decision": dict(by_decision),
        "failed_gate_counts": dict(gate_counts.most_common()),
    }


def read_entries(journal_dir: Path, *, from_date: str | None = None, to_date: str | None = None) -> list[dict[str, Any]]:
    """`ops.proof_30_mnq.read_journal_entries` plus optional inclusive date filtering
    on the `journal_YYYY-MM-DD.jsonl` filename (not per-row timestamp parsing)."""
    entries = read_journal_entries(journal_dir)
    if not (from_date or to_date):
        return entries
    lo = from_date or "0000-00-00"
    hi = to_date or "9999-99-99"
    return [
        entry for entry in entries
        if lo <= _file_date(entry) <= hi
    ]


def _file_date(entry: dict[str, Any]) -> str:
    path = str(entry.get("_path") or "")
    stem = Path(path).stem  # journal_YYYY-MM-DD
    return stem.removeprefix("journal_") or "0000-00-00"
