#!/usr/bin/env python3
"""Read-only journal audit of planned entries versus actual broker fills."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.broker_interface import BracketOrder
from execution.post_fill_validation import validate_post_fill


LIMITS = {
    "MNQ": {"max_slippage_ticks": 32.0, "max_stop_ticks": 120.0},
    "MES": {"max_slippage_ticks": 16.0, "max_stop_ticks": 60.0},
}


def _rows(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        with path.open() as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
    return sorted(rows, key=lambda row: str(row.get("ts") or ""))


def audit(paths: list[Path]) -> dict:
    audited = []
    # Pair within each UTC journal day only. Old journals contain unresolved
    # phantom TRADE rows from earlier incidents; carrying those queues across
    # dates would falsely attach a July fill to a June plan.
    for path in sorted(paths):
        pending: dict[str, deque] = defaultdict(deque)
        for row in _rows([path]):
            instrument = str(row.get("instrument") or "").upper()
            if row.get("decision") == "TRADE" and not row.get("paper_order_id"):
                pending[instrument].append(row)
                continue
            if row.get("type") != "OUTCOME" or not pending[instrument]:
                continue
            outcome = row.get("outcome") or {}
            if outcome.get("result") in ("CANCELLED", "VOID"):
                continue
            actual_entry = outcome.get("entry_price")
            if actual_entry is None:
                continue
            trade = pending[instrument].popleft()
            setup = trade.get("setup") or {}
            limits = LIMITS.get(instrument, {})
            tick_value = 0.50 if instrument == "MNQ" else 1.25
            tick_size = 0.25
            contracts = int(setup.get("contracts") or outcome.get("contracts") or 1)
            approved_risk_budget = (
                abs(float(setup.get("entry")) - float(setup.get("stop"))) / tick_size
                + float(limits.get("max_slippage_ticks") or 0)
            ) * tick_value * contracts
            order = BracketOrder(
                instrument=instrument,
                direction=setup.get("direction"),
                entry=float(setup.get("entry")),
                stop=float(setup.get("stop")),
                target=float(setup.get("target")),
                rr_ratio=float(setup.get("rr_ratio") or 0),
                strategy=str(setup.get("strategy") or "missing"),
                contracts=contracts,
                min_rr_ratio=2.0,
                max_dollar_risk=approved_risk_budget,
                max_stop_ticks=limits.get("max_stop_ticks"),
                max_slippage_ticks=limits.get("max_slippage_ticks"),
            )
            result = validate_post_fill(order, float(actual_entry))
            audited.append({
            "signal_timestamp": (trade.get("context") or {}).get("timestamp"),
            "decision_timestamp": trade.get("ts"),
            "outcome_timestamp": row.get("ts"),
            "instrument": instrument,
            "strategy": setup.get("strategy"),
            "direction": setup.get("direction"),
            "old_result": outcome.get("result"),
            "old_pnl_dollars": outcome.get("pnl_dollars"),
            "old_exit_reason": outcome.get("exit_reason"),
            "commission": None,
            "mae": None,
            "mfe": None,
            "corrected_action": "ACCEPT" if result.accepted else "FLATTEN_IMMEDIATELY",
            "corrected_flatten_price": None if not result.accepted else outcome.get("exit_price"),
            "corrected_pnl_dollars": outcome.get("pnl_dollars") if result.accepted else None,
                "validation": result.to_dict(),
            })
    rejected = [row for row in audited if row["corrected_action"] != "ACCEPT"]
    accepted = [row for row in audited if row["corrected_action"] == "ACCEPT"]
    return {
        "trades_audited": len(audited),
        "accepted": len(accepted),
        "flatten_immediately": len(rejected),
        "rejected_old_winners": sum(1 for row in rejected if row["old_result"] == "WIN"),
        "rejected_old_losses": sum(1 for row in rejected if row["old_result"] == "LOSS"),
        "known_old_pnl_accepted": round(sum(float(row["old_pnl_dollars"] or 0) for row in accepted), 2),
        "known_old_pnl_rejected": round(sum(float(row["old_pnl_dollars"] or 0) for row in rejected), 2),
        "corrected_expectancy": None,
        "corrected_drawdown": None,
        "limitations": [
            "Historical journals do not contain the counterfactual immediate-flatten fill price.",
            "Commission, MAE, and MFE are not present in these journal rows.",
            "Corrected expectancy and drawdown cannot be stated exactly without those fills.",
        ],
        "trades": audited,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.paths)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
