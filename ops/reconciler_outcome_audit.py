"""Read-only audit inventory for reconciler-touched journal outcomes."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ops.proof_30_mnq import RECONCILER_MARKERS, parse_proof_ts, read_journal_entries


TOUCHED_SESSION = "reconcile"
TOUCHED_MARKERS = tuple(sorted(set(RECONCILER_MARKERS + ("auto flatten",))))
FILLED_RESULTS = {"WIN", "LOSS", "BREAKEVEN"}


@dataclass(frozen=True)
class OperatorOverride:
    heading: str
    instrument: str | None
    session_date: str | None
    ruling: str | None

    def matches(self, item: dict[str, Any]) -> bool:
        if self.instrument and self.instrument.upper() != str(item.get("instrument") or "").upper():
            return False
        dates = {str(item.get("trade_date") or ""), str(item.get("outcome_date") or "")}
        if self.session_date and self.session_date not in dates:
            return False
        return True

    def to_summary(self) -> dict[str, Any]:
        return {
            "heading": self.heading,
            "instrument": self.instrument,
            "session_date": self.session_date,
            "ruling": self.ruling,
        }


def _first_backtick_value(line: str) -> str | None:
    match = re.search(r"`([^`]+)`", line)
    return match.group(1).strip() if match else None


def _plain_bullet_value(line: str) -> str | None:
    _, _, value = line.partition(":")
    value = value.strip()
    return value or None


def load_operator_overrides(path: Path) -> list[OperatorOverride]:
    """Extract public-safe matching hints from docs/proof-operator-overrides.md."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    overrides: list[OperatorOverride] = []
    current: dict[str, Any] | None = None
    in_ruling = False
    ruling_lines: list[str] = []

    def flush() -> None:
        nonlocal current, ruling_lines
        if not current:
            return
        ruling = " ".join(line.strip() for line in ruling_lines if line.strip()) or None
        overrides.append(
            OperatorOverride(
                heading=str(current.get("heading") or ""),
                instrument=current.get("instrument"),
                session_date=current.get("session_date"),
                ruling=ruling,
            )
        )
        current = None
        ruling_lines = []

    for line in lines:
        if line.startswith("## "):
            flush()
            current = {"heading": line[3:].strip()}
            in_ruling = False
            continue
        if current is None:
            continue
        stripped = line.strip()
        lower = stripped.lower()
        if lower.startswith("- instrument:"):
            current["instrument"] = _first_backtick_value(stripped) or _plain_bullet_value(stripped)
            continue
        if lower.startswith("- session date:"):
            current["session_date"] = _first_backtick_value(stripped) or _plain_bullet_value(stripped)
            continue
        if stripped.startswith("### "):
            in_ruling = stripped.lower() == "### operator ruling"
            continue
        if in_ruling and stripped and not stripped.startswith("-"):
            ruling_lines.append(stripped)
    flush()
    return overrides


def _entry_date(entry: dict[str, Any] | None) -> str | None:
    if not entry:
        return None
    dt = parse_proof_ts(entry.get("ts"))
    return dt.date().isoformat() if dt else None


def _touch_markers(entry: dict[str, Any]) -> list[str]:
    outcome = entry.get("outcome") or {}
    session = str(entry.get("session") or "").lower()
    reason = str(outcome.get("exit_reason") or "").lower()
    markers = [marker for marker in TOUCHED_MARKERS if marker in reason]
    if session == TOUCHED_SESSION:
        markers.insert(0, "session:reconcile")
    return list(dict.fromkeys(markers))


def is_reconciler_touched_outcome(entry: dict[str, Any]) -> bool:
    return entry.get("type") == "OUTCOME" and bool(_touch_markers(entry))


