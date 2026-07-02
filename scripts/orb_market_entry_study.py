#!/usr/bin/env python3
"""ORB immediate-entry study: what does a guaranteed fill actually cost?

Question: the 15m replay edge fills orb entries AT the level (fiction — live,
price has already passed it by authorization time). If we instead enter at the
real market price at authorization (first 5m open after the 15m bar closes),
paying the gap as slippage — capped at a budget — does the edge survive?

Mechanism modeled (per arm, causal):
  - authorization at 15m bar close (armed_at, same as #142 scorecard)
  - first 5m bar opening at/after armed_at gives the market price
  - LONG: if open >= level: immediate fill at open + 1 tick adverse slip,
          adverse gap = open - level; REJECT arm if gap > cap ticks.
          if open < level: resting stop at level; fill at level + 1 tick on the
          first 5m bar (within 20 min) whose high >= level; else NO_FILL.
  - SHORT: mirrored.
  - Exit: original structural stop, runner mode (1.0R activation / 0.5R trail,
    R from ORIGINAL bracket), pessimistic both-hit — identical resolve harness
    to #142's scorecard.
  - Baseline leg: assumed fill at level + 1 tick (the replay fiction) through
    the SAME resolve harness, so gap cost is isolated.

Grid is predefined, no optimization: cap in {2, 4, 8, 999} ticks.
Walk-forward: midpoint split per instrument (same convention as #142).
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
    """Return (status, fill_px, adverse_gap_ticks, fill_ts)."""
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
        px = mkt + TICK if long else mkt - TICK  # 1 tick adverse slip
        return ("FILLED", px, gap, first["ts"])
    # market pulled back behind the level: resting stop at level, 20 min
    deadline = arm["armed_at"] + timedelta(minutes=20)
    for b in after:
        if b["ts"] > deadline:
            break
        hit = (b["high"] >= level) if long else (b["low"] <= level)
        if hit:
            px = level + TICK if long else level - TICK
            return ("FILLED", px, 0.0, b["ts"])
    return ("NO_FILL", 0.0, gap, None)


def resolve(arm: dict, px: float, fill_ts, bars: list[dict]) -> tuple[str, float]:
    broker = PaperBroker(
        starting_balance=1500.0,
        slippage_ticks=0.0,  # slip already applied to entry px
        pessimistic_both_hit=True,
        runner_mode=True,
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
    equity = peak = dd = 0.0
    streak = max_streak = 0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
        streak = streak + 1 if p < 0 else 0
        max_streak = max(max_streak, streak)
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
        "max_drawdown": round(dd, 2),
        "max_consec_losses": max_streak,
        "avg_adverse_gap_ticks": round(
            statistics.fmean([r["gap"] for r in rows if r["status"] == "FILLED"]), 2
        ) if any(r["status"] == "FILLED" for r in rows) else None,
    }


def run(instrument: str, arms: list[dict], cap: float, assumed: bool) -> list[dict]:
    rows = []
    cache: dict[str, list[dict]] = {}
    for arm in arms:
        day = arm["armed_at"].date().isoformat()
        bars = cache.setdefault(day, load_bars(instrument, day))
        if assumed:
            # replay-fiction baseline: fill at level +1 tick at authorization
            after = [b for b in bars if b["ts"] >= arm["armed_at"]]
            if not after:
                rows.append({"status": "NO_DATA", "outcome": "NO_FILL", "pnl": 0.0, "gap": 0.0})
                continue
            long = arm["direction"] == "LONG"
            px = arm["entry"] + (TICK if long else -TICK)
            status, ts = "FILLED", after[0]["ts"]
            gap = 0.0
        else:
            status, px, gap, ts = fill_price(arm, bars, cap)
        if status != "FILLED":
            rows.append({"status": status, "outcome": "NO_FILL", "pnl": 0.0, "gap": gap})
            continue
        outcome, pnl = resolve(arm, px, ts, bars)
        rows.append({"status": status, "outcome": outcome, "pnl": pnl, "gap": gap})
    return rows


def main() -> None:
    all_arms = load_arms(JOURNALS / "MES") + load_arms(JOURNALS / "MNQ")
    results: dict = {}
    for inst in ("MES", "MNQ"):
        arms = [a for a in all_arms if a["instrument"] == inst]
        arms.sort(key=lambda a: a["armed_at"])
        mid = len(arms) // 2
        inst_out: dict = {"n_arms": len(arms)}
        legs = [("assumed_fill_baseline", None, True)] + [
            (f"market_cap{c}", c, False) for c in (2, 4, 8, 999)
        ]
        for name, cap, assumed in legs:
            rows = run(inst, arms, cap or 0, assumed)
            inst_out[name] = {
                "all": summarize(rows),
                "first_half": summarize(rows[:mid]),
                "second_half": summarize(rows[mid:]),
            }
        results[inst] = inst_out
    out = Path(__file__).parent / "orb_market_entry_results.json"
    out.write_text(json.dumps(results, indent=1))
    # compact report
    for inst, r in results.items():
        print(f"\n=== {inst} ({r['n_arms']} arms) ===")
        for leg in ("assumed_fill_baseline", "market_cap2", "market_cap4", "market_cap8", "market_cap999"):
            a, f, s = r[leg]["all"], r[leg]["first_half"], r[leg]["second_half"]
            print(
                f"{leg:22s} filled={a['filled']:4d} resolved={a['resolved']:4d} "
                f"WR={a['win_rate']} net=${a['net_pnl']:>9.2f} exp={a['expectancy']} "
                f"halves=({f['expectancy']}, {s['expectancy']}) "
                f"PF={a['profit_factor']} dd={a['max_drawdown']} gap={a['avg_adverse_gap_ticks']}"
            )


if __name__ == "__main__":
    main()
