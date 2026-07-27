#!/usr/bin/env python3
"""Causal-stop distribution across every known historical 12HR Miyagi
candidate date (PR #343's own 15 MNQ + 19 MES dates, study_range
2024-07-02..2026-06-26), computed by driving strategy/strat_12hr_miyagi.py
directly (not through DecisionEngine/RiskEngine -- this measures the
causal stop itself, independent of any gate) against real 5-minute Polygon
data (data/replay_corpus_v1_5m, same source/methodology as the #338-
corrected 15m corpus).

Requested by the operator as the required due-diligence step before the
bulk causal-stop study: does causal Miyagi still have fills inside the
account's existing max_stop_ticks envelope (risk_rules.yaml: MNQ=120,
MES=60), given the causal fix can produce a materially different (and
structurally unbounded -- last-completed-1H-bar high/low) stop than the
old lookahead-flawed detector's stop.

Usage:
    python3 scripts/miyagi_causal_stop_distribution.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, date as date_cls
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from strategy.strat_12hr_miyagi import advance_strat_12hr_miyagi  # noqa: E402

ET = ZoneInfo("America/New_York")
CORPUS = REPO / "data" / "replay_corpus_v1_5m"
TICK_SIZE = {"MNQ": 0.25, "MES": 0.25}
MAX_STOP_TICKS = {"MNQ": 120, "MES": 60}

CANDIDATE_DATES = {
    "MNQ": [
        "2024-08-22", "2024-08-23", "2024-09-18", "2024-10-11", "2024-10-23",
        "2024-12-11", "2025-02-27", "2025-03-06", "2025-03-21", "2025-05-16",
        "2025-05-23", "2025-09-25", "2025-12-18", "2026-01-14", "2026-02-11",
    ],
    "MES": [
        "2024-07-12", "2024-07-17", "2024-08-22", "2024-08-23", "2024-09-19",
        "2024-10-23", "2024-10-25", "2025-03-06", "2025-03-21", "2025-04-02",
        "2025-04-30", "2025-05-23", "2025-09-25", "2025-10-08", "2025-12-04",
        "2026-01-14", "2026-02-05", "2026-02-11", "2026-04-10",
    ],
}


def _load_old_evidence(instrument: str) -> dict[str, dict]:
    path = REPO / f"docs/strategy-rules/evidence_12hr_miyagi/{instrument.lower()}_results.json"
    data = json.load(open(path))
    trades = data["base_case"]["trades"]
    return {t["date"]: t for t in trades}


def _run_instrument(instrument: str) -> list[dict]:
    files = sorted(CORPUS.glob(f"{instrument}/{instrument}_*.jsonl"))
    if not files:
        raise RuntimeError(f"no corpus files found for {instrument}")
    old_evidence = _load_old_evidence(instrument)
    target_dates = set(CANDIDATE_DATES[instrument])

    results: dict[str, dict] = {}
    state: dict | None = None
    all_bars_so_far: list[dict] = []

    for path in files:
        day_str = path.stem.rsplit("_", 1)[-1]
        with path.open() as fh:
            day_bars = [json.loads(line) for line in fh if line.strip()]
        all_bars_so_far.extend(day_bars)

        if day_str not in target_dates:
            continue

        # A UTC-midnight-aligned day file's first ~5 hours of bars fall in
        # the PREVIOUS ET calendar date's evening (UTC 00:00 = ET 19:00/
        # 20:00 the day before, depending on DST) -- advance_strat_12hr_
        # miyagi correctly evaluates those as that prior trading_date's own
        # (typically already-past-9:30, terminal) state. Breaking on the
        # first terminal status -- exactly what replay/replay_engine.py
        # does NOT do, it just keeps calling evaluate() every candle
        # regardless -- would kill the loop before ever reaching the
        # file's OWN target date's 4am-9:30am ET setup window. Process
        # every bar unconditionally; only capture the result once
        # next_state's OWN trading_date matches this file's target day.
        day_state: dict | None = None
        day_candidate = None
        day_terminal_state: dict | None = None
        for b in day_bars:
            ts = datetime.fromisoformat(b["timestamp"])
            next_state, candidate = advance_strat_12hr_miyagi(
                bars_5m=all_bars_so_far,
                current_bar_ts=ts,
                instrument=instrument,
                persisted_state=day_state,
            )
            day_state = next_state
            if next_state.get("trading_date") != day_str:
                continue
            if candidate is not None:
                day_candidate = candidate
                break
            if next_state.get("status") in ("EXPIRED", "INVALIDATED"):
                day_terminal_state = next_state
                break

        old = old_evidence.get(day_str, {})
        if day_candidate is not None:
            entry = float(day_candidate["entry"])
            stop = float(day_candidate["stop"])
            direction = day_candidate["direction"]
            tick = TICK_SIZE[instrument]
            risk_pts = abs(entry - stop)
            risk_ticks = risk_pts / tick
            max_ticks = MAX_STOP_TICKS[instrument]
            results[day_str] = {
                "date": day_str,
                "instrument": instrument,
                "direction": direction,
                "status": "TRIGGERED",
                "entry": entry,
                "causal_stop": stop,
                "old_stop": old.get("stop"),
                "stop_changed": (
                    old.get("stop") is not None and abs(old["stop"] - stop) > 1e-6
                ),
                "stop_points": round(risk_pts, 4),
                "stop_ticks": round(risk_ticks, 2),
                "max_stop_ticks": max_ticks,
                "passes_stop_cap": risk_ticks <= max_ticks,
                "old_result": old.get("result"),
                "old_net_pnl": old.get("net_pnl"),
            }
        else:
            final = day_terminal_state or day_state or {}
            results[day_str] = {
                "date": day_str,
                "instrument": instrument,
                "status": final.get("status", "UNKNOWN"),
                "invalidation": final.get("invalidation"),
                "old_result": old.get("result"),
                "old_net_pnl": old.get("net_pnl"),
                "note": "never triggered under causal logic (no entry/stop to evaluate)",
            }

    # Preserve requested chronological order, report any date the corpus
    # coverage couldn't reach (should be zero once the full pull lands).
    ordered = []
    for d in CANDIDATE_DATES[instrument]:
        if d in results:
            ordered.append(results[d])
        else:
            ordered.append({"date": d, "instrument": instrument, "status": "CORPUS_DATE_MISSING"})
    return ordered


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(pct / 100 * (len(s) - 1))))
    return s[idx]


def main() -> int:
    all_results: dict[str, list[dict]] = {}
    summary: dict[str, dict] = {}
    for instrument in ("MNQ", "MES"):
        rows = _run_instrument(instrument)
        all_results[instrument] = rows
        triggered = [r for r in rows if r.get("status") == "TRIGGERED"]
        passing = [r for r in triggered if r["passes_stop_cap"]]
        failing = [r for r in triggered if not r["passes_stop_cap"]]
        ticks = [r["stop_ticks"] for r in triggered]
        summary[instrument] = {
            "triggered": len(triggered),
            "pass_stop_cap": len(passing),
            "fail_stop_cap": len(failing),
            "pct_rejected": round(100 * len(failing) / len(triggered), 1) if triggered else None,
            "median_stop_ticks": _percentile(ticks, 50),
            "p75_stop_ticks": _percentile(ticks, 75),
            "p90_stop_ticks": _percentile(ticks, 90),
            "max_stop_ticks_observed": max(ticks) if ticks else None,
            "stop_changed_from_old_count": sum(1 for r in triggered if r.get("stop_changed")),
        }

    out = {"results": all_results, "summary": summary}
    out_path = REPO / "scripts/miyagi_causal_stop_distribution_results.json"
    out_path.write_text(json.dumps(out, indent=2, default=str) + "\n")

    print(json.dumps(summary, indent=2))
    print()
    print("=== Per-trade detail ===")
    for instrument in ("MNQ", "MES"):
        print(f"\n--- {instrument} ---")
        for r in all_results[instrument]:
            if r.get("status") == "TRIGGERED":
                cap = "PASS" if r["passes_stop_cap"] else "FAIL"
                print(
                    f"  {r['date']} {r['direction']:5s} entry={r['entry']:.3f} "
                    f"causal_stop={r['causal_stop']:.3f} ticks={r['stop_ticks']:.1f} "
                    f"(cap={r['max_stop_ticks']}) [{cap}] old_result={r['old_result']} "
                    f"stop_changed={r['stop_changed']}"
                )
            else:
                print(f"  {r['date']} status={r.get('status')} old_result={r.get('old_result')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
