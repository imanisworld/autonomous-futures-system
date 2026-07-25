#!/usr/bin/env python3
"""
scripts/corpus_v1_report.py

Aggregates the "Corpus v1 clean baseline" replay evidence run (MNQ + MES,
2025-07-24 -> 2026-07-23, replayed at main@6628946 with the current
production config -- realistic stop-first same-bar fills, commissions +
slippage included, strategy_status gate applied as-is, no strategy changes).
See memory/project_corpus_v1_clean_baseline_scope.md for the authorized
scope this reproduces.

Expects instrument journals under:
    <logs-base>/<INSTR>/journal_YYYY-MM-DD.jsonl
(produced by scripts/run_replay_batch.py --candles data/replay_corpus_v1/<INSTR>)

Emits:
    scripts/corpus_v1_results.json     full machine-readable results
    scripts/corpus_v1_raw_trades.jsonl one line per approved/resolved trade
    markdown summary tables on stdout

Trade pairing mirrors run_replay_batch._strategy_breakdown and
ioc_baseline_622d_analysis._load_trades: approved TRADE decision rows are
matched FIFO against type=OUTCOME rows within each day.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

INSTRUMENTS = ("MNQ", "MES")

FULL_START, FULL_END = "2025-07-24", "2026-07-23"
H1 = ("2025-07-24", "2026-01-23")
H2 = ("2026-01-24", "2026-07-23")
QUARTERS = [
    ("Q1", "2025-07-24", "2025-10-23"),
    ("Q2", "2025-10-24", "2026-01-23"),
    ("Q3", "2026-01-24", "2026-04-23"),
    ("Q4", "2026-04-24", "2026-07-23"),
]


def _load_journal_entries(log_dir: Path) -> list[dict]:
    entries = []
    for path in sorted(log_dir.glob("journal_*.jsonl")):
        date = path.stem.replace("journal_", "")
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            e["_date"] = date
            entries.append(e)
    return entries


def _load_trades(entries: list[dict]) -> list[dict]:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_date[e["_date"]].append(e)

    trades: list[dict] = []
    for date, day_entries in sorted(by_date.items()):
        outcome_map: dict[str, list[dict]] = defaultdict(list)
        for e in day_entries:
            if e.get("type") == "OUTCOME":
                outcome_map[e.get("instrument", "")].append(e)
        for e in day_entries:
            rc = e.get("risk_check") or {}
            if e.get("decision") == "TRADE" and rc.get("result") == "APPROVED":
                instr = e.get("instrument", "")
                out = outcome_map[instr].pop(0) if outcome_map[instr] else None
                oc = (out or {}).get("outcome") or {}
                trades.append({
                    "date": date,
                    "instrument": instr,
                    "strategy": (e.get("setup") or {}).get("strategy", "unknown"),
                    "result": oc.get("result"),
                    "exit_reason": oc.get("exit_reason"),
                    "pnl": float(oc["pnl_dollars"]) if oc.get("pnl_dollars") is not None else None,
                })
    return trades


def _load_no_trade(entries: list[dict]) -> list[dict]:
    """Every NO_TRADE/RISK_REJECTED decision row -- the why-no-trade evidence.
    A bar with multiple failed_gates tallies each gate once."""
    rows = []
    for e in entries:
        d = e.get("decision")
        if d not in ("NO_TRADE", "RISK_REJECTED"):
            continue
        rows.append({
            "date": e["_date"],
            "instrument": e.get("instrument", ""),
            "decision": d,
            "strategy": (e.get("setup") or {}).get("strategy") or "no_candidate",
            "failed_gates": e.get("failed_gates") or ["UNSPECIFIED"],
        })
    return rows


def _in_range(date: str, start: str, end: str) -> bool:
    return start <= date <= end


def _stats(trades: list[dict]) -> dict:
    attempts = len(trades)
    wins = sum(1 for t in trades if t["result"] == "WIN")
    losses = sum(1 for t in trades if t["result"] == "LOSS")
    resolved = wins + losses
    pnl = sum(t["pnl"] or 0.0 for t in trades)
    return {
        "attempts": attempts,
        "wins": wins,
        "losses": losses,
        "resolved": resolved,
        "open": attempts - resolved,
        "win_rate": round(wins / resolved, 4) if resolved else None,
        "net_pnl": round(pnl, 2),
        "expectancy": round(pnl / resolved, 2) if resolved else None,
    }


def _why_no_trade(rows: list[dict]) -> dict:
    by_gate: dict[str, int] = defaultdict(int)
    by_decision: dict[str, int] = defaultdict(int)
    by_strategy: dict[str, int] = defaultdict(int)
    for r in rows:
        by_decision[r["decision"]] += 1
        by_strategy[r["strategy"]] += 1
        for g in r["failed_gates"]:
            by_gate[g] += 1
    return {
        "total": len(rows),
        "by_decision": dict(sorted(by_decision.items(), key=lambda x: -x[1])),
        "by_failed_gate": dict(sorted(by_gate.items(), key=lambda x: -x[1])),
        "by_blocked_strategy": dict(sorted(by_strategy.items(), key=lambda x: -x[1])),
    }


def _period_block(trades: list[dict], no_trade_rows: list[dict], start: str, end: str) -> dict:
    t = [x for x in trades if _in_range(x["date"], start, end)]
    nt = [x for x in no_trade_rows if _in_range(x["date"], start, end)]
    per_strategy = {
        strat: _stats([x for x in t if x["strategy"] == strat])
        for strat in sorted({x["strategy"] for x in t})
    }
    return {
        "range": [start, end],
        "all": _stats(t),
        "per_strategy": per_strategy,
        "why_no_trade": _why_no_trade(nt),
    }


def _splits(trades: list[dict], no_trade_rows: list[dict]) -> dict:
    return {
        "full_period": _period_block(trades, no_trade_rows, FULL_START, FULL_END),
        "h1": _period_block(trades, no_trade_rows, *H1),
        "h2": _period_block(trades, no_trade_rows, *H2),
        "quarterly": {
            label: _period_block(trades, no_trade_rows, qstart, qend)
            for label, qstart, qend in QUARTERS
        },
    }


def _fmt(v, money: bool = False) -> str:
    if v is None:
        return "—"
    if money:
        return f"${v:,.0f}"
    if isinstance(v, float):
        return f"{v * 100:.1f}%"
    return str(v)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate the Corpus v1 clean-baseline replay run")
    parser.add_argument("--logs-base", default="logs/replay_corpus_v1",
                         help="Directory holding <INSTR>/journal_*.jsonl")
    parser.add_argument("--out", default="scripts/corpus_v1_results.json", help="Output JSON path")
    args = parser.parse_args()

    base = Path(args.logs_base)
    all_trades: list[dict] = []
    all_no_trade: list[dict] = []
    per_instrument: dict = {}

    for instr in INSTRUMENTS:
        log_dir = base / instr
        if not log_dir.exists():
            print(f"[corpus_v1] MISSING log dir: {log_dir}")
            continue
        entries = _load_journal_entries(log_dir)
        trades = _load_trades(entries)
        no_trade_rows = _load_no_trade(entries)
        all_trades.extend(trades)
        all_no_trade.extend(no_trade_rows)
        per_instrument[instr] = _splits(trades, no_trade_rows)

    results = {
        "meta": {
            "main_sha": "662894654a9edaf2ae66673a34f340966245bc73",
            "instruments": list(INSTRUMENTS),
            "range": [FULL_START, FULL_END],
        },
        "per_instrument": per_instrument,
        "combined": _splits(all_trades, all_no_trade),
        "raw_trade_count": len(all_trades),
        "raw_no_trade_count": len(all_no_trade),
    }

    Path(args.out).write_text(json.dumps(results, indent=2) + "\n")
    print(f"[corpus_v1] wrote {args.out}")

    raw_path = Path(args.out).with_name("corpus_v1_raw_trades.jsonl")
    with raw_path.open("w") as f:
        for t in sorted(all_trades, key=lambda x: (x["date"], x["instrument"])):
            f.write(json.dumps(t) + "\n")
    print(f"[corpus_v1] wrote {raw_path}")

    print("\n=== FULL PERIOD (2025-07-24 -> 2026-07-23) ===")
    print("| Instrument | Attempts | Resolved | WR | Net P&L | Expectancy |")
    print("|---|---:|---:|---:|---:|---:|")
    for instr in INSTRUMENTS:
        a = per_instrument.get(instr, {}).get("full_period", {}).get("all", {})
        if a:
            print(f"| {instr} | {a['attempts']} | {a['resolved']} | {_fmt(a['win_rate'])} "
                  f"| {_fmt(a['net_pnl'], True)} | {_fmt(a['expectancy'], True)} |")
    ac = results["combined"]["full_period"]["all"]
    print(f"| COMBINED | {ac['attempts']} | {ac['resolved']} | {_fmt(ac['win_rate'])} "
          f"| {_fmt(ac['net_pnl'], True)} | {_fmt(ac['expectancy'], True)} |")

    print("\n=== H1 vs H2 (combined) ===")
    print("| Half | Attempts | Resolved | WR | Net P&L |")
    print("|---|---:|---:|---:|---:|")
    for label, block in (
        (f"H1 {H1[0]}..{H1[1]}", results["combined"]["h1"]),
        (f"H2 {H2[0]}..{H2[1]}", results["combined"]["h2"]),
    ):
        a = block["all"]
        print(f"| {label} | {a['attempts']} | {a['resolved']} | {_fmt(a['win_rate'])} | {_fmt(a['net_pnl'], True)} |")

    print("\n=== Quarterly (combined) ===")
    print("| Quarter | Range | Attempts | Resolved | WR | Net P&L |")
    print("|---|---|---:|---:|---:|---:|")
    for label, qstart, qend in QUARTERS:
        a = results["combined"]["quarterly"][label]["all"]
        print(f"| {label} | {qstart}..{qend} | {a['attempts']} | {a['resolved']} | {_fmt(a['win_rate'])} | {_fmt(a['net_pnl'], True)} |")

    print("\n=== Per-strategy (full period, combined) ===")
    print("| Strategy | Attempts | Resolved | WR | Net P&L | Expectancy |")
    print("|---|---:|---:|---:|---:|---:|")
    per_strat = results["combined"]["full_period"]["per_strategy"]
    for strat, s in sorted(per_strat.items(), key=lambda x: -x[1]["attempts"]):
        print(f"| {strat} | {s['attempts']} | {s['resolved']} | {_fmt(s['win_rate'])} "
              f"| {_fmt(s['net_pnl'], True)} | {_fmt(s['expectancy'], True)} |")

    print("\n=== Why-no-trade (full period, combined, top gates) ===")
    wnt = results["combined"]["full_period"]["why_no_trade"]
    print(f"Total no-trade/risk-rejected decision rows: {wnt['total']}")
    print("| Failed gate | Count |")
    print("|---|---:|")
    for gate, count in list(wnt["by_failed_gate"].items())[:15]:
        print(f"| {gate} | {count} |")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
