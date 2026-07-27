#!/usr/bin/env python3
"""ORB Reclaim V4-R -- raw detector population + honest-fill simulation.

Preregistration: docs/strategy-rules/ORB_RECLAIM_V4R_PREREGISTRATION_2026-07-27.md

Answers "does the rule itself have edge, before any runtime risk/quality
gate is applied" (population 1 of 3 required by the preregistration). Reads
orb_status/session/vwap directly off data/replay_corpus_v1_market_condition_
fixed's own corpus rows (produced by scripts/polygon_to_replay.py's
derive_orb_status -- proven identical to the live Pine transition logic and
to scripts/csv_to_replay.py's version, tests/test_replay_orb_status.py).
No new state-machine logic -- this script only labels existing, proven
transition primitives with the three candidate definitions and simulates
the strategy's own frozen bracket/fill mechanics (byte-identical to
strategy/signal_engine.py::_try_orb_reclaim and execution/paper_broker.py's
ioc_limit single-bar-close semantics) with NO risk/quality gate applied --
no TRENDING, no confluence, no stop-cap, no entry-detachment, nothing but
the strategy's own reclaimed_high+VWAP-above requirement and its bracket.

Three variant tags per candidate (a candidate may qualify for more than
one):
  - first_cross: reclaimed_high + VWAP-above (today's actual production
    definition -- no session or rejection restriction)
  - v4_original: first_cross + session==new_york + true_reclaim (Pass 1's
    own "true_reclaim" flag = any earlier same-level bar closed above the
    ORB high, no rejection required)
  - v4_r: first_cross + session==new_york + prior_rejected_high (this
    study's frozen definition = an earlier same-level bar independently
    completed the proven rejected_high transition)

Fill/exit mechanics mirror execution/paper_broker.py exactly:
  - ioc_limit: fill/no-fill decided ONCE using the trigger bar's own CLOSE
    as market_price (replay/replay_engine.py:692-693's own call site,
    market_price=candle.close of the SAME candle the decision fired on) --
    not a multi-bar touch search.
  - Walk-forward stop/target resolution across subsequent bars (spanning
    day boundaries -- orb_reclaim is NOT in execution/day_only_exit.py's
    DAY_ONLY_STRATEGIES, confirmed by direct grep before writing this
    script), pessimistic same-bar-both-hit resolution (stop wins),
    matching production's fill_pessimistic_both_hit=True default.
  - $1.48 round-trip commission applied at the analysis layer only,
    matching every other honest-fill lane in this repo.

Corpus: data/replay_corpus_v1_market_condition_fixed (MNQ+MES, 313 daily
files each, 2025-07-24..2026-07-23 -- the full canonical range, matches
PR #352's own substrate exactly, confirmed in the preregistration doc).

Usage:
    python3 scripts/orb_reclaim_v4r_detector.py --out <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from execution.paper_broker import TICK_SIZE as TICK, TICK_VALUE  # noqa: E402

CORPUS = REPO / "data" / "replay_corpus_v1_market_condition_fixed"
INSTRUMENTS = ("MNQ", "MES")
MAX_ORB_STOP_TICKS = {"MNQ": 80, "MES": 40}
MIN_TARGET_POINTS = {"MNQ": 15.0, "MES": 15.0}
ENTRY_OFFSET_TICKS = 2
STOP_BELOW_ORB_LOW_TICKS = 4
TARGET_RR_MULTIPLE = 2.5
CANONICAL_ENTRY_TOLERANCE_TICKS = {"MNQ": 32.0, "MES": 16.0}
COMMISSION_ROUND_TRIP = 1.48
HALVES = {"H1": ("2025-07-24", "2026-01-23"), "H2": ("2026-01-24", "2026-07-23")}


def _load_all_bars(instrument: str) -> list[dict]:
    files = sorted((CORPUS / instrument).glob(f"{instrument}_*.jsonl"))
    if len(files) != 313:
        raise RuntimeError(f"{instrument}: expected 313 daily files, found {len(files)}")
    bars: list[dict] = []
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                bars.append(json.loads(line))
    bars.sort(key=lambda b: b["timestamp"])
    return bars


def _period_label(date_str: str) -> str:
    for label, (start, end) in HALVES.items():
        if start <= date_str <= end:
            return label
    return "OUT_OF_RANGE"


def _bracket(instrument: str, orb_high: float, orb_low: float) -> dict:
    tick = TICK[instrument]
    entry = float(orb_high) + tick * ENTRY_OFFSET_TICKS
    stop = max(
        float(orb_low) - tick * STOP_BELOW_ORB_LOW_TICKS,
        entry - tick * MAX_ORB_STOP_TICKS[instrument],
    )
    risk = entry - stop
    target = entry + max(risk * TARGET_RR_MULTIPLE, MIN_TARGET_POINTS[instrument])
    rr = (target - entry) / risk if risk > 0 else None
    return {"entry": entry, "stop": stop, "target": target, "risk": risk, "rr_ratio": rr}


def _find_candidates(bars: list[dict], instrument: str) -> list[dict]:
    """Walk the full bar stream once, tracking same-day/same-ORB-level
    transition history, tagging every reclaimed_high+VWAP-above bar with
    its variant eligibility and attempt index."""
    candidates: list[dict] = []
    day_state: dict[str, Any] = {}
    current_day: Optional[str] = None
    attempt_index = 0
    # history of (orb_high, orb_low, closed_above_before, was_rejected_before)
    level_seen_above: set[tuple] = set()
    level_seen_rejected: set[tuple] = set()

    for bar in bars:
        ts = bar["timestamp"]
        day = ts[:10]
        if day != current_day:
            current_day = day
            attempt_index = 0
            level_seen_above.clear()
            level_seen_rejected.clear()

        orb_high = bar.get("orb_high")
        orb_low = bar.get("orb_low")
        orb_status = bar.get("orb_status")
        close = bar.get("close")
        if orb_high is None or orb_low is None:
            continue
        level_key = (round(float(orb_high), 4), round(float(orb_low), 4))

        if orb_status == "reclaimed_high":
            true_reclaim = level_key in level_seen_above
            prior_rejected_high = level_key in level_seen_rejected
            vwap_above = bar.get("price_vs_vwap") == "above"
            session_ny = bar.get("session") == "new_york"

            if vwap_above:
                attempt_index += 1
                bracket = _bracket(instrument, orb_high, orb_low)
                candidates.append({
                    "instrument": instrument,
                    "date": day,
                    "bar_ts": ts,
                    "attempt_index": attempt_index,
                    "session": bar.get("session"),
                    "market_condition": bar.get("market_condition"),
                    "trend_strength": bar.get("trend_strength"),
                    "close_at_trigger": close,
                    "orb_high": orb_high,
                    "orb_low": orb_low,
                    "true_reclaim": true_reclaim,
                    "prior_rejected_high": prior_rejected_high,
                    "first_cross_eligible": True,
                    "v4_original_eligible": session_ny and true_reclaim,
                    "v4_r_eligible": session_ny and prior_rejected_high,
                    **bracket,
                })

        # Update same-day/same-level transition history AFTER using it above
        # (a bar can't count as its own "earlier" evidence).
        if close is not None:
            if float(close) > float(orb_high):
                level_seen_above.add(level_key)
        if orb_status == "rejected_high":
            level_seen_rejected.add(level_key)

    return candidates


def _simulate_fill_and_exit(
    candidate: dict, bars_by_ts: dict[str, dict], sorted_ts: list[str], ts_index: dict[str, int],
    instrument: str,
) -> dict:
    tick = TICK[instrument]
    tol = CANONICAL_ENTRY_TOLERANCE_TICKS[instrument] * tick
    entry, stop, target = candidate["entry"], candidate["stop"], candidate["target"]
    market_price = candidate["close_at_trigger"]

    limit_px = entry + tol
    if market_price > limit_px:
        return {"filled": False, "result": "CANCELLED", "exit_reason": "ENTRY_NOT_FILLED",
                "net_pnl": 0.0, "fill_entry": None, "exit_price": None, "resolved_date": None}
    fill_entry = min(limit_px, market_price)  # no adverse-slippage sweep in this pass (base case)

    idx = ts_index[candidate["bar_ts"]]
    point_value = TICK_VALUE[instrument] / tick  # $/point, derived from production tick constants
    for j in range(idx + 1, len(sorted_ts)):
        bar = bars_by_ts[sorted_ts[j]]
        high, low = float(bar["high"]), float(bar["low"])
        stop_hit = low <= stop
        target_hit = high >= target
        if stop_hit and target_hit:
            # pessimistic_both_hit=True (production default) -> STOP wins
            pnl_pts = stop - fill_entry
            return {
                "filled": True, "result": "LOSS" if pnl_pts < 0 else "BREAKEVEN",
                "exit_reason": "STOP", "net_pnl": round(pnl_pts * point_value - COMMISSION_ROUND_TRIP, 2),
                "fill_entry": fill_entry, "exit_price": stop, "resolved_date": bar["timestamp"][:10],
            }
        if stop_hit:
            pnl_pts = stop - fill_entry
            return {
                "filled": True, "result": "LOSS" if pnl_pts < 0 else "BREAKEVEN",
                "exit_reason": "STOP", "net_pnl": round(pnl_pts * point_value - COMMISSION_ROUND_TRIP, 2),
                "fill_entry": fill_entry, "exit_price": stop, "resolved_date": bar["timestamp"][:10],
            }
        if target_hit:
            pnl_pts = target - fill_entry
            return {
                "filled": True, "result": "WIN", "exit_reason": "TARGET",
                "net_pnl": round(pnl_pts * point_value - COMMISSION_ROUND_TRIP, 2),
                "fill_entry": fill_entry, "exit_price": target, "resolved_date": bar["timestamp"][:10],
            }
    # ran off the end of the corpus still open
    return {"filled": True, "result": "UNRESOLVED", "exit_reason": "CORPUS_END",
            "net_pnl": None, "fill_entry": fill_entry, "exit_price": None, "resolved_date": None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    all_candidates: list[dict] = []
    for instrument in INSTRUMENTS:
        bars = _load_all_bars(instrument)
        bars_by_ts = {b["timestamp"]: b for b in bars}
        sorted_ts = [b["timestamp"] for b in bars]
        ts_index = {ts: i for i, ts in enumerate(sorted_ts)}

        candidates = _find_candidates(bars, instrument)
        for cand in candidates:
            sim = _simulate_fill_and_exit(cand, bars_by_ts, sorted_ts, ts_index, instrument)
            cand.update(sim)
            cand["half"] = _period_label(cand["date"])
        all_candidates.extend(candidates)
        print(f"[detector] {instrument}: {len(candidates)} raw candidates "
              f"({sum(1 for c in candidates if c['v4_original_eligible'])} v4_original, "
              f"{sum(1 for c in candidates if c['v4_r_eligible'])} v4_r)", flush=True)

    out = {
        "corpus": str(CORPUS.relative_to(REPO)),
        "instruments": list(INSTRUMENTS),
        "candidate_count": len(all_candidates),
        "candidates": all_candidates,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[done] wrote {args.out} ({len(all_candidates)} total raw candidates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
