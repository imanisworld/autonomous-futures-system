#!/usr/bin/env python3
"""
scripts/strategy_validation_report.py

Per-strategy deep validation pass over the verified Corpus v1 replay evidence
(main@a5434794e471137af83f6e5886b535fb9e3cfcd5 -- see scripts/corpus_v1_report.py
and memory/project_corpus_v1_clean_baseline_scope.md for the run this reads).

Read-only: consumes the already-generated journals under
logs/replay_corpus_v1/<INSTR>/journal_*.jsonl via adaptive.journal_reader
.JournalReader (the same #327/#332 identity-join path corpus_v1_report.py
uses -- no FIFO fallback, no re-derivation of trade pairing). Makes no
changes to any strategy, gate, replay, or runtime code.

For one strategy, breaks the corpus down by instrument, half, quarter,
direction, session, and reports profit factor, expectancy, average win/loss,
largest loss, max drawdown, longest loss streak, trade frequency, and a P&L
distribution -- the operator's "does the edge survive both instruments and
multiple periods" question, not just a single aggregate number.

Usage:
    python3 scripts/strategy_validation_report.py --strategy orb_reclaim
    python3 scripts/strategy_validation_report.py --strategy orb_reclaim --out scripts/validation_orb_reclaim.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adaptive.journal_reader import JournalReader  # noqa: E402

INSTRUMENTS = ("MNQ", "MES")
LOGS_BASE = "logs/replay_corpus_v1"

FULL_START, FULL_END = "2025-07-24", "2026-07-23"
H1 = ("2025-07-24", "2026-01-23")
H2 = ("2026-01-24", "2026-07-23")
QUARTERS = [
    ("Q1", "2025-07-24", "2025-10-23"),
    ("Q2", "2025-10-24", "2026-01-23"),
    ("Q3", "2026-01-24", "2026-04-23"),
    ("Q4", "2026-04-24", "2026-07-23"),
]


def _load_all(strategy: str) -> dict[str, list]:
    """All TradeRecords for `strategy`, per instrument, in file (chronological)
    order. A day's trades come out of _trades_for_day already in decision
    order (asian -> london -> new_york), and day files are iterated in
    sorted-date order, so each instrument's list is a valid chronological
    sequence for drawdown/streak purposes. Cross-instrument interleaving for
    the 'combined' equity curve is date-sorted only (same-day MNQ/MES order
    is arbitrary -- they are independent replay runs) and is flagged as such
    wherever it matters."""
    out: dict[str, list] = {}
    for instr in INSTRUMENTS:
        log_dir = Path(LOGS_BASE) / instr
        reader = JournalReader(log_dir)
        trades = []
        for path in sorted(log_dir.glob("journal_*.jsonl")):
            day = _date.fromisoformat(path.stem.replace("journal_", ""))
            for r in reader._trades_for_day(day):
                if r.strategy == strategy:
                    trades.append(r)
        out[instr] = trades
    return out


def _in_range(d: str, start: str, end: str) -> bool:
    return start <= d <= end


def _resolved(trades: list) -> list:
    return [t for t in trades if t.result in ("WIN", "LOSS")]


def _open(trades: list) -> list:
    return [t for t in trades if t.result is None and not t.unjoinable_legacy]


def _unjoinable(trades: list) -> list:
    return [t for t in trades if t.unjoinable_legacy]


def _core_stats(trades: list) -> dict:
    res = _resolved(trades)
    wins = [t for t in res if t.result == "WIN"]
    losses = [t for t in res if t.result == "LOSS"]
    gross_win = sum(t.pnl_dollars or 0.0 for t in wins)
    gross_loss = sum(t.pnl_dollars or 0.0 for t in losses)  # negative
    net = gross_win + gross_loss
    n_res = len(res)
    pf = None
    if gross_loss < 0:
        pf = round(gross_win / abs(gross_loss), 3)
    elif gross_win > 0:
        pf = float("inf")
    return {
        "attempts": len(trades),
        "resolved": n_res,
        "open": len(_open(trades)),
        "unjoinable": len(_unjoinable(trades)),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n_res, 4) if n_res else None,
        "gross_win": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(net, 2),
        "profit_factor": pf,
        "expectancy": round(net / n_res, 2) if n_res else None,
        "avg_win": round(gross_win / len(wins), 2) if wins else None,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else None,
        "largest_win": round(max((t.pnl_dollars for t in wins), default=0.0), 2) if wins else None,
        "largest_loss": round(min((t.pnl_dollars for t in losses), default=0.0), 2) if losses else None,
    }


def _drawdown_and_streaks(trades_in_order: list) -> dict:
    """Equity-curve max drawdown (peak-to-trough $) and longest consecutive
    loss/win streaks, over RESOLVED trades only, in the given chronological
    order."""
    res = [t for t in trades_in_order if t.result in ("WIN", "LOSS")]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    max_dd_peak_date = None
    max_dd_trough_date = None
    cur_peak_date = res[0].date if res else None

    cur_loss_streak = 0
    max_loss_streak = 0
    max_loss_streak_pnl = 0.0
    running_loss_streak_pnl = 0.0
    cur_win_streak = 0
    max_win_streak = 0

    for t in res:
        equity += t.pnl_dollars or 0.0
        if equity > peak:
            peak = equity
            cur_peak_date = t.date
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
            max_dd_peak_date = cur_peak_date
            max_dd_trough_date = t.date

        if t.result == "LOSS":
            cur_loss_streak += 1
            running_loss_streak_pnl += t.pnl_dollars or 0.0
            cur_win_streak = 0
            if cur_loss_streak > max_loss_streak:
                max_loss_streak = cur_loss_streak
                max_loss_streak_pnl = running_loss_streak_pnl
        else:
            cur_win_streak += 1
            cur_loss_streak = 0
            running_loss_streak_pnl = 0.0
            max_win_streak = max(max_win_streak, cur_win_streak)

    return {
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_peak_date": max_dd_peak_date,
        "max_drawdown_trough_date": max_dd_trough_date,
        "final_equity": round(equity, 2),
        "longest_loss_streak": max_loss_streak,
        "longest_loss_streak_pnl": round(max_loss_streak_pnl, 2),
        "longest_win_streak": max_win_streak,
    }


def _pnl_distribution(trades: list) -> dict:
    res = _resolved(trades)
    pnls = sorted(t.pnl_dollars or 0.0 for t in res)
    if not pnls:
        return {"n": 0}
    n = len(pnls)

    def pct(p: float) -> float:
        idx = min(n - 1, max(0, int(round(p * (n - 1)))))
        return round(pnls[idx], 2)

    buckets_edges = [-1000, -500, -300, -200, -100, -50, 0, 50, 100, 200, 300, 500, 1000, 10000]
    hist: dict[str, int] = {}
    for i in range(len(buckets_edges) - 1):
        lo, hi = buckets_edges[i], buckets_edges[i + 1]
        label = f"[{lo},{hi})"
        hist[label] = sum(1 for v in pnls if lo <= v < hi)

    return {
        "n": n,
        "min": round(pnls[0], 2),
        "p10": pct(0.10),
        "p25": pct(0.25),
        "median": pct(0.50),
        "p75": pct(0.75),
        "p90": pct(0.90),
        "max": round(pnls[-1], 2),
        "mean": round(statistics.mean(pnls), 2),
        "stdev": round(statistics.pstdev(pnls), 2) if n > 1 else 0.0,
        "histogram": hist,
    }


def _frequency(trades: list, start: str, end: str) -> dict:
    days_in_range = (_date.fromisoformat(end) - _date.fromisoformat(start)).days + 1
    weeks = days_in_range / 7.0
    n = len([t for t in trades if _in_range(t.date, start, end)])
    trading_days_hit = len({t.date for t in trades if _in_range(t.date, start, end)})
    return {
        "attempts": n,
        "distinct_days_with_a_trade": trading_days_hit,
        "attempts_per_week": round(n / weeks, 3) if weeks else None,
    }


def _breakdown(trades: list, key_fn, labels: list[str] | None = None) -> dict:
    groups: dict[str, list] = {}
    for t in trades:
        k = key_fn(t)
        groups.setdefault(k, []).append(t)
    keys = labels if labels else sorted(groups.keys())
    return {k: _core_stats(groups.get(k, [])) for k in keys if k in groups or labels is None}


def build_report(strategy: str) -> dict:
    by_instr = _load_all(strategy)
    all_trades = by_instr["MNQ"] + by_instr["MES"]
    # Combined chronological order for combined-level drawdown/streaks --
    # date-sorted, instrument as tiebreak. Same-day cross-instrument
    # ordering is arbitrary (independent replay runs); flagged in output.
    combined_order = sorted(all_trades, key=lambda t: (t.date, t.instrument))

    report: dict = {
        "strategy": strategy,
        "main_sha": "a5434794e471137af83f6e5886b535fb9e3cfcd5",
        "range": [FULL_START, FULL_END],
        "combined": {
            "all": _core_stats(all_trades),
            "drawdown_and_streaks": _drawdown_and_streaks(combined_order),
            "drawdown_note": "combined equity curve is date-sorted with MNQ/MES as an arbitrary same-day tiebreak -- treat per-instrument drawdown as authoritative",
            "pnl_distribution": _pnl_distribution(all_trades),
            "frequency": _frequency(all_trades, FULL_START, FULL_END),
            "by_half": {
                "H1": _core_stats([t for t in all_trades if _in_range(t.date, *H1)]),
                "H2": _core_stats([t for t in all_trades if _in_range(t.date, *H2)]),
            },
            "by_quarter": {
                label: _core_stats([t for t in all_trades if _in_range(t.date, qs, qe)])
                for label, qs, qe in QUARTERS
            },
            "by_direction": _breakdown(all_trades, lambda t: t.direction or "UNKNOWN"),
            "by_session": _breakdown(all_trades, lambda t: t.session or "UNKNOWN"),
        },
        "per_instrument": {},
    }

    for instr in INSTRUMENTS:
        trades = by_instr[instr]
        report["per_instrument"][instr] = {
            "all": _core_stats(trades),
            "drawdown_and_streaks": _drawdown_and_streaks(trades),
            "pnl_distribution": _pnl_distribution(trades),
            "frequency": _frequency(trades, FULL_START, FULL_END),
            "by_half": {
                "H1": _core_stats([t for t in trades if _in_range(t.date, *H1)]),
                "H2": _core_stats([t for t in trades if _in_range(t.date, *H2)]),
            },
            "by_quarter": {
                label: _core_stats([t for t in trades if _in_range(t.date, qs, qe)])
                for label, qs, qe in QUARTERS
            },
            "by_direction": _breakdown(trades, lambda t: t.direction or "UNKNOWN"),
            "by_session": _breakdown(trades, lambda t: t.session or "UNKNOWN"),
        }

    return report


def _fmt(v, money: bool = False) -> str:
    if v is None:
        return "—"
    if v == float("inf"):
        return "∞"
    if money:
        return f"${v:,.0f}"
    if isinstance(v, float) and -1 <= v <= 1:
        return f"{v * 100:.1f}%"
    return str(v)


def print_markdown(report: dict) -> None:
    strat = report["strategy"]
    print(f"\n# Strategy validation: {strat}")
    print(f"main@{report['main_sha']}  range {report['range'][0]}..{report['range'][1]}\n")

    c = report["combined"]["all"]
    print("## Combined (both instruments, full period)")
    print(f"attempts={c['attempts']} resolved={c['resolved']} open={c['open']} unjoinable={c['unjoinable']} "
          f"WR={_fmt(c['win_rate'])} PF={_fmt(c['profit_factor'])} net={_fmt(c['net_pnl'], True)} "
          f"expectancy={_fmt(c['expectancy'], True)} avg_win={_fmt(c['avg_win'], True)} avg_loss={_fmt(c['avg_loss'], True)} "
          f"largest_loss={_fmt(c['largest_loss'], True)}")
    dd = report["combined"]["drawdown_and_streaks"]
    print(f"max_drawdown={_fmt(dd['max_drawdown'], True)} ({dd['max_drawdown_peak_date']}->{dd['max_drawdown_trough_date']}) "
          f"longest_loss_streak={dd['longest_loss_streak']} ({_fmt(dd['longest_loss_streak_pnl'], True)}) "
          f"longest_win_streak={dd['longest_win_streak']}")
    freq = report["combined"]["frequency"]
    print(f"frequency: {freq['attempts']} attempts / {freq['attempts_per_week']} per week / "
          f"{freq['distinct_days_with_a_trade']} distinct days")

    print("\n## Per instrument")
    print("| Instrument | Attempts | Resolved | Open | WR | PF | Net P&L | Expectancy | Avg Win | Avg Loss | Largest Loss | Max DD |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for instr in INSTRUMENTS:
        s = report["per_instrument"][instr]["all"]
        dd = report["per_instrument"][instr]["drawdown_and_streaks"]
        print(f"| {instr} | {s['attempts']} | {s['resolved']} | {s['open']} | {_fmt(s['win_rate'])} | {_fmt(s['profit_factor'])} "
              f"| {_fmt(s['net_pnl'], True)} | {_fmt(s['expectancy'], True)} | {_fmt(s['avg_win'], True)} | {_fmt(s['avg_loss'], True)} "
              f"| {_fmt(s['largest_loss'], True)} | {_fmt(dd['max_drawdown'], True)} |")

    print("\n## H1 vs H2 (combined + per instrument)")
    print("| Scope | Half | Attempts | Resolved | WR | PF | Net P&L | Expectancy |")
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    for scope_label, block in [("Combined", report["combined"])] + [(i, report["per_instrument"][i]) for i in INSTRUMENTS]:
        for half in ("H1", "H2"):
            s = block["by_half"][half]
            print(f"| {scope_label} | {half} | {s['attempts']} | {s['resolved']} | {_fmt(s['win_rate'])} | {_fmt(s['profit_factor'])} "
                  f"| {_fmt(s['net_pnl'], True)} | {_fmt(s['expectancy'], True)} |")

    print("\n## Quarterly (combined + per instrument)")
    print("| Scope | Quarter | Attempts | Resolved | WR | PF | Net P&L | Expectancy |")
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    for scope_label, block in [("Combined", report["combined"])] + [(i, report["per_instrument"][i]) for i in INSTRUMENTS]:
        for label, _, _ in QUARTERS:
            s = block["by_quarter"][label]
            print(f"| {scope_label} | {label} | {s['attempts']} | {s['resolved']} | {_fmt(s['win_rate'])} | {_fmt(s['profit_factor'])} "
                  f"| {_fmt(s['net_pnl'], True)} | {_fmt(s['expectancy'], True)} |")

    print("\n## Direction (combined + per instrument)")
    print("| Scope | Direction | Attempts | Resolved | WR | PF | Net P&L | Expectancy |")
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    for scope_label, block in [("Combined", report["combined"])] + [(i, report["per_instrument"][i]) for i in INSTRUMENTS]:
        for direction, s in block["by_direction"].items():
            print(f"| {scope_label} | {direction} | {s['attempts']} | {s['resolved']} | {_fmt(s['win_rate'])} | {_fmt(s['profit_factor'])} "
                  f"| {_fmt(s['net_pnl'], True)} | {_fmt(s['expectancy'], True)} |")

    print("\n## Session (combined + per instrument)")
    print("| Scope | Session | Attempts | Resolved | WR | PF | Net P&L | Expectancy |")
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    for scope_label, block in [("Combined", report["combined"])] + [(i, report["per_instrument"][i]) for i in INSTRUMENTS]:
        for session, s in block["by_session"].items():
            print(f"| {scope_label} | {session} | {s['attempts']} | {s['resolved']} | {_fmt(s['win_rate'])} | {_fmt(s['profit_factor'])} "
                  f"| {_fmt(s['net_pnl'], True)} | {_fmt(s['expectancy'], True)} |")

    print("\n## P&L distribution (combined, resolved trades)")
    dist = report["combined"]["pnl_distribution"]
    print(f"n={dist['n']} min={_fmt(dist['min'], True)} p10={_fmt(dist['p10'], True)} p25={_fmt(dist['p25'], True)} "
          f"median={_fmt(dist['median'], True)} p75={_fmt(dist['p75'], True)} p90={_fmt(dist['p90'], True)} max={_fmt(dist['max'], True)} "
          f"mean={_fmt(dist['mean'], True)} stdev={_fmt(dist['stdev'], True)}")
    print("| Bucket | Count |")
    print("|---|---:|")
    for label, count in dist["histogram"].items():
        if count:
            print(f"| {label} | {count} |")


def main() -> int:
    parser = argparse.ArgumentParser(description="Deep per-strategy validation breakdown over Corpus v1")
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--out", default=None, help="Optional JSON output path")
    args = parser.parse_args()

    report = build_report(args.strategy)
    print_markdown(report)

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
        print(f"\n[strategy_validation] wrote {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
