#!/usr/bin/env python3
"""4HR Re-Trigger -- OFFLINE gate-overlap attribution (no engine, no replay).

The real engine's evaluate() checks gates SEQUENTIALLY with early returns
(TRENDING -> regime/GEX -> STRONG-trend -> volume -> bar-close -> EMA-stack
-> strategy-selection -> entry-detached -> RR -> risk-layer). A candidate's
baseline classification (scripts/four_hr_retrigger_parity_audit_results.json)
is only the FIRST gate that blocked it -- later gates in the sequence were
NEVER EVALUATED for that candidate, so the baseline alone cannot answer
"would this candidate ALSO have failed gate X". This script answers that
directly from already-available data (corpus's own annotated fields +
advance_4hr_retrigger's own deterministic bracket), independent of and
complementary to the real ceiling-pass engine run
(scripts/four_hr_retrigger_ceiling_pass.py running in a #365 worktree).

For every one of the 157 known #334 candidates, re-derives the trigger bar
via the pure state machine (fast, no engine), looks up that bar's own
corpus row, and independently evaluates whether each of TRENDING,
STRONG-trend, EMA-stack, and RR>=2.0 would pass or fail -- giving a full
per-candidate boolean matrix and cross-tab against the baseline's actual
(first-gate-only) classification.

Caveat: does not model _admit_moderate's MODERATE-trend rescue path for
STRONG-trend/EMA-stack (a soft-admit exception in the real engine) --
flagged per-row so it can be distinguished from a hard fail.

Usage:
    python3 scripts/four_hr_retrigger_gate_overlap_offline.py --out <path>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from strategy.four_hr_retrigger import advance_4hr_retrigger  # noqa: E402

INSTRUMENTS = ("MNQ", "MES")
CORPUS = REPO / "data" / "replay_corpus_v1_5m_4hr_audit"
KNOWN_RESULTS = REPO / "scripts" / "four_hr_retrigger_stop_study_results.json"
BASELINE_RESULTS = REPO / "scripts" / "four_hr_retrigger_parity_audit_results.json"
ROLLING_WINDOW_DAYS = 5
MIN_RR = 2.0


def _json_lines(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _walk_instrument(instrument: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """Returns (per_day trigger info incl. entry/stop/target, bar_ts -> corpus row)."""
    files = sorted((CORPUS / instrument).glob(f"{instrument}_*.jsonl"))
    state: dict = {}
    window: list[dict] = []
    per_day: dict[str, dict[str, Any]] = {}
    rows_by_ts: dict[str, dict] = {}
    for f in files:
        for row in _json_lines(f):
            rows_by_ts[row["timestamp"]] = row
            ts = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            window.append(row)
            cutoff = ts - timedelta(days=ROLLING_WINDOW_DAYS)
            while window and datetime.fromisoformat(
                str(window[0]["timestamp"]).replace("Z", "+00:00")
            ) < cutoff:
                window.pop(0)
            state, candidate = advance_4hr_retrigger(
                bars_5m=window, current_bar_ts=ts, instrument=instrument,
                persisted_state=state,
            )
            day = state.get("trading_date")
            if day and day not in per_day:
                per_day[day] = {"trigger_bar_ts": None}
            if candidate is not None and day:
                per_day[day] = {
                    "trigger_bar_ts": row["timestamp"],
                    "direction": candidate.get("direction"),
                    "entry": candidate.get("entry"),
                    "stop": candidate.get("stop"),
                    "target": candidate.get("target"),
                }
    return per_day, rows_by_ts


def _rr(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    reward = abs(target - entry)
    return reward / risk if risk else float("inf")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    known = json.loads(KNOWN_RESULTS.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE_RESULTS.read_text(encoding="utf-8"))
    baseline_by_key = {(t["instrument"], t["date"]): t for t in baseline["trades"]}

    rows_out = []
    for instrument in INSTRUMENTS:
        print(f"[walk] {instrument}...", flush=True)
        per_day, rows_by_ts = _walk_instrument(instrument)
        known_days = {t["day"] for t in known["instruments"][instrument]["trades_baseline"]}

        for day in sorted(known_days):
            trig = per_day.get(day, {})
            bar_ts = trig.get("trigger_bar_ts")
            base = baseline_by_key.get((instrument, day), {})
            if bar_ts is None:
                rows_out.append({"instrument": instrument, "date": day,
                                  "note": "no_trigger_in_pure_sm", "baseline_classification": base.get("classification")})
                continue
            corpus_row = rows_by_ts.get(bar_ts, {})
            direction = trig.get("direction")
            entry, stop, target = trig.get("entry"), trig.get("stop"), trig.get("target")

            market_condition = corpus_row.get("reconstructed_market_condition") or corpus_row.get("market_condition")
            trend_strength = corpus_row.get("trend_strength")
            ema9 = corpus_row.get("ema_9")
            ema21 = corpus_row.get("ema_21")
            ema55 = corpus_row.get("ema_55")
            close = corpus_row.get("close")

            passes_trending = market_condition == "TRENDING"
            passes_strong_trend = trend_strength == "STRONG"
            moderate_trend = trend_strength == "MODERATE"

            ema_available = None not in (ema9, ema21, ema55, close)
            if ema_available and direction == "LONG":
                passes_ema = close > ema9 > ema21 > ema55
            elif ema_available and direction == "SHORT":
                passes_ema = close < ema9 < ema21 < ema55
            else:
                passes_ema = None

            rr = _rr(entry, stop, target) if None not in (entry, stop, target) else None
            passes_rr = (rr >= MIN_RR) if rr is not None else None

            rows_out.append({
                "instrument": instrument, "date": day, "direction": direction,
                "entry": entry, "stop": stop, "target": target, "rr_ratio": rr,
                "market_condition": market_condition, "trend_strength": trend_strength,
                "baseline_classification": base.get("classification"),
                "passes_trending": passes_trending,
                "passes_strong_trend_hard": passes_strong_trend,
                "moderate_trend_soft_admit_eligible": moderate_trend,
                "passes_ema_stack": passes_ema,
                "passes_rr_2_0": passes_rr,
                # Would survive ALL FOUR of TRENDING/STRONG-trend/EMA-stack/RR
                # (ignoring _admit_moderate soft-rescue, and ignoring
                # downstream entry-detached/stop-cap/confluence checks, which
                # are NOT part of this offline model -- see real ceiling-pass
                # engine run for those).
                "would_pass_all_four_hard": bool(
                    passes_trending and passes_strong_trend
                    and (passes_ema in (True, None)) and (passes_rr in (True, None))
                ),
            })

    # Cross-tab: of the candidates baseline-blocked by TRENDING specifically,
    # how many would ALSO fail STRONG-trend / EMA-stack / RR (hard, no
    # _admit_moderate rescue modeled)?
    trending_blocked = [r for r in rows_out if r.get("baseline_classification") == "MARKET_CONDITION_NOT_TRENDING"]
    overlap = {
        "trending_blocked_count": len(trending_blocked),
        "of_those__also_fails_strong_trend_hard": sum(
            1 for r in trending_blocked if r.get("passes_strong_trend_hard") is False
        ),
        "of_those__strong_trend_moderate_soft_admit_eligible": sum(
            1 for r in trending_blocked if r.get("moderate_trend_soft_admit_eligible")
        ),
        "of_those__also_fails_ema_stack_given_strong_trend_passes": sum(
            1 for r in trending_blocked
            if r.get("passes_strong_trend_hard") and r.get("passes_ema_stack") is False
        ),
        "of_those__also_fails_rr_given_trend_and_ema_pass": sum(
            1 for r in trending_blocked
            if r.get("passes_strong_trend_hard")
            and r.get("passes_ema_stack") in (True, None)
            and r.get("passes_rr_2_0") is False
        ),
        "of_those__would_clear_all_three_downstream_hard_checks": sum(
            1 for r in trending_blocked if r.get("would_pass_all_four_hard")
        ),
    }

    rr_blocked = [r for r in rows_out if r.get("baseline_classification") == "RR_BELOW_MINIMUM"]
    ema_blocked = [r for r in rows_out if r.get("baseline_classification") == "EMA_STACK_NOT_ALIGNED"]

    out = {
        "note": "Offline model: TRENDING/STRONG-trend/EMA-stack computed directly from corpus "
                "fields at the trigger bar; RR computed from advance_4hr_retrigger's own "
                "deterministic entry/stop/target. Does NOT model _admit_moderate's soft-rescue "
                "path, ENTRY_DETACHED_FROM_PRICE, stop_too_wide/max_stop_ticks, target_too_close, "
                "or min_confluence_grade -- those require the real engine (see the ceiling-pass "
                "ReplayEngine run for the authoritative executable-population numbers).",
        "trending_blocked_overlap": overlap,
        "rr_blocked_count": len(rr_blocked),
        "rr_blocked_also_fails_ema_stack": sum(1 for r in rr_blocked if r.get("passes_ema_stack") is False),
        "ema_blocked_count": len(ema_blocked),
        "rows": rows_out,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n[done] wrote {args.out}")
    print(json.dumps(overlap, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
