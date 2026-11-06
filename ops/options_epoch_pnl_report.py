#!/usr/bin/env python3
"""Read-only cohort P&L + counterfactual-reason report for OPTIONS_PAPER_V1.

The report answers two narrow questions without changing scanner behavior:

1. What is the financial result of the selected evidence cohort only?
2. What happened to COUNTERFACTUAL observations, grouped by the exact reason
   they were filtered out?

Counterfactual rows are observations, not a trade population. Their pricing is
reported descriptively and must not be called expectancy or mixed into ACTIVE
P&L.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_DB = "logs/options_scanner.sqlite"
DEFAULT_EPOCH = "docs/options_v1_evidence_epoch.json"
CLOSED = {"WIN", "LOSS", "BREAKEVEN", "EXPIRED"}
CONSUMED = {"TARGET_CONSUMED_AT_ENTRY", "STOP_CONSUMED_AT_ENTRY"}
COUNTERFACTUAL = "COUNTERFACTUAL"
ACTIVE = "ACTIVE"


def _json(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _num(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def load_epoch(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    boundary = data.get("cohort_boundary") or {}
    first_id = boundary.get("options_shadow_journal_first_id")
    start = data.get("epoch_start")
    cohort = data.get("cohort")
    if not cohort or not start or not isinstance(first_id, int):
        raise ValueError("epoch file missing cohort/epoch_start/options_shadow_journal_first_id")
    return {
        "policy_id": str(data.get("policy_id") or "OPTIONS_PAPER_V1"),
        "cohort": str(cohort),
        "reason": data.get("reason"),
        "epoch_start": str(start),
        "first_shadow_id": first_id,
        "deployed_sha": data.get("deployed_sha"),
    }


def load_rows(db_path: str | Path, *, first_shadow_id: int, epoch_start: str) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, timestamp, ticker, direction, status,
                   setup_inputs_json, selected_contract_json, outcome_json
            FROM options_shadow_journal
            WHERE id >= ? AND timestamp >= ?
            ORDER BY id ASC
            """,
            (first_shadow_id, epoch_start),
        ).fetchall()
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        selected = _json(row["selected_contract_json"])
        inputs = _json(row["setup_inputs_json"])
        outcome = _json(row["outcome_json"])
        lane = str(
            selected.get("paper_evidence_lane")
            or inputs.get("paper_evidence_lane")
            or ACTIVE
        ).upper()
        if selected.get("risk_budget_consumed") is False:
            lane = COUNTERFACTUAL
        out.append(
            {
                "id": int(row["id"]),
                "timestamp": str(row["timestamp"]),
                "ticker": str(row["ticker"]),
                "direction": str(row["direction"]),
                "status": str(row["status"] or "OPEN").upper(),
                "lane": lane,
                "filter_reason": str(
                    selected.get("counterfactual_filter_reason")
                    or inputs.get("counterfactual_filter_reason")
                    or "UNSPECIFIED"
                ),
                "pnl_dollars": _num(outcome.get("pnl_dollars")),
                "resolved_at": outcome.get("resolved_at"),
            }
        )
    return out


