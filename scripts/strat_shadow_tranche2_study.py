#!/usr/bin/env python3
"""Tranche 2: shadow strategy families under the tranche-1 honest fill model.

Re-tests the strat_22_reversal / strat_22_continuation / ema_pullback_trend /
strat_312 / strat_322_reversal shadow families with MARKET entry + RUNNER exit
+ costs (the tranche-1 / proof-lane model), replacing the 2026-07-09 study's
resting-entry static-target model that was later proven (PR #283, vwap_hold
paired fill study) to distort verdicts.

Honesty addition over tranche 1: shadow candidates fire on many consecutive
bars with no position management, so arms are resolved SEQUENTIALLY
NON-OVERLAPPING per (instrument, strategy, variant) — a candidate arriving
while the prior trade is still open is skipped, exactly as a real
one-position-at-a-time lane would behave. Both orderings are reported
(all-signals inflated view vs lane-shaped sequential view) but the verdict
column is the sequential one.

Read-only research. No production code touched.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

spec = importlib.util.spec_from_file_location(
    "t1", REPO / "scripts/strategy_matrix_tranche1.py")
t1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t1)

from context.bar_history import _parse_dt  # noqa: E402

SHADOW_JOURNALS = REPO / "logs/replay_622d_market_static"
FAMILIES = (
    "strat_22_reversal_observed",
    "strat_22_continuation_observed",
    "ema_pullback_trend",
    "strat_312_observed",
    "strat_322_reversal_observed",
)


def load_shadow_arms(instrument: str) -> list[dict]:
    arms = []
    for path in sorted((SHADOW_JOURNALS / instrument).glob("journal_*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_dt(str(row.get("bar_ts") or ""))
            if ts is None:
                continue
            for sc in row.get("shadow_candidates") or []:
                strat = sc.get("strategy")
                if strat not in FAMILIES:
                    continue
                direction = str(sc.get("direction") or "").upper()
                try:
                    entry = float(sc["entry"]); stop = float(sc["stop"]); target = float(sc["target"])
                except (KeyError, TypeError, ValueError):
                    continue
                if direction not in ("LONG", "SHORT"):
                    continue
                arms.append({
                    "instrument": instrument,
                    "armed_at": ts + timedelta(minutes=15),
                    "direction": direction,
                    "entry": entry, "stop": stop, "target": target,
                    "strategy": strat,
                    "session": str(row.get("session") or ""),
                    "month": str(row.get("bar_ts") or "")[:7],
                    "f_ema200": False, "f_orb_confirms": False,
                    "f_volume": False, "f_strat": True,
                })
    arms.sort(key=lambda a: a["armed_at"])
    return arms


def run_sequential(arms: list[dict]) -> list[dict]:
    """One-position-at-a-time lane shape: skip arms arming while busy."""
    rows, busy_until, skipped = [], None, 0
    for arm in arms:
        if busy_until is not None and arm["armed_at"] < busy_until:
            skipped += 1
            continue
        day = arm["armed_at"].date().isoformat()
        bars = t1.load_bars(arm["instrument"], day)
        status, px, ts = t1.fill_price(arm, bars)
        if status != "FILLED":
            rows.append({"arm": arm, "status": status, "outcome": "NO_FILL", "pnl": 0.0,
                         "mae_ticks": None, "mfe_ticks": None, "hold_min": None})
            continue
        res = t1.resolve(arm, px, ts, bars, runner=True)
        res["contracts"] = 1
        if res["outcome"] in {"WIN", "LOSS", "BREAKEVEN"}:
            tick_val = t1.TICK_VALUE[arm["instrument"]]
            drag = (t1.COMMISSION_RT + t1.SLIPPAGE_TICKS_RT * tick_val)
            res = {**res, "pnl": res["pnl"] - drag}
        rows.append({"arm": arm, "status": status, **res})
        if res["hold_min"] is not None and ts is not None:
            busy_until = ts + timedelta(minutes=res["hold_min"])
        elif res["outcome"] == "OPEN":
            busy_until = arm["armed_at"] + timedelta(hours=23)
    if rows:
        rows[0]["_skipped_overlap"] = skipped
    return rows


def main() -> None:
    out = {}
    for instrument in ("MNQ", "MES"):
        all_arms = load_shadow_arms(instrument)
        by_strat = defaultdict(list)
        for a in all_arms:
            by_strat[a["strategy"]].append(a)
        for strat, arms in sorted(by_strat.items()):
            for variant, subset in (
                ("all_sessions", arms),
                ("ny_only", [a for a in arms if a["session"] == "new_york"]),
            ):
                rows = run_sequential(subset)
                full = t1.summarize(rows)
                h1, h2 = t1.halves(rows)
                key = f"{instrument}|{strat}|{variant}"
                out[key] = {
                    "signals_raw": len(subset),
                    "skipped_overlap": rows[0].get("_skipped_overlap") if rows else 0,
                    "full": full,
                    "half1": {k: h1[k] for k in ("resolved", "net_pnl", "expectancy", "win_rate", "profit_factor")},
                    "half2": {k: h2[k] for k in ("resolved", "net_pnl", "expectancy", "win_rate", "profit_factor")},
                }
                f = out[key]["full"]
                print(f"{key}: raw={len(subset)} taken={f['arms']} resolved={f['resolved']} "
                      f"net=${f['net_pnl']} exp=${f['expectancy']} wr={f['win_rate']} pf={f['profit_factor']} "
                      f"| h1 ${h1['net_pnl']} ({h1['expectancy']}/t) h2 ${h2['net_pnl']} ({h2['expectancy']}/t)",
                      flush=True)
    Path(__file__).with_name("strat_shadow_tranche2_results.json").write_text(
        json.dumps(out, indent=2))
    print("results written")


if __name__ == "__main__":
    main()
