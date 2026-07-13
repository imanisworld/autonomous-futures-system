#!/usr/bin/env python3
"""MNQ 5-minute impulse -> pullback -> continuation replay study.

Follow-up to the REJECTED structural-level break/retest study (PR #271).
Walks every day of data/replay_polygon_5m/MNQ (621 daily files) bar by bar,
using ONLY strictly-prior, already-closed bars as detector history (no
lookahead). Resolves ACCEPTED candidates forward with
execution.paper_broker.PaperBroker (pessimistic both-hit, fixed R-multiple
target -- never a mapped level), using 2-tick adverse slippage on entry from
the start (per the operator's explicit instruction: "use realistic 2-tick or
worse slippage from the beginning"), $1.48 round-trip commission. Long and
short reported separately; overnight/premarket/rth reported separately;
R-multiple swept across {1.5, 2.0, 3.0} per the operator's suggested range.

Reproduce: python3 scripts/mnq_5m_impulse_pullback_continuation_study.py
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from research.mnq_5m_impulse_pullback_continuation import detect_candidates  # noqa: E402
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.paper_broker import NextBarOHLC, PaperBroker  # noqa: E402

FINE_ROOT = REPO / "data/replay_polygon_5m/MNQ"
RESULTS = REPO / "scripts/mnq_5m_impulse_pullback_continuation_results.json"
TICK = 0.25
COMMISSION_RT = 1.48
SLIPPAGE_TICKS = 2.0  # realistic from the start, per operator instruction
STRUCTURE_LOOKBACK_BARS = 20  # 100 minutes -- generous vs. an 8-bar max pullback
R_MULTIPLES = (1.5, 2.0, 3.0)


def load_day(path: Path) -> list:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def resolve(candidate: dict, bars_after: list) -> tuple:
    direction = candidate["direction"].upper()
    slip = TICK * SLIPPAGE_TICKS
    entry = candidate["entry"] + (slip if direction == "LONG" else -slip)
    broker = PaperBroker(
        starting_balance=1500.0, slippage_ticks=0.0, pessimistic_both_hit=True, runner_mode=False,
    )
    broker.execute_bracket(
        BracketOrder(
            instrument="MNQ", direction=direction, entry=entry, stop=candidate["stop"],
            target=candidate["target"], rr_ratio=candidate["rr"],
            strategy="impulse_pullback_continuation", contracts=1,
        )
    )
    for b in bars_after:
        out = broker.resolve_position(NextBarOHLC(high=float(b["high"]), low=float(b["low"])))
        if out is not None:
            return out.result, float(out.pnl_dollars or 0.0) - COMMISSION_RT, b.get("timestamp")
    return "OPEN", 0.0, None


def run(r_multiple: float) -> list:
    files = sorted(FINE_ROOT.glob("MNQ_*.jsonl"))
    resolved_rows = []
    prior_day_tail = []

    for path in files:
        day_bars = load_day(path)
        if not day_bars:
            continue
        day_bars.sort(key=lambda r: r["timestamp"])
        bars = (prior_day_tail + day_bars) if prior_day_tail else day_bars
        offset = len(prior_day_tail)
        bars_since_trigger = {"long": None, "short": None}

        for i in range(offset, len(bars)):
            current_bar = bars[i]
            window = bars[max(0, i - STRUCTURE_LOOKBACK_BARS):i]

            for direction in ("long", "short"):
                if bars_since_trigger[direction] is not None:
                    bars_since_trigger[direction] += 1

            candidates = detect_candidates(
                window=window, current_bar=current_bar, session=current_bar.get("session"),
                r_multiple=r_multiple, bars_since_last_trigger=bars_since_trigger,
            )
            for cand in candidates:
                if cand.get("decision") != "ACCEPTED":
                    continue
                bars_since_trigger[cand["direction"]] = 0
                bars_after = bars[i + 1:i + 1 + 500]
                result, pnl, exit_ts = resolve(cand, bars_after)
                if result == "OPEN":
                    continue
                resolved_rows.append({
                    **cand, "outcome": result, "pnl": pnl, "exit_ts": exit_ts,
                    "month": str(current_bar["timestamp"])[:7], "file": path.name,
                })

        prior_day_tail = day_bars[-STRUCTURE_LOOKBACK_BARS:]

    return resolved_rows


def _stats(rows: list) -> dict:
    if not rows:
        return {"n": 0}
    pnls = [r["pnl"] for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    running = peak = max_dd = 0.0
    for p in pnls:
        running += p
        peak = max(peak, running)
        max_dd = min(max_dd, running - peak)
    return {
        "n": len(rows),
        "win_rate": round(len(wins) / len(rows), 3),
        "net_pnl": round(sum(pnls), 2),
        "expectancy": round(statistics.fmean(pnls), 2),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if wins and losses else None,
        "max_drawdown": round(max_dd, 2),
    }


def summarize(resolved_rows: list) -> dict:
    def group(key_fn):
        buckets = defaultdict(list)
        for r in resolved_rows:
            buckets[key_fn(r)].append(r)
        return {str(k): _stats(v) for k, v in sorted(buckets.items(), key=lambda kv: str(kv[0]))}

    halves = None
    if resolved_rows:
        months = sorted({r["month"] for r in resolved_rows})
        mid = len(months) // 2
        fh_months = set(months[:mid]) if mid else set(months)
        fh = [r for r in resolved_rows if r["month"] in fh_months]
        sh = [r for r in resolved_rows if r["month"] not in fh_months]
        halves = {"first_half": _stats(fh), "second_half": _stats(sh)}

    return {
        "overall": _stats(resolved_rows),
        "by_direction": group(lambda r: r["direction"]),
        "by_session": group(lambda r: r["session"]),
        "walk_forward_halves": halves,
    }


if __name__ == "__main__":
    all_results = {}
    for rm in R_MULTIPLES:
        rows = run(rm)
        all_results[str(rm)] = {
            "summary": summarize(rows),
            "resolved_rows_sample": rows[:30],
        }
        print(f"R={rm}:", json.dumps(all_results[str(rm)]["summary"]["overall"], indent=2))
        halves = all_results[str(rm)]["summary"]["walk_forward_halves"]
        if halves:
            print("  first_half:", halves["first_half"])
            print("  second_half:", halves["second_half"])

    RESULTS.write_text(json.dumps(all_results, indent=2, default=str))
