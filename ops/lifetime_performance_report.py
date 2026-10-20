#!/usr/bin/env python3
"""Read-only lifetime accounting from copied paper journals and options SQLite.

Source-only report. No broker, live runtime, service, Discord, or trading calls.
Unjoinable, unpriced, malformed, and cohort-ambiguous evidence stays visible.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CLOSED = frozenset({"WIN", "LOSS", "BREAKEVEN", "EXPIRED"})
NON_OUTCOMES = frozenset({"TARGET_CONSUMED_AT_ENTRY", "STOP_CONSUMED_AT_ENTRY"})
FUTURES_RESOLVED = frozenset({"WIN", "LOSS", "BREAKEVEN"})


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else None


def _money(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
    except (ValueError, TypeError):
        return None
    return n if math.isfinite(n) else None


def _obj(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value or "{}")
    except (ValueError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _totals(rows: list[dict], *, closed: frozenset[str] = CLOSED) -> dict:
    finished = [r for r in rows if r["status"] in closed]
    amounts = [_money(r.get("pnl_usd")) for r in finished]
    prices = [p for p in amounts if p is not None]
    missing = len(amounts) - len(prices)
    return {
        "records": len(rows),
        "closed": len(finished),
        "priced_closed": len(prices),
        "unpriced_closed": missing,
        "open": sum(r["status"] == "OPEN" for r in rows),
        "other_or_non_outcome": sum(r["status"] not in closed and r["status"] != "OPEN" for r in rows),
        "financial_wins": sum(p > 0 for p in prices),
        "financial_losses": sum(p < 0 for p in prices),
        "financial_breakeven": sum(p == 0 for p in prices),
        "priced_subtotal_usd": round(sum(prices), 2),
        "complete_pnl_usd": round(sum(prices), 2) if not missing else None,
        "pnl_complete": not missing,
        "model": "recorded_paper_pnl_only",
    }


def _groups(rows: list[dict], field: str, *, closed: frozenset[str] = CLOSED) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field) or "UNSPECIFIED")].append(row)
    return {key: _totals(grouped[key], closed=closed) for key in sorted(grouped)}


def _read_journals(folder: Path) -> tuple[list[dict], int, int]:
    if not folder.is_dir():
        raise FileNotFoundError(f"missing futures journal directory: {folder}")
    paths = sorted(folder.glob("journal_????-??-??.jsonl"))
    if not paths:
        raise ValueError("no daily futures journal files found; cannot report lifetime")
    rows: list[dict] = []
    malformed = 0
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(record, dict):
                    malformed += 1
                    continue
                rows.append(record)
    return rows, malformed, len(paths)


def _futures_changes(path: Path | None) -> list[dict]:
    if path is None:
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    changes = doc.get("changes")
    if not isinstance(changes, list):
        raise ValueError("change file must contain changes array")
    result = []
    for item in changes:
        if not isinstance(item, dict) or not item.get("label") or not item.get("evidence_ref"):
            raise ValueError("change must have a label and a source evidence_ref")
        ts = _time(item.get("effective_utc"))
        if ts is None or item.get("instrument") is None:
            raise ValueError("change must have UTC effective_utc and explicit instrument")
        result.append({**item, "_time": ts})
    if len({(x["instrument"], x["_time"]) for x in result}) != len(result):
        raise ValueError("ambiguous duplicate change dates")
    return sorted(result, key=lambda x: x["_time"])


def _futures_segment(row: dict, changes: list[dict]) -> str:
    if not changes:
        return "CHANGE_BOUNDARIES_UNVERIFIED"
    instant = _time(row.get("ts"))
    if instant is None:
        return "MISSING_ENTRY_TIMESTAMP"
    relevant = [x for x in changes if x["instrument"] in (row["ticker"], "*") and x["_time"] <= instant]
    return relevant[-1]["label"] if relevant else "BEFORE_FIRST_DOCUMENTED_CHANGE"


def futures_report(journal_rows: list[dict], *, changes: list[dict] | None = None) -> dict:
    """Exact cross-file paper_order_id join. Never FIFO-pair by market or date."""
    changes = changes or []
    decisions: dict[str, list[dict]] = defaultdict(list)
    outcomes: dict[str, list[dict]] = defaultdict(list)
    shadow: dict[tuple[str, str, str, str, str], dict] = {}
    shadow_unidentified = 0
    for row in journal_rows:
        if row.get("decision") == "TRADE" and _obj(row.get("risk_check")).get("result") == "APPROVED":
            oid = row.get("paper_order_id")
            decisions[str(oid) if oid else ""].append(row)
        if row.get("type") == "OUTCOME":
            out = _obj(row.get("outcome"))
            oid = out.get("paper_order_id")
            outcomes[str(oid) if oid else ""].append(out)
        if row.get("type") == "SHADOW_OUTCOME":
            cid = row.get("candidate_key") or row.get("candidate_id")
            if not cid:
                shadow_unidentified += 1
            else:
                # Existing forward resolver semantics: last journal revision wins.
                shadow[(str(row.get('lane') or ''), str(row.get('strategy') or ''), str(row.get('instrument') or ''), str(row.get('evidence_epoch') or ''), str(cid))] = row
    clean: list[dict] = []
    unresolved = 0
    invalid_identity = len(decisions.pop("", []))
    ambiguous = 0
    for oid, entries in decisions.items():
        if len(entries) != 1 or len(outcomes.get(oid, [])) > 1:
            ambiguous += len(entries)
            continue
        trade = entries[0]
        outcomes_here = outcomes.get(oid, [])
        inline = _obj(trade.get("outcome"))
        if inline.get("result") and outcomes_here:
            ambiguous += 1
            continue
        exit_ = inline if inline.get("result") else (outcomes_here[0] if outcomes_here else {})
        result = str(exit_.get("result") or "OPEN").upper()
        if not outcomes_here and not inline.get("result"):
            unresolved += 1
        setup = _obj(trade.get("setup"))
        ticker = str(trade.get("instrument") or "UNKNOWN")
        item = {
            "ticker": ticker,
            "strategy": str(setup.get("strategy") or "UNSPECIFIED"),
            "status": result,
            "pnl_usd": exit_.get("pnl_dollars") if result in FUTURES_RESOLVED else None,
            "ts": trade.get("ts"),
        }
        item["change_period"] = _futures_segment(item, changes)
        clean.append(item)
    # Orphan outcomes and paper decisions without stable IDs are not fabricated.
    orphan = sum(len(v) for k, v in outcomes.items() if k and k not in decisions)
    shadow_rows: list[dict] = []
    for row in shadow.values():
        out = _obj(row.get("shadow_outcome"))
        shadow_rows.append({
            "ticker": str(row.get("instrument") or "UNKNOWN"),
            "strategy": str(row.get("strategy") or "UNSPECIFIED"),
            "epoch": str(row.get("evidence_epoch") or "UNSTAMPED"),
            "status": str(out.get("result") or "OPEN"),
            "pnl_ticks": out.get("pnl_ticks"),
        })
    shadow_summary: dict[str, dict] = {}
    for ticker in sorted({row["ticker"] for row in shadow_rows}):
        members = [r for r in shadow_rows if r["ticker"] == ticker]
        shadow_summary[ticker] = {
            "observations": len(members),
            "resolved_with_ticks": sum(r["status"] in ("WIN", "LOSS") and _money(r["pnl_ticks"]) is not None for r in members),
            "unpriced_or_unresolved": sum(r["status"] in ("WIN", "LOSS") and _money(r["pnl_ticks"]) is None for r in members),
            "pnl_ticks_priced_subtotal": round(sum(_money(r["pnl_ticks"]) or 0 for r in members if r["status"] in ("WIN", "LOSS")), 2),
            "epochs": {k: sum(r["epoch"] == k for r in members) for k in sorted({r["epoch"] for r in members})},
            "note": "hypothetical shadow ticks only; NOT dollars, executable fills, or account profit",
        }
    return {
        "paper_trade_lifetime": _totals(clean, closed=FUTURES_RESOLVED),
        "paper_by_ticker": _groups(clean, "ticker", closed=FUTURES_RESOLVED),
        "paper_by_change": _groups(clean, "change_period", closed=FUTURES_RESOLVED),
        "paper_by_ticker_and_change": {
            ticker: _groups([r for r in clean if r["ticker"] == ticker], "change_period", closed=FUTURES_RESOLVED)
            for ticker in sorted({r["ticker"] for r in clean})
        },
        "identity_problems": {
            "unjoinable_legacy_decisions": invalid_identity,
            "ambiguous_duplicate_id_decisions": ambiguous,
            "unresolved_with_identity": unresolved,
            "orphan_outcomes": orphan,
        },
        "shadow_observations_by_ticker": shadow_summary,
        "shadow_unidentified_excluded": shadow_unidentified,
        "change_source": "operator-supplied external documented boundaries" if changes else "NO VERIFIED FUTURES CHANGE BOUNDARIES",
        "reconciled_within_supplied_journals": invalid_identity == 0 and ambiguous == 0 and orphan == 0,
    }


def _epochs(path: Path) -> list[dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    raw = list(doc.get("previous_epochs") or []) + [doc]
    boundaries = []
    for epoch in raw:
        boundary = _obj(epoch.get("cohort_boundary"))
        start = _time(epoch.get("epoch_start"))
        first = boundary.get("options_shadow_journal_first_id")
        if start is None or not isinstance(first, int) or not epoch.get("cohort"):
            raise ValueError("options epoch registry missing a valid timestamp, first ID, or name")
        boundaries.append({"name": str(epoch["cohort"]), "start": start, "first_id": first,
                           "reason": epoch.get("reason"), "source": "docs/options_v1_evidence_epoch.json"})
    boundaries.sort(key=lambda x: x["first_id"])
    if any(b["first_id"] >= n["first_id"] or b["start"] >= n["start"]
           for b, n in zip(boundaries, boundaries[1:])):
        raise ValueError("options epoch boundaries overlap or run backwards")
    return boundaries


def options_report(db_path: Path, registry: Path) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(f"missing options snapshot: {db_path}")
    boundaries = _epochs(registry)
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        db_rows = conn.execute(
            "SELECT id,timestamp,ticker,status,setup_inputs_json,selected_contract_json,outcome_json "
            "FROM options_shadow_journal ORDER BY id"
        ).fetchall()
    active: list[dict] = []
    counterfactual = 0
    unknown_lane = 0
    boundary_conflicts = 0
    prior_rows = 0
    for rid, ts, ticker, status, input_json, contract_json, result_json in db_rows:
        selected, inputs, out = _obj(contract_json), _obj(input_json), _obj(result_json)
        lane = str(selected.get("paper_evidence_lane") or inputs.get("paper_evidence_lane") or "").upper()
        if selected.get("risk_budget_consumed") is False:
            lane = "COUNTERFACTUAL"
        if lane == "COUNTERFACTUAL":
            counterfactual += 1
            continue
        if lane != "ACTIVE":
            unknown_lane += 1
            continue
        start = _time(ts)
        if start is None:
            boundary_conflicts += 1
            epoch = "INVALID_TIMESTAMP"
        else:
            id_epoch = next((i for i, b in enumerate(boundaries) if rid < b["first_id"]), len(boundaries)) - 1
            ts_epoch = next((i for i, b in enumerate(boundaries) if start < b["start"]), len(boundaries)) - 1
            if id_epoch != ts_epoch:
                boundary_conflicts += 1
                epoch = "BOUNDARY_CONFLICT"
            elif id_epoch < 0:
                prior_rows += 1
                epoch = "BEFORE_REGISTERED_EPOCHS"
            else:
                epoch = boundaries[id_epoch]["name"]
        active.append({
            "ticker": str(ticker or "UNKNOWN"),
            "epoch": epoch,
            "status": str(status or "OPEN").upper(),
            "pnl_usd": out.get("pnl_dollars"),
        })
    return {
        "lifetime_active": _totals(active),
        "active_by_ticker": _groups(active, "ticker"),
        "active_by_epoch": _groups(active, "epoch"),
        "active_by_ticker_and_epoch": {
            ticker: _groups([r for r in active if r["ticker"] == ticker], "epoch")
            for ticker in sorted({r["ticker"] for r in active})
        },
        "epochs": [{k: x[k] for k in ("name", "first_id", "reason")} | {"start_utc": x["start"].isoformat()} for x in boundaries],
        "counterfactual_excluded": counterfactual,
        "unknown_lane_excluded": unknown_lane,
        "epoch_boundary_conflicts": boundary_conflicts,
        "before_registered_epochs": prior_rows,
        "reconciled_within_supplied_options_snapshot": unknown_lane == 0 and boundary_conflicts == 0,
        "cost_model": "recorded scanner ASK entry / BID exit; commissions excluded",
        "warning": "unpriced closed options invalidate complete P&L; structural WIN is not necessarily financial profit",
    }


def build_report(journal_dir: Path, options_db: Path, options_epochs: Path,
                 futures_changes: Path | None = None) -> dict:
    rows, malformed, nfiles = _read_journals(journal_dir)
    return {
        "schema": "afs-lifetime-paper-accounting-v1",
        "authority": "READ_ONLY_ACCOUNTING_NOT_STRATEGY_VALIDATION",
        "source": {"journal_files": nfiles, "journal_rows": len(rows), "malformed_journal_rows": malformed,
                   "options_snapshot": str(options_db), "options_epoch_registry": str(options_epochs)},
        "futures": futures_report(rows, changes=_futures_changes(futures_changes)),
        "options": options_report(options_db, options_epochs),
        "parse_complete": malformed == 0,
        "lifetime_source_coverage_verified": False,
        "source_coverage_warning": "Input history and epoch version coverage must be independently verified before declaring since-inception performance.",
        "interpretation": "actual/demo futures and ACTIVE options are separate paper-ledger populations; "
                          "shadow observer ticks are not money or tradable fills; "
                          "pre/post changes are descriptive cohorts, never causal improvement claims",
    }



def format_human(report: dict) -> str:
    """One compact private operator readout; incomplete dollars never look final."""
    def dollars(t: dict) -> str:
        value = t["complete_pnl_usd"]
        if value is None:
            return (f"INCOMPLETE (priced subset ${t['priced_subtotal_usd']:+,.2f}; "
                    f"{t['unpriced_closed']} unpriced closed)")
        return f"${value:+,.2f} ({t['priced_closed']} priced closed)"

    lines = ["LIFETIME PAPER EVIDENCE · READ ONLY",
             "Supplied-file history coverage: NOT YET INDEPENDENTLY VERIFIED",
             "Futures approved journal outcomes (not broker account balance):"]
    for ticker, totals in report["futures"]["paper_by_ticker"].items():
        lines.append(f"  {ticker}: {dollars(totals)}")
        changes = report["futures"]["paper_by_ticker_and_change"][ticker]
        for change, segment in changes.items():
            lines.append(f"    {change}: {dollars(segment)}")
    lines.append("Options ACTIVE paper marks (ASK entry / BID exit; commissions excluded):")
    for ticker, totals in report["options"]["active_by_ticker"].items():
        lines.append(f"  {ticker}: {dollars(totals)}")
        for epoch, segment in report["options"]["active_by_ticker_and_epoch"][ticker].items():
            lines.append(f"    {epoch}: {dollars(segment)}")
    lines.append("Futures shadow/observer data: separate hypothetical ticks, NOT account dollars")
    for ticker, item in report["futures"]["shadow_observations_by_ticker"].items():
        lines.append(f"  {ticker}: {item['pnl_ticks_priced_subtotal']:+,.1f} recorded ticks "
                     f"({item['resolved_with_ticks']} resolved with ticks)")
    lines.append("Reconciliation: "
                 f"futures {report['futures']['identity_problems']}; "
                 f"options unclassified {report['options']['unknown_lane_excluded']}, "
                 f"epoch conflicts {report['options']['epoch_boundary_conflicts']}")
    lines.append("Do not interpret these data as verified since-inception performance, "
                 "broker-realized P&L, or a causal strategy improvement.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--journal-dir", type=Path, required=True, help="complete COPY of futures journal files")
    p.add_argument("--options-db", type=Path, required=True, help="consistent read-only COPY of options scanner SQLite")
    p.add_argument("--options-epochs", type=Path, default=Path("docs/options_v1_evidence_epoch.json"))
    p.add_argument("--futures-changes", type=Path, help="optional independently verified change-boundary JSON")
    p.add_argument("--text", action="store_true", help="human-readable private operator summary")
    args = p.parse_args(argv)
    report = build_report(args.journal_dir, args.options_db, args.options_epochs, args.futures_changes)
    print(format_human(report) if args.text else json.dumps(report, indent=2, sort_keys=True))
    # Partial/unreconciled input refuses a success status; JSON remains available for triage.
    ok = (report["parse_complete"] and report["futures"]["reconciled_within_supplied_journals"]
          and report["futures"]["paper_trade_lifetime"]["pnl_complete"]
          and report["options"]["reconciled_within_supplied_options_snapshot"] and report["options"]["lifetime_active"]["pnl_complete"])
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
