#!/usr/bin/env python3
"""Strategy-matrix tranche 1 (2026-07-14, operator-directed component-role study).

Read-only analysis. Reuses the EXACT arm-replay architecture of
scripts/orb_breakout_entry_study.py (itself derived from the validated PR #143
harness): arms = real engine TRADE decisions from logs/retest_baseline_off,
fills = market-entry vs the first 5m bar at/after arming (data/replay_polygon_5m),
resolution = the real PaperBroker (same runtime formulas as the live box).

Tranche 1 scope (what is historically testable from this arm set):
  - Baselines per strategy x instrument: static / runner / partial(2ct approx),
    each raw and after commission+slippage.
  - Stop-width variants under runner: engine stop, 2x wider, 0.5x tighter.
  - One-at-a-time filters on the runner baseline (post-hoc subsets, no extra
    sim): session, EMA200 macro alignment, ORB-confirms, volume confirmation,
    Strat confirmation, long-only, short-only.
  - Metrics per cell: n/filled/resolved, WR, avg win/loss, expectancy, PF,
    net, max drawdown, avg MAE/MFE (ticks), avg holding minutes, monthly net,
    walk-forward halves.

NOT testable from this set (stated, not inferred): trend gate (constant ON --
already live-validated as hard blocker), VWAP/EMA9-21 alignment (constant --
part of the entry definition), GEX/Signa/regime/HTF (placeholder or degraded
values in replay; live-observe evidence path only).

Costs model: $1.24 commission round-turn per contract + 1 tick slippage per
side (2 ticks RT). MNQ tick=$0.50, MES tick=$1.25 -> MNQ $2.24/RT, MES $4.99/RT
total drag. Stated here so results are reproducible.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

REPO = Path("/Users/djb.a.e/MAINVSCODE/autonomous-futures-system")
sys.path.insert(0, str(REPO))

from context.bar_history import _parse_dt  # noqa: E402
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.paper_broker import NextBarOHLC, PaperBroker  # noqa: E402

TICK = 0.25
TICK_VALUE = {"MNQ": 0.50, "MES": 1.25}
COMMISSION_RT = 1.24  # per contract round-turn
SLIPPAGE_TICKS_RT = 2.0  # 1 tick per side
JOURNALS = REPO / "logs/retest_baseline_off"
FINE_ROOT = REPO / "data/replay_polygon_5m"
STRATEGIES = ("orb_breakout", "orb_reclaim", "pdh_reclaim", "pdl_reclaim",
              "vwap_hold", "vwap_reclaim")


def load_arms(journal_dir: Path, instrument: str) -> list[dict]:
    arms = []
    for path in sorted(journal_dir.glob("journal_*.jsonl")):
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
                or setup.get("strategy") not in STRATEGIES
            ):
                continue
            ts = _parse_dt(str(row.get("bar_ts") or ""))
            if ts is None:
                continue
            factors = [str(f) for f in (row.get("confluence") or {}).get("factors") or []]
            arms.append(
                {
                    "instrument": instrument,
                    "armed_at": ts + timedelta(minutes=15),
                    "direction": str(setup["direction"]).upper(),
                    "entry": float(setup["entry"]),
                    "stop": float(setup["stop"]),
                    "target": float(setup["target"]),
                    "strategy": str(setup["strategy"]),
                    "session": str(row.get("session") or ""),
                    "month": str(row.get("bar_ts") or "")[:7],
                    "f_ema200": any("EMA 200" in f for f in factors),
                    "f_orb_confirms": any("ORB confirms" in f for f in factors),
                    "f_volume": any(f.startswith("Volume") for f in factors),
                    "f_strat": any("Strat " in f for f in factors),
                }
            )
    return arms


_bar_cache: dict[tuple, list[dict]] = {}


def load_bars(instrument: str, day: str) -> list[dict]:
    key = (instrument, day)
    if key in _bar_cache:
        return _bar_cache[key]
    path = FINE_ROOT / instrument / f"{instrument}_{day}.jsonl"
    out = []
    if path.exists():
        for line in path.read_text().splitlines():
            row = json.loads(line)
            ts = _parse_dt(str(row.get("timestamp") or ""))
            if ts is not None:
                out.append({"ts": ts, "open": float(row["open"]), "high": float(row["high"]),
                            "low": float(row["low"]), "close": float(row["close"])})
        out.sort(key=lambda b: b["ts"])
    _bar_cache[key] = out
    return out


def fill_price(arm: dict, bars: list[dict]) -> tuple[str, float, object]:
    """Unbounded market entry -- the proof-lane fill model (PR #259/#282)."""
    after = [b for b in bars if b["ts"] >= arm["armed_at"]]
    if not after:
        return ("NO_DATA", 0.0, None)
    first = after[0]
    long = arm["direction"] == "LONG"
    level = arm["entry"]
    mkt = first["open"]
    gap = (mkt - level) / TICK if long else (level - mkt) / TICK
    if gap >= 0:
        px = mkt + TICK if long else mkt - TICK
        return ("FILLED", px, first["ts"])
    deadline = arm["armed_at"] + timedelta(minutes=20)
    for b in after:
        if b["ts"] > deadline:
            break
        hit = (b["high"] >= level) if long else (b["low"] <= level)
        if hit:
            px = level + TICK if long else level - TICK
            return ("FILLED", px, b["ts"])
    return ("NO_FILL", 0.0, None)


def resolve(arm: dict, px: float, fill_ts, bars: list[dict], *, runner: bool,
            stop_mult: float = 1.0) -> dict:
    long = arm["direction"] == "LONG"
    stop_dist = abs(px - arm["stop"]) * stop_mult
    stop = px - stop_dist if long else px + stop_dist
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
            instrument=arm["instrument"], direction=arm["direction"],
            entry=px, stop=stop, target=arm["target"], rr_ratio=2.0,
            strategy=arm["strategy"], contracts=1,
        )
    )
    mae_ticks = 0.0
    mfe_ticks = 0.0
    last_ts = fill_ts
    for b in bars:
        if fill_ts is not None and b["ts"] <= fill_ts:
            continue
        adverse = (px - b["low"]) / TICK if long else (b["high"] - px) / TICK
        favor = (b["high"] - px) / TICK if long else (px - b["low"]) / TICK
        mae_ticks = max(mae_ticks, adverse)
        mfe_ticks = max(mfe_ticks, favor)
        last_ts = b["ts"]
        fill = broker.resolve_position(NextBarOHLC(high=b["high"], low=b["low"]))
        if fill is not None:
            hold_min = (b["ts"] - fill_ts).total_seconds() / 60.0 if fill_ts else None
            return {"outcome": fill.result, "pnl": float(fill.pnl_dollars or 0.0),
                    "mae_ticks": mae_ticks, "mfe_ticks": mfe_ticks, "hold_min": hold_min}
    hold_min = (last_ts - fill_ts).total_seconds() / 60.0 if (fill_ts and last_ts) else None
    return {"outcome": "OPEN", "pnl": 0.0, "mae_ticks": mae_ticks,
            "mfe_ticks": mfe_ticks, "hold_min": hold_min}


