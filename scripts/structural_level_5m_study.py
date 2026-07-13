#!/usr/bin/env python3
"""MNQ structural-level 5-minute break/retest/reclaim/rejection replay study.

RESULT: REJECTED -- see docs/mnq-structural-level-5m-study-2026-07-13.md.
This script and research/mnq_structural_level_5m.py are a research record,
not a pending build. Fixed-target exit is robustly negative; a runner-exit
variant fails a 2-tick slippage stress test. No shadow/live lane was built.

Replay-first proof for research/mnq_structural_level_5m.py. Walks every day
of data/replay_polygon_5m/MNQ (621 daily files, 2024-07-02..2026-06-26) bar
by bar, generates candidates via detect_candidates() using ONLY prior,
already-closed bars as history (no lookahead by construction: `window`
passed to the detector is `bars[:i]`, `current_bar` is `bars[i]`), and
resolves ACCEPTED candidates forward using the same PaperBroker
pessimistic-both-hit resolver already validated in
scripts/mnq_entry_refresh_study.py (fixed target = next mapped level, stop =
structural stop, stop-priority on same-bar double-hit, 1 tick adverse
slippage, $1.48 round-trip commission).

Reproduce: python3 scripts/structural_level_5m_study.py
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from research.mnq_structural_level_5m import detect_candidates  # noqa: E402
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.paper_broker import NextBarOHLC, PaperBroker  # noqa: E402

FINE_ROOT = REPO / "data/replay_polygon_5m/MNQ"
RESULTS = REPO / "scripts/structural_level_5m_results.json"
TICK = 0.25
COMMISSION_RT = 1.48
SLIPPAGE_TICKS = 1.0
STRUCTURE_LOOKBACK_BARS = 36  # 3h of 5m bars kept as detector history window


def load_day(path: Path) -> list:
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def resolve(candidate: dict, bars_after: list) -> tuple:
    """Fixed-target resolution via PaperBroker, pessimistic both-hit, 1-tick
    adverse slippage on entry, no runner (primary target = next mapped level,
    per spec's TARGET LOGIC: 'do not use optimistic same-bar target
    priority')."""
    direction = candidate["direction"].upper()
    slip = TICK * SLIPPAGE_TICKS
    entry = candidate["entry"] + (slip if direction == "LONG" else -slip)
    broker = PaperBroker(
        starting_balance=1500.0,
        slippage_ticks=0.0,
        pessimistic_both_hit=True,
        runner_mode=False,
    )
    broker.execute_bracket(
        BracketOrder(
            instrument="MNQ",
            direction=direction,
            entry=entry,
            stop=candidate["stop"],
            target=candidate["target"],
            rr_ratio=candidate["rr"],
            strategy=f"structural_{candidate['setup_type']}",
            contracts=1,
        )
    )
    for b in bars_after:
        out = broker.resolve_position(NextBarOHLC(high=float(b["high"]), low=float(b["low"])))
        if out is not None:
            pnl = float(out.pnl_dollars or 0.0) - COMMISSION_RT
            return out.result, pnl, b.get("timestamp")
    return "OPEN", 0.0, None


def run() -> dict:
    files = sorted(FINE_ROOT.glob("MNQ_*.jsonl"))
    all_rows = []  # every considered candidate (accepted + rejected), for rejection-reason stats
    resolved_rows = []  # accepted + resolved (WIN/LOSS/BREAKEVEN), for expectancy stats

    dedupe_seen_per_day = None
    prior_day_tail = []  # small cross-day window carry (bounded, same-instrument continuity)

    for fi, path in enumerate(files):
        day_bars = load_day(path)
        if not day_bars:
            continue
        day_bars.sort(key=lambda r: r["timestamp"])
        bars = (prior_day_tail + day_bars) if prior_day_tail else day_bars
        offset = len(prior_day_tail)
        dedupe_seen = set()

        for i in range(offset, len(bars)):
            current_bar = bars[i]
            window = bars[max(0, i - STRUCTURE_LOOKBACK_BARS):i]
            candidates = detect_candidates(
                window=window,
                current_bar=current_bar,
                session=current_bar.get("session"),
                trend_direction=current_bar.get("trend_direction"),
                trend_strength=current_bar.get("trend_strength"),
                market_condition=current_bar.get("market_condition"),
            )
            for cand in candidates:
                cand = dict(cand)
                cand["file"] = path.name
                cand["month"] = str(current_bar["timestamp"])[:7]
                all_rows.append(cand)

                if cand.get("decision") != "ACCEPTED":
                    continue

                dedupe_key = (cand["source_level_name"], cand["direction"], cand["setup_type"])
                if dedupe_key in dedupe_seen:
                    cand["decision"] = "REJECTED"
                    cand["rejection_reason"] = "DUPLICATE_SETUP"
                    continue
                dedupe_seen.add(dedupe_key)

                bars_after = bars[i + 1:i + 1 + 500]
                result, pnl, exit_ts = resolve(cand, bars_after)
                if result == "OPEN":
                    continue  # never resolved within lookahead window -- excluded, not counted as a loss
                resolved_rows.append({
                    **cand, "outcome": result, "pnl": pnl, "exit_ts": exit_ts,
                })

        prior_day_tail = day_bars[-STRUCTURE_LOOKBACK_BARS:]

    return {"all_rows": all_rows, "resolved_rows": resolved_rows}


def _stats(rows: list) -> dict:
    pnls = [r["pnl"] for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    if not rows:
        return {"n": 0}
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        running += p
        peak = max(peak, running)
        max_dd = min(max_dd, running - peak)
    return {
        "n": len(rows),
        "win_rate": round(len(wins) / len(rows), 3) if rows else None,
        "net_pnl": round(sum(pnls), 2),
        "expectancy": round(statistics.fmean(pnls), 2) if pnls else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if wins and losses else None,
        "max_drawdown": round(max_dd, 2),
    }


def summarize(all_rows: list, resolved_rows: list) -> dict:
    considered = len(all_rows)
    accepted = sum(1 for r in all_rows if r.get("decision") == "ACCEPTED")
    rejection_counts = defaultdict(int)
    for r in all_rows:
        if r.get("decision") == "REJECTED":
            rejection_counts[r.get("rejection_reason") or "UNKNOWN"] += 1

    def group(key_fn):
        buckets = defaultdict(list)
        for r in resolved_rows:
            buckets[key_fn(r)].append(r)
        return {str(k): _stats(v) for k, v in sorted(buckets.items(), key=lambda kv: str(kv[0]))}

    halves = None
    if resolved_rows:
        months = sorted({r["month"] for r in resolved_rows})
        mid = len(months) // 2
        first_half_months = set(months[:mid]) if mid else set(months)
        first_half = [r for r in resolved_rows if r["month"] in first_half_months]
        second_half = [r for r in resolved_rows if r["month"] not in first_half_months]
        halves = {"first_half": _stats(first_half), "second_half": _stats(second_half),
                  "first_half_months": sorted(first_half_months),
                  "second_half_months": sorted(set(months) - first_half_months)}

    return {
        "considered": considered,
        "accepted": accepted,
        "resolved": len(resolved_rows),
        "rejection_counts": dict(sorted(rejection_counts.items(), key=lambda kv: -kv[1])),
        "overall": _stats(resolved_rows),
        "by_direction": group(lambda r: r["direction"]),
        "by_setup_type": group(lambda r: r["setup_type"]),
        "by_entry_mode": group(lambda r: r["entry_mode"]),
        "by_source_level": group(lambda r: r["source_level_name"]),
        "by_session": group(lambda r: r["session"]),
        "by_trend_context": group(lambda r: r["trend_context"]),
        "by_month": group(lambda r: r["month"]),
        "walk_forward_halves": halves,
    }


if __name__ == "__main__":
    raw = run()
    summary = summarize(raw["all_rows"], raw["resolved_rows"])
    RESULTS.write_text(json.dumps({
        "summary": summary,
        "resolved_rows_sample": raw["resolved_rows"][:50],
    }, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