def summarize_active(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active = [r for r in rows if r["lane"] != COUNTERFACTUAL]
    closed = [r for r in active if r["status"] in CLOSED]
    priced = [r for r in closed if r["pnl_dollars"] is not None]
    consumed = [r for r in active if r["status"] in CONSUMED]
    pnl = [float(r["pnl_dollars"]) for r in priced]
    return {
        "rows": len(active),
        "open": sum(r["status"] == "OPEN" for r in active),
        "closed_structural": len(closed),
        "structural_target_events": sum(r["status"] == "WIN" for r in closed),
        "structural_stop_events": sum(r["status"] == "LOSS" for r in closed),
        "priced_closed": len(priced),
        "financial_profits": sum(v > 0 for v in pnl),
        "financial_losses": sum(v < 0 for v in pnl),
        "financial_breakeven": sum(v == 0 for v in pnl),
        "unpriced_closed": len(closed) - len(priced),
        "entry_consumed_non_outcomes": len(consumed),
        "pnl_usd": round(sum(pnl), 2),
        "average_priced_closed_usd": round(sum(pnl) / len(pnl), 2) if pnl else None,
    }


def summarize_counterfactual(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cf = [r for r in rows if r["lane"] == COUNTERFACTUAL]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in cf:
        groups[row["filter_reason"]].append(row)

    by_reason: list[dict[str, Any]] = []
    for reason, members in sorted(groups.items()):
        closed = [r for r in members if r["status"] in CLOSED]
        priced = [r for r in closed if r["pnl_dollars"] is not None]
        values = [float(r["pnl_dollars"]) for r in priced]
        by_reason.append(
            {
                "filter_reason": reason,
                "observations": len(members),
                "open": sum(r["status"] == "OPEN" for r in members),
                "entry_consumed_non_outcomes": sum(r["status"] in CONSUMED for r in members),
                "closed_priced_observations": len(priced),
                "priced_positive": sum(v > 0 for v in values),
                "priced_negative": sum(v < 0 for v in values),
                "priced_breakeven": sum(v == 0 for v in values),
                "observed_pnl_usd": round(sum(values), 2),
                "average_priced_observation_usd": (
                    round(sum(values) / len(values), 2) if values else None
                ),
                "trade_population": False,
            }
        )

    all_closed = [r for r in cf if r["status"] in CLOSED and r["pnl_dollars"] is not None]
    all_values = [float(r["pnl_dollars"]) for r in all_closed]
    return {
        "observations": len(cf),
        "closed_priced_observations": len(all_closed),
        "observed_pnl_usd": round(sum(all_values), 2),
        "trade_population": False,
        "warning": "COUNTERFACTUAL rows were filtered out and did not consume ACTIVE risk; descriptive only, not expectancy.",
        "by_filter_reason": by_reason,
    }


def build_report(rows: list[dict[str, Any]], epoch: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_id": epoch["policy_id"],
        "cohort": epoch["cohort"],
        "cohort_reason": epoch.get("reason"),
        "epoch_start": epoch["epoch_start"],
        "first_shadow_id": epoch["first_shadow_id"],
        "deployed_sha": epoch.get("deployed_sha"),
        "rows_loaded": len(rows),
        "active": summarize_active(rows),
        "counterfactual": summarize_counterfactual(rows),
        "cost_model": "recorded scanner pnl_dollars: entry at ask, exit at bid, no commission",
        "read_only": True,
    }


def format_text(report: dict[str, Any]) -> str:
    a = report["active"]
    c = report["counterfactual"]
    lines = [
        f"Options cohort audit · {report['cohort']}",
        f"Start: {report['epoch_start']} · first shadow id: {report['first_shadow_id']}",
        (
            "ACTIVE: "
            f"{a['priced_closed']} priced closed · "
            f"{a['financial_profits']} profitable / {a['financial_losses']} losing / "
            f"{a['financial_breakeven']} breakeven · ${a['pnl_usd']:+.2f}"
        ),
        (
            "Underlying events: "
            f"{a['structural_target_events']} target / {a['structural_stop_events']} stop · "
            f"open {a['open']} · entry-consumed non-outcomes {a['entry_consumed_non_outcomes']}"
        ),
        (
            "COUNTERFACTUAL observations: "
            f"{c['closed_priced_observations']} priced closed · ${c['observed_pnl_usd']:+.2f} "
            "(descriptive only; not trades/expectancy)"
        ),
    ]
    for group in c["by_filter_reason"]:
        lines.append(
            "  - "
            f"{group['filter_reason']}: {group['closed_priced_observations']} priced closed · "
            f"{group['priced_positive']} positive / {group['priced_negative']} negative / "
            f"{group['priced_breakeven']} breakeven · ${group['observed_pnl_usd']:+.2f}"
        )
    lines.append(f"[{report['cost_model']} · read-only]")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--epoch", default=DEFAULT_EPOCH)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    epoch = load_epoch(args.epoch)
    rows = load_rows(
        args.db,
        first_shadow_id=epoch["first_shadow_id"],
        epoch_start=epoch["epoch_start"],
    )
    report = build_report(rows, epoch)
    rendered = json.dumps(report, indent=2, sort_keys=True) if args.json else format_text(report)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
