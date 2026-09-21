"""ORB_BREAKOUT_LONG 60-minute time-exit study (`orbx-v0.1`) — research-only.

Prereg: docs/prereg-futures-orb-breakout-long-time-exit-2026-09-21.md (frozen
before the run). Entry at the next bar's open after the trigger bar, structural
stop (ORB low primary, ORB midpoint secondary), exit at the close of the 12th
bar after entry or the stop, whichever first; one trade per session per
instrument; 0/1/2-tick adverse slippage per side; $1.50 round-trip commission.
No import from strategy/, execution/ or engine/; nothing imports this module.

    python research/futures_orb_breakout_time_exit.py --out logs/orbx_v01
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.non_strat_coverage import observe_session  # noqa: E402
from research.futures_non_strat_coverage import (  # noqa: E402
    HALF_SPLIT,
    HISTORY_SESSIONS,
    RTH_BARS,
    load_rth_session,
    roll_excluded_sessions,
    session_files,
)

STUDY_VERSION = "orbx-v0.1"
TICK = 0.25
POINT_VALUE = {"MNQ": 2.0, "MES": 5.0}
COMMISSION_RT = 1.50
HOLD_BARS = 12
SLIPPAGE_TICKS = (0.0, 1.0, 2.0)
STOPS = ("ORB_LOW", "ORB_MID")


def _half(day: date) -> str:
    return "H1" if day < HALF_SPLIT else "H2"


def _trades(instrument: str) -> list[dict[str, Any]]:
    files = session_files(instrument)
    days = sorted(files)
    roll = roll_excluded_sessions(days)
    loaded = {}
    for d in days:
        bars, _ = load_rth_session(files[d])
        if len(bars) == RTH_BARS:
            loaded[d] = bars
    complete = sorted(loaded)
    out: list[dict[str, Any]] = []
    for idx, d in enumerate(complete):
        if d in roll:
            continue
        prior = complete[max(0, idx - HISTORY_SESSIONS) : idx]
        if not prior or prior[-1] in roll:
            continue
        bars = loaded[d]
        events = [
            e
            for e in observe_session(
                symbol=instrument,
                session_date=d.isoformat(),
                prior_session_bars=loaded[prior[-1]],
                session_bars=bars,
                history_bars=[b for p in prior for b in loaded[p]],
            )
            if e.family == "ORB_BREAKOUT_LONG"
        ]
        if not events:
            continue
        first = min(events, key=lambda e: e.bar_start)  # one trade per session
        trig = next(i for i, b in enumerate(bars) if b.start_utc.isoformat() == first.bar_start)
        if trig + 1 >= len(bars):
            continue
        orb_high = max(b.high for b in bars[:6])
        orb_low = min(b.low for b in bars[:6])
        entry_idx = trig + 1
        decision = float(bars[entry_idx].open)
        for stop_name in STOPS:
            stop = orb_low if stop_name == "ORB_LOW" else (orb_high + orb_low) / 2.0
            if stop >= decision:
                out.append({"instrument": instrument, "session": d.isoformat(), "half": _half(d), "stop": stop_name, "status": "BRACKET_INVALID"})
                continue
            for slip in SLIPPAGE_TICKS:
                entry = decision + slip * TICK
                exit_price = None
                reason = None
                last = min(entry_idx + HOLD_BARS, len(bars) - 1)
                # Entry bar included: we are in the market from its open.
                for j in range(entry_idx, last + 1):
                    if bars[j].low <= stop:
                        exit_price = stop - slip * TICK
                        reason = "STOP"
                        break
                if exit_price is None:
                    exit_price = float(bars[last].close) - slip * TICK
                    reason = "TIME" if last == entry_idx + HOLD_BARS else "EOD"
                pnl = (exit_price - entry) * POINT_VALUE[instrument] - COMMISSION_RT
                out.append({
                    "instrument": instrument, "session": d.isoformat(), "half": _half(d), "stop": stop_name,
                    "slip": slip, "status": "RESOLVED", "reason": reason, "entry": entry, "exit": exit_price,
                    "net": round(pnl, 2), "bars": (j if reason == "STOP" else last) - entry_idx,
                    "stop_ticks": round((decision - stop) / TICK, 1),
                })
    return out


def _cell(rows: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [r["net"] for r in rows]
    wins = [n for n in nets if n > 0]
    losses = [-n for n in nets if n < 0]
    peak = run = 0.0
    dd = 0.0
    for n in nets:
        run += n
        peak = max(peak, run)
        dd = max(dd, peak - run)
    return {
        "n": len(nets),
        "win_rate": round(len(wins) / len(nets), 4) if nets else None,
        "net": round(sum(nets), 2),
        "expectancy": round(sum(nets) / len(nets), 4) if nets else None,
        "pf": round(sum(wins) / sum(losses), 4) if losses else None,
        "max_dd": round(dd, 2),
        "stop_share": round(sum(1 for r in rows if r["reason"] == "STOP") / len(rows), 4) if rows else None,
        "median_bars": statistics.median(r["bars"] for r in rows) if rows else None,
        "stop_ticks_median": statistics.median(r["stop_ticks"] for r in rows) if rows else None,
    }


def report(all_rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [r for r in all_rows if r["status"] == "RESOLVED"]
    cells: dict[str, Any] = {}
    for stop in STOPS:
        for slip in SLIPPAGE_TICKS:
            for inst in ("MNQ", "MES"):
                for half in ("H1", "H2"):
                    rows = [r for r in resolved if r["stop"] == stop and r["slip"] == slip and r["instrument"] == inst and r["half"] == half]
                    cells[f"{stop}:{slip:g}:{inst}:{half}"] = _cell(rows)
                pooled = [r for r in resolved if r["stop"] == stop and r["slip"] == slip]
                cells[f"{stop}:{slip:g}:POOLED"] = _cell(pooled)
    # pre-registered pass: primary stop, 2 ticks, expectancy > 0 in all four cells
    four = [cells[f"ORB_LOW:2:{i}:{h}"] for i in ("MNQ", "MES") for h in ("H1", "H2")]
    primary = all(c["expectancy"] is not None and c["expectancy"] > 0 for c in four)
    pooled_pf = cells["ORB_LOW:2:POOLED"]["pf"]
    verdict = "CLOSED" if not primary else ("PROMISING_BUT_UNPROVEN" if (pooled_pf or 0) < 1.94 else "ELIGIBLE_FOR_FORWARD_PAPER_PREREG")
    return {"study": STUDY_VERSION, "cells": cells, "primary_pass": primary, "pooled_pf_2tick": pooled_pf, "verdict": verdict,
            "bracket_invalid": sum(1 for r in all_rows if r["status"] == "BRACKET_INVALID")}


def to_markdown(rep: dict[str, Any]) -> str:
    lines = [f"# {STUDY_VERSION} — ORB_BREAKOUT_LONG, next-bar-open entry, 60m time exit", "",
             f"verdict: **{rep['verdict']}** (primary pass = {rep['primary_pass']}, pooled PF @2 ticks = {rep['pooled_pf_2tick']}, null p95 1.94); bracket_invalid {rep['bracket_invalid']}", "",
             "| cell | n | win% | net $ | exp $ | PF | maxDD $ | stop share | med bars | stop ticks med |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for k, c in rep["cells"].items():
        lines.append(f"| {k} | {c['n']} | {c['win_rate']} | {c['net']} | {c['expectancy']} | {c['pf']} | {c['max_dd']} | {c['stop_share']} | {c['median_bars']} | {c['stop_ticks_median']} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "logs" / "orbx_v01"))
    a = p.parse_args(argv)
    rows = _trades("MNQ") + _trades("MES")
    rep = report(rows)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "orbx_v01.json").write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n")
    (out / "orbx_v01_trades.json").write_text(json.dumps(rows) + "\n")
    md = to_markdown(rep)
    (out / "orbx_v01.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
