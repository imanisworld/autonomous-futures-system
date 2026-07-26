#!/usr/bin/env python3
"""
scripts/corpus_v1_report.py

Aggregates the "Corpus v1 clean baseline" replay evidence run (MNQ + MES,
2025-07-24 -> 2026-07-23, replayed at main@a543479 with the current
production config -- realistic stop-first same-bar fills, commissions +
slippage included, strategy_status gate applied as-is, no strategy changes).
See memory/project_corpus_v1_clean_baseline_scope.md for the authorized
scope this reproduces.

Expects instrument journals under:
    <logs-base>/<INSTR>/journal_YYYY-MM-DD.jsonl
(produced by scripts/run_replay_batch.py --candles data/replay_corpus_v1/<INSTR>)

Emits:
    scripts/corpus_v1_results.json     full machine-readable results
    scripts/corpus_v1_raw_trades.jsonl one line per trade (resolved, open, or
                                        unjoinable_legacy -- see below)
    markdown summary tables on stdout

Trade pairing reuses adaptive.journal_reader.JournalReader._trades_for_day --
the same exact-paper_order_id identity join #327 fixed for the live journal
path, no FIFO fallback -- rather than maintaining an independent parser. An
earlier version of this script paired approved TRADE decisions against
type=OUTCOME rows via a positional per-instrument FIFO queue, reintroducing
the pre-#327 defect. Applying the identity join here first proved the then-
current Corpus v1 journals carried no usable identity at all (PR #332 traced
the root cause to replay/replay_engine.py never forwarding paper_order_id
into the journal) -- every trade reported unjoinable_legacy, the honest
result of a fail-closed join against journals that didn't carry the
identity. PR #332 fixed replay_engine.py itself and this corpus was
regenerated end to end (fresh replay run, same already-downloaded candle
data, no new Polygon pull) at main@a543479; every trade below now resolves
with a real, verified paper_order_id -- 0 unjoinable across both
instruments. See memory/project_corpus_v1_clean_baseline_scope.md and
memory/project_replay_identity_propagation_pr332.md for the full trace.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from datetime import date as _date

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adaptive.journal_reader import JournalReader  # noqa: E402

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


def _load_trades(log_dir: Path) -> list[dict]:
    """Approved trades for every day file in log_dir, via JournalReader's
    exact-paper_order_id identity join (#327) -- no FIFO fallback. A TRADE
    row with no paper_order_id at all is reported unjoinable_legacy, never
    guessed onto an unrelated OUTCOME row."""
    reader = JournalReader(log_dir)
    trades: list[dict] = []
    for path in sorted(log_dir.glob("journal_*.jsonl")):
        day = _date.fromisoformat(path.stem.replace("journal_", ""))
        for record in reader._trades_for_day(day):
            trades.append({
                "date": record.date,
                "instrument": record.instrument,
                "strategy": record.strategy,
                "result": record.result,
                "pnl": record.pnl_dollars,
                "unjoinable_legacy": record.unjoinable_legacy,
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
    unjoinable = sum(1 for t in trades if t["unjoinable_legacy"])
    # Genuinely still open: carried a real paper_order_id but no OUTCOME row
    # has arrived yet. Distinct from unjoinable -- this bucket is expected to
    # be a handful of trades at most (the run's tail end), never the bulk.
    open_with_identity = sum(
        1 for t in trades if not t["unjoinable_legacy"] and t["result"] is None
    )
    pnl = sum(t["pnl"] or 0.0 for t in trades)
    gross_win = sum(t["pnl"] or 0.0 for t in trades if t["result"] == "WIN")
    gross_loss = sum(t["pnl"] or 0.0 for t in trades if t["result"] == "LOSS")  # negative
    if gross_loss < 0:
        profit_factor = round(gross_win / abs(gross_loss), 3)
    elif gross_win > 0:
        profit_factor = float("inf")
    else:
        profit_factor = None
    return {
        "attempts": attempts,
        "wins": wins,
        "losses": losses,
        "resolved": resolved,
        "open_with_identity": open_with_identity,
        "unjoinable_legacy": unjoinable,
        "win_rate": round(wins / resolved, 4) if resolved else None,
        "gross_win": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": profit_factor,
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
        trades = _load_trades(log_dir)
        no_trade_rows = _load_no_trade(entries)
        all_trades.extend(trades)
        all_no_trade.extend(no_trade_rows)
        per_instrument[instr] = _splits(trades, no_trade_rows)

    results = {
        "meta": {
            "main_sha": "a5434794e471137af83f6e5886b535fb9e3cfcd5",
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
    print("| Instrument | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for instr in INSTRUMENTS:
        a = per_instrument.get(instr, {}).get("full_period", {}).get("all", {})
        if a:
            print(f"| {instr} | {a['attempts']} | {a['resolved']} | {a['unjoinable_legacy']} "
                  f"| {_fmt(a['win_rate'])} | {_fmt(a['net_pnl'], True)} | {_fmt(a['expectancy'], True)} |")
    ac = results["combined"]["full_period"]["all"]
    print(f"| COMBINED | {ac['attempts']} | {ac['resolved']} | {ac['unjoinable_legacy']} "
          f"| {_fmt(ac['win_rate'])} | {_fmt(ac['net_pnl'], True)} | {_fmt(ac['expectancy'], True)} |")

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
    print("| Strategy | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    per_strat = results["combined"]["full_period"]["per_strategy"]
    for strat, s in sorted(per_strat.items(), key=lambda x: -x[1]["attempts"]):
        print(f"| {strat} | {s['attempts']} | {s['resolved']} | {s['unjoinable_legacy']} "
              f"| {_fmt(s['win_rate'])} | {_fmt(s['net_pnl'], True)} | {_fmt(s['expectancy'], True)} |")

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
