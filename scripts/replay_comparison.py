#!/usr/bin/env python3
"""
Compare first-match vs ranked vs ranked+profit-protection on the Polygon replay set.

Three modes per instrument:
  first_match      — current default (first qualifying strategy fires)
  ranked           — strategy_selection_mode="ranked"
  ranked_protect   — ranked + daily_profit_protect_threshold=$THRESHOLD

Each mode runs in an isolated log dir (never pollutes existing replay journals).
Results are cached in --out-dir so reruns skip already-completed modes.

Usage:
    python scripts/replay_comparison.py
    python scripts/replay_comparison.py --instruments MNQ
    python scripts/replay_comparison.py --instruments MES MNQ --threshold 300
    python scripts/replay_comparison.py --rerun   # force fresh run for all modes
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_config
from replay.replay_engine import ReplayEngine

DEFAULT_CANDLES = "data/replay_polygon"
DEFAULT_OUT_DIR = "logs/replay_comparison"
DEFAULT_THRESHOLD = 300.0
INSTRUMENTS = ["MES", "MNQ"]

MODES = ["first_match", "mod_pullback", "mod_all"]


# ─── Stats dataclass ──────────────────────────────────────────────────────────

@dataclass
class ModeStats:
    mode: str
    instrument: str
    days_run: int
    trades: int
    wins: int
    losses: int
    total_pnl: float
    gross_wins: float
    gross_losses: float
    days_at_goal: int
    skipped_by_gate: int
    max_drawdown: float          # deepest peak-to-trough dip of the equity curve ($)
    max_consec_losses: int       # longest run of consecutive losing trades
    strategy_counts: Dict[str, int]

    @property
    def resolved(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        return self.wins / self.resolved if self.resolved else 0.0

    @property
    def profit_factor(self) -> float:
        if self.gross_losses >= 0:
            return float("inf")
        return round(self.gross_wins / abs(self.gross_losses), 4)

    @property
    def expectancy(self) -> float:
        return round(self.total_pnl / self.resolved, 2) if self.resolved else 0.0


# ─── Replay runner ────────────────────────────────────────────────────────────

def _run_mode(
    candle_files: List[Path],
    mode: str,
    log_dir: Path,
    config,
) -> None:
    engine = ReplayEngine(config=config, log_dir=str(log_dir))
    total = len(candle_files)
    for i, candle_file in enumerate(candle_files, 1):
        print(f"  [{mode}] {i}/{total} {candle_file.name}", end="\r", flush=True)
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", candle_file.stem)
        date_hint = date_match.group(1) if date_match else None
        journal_path = log_dir / f"journal_{date_hint}.jsonl" if date_hint else None
        if journal_path and journal_path.exists():
            continue
        try:
            engine.run(candle_file, review_date=date_hint)
        except Exception as exc:
            print(f"\n  [{mode}] WARN {candle_file.name}: {exc}", file=sys.stderr)
    print(f"  [{mode}] done ({total} days)      ")


# ─── Stats collector ──────────────────────────────────────────────────────────

def _collect_stats(
    log_dir: Path,
    mode: str,
    instrument: str,
    goal_threshold: float,
) -> ModeStats:
    journals = sorted(log_dir.glob("journal_*.jsonl"))
    trades = wins = losses = skipped_by_gate = days_at_goal = 0
    total_pnl = gross_wins = gross_losses = 0.0
    strategy_counts: Dict[str, int] = defaultdict(int)

    # Risk metrics tracked across the chronological (date-sorted) trade sequence.
    equity = 0.0          # running cumulative P&L
    equity_peak = 0.0     # highest equity seen so far
    max_drawdown = 0.0    # deepest peak-to-trough dip ($, reported as a positive number)
    consec_losses = 0     # current losing streak
    max_consec_losses = 0

    for journal_path in journals:
        lines = [l for l in journal_path.read_text().splitlines() if l.strip()]
        entries = [json.loads(l) for l in lines]

        # Pre-index outcomes by instrument (in order)
        outcome_queue: Dict[str, List[dict]] = defaultdict(list)
        for e in entries:
            if e.get("type") == "OUTCOME":
                outcome_queue[e.get("instrument", "")].append(e)

        day_pnl = 0.0
        for e in entries:
            if e.get("type") == "OUTCOME":
                continue
            rc = e.get("risk_check") or {}
            decision = e.get("decision", "")

            # Count gate-blocked trades
            if (decision == "NO_TRADE" and
                    rc.get("result") == "REJECTED" and
                    rc.get("failed_rule") == "profit_protect_gate"):
                skipped_by_gate += 1
                continue

            if decision != "TRADE" or rc.get("result") != "APPROVED":
                continue

            strat = (e.get("setup") or {}).get("strategy", "unknown")
            strategy_counts[strat] += 1
            trades += 1

            instr = e.get("instrument", "")
            outcome_entry = outcome_queue[instr].pop(0) if outcome_queue[instr] else None
            result = (outcome_entry.get("outcome") or {}).get("result") if outcome_entry else None
            pnl = (outcome_entry.get("outcome") or {}).get("pnl_dollars") if outcome_entry else None
            pnl_val = float(pnl) if pnl is not None else 0.0

            if result == "WIN":
                wins += 1
                gross_wins += pnl_val
                consec_losses = 0
            elif result == "LOSS":
                losses += 1
                gross_losses += pnl_val
                consec_losses += 1
                max_consec_losses = max(max_consec_losses, consec_losses)

            # Update the equity curve per resolved trade (chronological).
            if result in ("WIN", "LOSS"):
                equity += pnl_val
                equity_peak = max(equity_peak, equity)
                max_drawdown = max(max_drawdown, equity_peak - equity)

            day_pnl += pnl_val

        total_pnl += day_pnl
        if day_pnl >= goal_threshold:
            days_at_goal += 1

    return ModeStats(
        mode=mode,
        instrument=instrument,
        days_run=len(journals),
        trades=trades,
        wins=wins,
        losses=losses,
        total_pnl=round(total_pnl, 2),
        gross_wins=round(gross_wins, 2),
        gross_losses=round(gross_losses, 2),
        days_at_goal=days_at_goal,
        skipped_by_gate=skipped_by_gate,
        max_drawdown=round(max_drawdown, 2),
        max_consec_losses=max_consec_losses,
        strategy_counts=dict(strategy_counts),
    )


# ─── Report printer ───────────────────────────────────────────────────────────

def _print_report(
    results: Dict[str, Dict[str, ModeStats]],
    goal_threshold: float,
) -> None:
    sep = "═" * 90
    dash = "─" * 90
    col_w = 22

    print(f"\n{sep}")
    print(f"  SELECTOR COMPARISON  ·  protect threshold ${goal_threshold:.0f}")
    print(sep)

    for instrument in INSTRUMENTS:
        if instrument not in results:
            continue
        modes_data = results[instrument]
        base = modes_data.get("first_match")
        print(f"\n  {instrument}")
        print(f"  {'─'*86}")
        header = f"  {'Metric':<28}" + "".join(f"  {m:<{col_w}}" for m in MODES)
        print(header)
        print(f"  {'─'*86}")

        def row(label: str, values: List[str]) -> None:
            print(f"  {label:<28}" + "".join(f"  {v:<{col_w}}" for v in values))

        def delta(cur: float, base_val: float, fmt: str = "+.2f") -> str:
            d = cur - base_val
            return f"({d:{fmt}})" if d != 0 else ""

        def pct_delta(cur: float, base_val: float) -> str:
            d = cur - base_val
            return f"({d:+.1f}pp)" if d != 0 else ""

        stats_list = [modes_data.get(m) for m in MODES]

        # Total P&L
        pnl_vals = []
        for i, s in enumerate(stats_list):
            if s is None:
                pnl_vals.append("—")
                continue
            base_pnl = base.total_pnl if base else s.total_pnl
            d = f" {delta(s.total_pnl, base_pnl, '+,.0f')}" if i > 0 and base else ""
            pnl_vals.append(f"${s.total_pnl:>10,.2f}{d}")
        row("Total P&L", pnl_vals)

        # Trades
        trade_vals = []
        for i, s in enumerate(stats_list):
            if s is None:
                trade_vals.append("—")
                continue
            base_t = base.trades if base else s.trades
            d = f" ({s.trades - base_t:+d})" if i > 0 and base else ""
            trade_vals.append(f"{s.trades}{d}")
        row("Trades", trade_vals)

        # Win rate
        wr_vals = []
        for i, s in enumerate(stats_list):
            if s is None:
                wr_vals.append("—")
                continue
            base_wr = base.win_rate if base else s.win_rate
            d = f" {pct_delta(s.win_rate * 100, base_wr * 100)}" if i > 0 and base else ""
            wr_vals.append(f"{s.win_rate * 100:.1f}%{d}")
        row("Win rate", wr_vals)

        # Profit factor
        pf_vals = []
        for i, s in enumerate(stats_list):
            if s is None:
                pf_vals.append("—")
                continue
            base_pf = base.profit_factor if base else s.profit_factor
            d = f" ({s.profit_factor - base_pf:+.4f})" if i > 0 and base and base_pf != float("inf") else ""
            pf_vals.append(f"{s.profit_factor:.4f}{d}")
        row("Profit factor", pf_vals)

        # Expectancy
        exp_vals = []
        for i, s in enumerate(stats_list):
            if s is None:
                exp_vals.append("—")
                continue
            base_exp = base.expectancy if base else s.expectancy
            d = f" ({s.expectancy - base_exp:+.2f})" if i > 0 and base else ""
            exp_vals.append(f"${s.expectancy:.2f}{d}")
        row("Expectancy/trade", exp_vals)

        # Days at goal
        goal_vals = []
        for i, s in enumerate(stats_list):
            if s is None:
                goal_vals.append("—")
                continue
            base_g = base.days_at_goal if base else s.days_at_goal
            d = f" ({s.days_at_goal - base_g:+d})" if i > 0 and base else ""
            goal_vals.append(f"{s.days_at_goal}/{s.days_run}{d}")
        row(f"Days ≥ ${goal_threshold:.0f}", goal_vals)

        # Max drawdown (lower is better — show worsening as the signed delta)
        dd_vals = []
        for i, s in enumerate(stats_list):
            if s is None:
                dd_vals.append("—")
                continue
            base_dd = base.max_drawdown if base else s.max_drawdown
            d = f" ({s.max_drawdown - base_dd:+,.0f})" if i > 0 and base else ""
            dd_vals.append(f"${s.max_drawdown:>9,.2f}{d}")
        row("Max drawdown", dd_vals)

        # Max consecutive losses (lower is better)
        mcl_vals = []
        for i, s in enumerate(stats_list):
            if s is None:
                mcl_vals.append("—")
                continue
            base_mcl = base.max_consec_losses if base else s.max_consec_losses
            d = f" ({s.max_consec_losses - base_mcl:+d})" if i > 0 and base else ""
            mcl_vals.append(f"{s.max_consec_losses}{d}")
        row("Max consec losses", mcl_vals)

        # Skipped by gate (only ranked_protect)
        gate_vals = []
        for s in stats_list:
            if s is None:
                gate_vals.append("—")
            elif s.mode == "ranked_protect":
                gate_vals.append(str(s.skipped_by_gate))
            else:
                gate_vals.append("—")
        row("Skipped by gate", gate_vals)

        print(f"  {'─'*86}")

        # Strategy mix
        all_strats = set()
        for s in stats_list:
            if s:
                all_strats.update(s.strategy_counts.keys())
        if all_strats:
            print(f"\n  Strategy mix:")
            for strat in sorted(all_strats, key=lambda s: -(modes_data.get("first_match") or
                    next(iter(modes_data.values()))).strategy_counts.get(s, 0)):
                counts = []
                for s in stats_list:
                    if s is None:
                        counts.append("  —")
                    else:
                        n = s.strategy_counts.get(strat, 0)
                        pct = n / s.trades * 100 if s.trades else 0
                        counts.append(f"{n:>4} ({pct:4.1f}%)")
                print(f"    {strat:<35}" + "  ".join(counts))

    print(f"\n{sep}\n")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candles",
        default=DEFAULT_CANDLES,
        help=f"Root dir containing per-instrument subdirs (default: {DEFAULT_CANDLES})",
    )
    parser.add_argument(
        "--instruments",
        nargs="+",
        default=INSTRUMENTS,
        metavar="INST",
        help="Instruments to compare (default: MES MNQ)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Daily profit-protect threshold in dollars (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"Root dir for mode journal caches (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Re-run all replay days even if cached journals exist",
    )
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass

    try:
        base_config = load_config()
    except Exception as exc:
        print(f"[comparison] Config error: {exc}", file=sys.stderr)
        return 1

    insts = args.instruments
    all_on = {i: True for i in insts}
    mode_configs = {
        # Baseline: current production gate (STRONG trend required, both walls).
        "first_match": base_config,
        # Admit MODERATE-PULLBACK only (confirmed-trend dip to ema9) past both walls.
        "mod_pullback": replace(
            base_config,
            allow_moderate_pullback=all_on,
        ),
        # Admit ALL MODERATE (pullback + early) — "trade any moderate trend".
        "mod_all": replace(
            base_config,
            allow_moderate_pullback=all_on,
            allow_moderate_early=all_on,
        ),
    }

    candles_root = Path(args.candles)
    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    results: Dict[str, Dict[str, ModeStats]] = {}

    for instrument in args.instruments:
        candle_dir = candles_root / instrument
        if not candle_dir.exists():
            print(f"[comparison] No candle dir for {instrument}: {candle_dir}", file=sys.stderr)
            continue

        candle_files = sorted(candle_dir.glob("*.jsonl"))
        if not candle_files:
            print(f"[comparison] No .jsonl files in {candle_dir}", file=sys.stderr)
            continue

        print(f"\n[comparison] {instrument} — {len(candle_files)} days")
        results[instrument] = {}

        for mode in MODES:
            log_dir = out_root / instrument / mode
            if args.rerun and log_dir.exists():
                import shutil
                shutil.rmtree(log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)

            _run_mode(candle_files, mode, log_dir, mode_configs[mode])
            stats = _collect_stats(log_dir, mode, instrument, args.threshold)
            results[instrument][mode] = stats

    if not results:
        print("[comparison] No results collected.", file=sys.stderr)
        return 1

    _print_report(results, args.threshold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
