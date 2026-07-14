#!/usr/bin/env python3
"""Paired fill-model comparison for MNQ vwap_hold (2026-07-14, operator-required
verification before the vwap_hold restoration lane is built).

Question: is the newly-positive vwap_hold replay result an EXECUTION-MODEL
correction (market entry fixing the stale-anchored-IOC no-fill blocker), or a
quietly different backtest (different candidates, sessions, or eligibility)?

Design: ONE arm population -> TWO fill legs, everything else held identical.
  - Arms: the exact same loader as scripts/strategy_matrix_tranche1.py over
    logs/retest_baseline_off/MNQ, strategy==vwap_hold. The population is
    fingerprinted (sha256 over sorted (bar_ts, direction, entry, stop, target))
    and recorded in the output so any future re-run can prove identity.
  - Order-arrival timing: both legs use the SAME first 5m bar at/after
    armed_at (= decision bar_ts + 15min). No leg sees data the other doesn't.
  - Leg OLD (anchored IOC): the real PaperBroker with
    entry_fill_model="ioc_limit", entry_tolerance_ticks_by_root={"MNQ": 32}
    (the live pin ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ=32), market_price = the
    arrival bar's open. Unmarketable -> ENTRY_NOT_FILLED, exactly as live books
    it.
  - Leg NEW (market): the tranche-1 fill (arrival bar open + 1 adverse tick,
    or a touch within 20min if the market hasn't reached the level).
  - Both legs: same ordered stop (arm stop), same runner exit (activation 1.0R,
    trail 0.5R), pessimistic_both_hit=True, same bar-walk resolution, and the
    same after-cost model ($1.24 commission RT + 2 ticks RT slippage).

The NEW leg is also the independent reproduction of the tranche-1 headline
numbers (recomputed from journals + bars, not read from the tranche-1 JSON).
"""
from __future__ import annotations

import hashlib
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

TICK = 0.25
TICK_VALUE_MNQ = 0.50
COMMISSION_RT = 1.24
SLIPPAGE_TICKS_RT = 2.0
COST_RT = COMMISSION_RT + SLIPPAGE_TICKS_RT * TICK_VALUE_MNQ  # $2.24
IOC_TOL_TICKS = 32.0  # live pin ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ
JOURNALS = REPO / "logs/retest_baseline_off/MNQ"
FINE_ROOT = REPO / "data/replay_polygon_5m"