def run_pass(arms: list[dict], *, exit_mode: str, stop_mult: float = 1.0,
             costs: bool = False) -> list[dict]:
    rows = []
    for arm in arms:
        day = arm["armed_at"].date().isoformat()
        bars = load_bars(arm["instrument"], day)
        status, px, ts = fill_price(arm, bars)
        base = {"arm": arm, "status": status}
        if status != "FILLED":
            rows.append({**base, "outcome": "NO_FILL", "pnl": 0.0,
                         "mae_ticks": None, "mfe_ticks": None, "hold_min": None})
            continue
        if exit_mode == "partial":
            r1 = resolve(arm, px, ts, bars, runner=False, stop_mult=stop_mult)
            r2 = resolve(arm, px, ts, bars, runner=True, stop_mult=stop_mult)
            res = {"outcome": r2["outcome"], "pnl": r1["pnl"] + r2["pnl"],
                   "mae_ticks": r2["mae_ticks"], "mfe_ticks": r2["mfe_ticks"],
                   "hold_min": r2["hold_min"], "contracts": 2}
        else:
            res = resolve(arm, px, ts, bars, runner=(exit_mode == "runner"),
                          stop_mult=stop_mult)
            res["contracts"] = 1
        if costs and res["outcome"] in {"WIN", "LOSS", "BREAKEVEN"}:
            tick_val = TICK_VALUE[arm["instrument"]]
            drag = (COMMISSION_RT + SLIPPAGE_TICKS_RT * tick_val) * res["contracts"]
            res = {**res, "pnl": res["pnl"] - drag}
        rows.append({**base, **res})
    return rows