def _pair_all_trades(entries: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    open_trades: dict[str, list[dict[str, Any]]] = defaultdict(list)
    paired: dict[int, dict[str, Any]] = {}
    for entry in entries:
        instrument = str(entry.get("instrument") or "").upper()
        if not instrument:
            continue
        risk = entry.get("risk_check") or {}
        if entry.get("decision") == "TRADE" and risk.get("result") == "APPROVED":
            open_trades[instrument].append(entry)
            continue
        if entry.get("type") == "OUTCOME" and open_trades[instrument]:
            paired[id(entry)] = open_trades[instrument].pop(0)
    return paired


def _audit_id(outcome: dict[str, Any]) -> str:
    path = Path(str(outcome.get("_path") or "journal")).name
    line = outcome.get("_line") or "?"
    instrument = str(outcome.get("instrument") or "?").upper()
    date = _entry_date(outcome) or "unknown-date"
    return f"{date}:{instrument}:{path}:{line}"


def _base_item(outcome: dict[str, Any], trade: dict[str, Any] | None) -> dict[str, Any]:
    body = outcome.get("outcome") or {}
    setup = (trade or {}).get("setup") or {}
    risk = (trade or {}).get("risk_check") or {}
    trade_ts = (trade or {}).get("ts")
    outcome_ts = outcome.get("ts")
    item = {
        "audit_id": _audit_id(outcome),
        "classification": "unaudited",
        "classification_reason": "needs broker verification under RUNBOOK evidence-chain reconciliation",
        "classification_source": None,
        "needs_broker_verification": True,
        "instrument": outcome.get("instrument"),
        "trade_ts": trade_ts,
        "outcome_ts": outcome_ts,
        "trade_date": _entry_date(trade),
        "outcome_date": _entry_date(outcome),
        "trade_journal_path": (trade or {}).get("_path"),
        "trade_journal_line": (trade or {}).get("_line"),
        "outcome_journal_path": outcome.get("_path"),
        "outcome_journal_line": outcome.get("_line"),
        "session": outcome.get("session"),
        "result": body.get("result"),
        "exit_reason": body.get("exit_reason"),
        "pnl_dollars": body.get("pnl_dollars"),
        "entry_price": body.get("entry_price"),
        "exit_price": body.get("exit_price"),
        "contracts": body.get("contracts") or setup.get("contracts"),
        "markers": _touch_markers(outcome),
        "trade": {
            "direction": setup.get("direction"),
            "strategy": setup.get("strategy"),
            "entry": setup.get("entry"),
            "stop": setup.get("stop"),
            "target": setup.get("target"),
            "risk": risk.get("result"),
        } if trade else None,
        "broker_follow_up": {
            "window_start": trade_ts or outcome_ts,
            "window_end": outcome_ts,
            "verify": [
                "broker order/fill history for this instrument and window",
                "realized P&L and entry/exit prices versus the journal outcome",
                "whether docs/proof-operator-overrides.md needs a public-safe ruling",
            ],
        },
        "operator_override_fields": {
            "instrument": outcome.get("instrument"),
            "session_date": _entry_date(trade) or _entry_date(outcome),
            "trade_window": f"{trade_ts or '?'} to {outcome_ts or '?'}",
            "journal_history": f"{body.get('result')} / {body.get('exit_reason')}",
            "journal_location": f"{outcome.get('_path')}:{outcome.get('_line')}",
        },
    }
    result = str(body.get("result") or "").upper()
    reason = str(body.get("exit_reason") or "").lower()
    completed_reconcile = (
        str(outcome.get("session") or "").lower() == TOUCHED_SESSION
        and result in FILLED_RESULTS
        and not any(marker in reason for marker in TOUCHED_MARKERS)
    )
    if completed_reconcile:
        item["classification"] = "classified"
        item["classification_source"] = "completed_trade_reconcile"
        item["classification_reason"] = (
            "reconciler resolved a completed broker trade after entry-fill confirmation; "
            "spot-check broker fills if this trade is proof-critical"
        )
        item["needs_broker_verification"] = False
    return item


def build_audit_report(
    *,
    journal_dir: Path,
    overrides_doc: Path | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    entries = read_journal_entries(journal_dir)
    if from_date or to_date:
        filtered_entries = []
        for entry in entries:
            date = _entry_date(entry)
            if not date:
                filtered_entries.append(entry)
                continue
            if from_date is not None and date < from_date:
                continue
            if to_date is not None and date > to_date:
                continue
            filtered_entries.append(entry)
        entries = filtered_entries
    read_errors = [entry for entry in entries if entry.get("type") == "READ_ERROR"]
    paired = _pair_all_trades(entries)
    overrides = load_operator_overrides(overrides_doc) if overrides_doc else []
    touched = [_base_item(entry, paired.get(id(entry))) for entry in entries if is_reconciler_touched_outcome(entry)]

    for item in touched:
        matches = [override for override in overrides if override.matches(item)]
        if matches:
            item["classification"] = "classified"
            item["classification_source"] = "operator_override"
            item["classification_reason"] = "matched repo operator override by instrument/date"
            item["needs_broker_verification"] = False
            item["operator_overrides"] = [match.to_summary() for match in matches]
        else:
            item["operator_overrides"] = []

    classified = [item for item in touched if item["classification"] == "classified"]
    unaudited = [item for item in touched if item["classification"] == "unaudited"]
    by_marker = defaultdict(int)
    by_instrument = defaultdict(int)
    for item in touched:
        by_instrument[str(item.get("instrument") or "?")] += 1
        for marker in item.get("markers") or ["?"]:
            by_marker[marker] += 1

    return {
        "ok": not read_errors,
        "audit_name": "reconciler_touched_outcomes",
        "read_only": True,
        "journal_dir": str(journal_dir),
        "overrides_doc": str(overrides_doc) if overrides_doc else None,
        "filters": {"from_date": from_date, "to_date": to_date},
        "journal_read_errors": read_errors,
        "summary": {
            "total_touched": len(touched),
            "classified": len(classified),
            "unaudited": len(unaudited),
            "journal_read_errors": len(read_errors),
            "by_instrument": dict(sorted(by_instrument.items())),
            "by_marker": dict(sorted(by_marker.items())),
        },
        "classified": classified,
        "unaudited": unaudited,
    }


def report_to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)