def load_arms() -> list[dict]:
    arms = []
    for path in sorted(JOURNALS.glob("journal_*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            setup = row.get("setup") or {}
            if (
                row.get("decision") != "TRADE"
                or (row.get("risk_check") or {}).get("result") != "APPROVED"
                or setup.get("strategy") != "vwap_hold"
            ):
                continue
            ts = _parse_dt(str(row.get("bar_ts") or ""))
            if ts is None:
                continue
            arms.append({
                "bar_ts": str(row.get("bar_ts")),
                "armed_at": ts + timedelta(minutes=15),
                "direction": str(setup["direction"]).upper(),
                "entry": float(setup["entry"]),
                "stop": float(setup["stop"]),
                "target": float(setup["target"]),
                "session": str(row.get("session") or ""),
            })
    arms.sort(key=lambda a: a["armed_at"])
    return arms


def fingerprint(arms: list[dict]) -> str:
    blob = json.dumps(
        sorted((a["bar_ts"], a["direction"], a["entry"], a["stop"], a["target"]) for a in arms)
    ).encode()
    return hashlib.sha256(blob).hexdigest()


_bar_cache: dict[str, list[dict]] = {}


def load_bars(day: str) -> list[dict]:
    if day in _bar_cache:
        return _bar_cache[day]
    path = FINE_ROOT / "MNQ" / f"MNQ_{day}.jsonl"
    out = []
    if path.exists():
        for line in path.read_text().splitlines():
            row = json.loads(line)
            ts = _parse_dt(str(row.get("timestamp") or ""))
            if ts is not None:
                out.append({"ts": ts, "open": float(row["open"]), "high": float(row["high"]),
                            "low": float(row["low"]), "close": float(row["close"])})
        out.sort(key=lambda b: b["ts"])
    _bar_cache[day] = out
    return out


def make_broker(fill_model: str) -> PaperBroker:
    kwargs = dict(
        starting_balance=1500.0,
        slippage_ticks=0.0,
        pessimistic_both_hit=True,
        runner_mode=True,
        runner_activation_r=1.0,
        runner_trail_r=0.5,
        entry_fill_model=fill_model,
    )
    if fill_model == "ioc_limit":
        kwargs["entry_tolerance_ticks_by_root"] = {"MNQ": IOC_TOL_TICKS}
    return PaperBroker(**kwargs)


def resolve_walk(broker: PaperBroker, bars: list[dict], after_ts) -> tuple[str, float]:
    for b in bars:
        if after_ts is not None and b["ts"] <= after_ts:
            continue
        fill = broker.resolve_position(NextBarOHLC(high=b["high"], low=b["low"]))
        if fill is not None:
            return fill.result, float(fill.pnl_dollars or 0.0)
    return ("OPEN", 0.0)


def run_leg(arms: list[dict], leg: str) -> list[dict]:
    rows = []
    for arm in arms:
        day = arm["armed_at"].date().isoformat()
        bars = load_bars(day)
        after = [b for b in bars if b["ts"] >= arm["armed_at"]]
        if not after:
            rows.append({**arm, "leg": leg, "status": "NO_DATA", "outcome": "NO_FILL", "pnl": 0.0})
            continue
        first = after[0]
        order = BracketOrder(
            instrument="MNQ", direction=arm["direction"], entry=arm["entry"],
            stop=arm["stop"], target=arm["target"], rr_ratio=2.0,
            strategy="vwap_hold", contracts=1,
        )
        if leg == "old_ioc":
            broker = make_broker("ioc_limit")
            fill = broker.execute_bracket(order, market_price=first["open"])
            if fill.result == "CANCELLED":
                rows.append({**arm, "leg": leg, "status": "ENTRY_NOT_FILLED",
                             "outcome": "NO_FILL", "pnl": 0.0})
                continue
            outcome, pnl = resolve_walk(broker, bars, first["ts"])
            rows.append({**arm, "leg": leg, "status": "FILLED", "outcome": outcome, "pnl": pnl})
        else:  # new_market: exact tranche-1 fill mechanism
            long = arm["direction"] == "LONG"
            level = arm["entry"]
            mkt = first["open"]
            gap = (mkt - level) / TICK if long else (level - mkt) / TICK
            px, fill_ts = None, None
            if gap >= 0:
                px = mkt + TICK if long else mkt - TICK
                fill_ts = first["ts"]
            else:
                deadline = arm["armed_at"] + timedelta(minutes=20)
                for b in after:
                    if b["ts"] > deadline:
                        break
                    if (b["high"] >= level) if long else (b["low"] <= level):
                        px = level + TICK if long else level - TICK
                        fill_ts = b["ts"]
                        break
            if px is None:
                rows.append({**arm, "leg": leg, "status": "NO_FILL", "outcome": "NO_FILL", "pnl": 0.0})
                continue
            broker = make_broker("market")
            broker.execute_bracket(BracketOrder(
                instrument="MNQ", direction=arm["direction"], entry=px,
                stop=arm["stop"], target=arm["target"], rr_ratio=2.0,
                strategy="vwap_hold", contracts=1,
            ))
            outcome, pnl = resolve_walk(broker, bars, fill_ts)
            rows.append({**arm, "leg": leg, "status": "FILLED", "outcome": outcome, "pnl": pnl})
    return rows


def summarize(rows: list[dict], *, after_cost: bool = False) -> dict:
    resolved = [r for r in rows if r["outcome"] in {"WIN", "LOSS", "BREAKEVEN"}]
    pnls = [(r["pnl"] - COST_RT) if after_cost else r["pnl"] for r in resolved]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    mid = len(pnls) // 2
    return {
        "arms": len(rows),
        "filled": sum(1 for r in rows if r["status"] == "FILLED"),
        "not_filled": sum(1 for r in rows if r["status"] in {"ENTRY_NOT_FILLED", "NO_FILL"}),
        "resolved": len(resolved),
        "net": round(sum(pnls), 2),
        "expectancy": round(statistics.fmean(pnls), 2) if pnls else None,
        "win_rate": round(len(wins) / len(resolved), 3) if resolved else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if wins and losses else None,
        "halves_expectancy": [
            round(statistics.fmean(pnls[:mid]), 2) if pnls[:mid] else None,
            round(statistics.fmean(pnls[mid:]), 2) if pnls[mid:] else None,
        ],
    }


def main() -> None:
    arms = load_arms()
    fp = fingerprint(arms)
    print(f"arm population: n={len(arms)}, fingerprint sha256={fp}")
    print(f"first arm: {arms[0]['bar_ts']}  last arm: {arms[-1]['bar_ts']}")
    dirs = {a['direction'] for a in arms}
    print(f"directions: {dirs}")

    old_rows = run_leg(arms, "old_ioc")
    new_rows = run_leg(arms, "new_market")

    out = {
        "arm_population": {"n": len(arms), "sha256": fp,
                           "first": arms[0]["bar_ts"], "last": arms[-1]["bar_ts"]},
        "identical_inputs": "both legs consumed the same arm list object; per-arm pairing below",
        "old_ioc": {"raw": summarize(old_rows), "after_cost": summarize(old_rows, after_cost=True)},
        "new_market": {"raw": summarize(new_rows), "after_cost": summarize(new_rows, after_cost=True)},
        "old_ioc_ny": {"raw": summarize([r for r in old_rows if r["session"] == "new_york"])},
        "new_market_ny": {"raw": summarize([r for r in new_rows if r["session"] == "new_york"])},
        "pairs": [
            {"bar_ts": o["bar_ts"], "session": o["session"],
             "old": {"status": o["status"], "outcome": o["outcome"], "pnl": round(o["pnl"], 2)},
             "new": {"status": n["status"], "outcome": n["outcome"], "pnl": round(n["pnl"], 2)}}
            for o, n in zip(old_rows, new_rows)
        ],
    }
    path = Path(__file__).parent / "vwap_hold_paired_fill_comparison_results.json"
    path.write_text(json.dumps(out, indent=1))

    for name in ("old_ioc", "new_market"):
        for cost in ("raw", "after_cost"):
            s = out[name][cost]
            print(f"{name:11s} {cost:10s} filled={s['filled']:3d}/{s['arms']} net=${s['net']:>9.2f} "
                  f"exp={s['expectancy']} WR={s['win_rate']} PF={s['profit_factor']} halves={s['halves_expectancy']}")
    print(f"NY-only:  old exp={out['old_ioc_ny']['raw']['expectancy']} (n={out['old_ioc_ny']['raw']['resolved']})  "
          f"new exp={out['new_market_ny']['raw']['expectancy']} (n={out['new_market_ny']['raw']['resolved']})")
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