def summarize(rows: list[dict]) -> dict:
    resolved = [r for r in rows if r["outcome"] in {"WIN", "LOSS", "BREAKEVEN"}]
    pnls = [r["pnl"] for r in resolved]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    # max drawdown over the trade-sequence equity curve
    eq, peak, maxdd = 0.0, 0.0, 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        maxdd = max(maxdd, peak - eq)
    maes = [r["mae_ticks"] for r in resolved if r["mae_ticks"] is not None]
    mfes = [r["mfe_ticks"] for r in resolved if r["mfe_ticks"] is not None]
    holds = [r["hold_min"] for r in resolved if r["hold_min"] is not None]
    monthly = defaultdict(float)
    for r in resolved:
        monthly[r["arm"]["month"]] += r["pnl"]
    return {
        "arms": len(rows),
        "filled": sum(1 for r in rows if r["status"] == "FILLED"),
        "resolved": len(resolved),
        "win_rate": round(len(wins) / len(resolved), 3) if resolved else None,
        "net_pnl": round(sum(pnls), 2),
        "expectancy": round(statistics.fmean(pnls), 2) if pnls else None,
        "avg_win": round(statistics.fmean(wins), 2) if wins else None,
        "avg_loss": round(statistics.fmean(losses), 2) if losses else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses and wins else None,
        "max_drawdown": round(maxdd, 2),
        "avg_mae_ticks": round(statistics.fmean(maes), 1) if maes else None,
        "avg_mfe_ticks": round(statistics.fmean(mfes), 1) if mfes else None,
        "avg_hold_min": round(statistics.fmean(holds), 1) if holds else None,
        "monthly_net": {k: round(v, 2) for k, v in sorted(monthly.items())},
    }


def halves(rows: list[dict]) -> tuple[dict, dict]:
    mid = len(rows) // 2
    return summarize(rows[:mid]), summarize(rows[mid:])


FILTERS = {
    "session_ny": lambda a: a["session"] == "new_york",
    "session_london": lambda a: a["session"] == "london",
    "session_asian": lambda a: a["session"] == "asian",
    "ema200_aligned": lambda a: a["f_ema200"],
    "orb_confirms": lambda a: a["f_orb_confirms"],
    "volume_confirmed": lambda a: a["f_volume"],
    "strat_confirmed": lambda a: a["f_strat"],
    "long_only": lambda a: a["direction"] == "LONG",
    "short_only": lambda a: a["direction"] == "SHORT",
}


def main() -> None:
    results: dict = {"_costs_model": {
        "commission_rt_per_contract": COMMISSION_RT,
        "slippage_ticks_rt": SLIPPAGE_TICKS_RT,
        "note": "after_cost cells subtract commission + slippage from every resolved trade",
    }}
    for inst in ("MNQ", "MES"):
        arms_all = load_arms(JOURNALS / inst, inst)
        arms_all.sort(key=lambda a: a["armed_at"])
        inst_out: dict = {}
        for strat in STRATEGIES:
            arms = [a for a in arms_all if a["strategy"] == strat]
            if not arms:
                continue
            s_out: dict = {"n_arms": len(arms)}
            # --- baselines: exits x raw/cost ---
            for exit_mode in ("static", "runner", "partial"):
                for costs in (False, True):
                    key = f"{exit_mode}{'_after_cost' if costs else ''}"
                    rows = run_pass(arms, exit_mode=exit_mode, costs=costs)
                    h1, h2 = halves(rows)
                    s_out[key] = {"all": summarize(rows),
                                  "first_half": {"expectancy": h1["expectancy"], "net": h1["net_pnl"]},
                                  "second_half": {"expectancy": h2["expectancy"], "net": h2["net_pnl"]}}
            # --- stop variants under runner (raw) ---
            for stop_name, mult in (("stop_2x", 2.0), ("stop_0.5x", 0.5)):
                rows = run_pass(arms, exit_mode="runner", stop_mult=mult)
                h1, h2 = halves(rows)
                s_out[stop_name] = {"all": summarize(rows),
                                    "first_half": {"expectancy": h1["expectancy"]},
                                    "second_half": {"expectancy": h2["expectancy"]}}
            # --- one-at-a-time filters on the runner baseline (post-hoc subsets) ---
            base_rows = run_pass(arms, exit_mode="runner")
            base_sum = summarize(base_rows)
            filt_out = {}
            for fname, fn in FILTERS.items():
                kept = [r for r in base_rows if fn(r["arm"])]
                if not kept:
                    continue
                ks = summarize(kept)
                kh1, kh2 = halves(kept)
                filt_out[fname] = {
                    "kept": len(kept), "removed": len(base_rows) - len(kept),
                    "expectancy": ks["expectancy"], "base_expectancy": base_sum["expectancy"],
                    "net": ks["net_pnl"], "win_rate": ks["win_rate"],
                    "profit_factor": ks["profit_factor"],
                    "halves_expectancy": [kh1["expectancy"], kh2["expectancy"]],
                }
            s_out["filters_on_runner"] = filt_out
            inst_out[strat] = s_out
            print(f"done {inst} {strat} n={len(arms)}", flush=True)
        results[inst] = inst_out

    out = Path(__file__).parent / "strategy_matrix_tranche1_results.json"
    out.write_text(json.dumps(results, indent=1))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
