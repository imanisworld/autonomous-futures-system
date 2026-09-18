#!/usr/bin/env python3
"""Audit the frozen Transition repair through the isolated research contract.

Read-only. No runtime activation, deployment, broker route, env hook, or global
risk/config mutation.

Pipeline under test:
  preserved Stage-B candidate timestamp
  -> current objective geometry / DecisionEngine candidate construction
  -> isolated RiskEngine
  -> real PaperBroker IOC
  -> stop-first or six-available-5m-bar time exit
  -> isolated $8k ledger accounting

The preserved Stage-B rows remain the population authority. This script proves
whether the new isolated executable contract can reproduce them economically.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHON_DOTENV_DISABLED", "1")

from config.settings import load_config
from context.transition_400t_30m_research import (
    STARTING_BALANCE,
    DAILY_LOSS_LIMIT,
    MAX_DRAWDOWN_PERCENT,
    MAX_TRADES_PER_DAY,
    canonical_candidate,
    open_research_position,
    resolve_six_available_5m_bars,
)
from risk.risk_engine import DailyState
from scripts.edge_decomposition_audit import StateBuilder, load_bars

CANDIDATE_FILE = ROOT / "scripts/edge_decomposition_audit_results_candidates.jsonl.gz"
LANE_CORPUS = {
    "transition_mnq": "replay_polygon_5m",
    "transition_mnq_audit": "replay_corpus_v1_5m",
}
LANES = tuple(LANE_CORPUS)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: Path, lane: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt") as handle:
        for line in handle:
            row = json.loads(line)
            control = ((row.get("control") or {}).get("30m") or {})
            if (
                row.get("lane") == lane
                and row.get("instrument") == "MNQ"
                and row.get("session") == "new_york"
                and control.get("exit_bar_ts") is not None
            ):
                rows.append(row)
    return sorted(rows, key=lambda row: _dt(row["bar_ts"]))


def _recent_5m(bars, idx: int) -> list[dict[str, Any]]:
    return [
        {k: v for k, v in row.items() if k != "_dt"}
        for row in bars.rows[max(0, idx - 7): idx + 1]
    ]


def _future_six(bars, idx: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in bars.rows[idx + 1:]:
        out.append({k: v for k, v in row.items() if k != "_dt"})
        if len(out) == 6:
            break
    return out


def _new_day_state(*, balance: float, peak: float, date_value: str) -> DailyState:
    return DailyState(
        date=date_value,
        account_balance=balance,
        account_peak_balance=peak,
        trade_count=0,
        consecutive_losses=0,
        has_open_position=False,
        realized_pnl_dollars=0.0,
        session_trade_counts={},
    )


def audit_lane(*, lane: str, candidate_file: Path, data_root: Path, slippage_ticks: float = 1.0) -> dict[str, Any]:
    rows = load_rows(candidate_file, lane)
    corpus = data_root / LANE_CORPUS[lane]
    bars = load_bars(corpus, "MNQ")
    builder = StateBuilder(load_config(), bars)
    cfg = load_config()

    counts = Counter()
    risk_failures = Counter()
    candidate_mismatches: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []

    balance = STARTING_BALANCE
    peak = STARTING_BALANCE
    daily: DailyState | None = None
    current_day: str | None = None
    busy_until: datetime | None = None
    halted_drawdown = False

    for row in rows:
        decision_ts = _dt(row["bar_ts"])
        day = str(row.get("date") or decision_ts.date().isoformat())

        idx = bars.by_dt.get(decision_ts)
        if idx is None:
            counts["BAR_MISSING"] += 1
            records.append({"bar_ts": row["bar_ts"], "status": "BAR_MISSING"})
            continue

        state = builder.state_at(idx)
        if state is None:
            counts["STATE_MISSING"] += 1
            records.append({"bar_ts": row["bar_ts"], "status": "STATE_MISSING"})
            continue
        state.transition_bar_history_5m = _recent_5m(bars, idx)

        candidate = canonical_candidate(state, cfg, None)
        if candidate is None:
            counts["NO_CANDIDATE"] += 1
            records.append({"bar_ts": row["bar_ts"], "status": "NO_CANDIDATE"})
            continue

        setup = candidate.setup
        planned_entry = float(row["entry"])
        entry_delta = float(setup.entry) - planned_entry
        if abs(entry_delta) > 0.25 + 1e-9:
            counts["ENTRY_DRIFT_GT_1T"] += 1
            candidate_mismatches.append(
                {
                    "bar_ts": row["bar_ts"],
                    "preserved_entry": planned_entry,
                    "canonical_entry": float(setup.entry),
                    "delta": entry_delta,
                }
            )
            records.append({"bar_ts": row["bar_ts"], "status": "ENTRY_DRIFT_GT_1T"})
            continue
        if abs(entry_delta) > 1e-9:
            counts["ENTRY_FEED_DRIFT_1T"] += 1
            candidate_mismatches.append(
                {
                    "bar_ts": row["bar_ts"],
                    "preserved_entry": planned_entry,
                    "canonical_entry": float(setup.entry),
                    "delta": entry_delta,
                }
            )
        else:
            counts["ENTRY_EXACT"] += 1
        counts["ENTRY_PARITY_WITHIN_1T"] += 1

        if halted_drawdown:
            counts["SKIP_DRAWDOWN_HALTED"] += 1
            records.append({"bar_ts": row["bar_ts"], "status": "SKIP_DRAWDOWN_HALTED"})
            continue

        if busy_until is not None and decision_ts <= busy_until:
            counts["SKIP_OPEN_POSITION"] += 1
            records.append({"bar_ts": row["bar_ts"], "status": "SKIP_OPEN_POSITION"})
            continue

        if day != current_day:
            current_day = day
            daily = _new_day_state(balance=balance, peak=peak, date_value=day)
        assert daily is not None

        result = open_research_position(
            state=state,
            cfg=cfg,
            daily_state=daily,
            market_price=float((row.get("gates") or {}).get("decision_close")),
            entry_slippage_ticks=slippage_ticks,
        )

        if result.status == "RISK_REJECTED":
            counts["RISK_REJECTED"] += 1
            failed = str((result.risk or {}).get("failed_rule") or "UNKNOWN")
            risk_failures[failed] += 1
            records.append(
                {
                    "bar_ts": row["bar_ts"],
                    "status": result.status,
                    "failed_rule": failed,
                    "reason": result.reason,
                }
            )
            continue

        if result.status == "NO_FILL":
            counts["NO_FILL"] += 1
            records.append({"bar_ts": row["bar_ts"], "status": "NO_FILL", "reason": result.reason})
            continue

        if result.status != "OPEN" or result.broker is None:
            counts["UNEXPECTED_OPEN_STATUS"] += 1
            records.append({"bar_ts": row["bar_ts"], "status": result.status})
            continue

        counts["FILLED"] += 1
        daily.trade_count += 1
        daily.session_trade_counts["new_york"] = int(daily.session_trade_counts.get("new_york", 0)) + 1
        daily.has_open_position = True

        path = _future_six(bars, idx)
        resolution = resolve_six_available_5m_bars(result.broker, path, exit_slippage_ticks=slippage_ticks)
        if resolution.status != "RESOLVED" or resolution.net_pnl_dollars is None:
            counts["UNRESOLVED"] += 1
            records.append(
                {
                    "bar_ts": row["bar_ts"],
                    "status": resolution.status,
                    "reason": resolution.exit_reason,
                }
            )
            break

        net = float(resolution.net_pnl_dollars)
        balance = round(balance + net, 2)
        peak = max(peak, balance)
        daily.account_balance = balance
        daily.account_peak_balance = peak
        daily.realized_pnl_dollars = round(daily.realized_pnl_dollars + net, 2)
        daily.has_open_position = False
        if net < 0:
            daily.consecutive_losses += 1
            counts["LOSS"] += 1
        elif net > 0:
            daily.consecutive_losses = 0
            counts["WIN"] += 1
        else:
            daily.consecutive_losses = 0
            counts["BREAKEVEN"] += 1

        exit_idx = idx + int(resolution.bars_consumed)
        if exit_idx >= len(bars.rows):
            counts["EXIT_INDEX_OOB"] += 1
            break
        busy_until = bars.rows[exit_idx]["_dt"]

        dd = peak - balance
        dd_pct = dd / peak if peak else 0.0
        if dd_pct > MAX_DRAWDOWN_PERCENT:
            halted_drawdown = True
            counts["DRAWDOWN_HALT"] += 1

        records.append(
            {
                "bar_ts": row["bar_ts"],
                "status": "RESOLVED",
                "exit_reason": resolution.exit_reason,
                "bars_consumed": resolution.bars_consumed,
                "net": net,
                "balance": balance,
                "peak": peak,
                "drawdown_pct": dd_pct,
            }
        )

    resolved = [r for r in records if r.get("status") == "RESOLVED"]
    pnls = [float(r["net"]) for r in resolved]
    midpoint = len(pnls) // 2
    gross_profit = sum(x for x in pnls if x > 0)
    gross_loss = -sum(x for x in pnls if x < 0)

    return {
        "lane": lane,
        "corpus": str(corpus),
        "eligible_candidates": len(rows),
        "counts": dict(counts),
        "risk_failures": dict(risk_failures),
        "entry_mismatch_sample": candidate_mismatches[:20],
        "economic": {
            "resolved_fills": len(resolved),
            "wins": sum(x > 0 for x in pnls),
            "losses": sum(x < 0 for x in pnls),
            "breakeven": sum(x == 0 for x in pnls),
            "net": round(sum(pnls), 2),
            "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss else None,
            "h1": round(sum(pnls[:midpoint]), 2),
            "h2": round(sum(pnls[midpoint:]), 2),
            "ending_balance": balance,
            "peak_balance": peak,
            "max_drawdown_dollars": round(max((float(r["peak"]) - float(r["balance"]) for r in resolved), default=0.0), 2),
            "max_drawdown_pct": round(max((float(r["drawdown_pct"]) for r in resolved), default=0.0), 6),
            "drawdown_halted": halted_drawdown,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, default=CANDIDATE_FILE)
    parser.add_argument("--data-root", type=Path, default=ROOT / "data")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--slippage-ticks", type=float, default=1.0)
    args = parser.parse_args()

    report = {
        "candidate_file": str(args.candidates),
        "candidate_sha256": _sha256(args.candidates),
        "contract": {
            "slippage_ticks_each_side": args.slippage_ticks,
            "starting_balance": STARTING_BALANCE,
            "daily_loss_limit": DAILY_LOSS_LIMIT,
            "max_drawdown_percent": MAX_DRAWDOWN_PERCENT,
            "max_trades_per_day": MAX_TRADES_PER_DAY,
            "runtime_activation": False,
            "external_broker_route": False,
        },
        "lanes": {
            lane: audit_lane(
                lane=lane,
                candidate_file=args.candidates,
                data_root=args.data_root,
                slippage_ticks=args.slippage_ticks,
            )
            for lane in LANES
        },
    }
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
