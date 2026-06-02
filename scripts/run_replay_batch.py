#!/usr/bin/env python3
"""
scripts/run_replay_batch.py

Batch replay runner — scans a directory for JSONL candle files, runs each
through the replay engine, and prints a per-strategy aggregate breakdown.

Usage:
    python scripts/run_replay_batch.py                        # scans data/replay/
    python scripts/run_replay_batch.py --candles data/my/    # custom directory
    python scripts/run_replay_batch.py --candles data/replay/2026-05-*.jsonl

Outputs:
    - logs/replay/journal_YYYY-MM-DD.jsonl      per-day journal
    - logs/replay/replay_report_YYYY-MM-DD.md   per-day report
    - logs/replay/multi_day_replay_report.md    aggregate report
    - Per-strategy breakdown printed to stdout
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Ensure repo root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import load_config
from replay.replay_engine import ReplayEngine


def _find_candle_files(directory: str | Path) -> list[Path]:
    base = Path(directory)
    if not base.exists():
        print(f"[batch] Directory not found: {base}", file=sys.stderr)
        return []
    files = sorted(base.glob("*.jsonl"))
    if not files:
        print(f"[batch] No .jsonl files found in {base}", file=sys.stderr)
    return files


def _strategy_breakdown(log_dir: Path) -> None:
    """Read all replay journals and print per-strategy stats."""
    from collections import defaultdict

    journals = sorted(log_dir.glob("journal_*.jsonl"))
    if not journals:
        print("\n[batch] No replay journals found — nothing to summarise.")
        return

    trades: list[dict] = []
    for path in journals:
        date = path.stem.replace("journal_", "")
        entries = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

        outcome_map: dict[str, list[dict]] = defaultdict(list)
        for e in entries:
            if e.get("type") == "OUTCOME":
                outcome_map[e.get("instrument", "")].append(e)

        for e in entries:
            rc = e.get("risk_check") or {}
            if e.get("decision") == "TRADE" and rc.get("result") == "APPROVED":
                instr = e.get("instrument", "")
                out = outcome_map[instr].pop(0) if outcome_map[instr] else None
                result = (out.get("outcome") or {}).get("result") if out else None
                pnl = (out.get("outcome") or {}).get("pnl_dollars") if out else None
                trades.append({
                    "date": date,
                    "strategy": (e.get("setup") or {}).get("strategy", "unknown"),
                    "result": result,
                    "pnl": float(pnl) if pnl is not None else None,
                    "session": e.get("session"),
                    "direction": (e.get("setup") or {}).get("direction"),
                    "market_condition": e.get("market_condition"),
                })

    by_strat: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "open": 0, "pnl": 0.0})
    for t in trades:
        s = t["strategy"]
        if t["result"] == "WIN":
            by_strat[s]["wins"] += 1
            by_strat[s]["pnl"] += t["pnl"] or 0
        elif t["result"] == "LOSS":
            by_strat[s]["losses"] += 1
            by_strat[s]["pnl"] += t["pnl"] or 0
        else:
            by_strat[s]["open"] += 1

    total_resolved = sum(d["wins"] + d["losses"] for d in by_strat.values())

    print(f"\n{'─' * 88}")
    print(f"  STRATEGY EDGE REPORT  ·  {len(journals)} days  ·  {len(trades)} approved trades  ·  {total_resolved} resolved")
    print(f"{'─' * 88}")
    print(f"  {'Strategy':<35} {'N':>5} {'Wins':>5} {'Loss':>5} {'Open':>5}  {'WR%':>6}  {'P&L':>10}  {'Expect':>8}  {'Sample'}")
    print(f"  {'─'*35} {'─'*5} {'─'*5} {'─'*5} {'─'*5}  {'─'*6}  {'─'*10}  {'─'*8}  {'─'*10}")
    for strat, d in sorted(by_strat.items(), key=lambda x: -(x[1]["wins"] + x[1]["losses"])):
        resolved = d["wins"] + d["losses"]
        total = resolved + d["open"]
        wr = d["wins"] / resolved * 100 if resolved else 0.0
        exp = d["pnl"] / resolved if resolved else 0.0
        # Sample sufficiency flag
        if resolved < 10:
            flag = "⚠ TOO SMALL"
        elif resolved < 30:
            flag = "~ BUILDING"
        else:
            flag = "✓ SUFFICIENT"
        print(f"  {strat:<35} {total:>5} {d['wins']:>5} {d['losses']:>5} {d['open']:>5}  {wr:>6.1f}  {d['pnl']:>10.2f}  {exp:>8.2f}  {flag}")
    print(f"{'─' * 88}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch replay runner")
    parser.add_argument(
        "--candles",
        default="data/replay",
        help="Directory containing .jsonl candle files (default: data/replay/)",
    )
    parser.add_argument(
        "--log-dir",
        default="logs/replay",
        help="Output log directory (default: logs/replay/)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Re-run all days even if a journal already exists",
    )
    args = parser.parse_args()

    candle_files = _find_candle_files(args.candles)
    if not candle_files:
        return 1

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        config = load_config()
    except Exception as exc:
        print(f"[batch] Config error: {exc}", file=sys.stderr)
        return 1

    engine = ReplayEngine(config=config, log_dir=str(log_dir))

    skipped = 0
    ran = 0
    for candle_file in candle_files:
        # Derive the date from the filename or first candle timestamp
        stem = candle_file.stem  # e.g. "CME_MINI_MNQ1!_5_2026-03-15" or "2026-03-15"
        # Try to extract a YYYY-MM-DD from the stem
        import re
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", stem)
        date_hint = date_match.group(1) if date_match else None

        # Skip if journal already exists and --fresh not set
        if not args.fresh and date_hint:
            existing = log_dir / f"journal_{date_hint}.jsonl"
            if existing.exists():
                skipped += 1
                continue

        print(f"[batch] Running {candle_file.name} ...", end=" ", flush=True)
        try:
            report = engine.run(candle_file, review_date=date_hint)
            print(
                f"trades={report.approved_trades} "
                f"wins={report.wins} losses={report.losses} "
                f"pnl=${report.realized_pnl_dollars:.2f}"
            )
            ran += 1
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)

    if skipped:
        print(f"[batch] Skipped {skipped} already-processed day(s). Use --fresh to re-run.")

    print(f"[batch] Ran {ran} day(s). Generating strategy breakdown...")
    _strategy_breakdown(log_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
