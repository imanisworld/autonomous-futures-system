#!/usr/bin/env python3
"""Read-only extension of scripts/orb_market_entry_study.py (PR #143, validated 2026-07-02).

Reuses the EXACT SAME arms (logs/retest_baseline_off), 5m bars
(data/replay_polygon_5m), fill mechanism, and resolve harness — zero new
assumptions. Adds two breakdowns the original study did not isolate:

  1. Per-strategy (orb_breakout vs orb_reclaim separately, not combined)
  2. Per-exit-mode (runner AND static — the original only ran static as a
     single combined "decisive control" sentence, not broken out by strategy)

Question this answers: under the box's ACTUAL currently-pinned exit mode
(EXIT_MODE=static), is unbounded market-entry for orb_breakout specifically
positive, negative, or dead — not just the breakout+reclaim blend.

No deployment. No config change. Analysis only, output printed + JSON dumped
to this scratchpad directory.
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import timedelta
from pathlib import Path

REPO = Path("/Users/djb.a.e/MAINVSCODE/autonomous-futures-system")
sys.path.insert(0, str(REPO))

from context.bar_history import _parse_dt  # noqa: E402
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.paper_broker import NextBarOHLC, PaperBroker  # noqa: E402

ORB = {"orb_breakout", "orb_reclaim"}
TICK = 0.25
JOURNALS = REPO / "logs/retest_baseline_off"
FINE_ROOT = REPO / "data/replay_polygon_5m"


def load_arms(journal_dir: Path) -> list[dict]:
    arms = []
    for path in sorted(journal_dir.glob("journal_*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            setup = row.get("setup") or {}
            if (
                row.get("decision") != "TRADE"
                or (row.get("risk_check") or {}).get("result") != "APPROVED"
                or setup.get("strategy") not in ORB
            ):
                continue
            ts = _parse_dt(str(row.get("bar_ts") or ""))
            if ts is None:
                continue
            arms.append(
                {
                    "instrument": str(row.get("instrument") or "").upper(),
                    "armed_at": ts + timedelta(minutes=15),
                    "direction": str(setup["direction"]).upper(),
                    "entry": float(setup["entry"]),
                    "stop": float(setup["stop"]),
                    "target": float(setup["target"]),
                    "strategy": str(setup["strategy"]),
                }
            )
    return arms


def load_bars(instrument: str, day: str) -> list[dict]:
    path = FINE_ROOT / instrument / f"{instrument}_{day}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        row = json.loads(line)
        ts = _parse_dt(str(row.get("timestamp") or ""))
        if ts is not None:
            out.append(
                {
                    "ts": ts,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                }
            )
    out.sort(key=lambda b: b["ts"])
    return out


def fill_price(arm: dict, bars: list[dict], cap_ticks: float) -> tuple[str, float, float, object]:
    after = [b for b in bars if b["ts"] >= arm["armed_at"]]
    if not after:
        return ("NO_DATA", 0.0, 0.0, None)
    first = after[0]
    long = arm["direction"] == "LONG"
    level = arm["entry"]
    mkt = first["open"]
    gap = (mkt - level) / TICK if long else (level - mkt) / TICK
    if gap >= 0:
        if gap > cap_ticks:
            return ("GAP_REJECTED", 0.0, gap, None)
        px = mkt + TICK if long else mkt - TICK
        return ("FILLED", px, gap, first["ts"])
    deadline = arm["armed_at"] + timedelta(minutes=20)
    for b in after:
        if b["ts"] > deadline:
            break
        hit = (b["high"] >= level) if long else (b["low"] <= level)
        if hit:
            px = level + TICK if long else level - TICK
            return ("FILLED", px, 0.0, b["ts"])
    return ("NO_FILL", 0.0, gap, None)


def resolve(arm: dict, px: float, fill_ts, bars: list[dict], *, runner: bool) -> tuple[str, float]:
    broker = PaperBroker(
        starting_balance=1500.0,
        slippage_ticks=0.0,
        pessimistic_both_hit=True,
        runner_mode=runner,
        runner_activation_r=1.0,
        runner_trail_r=0.5,
    )
    broker.execute_bracket(
        BracketOrder(
            instrument=arm["instrument"],
            direction=arm["direction"],
            entry=px,
            stop=arm["stop"],
            target=arm["target"],
            rr_ratio=2.0,
            strategy=arm["strategy"],
            contracts=1,
        )
    )
    for b in bars:
        if fill_ts is not None and b["ts"] <= fill_ts:
            continue
        fill = broker.resolve_position(NextBarOHLC(high=b["high"], low=b["low"]))
        if fill is not None:
            return fill.result, float(fill.pnl_dollars or 0.0)
    return ("OPEN", 0.0)


def summarize(rows: list[dict]) -> dict:
    resolved = [r for r in rows if r["outcome"] in {"WIN", "LOSS", "BREAKEVEN"}]
    pnls = [r["pnl"] for r in resolved]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    return {
        "arms": len(rows),
        "filled": sum(1 for r in rows if r["status"] == "FILLED"),
        "gap_rejected": sum(1 for r in rows if r["status"] == "GAP_REJECTED"),
        "resolved": len(resolved),
        "win_rate": round(len(wins) / len(resolved), 3) if resolved else None,
        "net_pnl": round(sum(pnls), 2),
        "expectancy": round(statistics.fmean(pnls), 2) if pnls else None,
        "avg_win": round(statistics.fmean(wins), 2) if wins else None,
        "avg_loss": round(statistics.fmean(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses and wins else None,
    }


def run(instrument: str, arms: list[dict], cap: float, *, runner: bool) -> list[dict]:
    rows = []
    cache: dict[str, list[dict]] = {}
    for arm in arms:
        day = arm["armed_at"].date().isoformat()
        bars = cache.setdefault(day, load_bars(instrument, day))
        status, px, gap, ts = fill_price(arm, bars, cap)
        if status != "FILLED":
            rows.append({"status": status, "outcome": "NO_FILL", "pnl": 0.0, "gap": gap, "strategy": arm["strategy"]})
            continue
        outcome, pnl = resolve(arm, px, ts, bars, runner=runner)
        rows.append({"status": status, "outcome": outcome, "pnl": pnl, "gap": gap, "strategy": arm["strategy"]})
    return rows


def main() -> None:
    all_arms = load_arms(JOURNALS / "MES") + load_arms(JOURNALS / "MNQ")
    results: dict = {}
    for inst in ("MES", "MNQ"):
        arms = [a for a in all_arms if a["instrument"] == inst]
        arms.sort(key=lambda a: a["armed_at"])
        mid = len(arms) // 2
        inst_out: dict = {"n_arms": len(arms)}
        for cap_name, cap in (("cap2", 2), ("cap4", 4), ("cap8", 8), ("unbounded", 999)):
            for exit_name, runner in (("runner", True), ("static", False)):
                rows = run(inst, arms, cap, runner=runner)
                key = f"{cap_name}_{exit_name}"
                by_strategy = {}
                for strat in ("orb_breakout", "orb_reclaim"):
                    strat_rows = [r for r in rows if r["strategy"] == strat]
                    strat_rows_first = [r for r, a in zip(rows, arms) if r["strategy"] == strat][:len(strat_rows)]
                    by_strategy[strat] = {
                        "all": summarize(strat_rows),
                    }
                    # walk-forward halves for this strategy specifically
                    idx = [i for i, a in enumerate(arms) if a["strategy"] == strat]
                    mid_s = len(idx) // 2
                    first_half_rows = [rows[i] for i in idx[:mid_s]]
                    second_half_rows = [rows[i] for i in idx[mid_s:]]
                    by_strategy[strat]["first_half"] = summarize(first_half_rows)
                    by_strategy[strat]["second_half"] = summarize(second_half_rows)
                inst_out[key] = {
                    "combined": summarize(rows),
                    "by_strategy": by_strategy,
                }
        results[inst] = inst_out

    out = Path(__file__).parent / "orb_breakout_entry_study_results.json"
    out.write_text(json.dumps(results, indent=1))

    for inst, r in results.items():
        print(f"\n{'='*90}\n{inst} ({r['n_arms']} arms)\n{'='*90}")
        for cap_name in ("cap2", "cap4", "cap8", "unbounded"):
            for exit_name in ("runner", "static"):
                key = f"{cap_name}_{exit_name}"
                combined = r[key]["combined"]
                print(f"\n--- {key} --- combined: n={combined['resolved']} net=${combined['net_pnl']:>9.2f} "
                      f"exp={combined['expectancy']} WR={combined['win_rate']} PF={combined['profit_factor']}")
                for strat in ("orb_breakout", "orb_reclaim"):
                    s = r[key]["by_strategy"][strat]
                    a, f, sh = s["all"], s["first_half"], s["second_half"]
                    print(f"    {strat:14s} n={a['resolved']:4d} net=${a['net_pnl']:>9.2f} exp={a['expectancy']} "
                          f"WR={a['win_rate']} PF={a['profit_factor']} halves=({f['expectancy']}, {sh['expectancy']})")


if __name__ == "__main__":
    main()
