#!/usr/bin/env python3
"""4HR Re-Trigger Batch-1 evidence study: fixed-1H-stop P&L, walk-forward, slippage.

Offline study only. Imports the canonical entry/stop state machine
(`strategy.four_hr_retrigger.advance_4hr_retrigger`, PR #317) and the
canonical day-only 4PM exit (`execution.day_only_exit`, PR #318) to generate
and resolve real candidates against real Polygon 5-minute data. No import of
`webhook/runner.py` or `replay/replay_engine.py`; no writes to journals,
config, or orders; no changes to strategy/detector/stop logic, replay
formulas, runtime configuration, risk rules, broker code, deployment, or
enablement.

Scope: `docs/strategy-rules/4HR_AUDIT_HANDOFF.md` Section 5 ("Batch 1"),
narrowed by operator authorization (2026-07-26) to the ONE stop currently
executable: `strategy/four_hr_retrigger.py::advance_4hr_retrigger` fixes the
stop once, at entry, to the most recently completed 1H candle's low/high
(`_completed_one_hour_stop`) — the state machine returns `previous` unchanged
for the rest of the day once `status == "TRIGGERED"`
(strategy/four_hr_retrigger.py:169-170), so it never advances or ratchets
that stop again. There is no ratchet implementation anywhere in the
codebase (`grep -rn "ratchet\\|RATCHET" --include="*.py" .` matches only
`stocks_advisory/` files, unrelated). The rules doc's own PASS/FAIL gate
(4HR_AUDIT_HANDOFF.md Section 3) is the ratcheting variant, which does not
exist to test. This script therefore evidences what the canonical strategy
ACTUALLY executes today (the fixed-at-entry stop) and says so explicitly in
its own classification output — it does not build ratchet logic to satisfy
the doc's original gate (that would be a stop-logic change, out of scope).

Cost assumptions (explicit, matching established system conventions, not a
new one-off number per the F4 finding in 4HR_AUDIT_HANDOFF.md):
  - slippage: PaperBroker adverse market-order slippage. Baseline = 1.0
    tick, matching config/settings.py's production default
    (`fill_slippage_ticks = 1.0`). Sensitivity reported at 1/2/3 ticks.
  - pessimistic_both_hit=True (production default, config/settings.py) —
    a bar that straddles both stop and target resolves as the stop.
  - entry_fill_model="market" (production default) — the candidate's
    `entry` (the trigger level) fills immediately once the state machine
    detects the trigger was crossed on that bar; no lookahead.
  - commission: $1.48 round-trip, matching
    execution/mnq_strat_evidence.py::MNQ_COMMISSION_ROUND_TRIP and
    execution/mes_trend_consolidation_break_evidence.py::MES_COMMISSION_ROUND_TRIP
    (both instruments use the same value in this repo already).

Resolution order on the entry bar's day mirrors replay/replay_engine.py's own
day-only-exit wiring exactly (stop/target has precedence over the day-only
flatten on the same bar; a missing exact EOD bar fails closed — no fill is
recorded, the sample is reported as excluded, never substituted):
see replay/replay_engine.py:536-571.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution.broker_interface import BracketOrder
from execution.day_only_exit import is_after_eod_close, is_exact_eod_bar, resolve_paper_eod
from execution.paper_broker import NextBarOHLC, PaperBroker
from strategy.four_hr_retrigger import advance_4hr_retrigger

ET = ZoneInfo("America/New_York")
STRATEGY = "strat_4hr_retrigger"
TIMEFRAME = "5m"
COMMISSION_ROUND_TRIP = 1.48
TICK_SIZE = {"MNQ": 0.25, "MES": 0.25}
BASELINE_SLIPPAGE_TICKS = 1.0
SENSITIVITY_SLIPPAGE_TICKS = (1.0, 2.0, 3.0)
# Bounds how many trailing 5-min bars are handed to advance_4hr_retrigger per
# call (it re-filters whatever it's given). 600 bars = 50 hours, comfortably
# covers the widest real lookback the state machine ever needs (a Monday's
# prior-day reference bar is Sunday's 4-8pm 4H bucket -> <36h back). Purely a
# performance bound on THIS script's own driver loop, not a detector change.
HISTORY_WINDOW_BARS = 600


def load_bars(root: str | Path, instrument: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((Path(root) / instrument).glob(f"{instrument}_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rows.append({
                "ts": row["timestamp"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            })
    rows.sort(key=lambda r: r["ts"])
    for row in rows:
        row["_dt"] = datetime.fromisoformat(row["ts"].replace("Z", "+00:00")).astimezone(ET)
    return rows


@dataclass
class Candidate:
    instrument: str
    day: str
    direction: str
    entry: float
    stop: float
    target: float
    entry_time: datetime
    entry_index: int


def detect_candidates(instrument: str, bars: list[dict[str, Any]]) -> list[Candidate]:
    """One pass of the canonical state machine. Slippage-independent."""
    candidates: list[Candidate] = []
    persisted_state: Optional[dict] = None
    window: deque = deque(maxlen=HISTORY_WINDOW_BARS)
    for idx, bar in enumerate(bars):
        window.append(bar)
        next_state, candidate = advance_4hr_retrigger(
            bars_5m=window,
            current_bar_ts=bar["_dt"],
            instrument=instrument,
            persisted_state=persisted_state,
        )
        persisted_state = next_state
        if candidate is not None:
            candidates.append(Candidate(
                instrument=instrument,
                day=bar["_dt"].date().isoformat(),
                direction=candidate["direction"],
                entry=float(candidate["entry"]),
                stop=float(candidate["stop"]),
                target=float(candidate["target"]),
                entry_time=candidate["entry_time"],
                entry_index=idx,
            ))
    return candidates


def _mae_mfe_ticks(direction: str, entry: float, tick: float, bars: list[dict[str, Any]]) -> tuple[float, float]:
    """Max adverse / favorable excursion (ticks) over the bars actually observed."""
    if not bars:
        return 0.0, 0.0
    if direction == "LONG":
        worst = min(b["low"] for b in bars) - entry
        best = max(b["high"] for b in bars) - entry
    else:
        worst = entry - max(b["high"] for b in bars)
        best = entry - min(b["low"] for b in bars)
    return round(min(worst, 0.0) / tick, 2), round(max(best, 0.0) / tick, 2)


def resolve_candidate(
    candidate: Candidate, bars: list[dict[str, Any]], *, slippage_ticks: float
) -> dict[str, Any]:
    """Resolve one candidate through the real PaperBroker + day-only-exit path.

    Mirrors replay/replay_engine.py:511-571 exactly: entry fills at the
    candidate's own bar (the state machine already located the trigger
    cross), resolution starts strictly at the NEXT bar, stop/target checked
    before the day-only flatten on the exact same bar, a missing exact EOD
    bar fails closed (excluded, never substituted).
    """
    tick = TICK_SIZE[candidate.instrument]
    risk = abs(candidate.entry - candidate.stop)
    reward = abs(candidate.target - candidate.entry)
    order = BracketOrder(
        instrument=candidate.instrument,
        direction=candidate.direction,
        entry=candidate.entry,
        stop=candidate.stop,
        target=candidate.target,
        rr_ratio=round(reward / risk, 4) if risk else 0.0,
        strategy=STRATEGY,
        contracts=1,
    )
    broker = PaperBroker(
        slippage_ticks=slippage_ticks,
        pessimistic_both_hit=True,
        entry_fill_model="market",
    )
    entry_fill = broker.execute_bracket(order, market_price=candidate.entry)

    trade_date = candidate.entry_time.astimezone(ET).date()
    observed: list[dict[str, Any]] = []
    fill = None
    exit_index = None
    for j in range(candidate.entry_index + 1, len(bars)):
        fb = bars[j]
        if fb["_dt"].date() != trade_date:
            break
        if is_after_eod_close(fb["_dt"]):
            break
        observed.append(fb)
        fill = broker.resolve_position(NextBarOHLC(open=fb["open"], high=fb["high"], low=fb["low"]))
        if fill is not None:
            exit_index = j
            break
        if is_exact_eod_bar(fb["_dt"], TIMEFRAME):
            fill = resolve_paper_eod(
                broker,
                {
                    "instrument": candidate.instrument,
                    "direction": candidate.direction,
                    "entry": entry_fill.entry_price,
                    "contracts": 1,
                    "strategy": STRATEGY,
                },
                timestamp=fb["_dt"],
                timeframe=TIMEFRAME,
                close=fb["close"],
            )
            exit_index = j
            break

    mae_ticks, mfe_ticks = _mae_mfe_ticks(candidate.direction, entry_fill.entry_price, tick, observed)

    if fill is None:
        return {
            "instrument": candidate.instrument,
            "day": candidate.day,
            "direction": candidate.direction,
            "excluded": True,
            "exclusion_reason": "EOD_BAR_MISSING_FAIL_CLOSED",
        }

    net_pnl = round(float(fill.pnl_dollars or 0.0) - COMMISSION_ROUND_TRIP, 2)
    return {
        "instrument": candidate.instrument,
        "day": candidate.day,
        "direction": candidate.direction,
        "excluded": False,
        "entry_time": candidate.entry_time.isoformat(),
        "exit_reason": fill.exit_reason,
        "result": fill.result,
        "gross_pnl": round(float(fill.pnl_dollars or 0.0), 2),
        "commission": COMMISSION_ROUND_TRIP,
        "net_pnl": net_pnl,
        "mae_ticks": mae_ticks,
        "mfe_ticks": mfe_ticks,
        "bars_held": len(observed),
    }


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [t for t in trades if not t["excluded"]]
    excluded = [t for t in trades if t["excluded"]]
    pnls = [t["net_pnl"] for t in resolved]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    equity = peak = max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "candidates": len(trades),
        "resolved": len(resolved),
        "excluded_fail_closed": len(excluded),
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(resolved) - len(wins) - len(losses),
        "win_rate_pct": round(100 * len(wins) / len(resolved), 1) if resolved else None,
        "gross_pnl": round(sum(t["gross_pnl"] for t in resolved), 2) if resolved else 0.0,
        "commission_total": round(COMMISSION_ROUND_TRIP * len(resolved), 2),
        "net_pnl": round(sum(pnls), 2) if resolved else 0.0,
        "expectancy_per_trade": round(sum(pnls) / len(resolved), 2) if resolved else None,
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 3) if losses
            else ("infinite" if wins else None)
        ),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "max_drawdown": round(max_dd, 2),
        "avg_mae_ticks": round(sum(t["mae_ticks"] for t in resolved) / len(resolved), 2) if resolved else None,
        "avg_mfe_ticks": round(sum(t["mfe_ticks"] for t in resolved) / len(resolved), 2) if resolved else None,
    }


def chronological_halves(trades: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    ordered = sorted(trades, key=lambda t: (t["day"], t["instrument"]))
    mid = len(ordered) // 2
    return {"first_half": ordered[:mid], "second_half": ordered[mid:]}


def by_direction(trades: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "long": [t for t in trades if t["direction"] == "LONG"],
        "short": [t for t in trades if t["direction"] == "SHORT"],
    }


def run_instrument(root: str | Path, instrument: str) -> dict[str, Any]:
    bars = load_bars(root, instrument)
    candidates = detect_candidates(instrument, bars)
    variants: dict[str, Any] = {}
    for slip in SENSITIVITY_SLIPPAGE_TICKS:
        trades = [resolve_candidate(c, bars, slippage_ticks=slip) for c in candidates]
        key = f"slippage_{int(slip)}_tick"
        halves = chronological_halves(trades)
        directions = by_direction([t for t in trades if not t["excluded"]])
        variants[key] = {
            "overall": summarize(trades),
            "chronological_first_half": summarize(halves["first_half"]),
            "chronological_second_half": summarize(halves["second_half"]),
            "long": summarize(directions["long"]),
            "short": summarize(directions["short"]),
        }
    date_range = None
    if bars:
        date_range = [bars[0]["_dt"].date().isoformat(), bars[-1]["_dt"].date().isoformat()]
    return {
        "instrument": instrument,
        "bars_loaded": len(bars),
        "date_range": date_range,
        "candidates_detected": len(candidates),
        "variants": variants,
        "trades_baseline": [
            resolve_candidate(c, bars, slippage_ticks=BASELINE_SLIPPAGE_TICKS) for c in candidates
        ],
    }


def _classify_one(instrument: str, report: dict[str, Any], baseline_key: str) -> dict[str, Any]:
    overall = report["variants"][baseline_key]["overall"]
    first = report["variants"][baseline_key]["chronological_first_half"]
    second = report["variants"][baseline_key]["chronological_second_half"]
    long_ = report["variants"][baseline_key]["long"]
    short_ = report["variants"][baseline_key]["short"]
    slip_nets = [
        report["variants"][f"slippage_{int(s)}_tick"]["overall"]["net_pnl"]
        for s in SENSITIVITY_SLIPPAGE_TICKS
    ]
    resolved = overall["resolved"] or 0
    if resolved < 20:
        verdict = "WAIT"
        reason = f"insufficient sample ({resolved} resolved trades < 20) -- do not extrapolate"
    elif (first["net_pnl"] or 0) > 0 and (second["net_pnl"] or 0) > 0 and all(n > 0 for n in slip_nets):
        verdict = "PROMISING BUT UNPROVEN"
        reason = (
            "positive net P&L in both chronological halves and at every slippage "
            "sensitivity point (1/2/3 ticks) -- a single in-sample offline study, "
            "not forward/live evidence, so it cannot be VALIDATED from this alone"
        )
    elif (second["net_pnl"] or 0) <= 0 or slip_nets[-1] <= 0:
        verdict = "BROKEN"
        reason = (
            "fails walk-forward and/or slippage-sensitivity: "
            f"H1=${first['net_pnl']} H2=${second['net_pnl']}, "
            f"net P&L at 1/2/3-tick slippage = {slip_nets} -- "
            "degrades to non-positive under conditions the documented rule must survive"
        )
    else:
        verdict = "PROMISING BUT UNPROVEN"
        reason = "positive overall but not uniformly robust across every check -- see halves/slippage/direction detail"
    return {
        "instrument": instrument,
        "verdict": verdict,
        "reason": reason,
        "resolved_trades": resolved,
        "net_pnl_baseline": overall["net_pnl"],
        "net_pnl_by_slippage_tick": dict(zip((1, 2, 3), slip_nets)),
        "chronological_first_half_net": first["net_pnl"],
        "chronological_second_half_net": second["net_pnl"],
        "long_net_pnl": long_["net_pnl"],
        "short_net_pnl": short_["net_pnl"],
    }


def classify(report_by_instrument: dict[str, Any]) -> dict[str, Any]:
    baseline_key = f"slippage_{int(BASELINE_SLIPPAGE_TICKS)}_tick"
    per_instrument = {
        instrument: _classify_one(instrument, report, baseline_key)
        for instrument, report in report_by_instrument.items()
    }
    combined_resolved = sum(v["resolved_trades"] for v in per_instrument.values())
    combined_net = sum(v["net_pnl_baseline"] or 0.0 for v in per_instrument.values())
    return {
        "per_instrument": per_instrument,
        "combined_resolved_trades": combined_resolved,
        "combined_net_pnl_baseline": round(combined_net, 2),
        "warning": (
            "Per-instrument verdicts are NOT uniform -- do not report a single "
            "blended verdict or a single blended net P&L number as if it applied "
            "to both instruments equally. See feedback_walk_forward_before_shipping: "
            "a full-period/combined aggregate can flip sign or hide fragility that "
            "only shows up split by instrument, half, and slippage."
        ),
        "note": (
            "This evaluates the stop strategy/four_hr_retrigger.py actually "
            "executes today (fixed-at-entry completed-1H-candle stop). The "
            "documented ratcheting 1H-flip stop (4HR_AUDIT_HANDOFF.md Section 3, "
            "the doc's own PASS/FAIL gate for 'the documented strategy') has no "
            "implementation anywhere in this codebase and was NOT built or "
            "tested here -- building it would be a stop-logic change, out of "
            "this study's authorized scope. A ratcheting stop can only ever be "
            "as-or-more favorable than a fixed one (it moves in the trade's "
            "favor and never against it), so this result is not evidence "
            "against the documented rule -- it is simply silent on it."
        ),
        "options_p&l": "OUT OF SCOPE, separately blocked on missing historical QQQ options-chain data -- not attempted",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candles", default="data/replay_polygon_5m")
    parser.add_argument("--instrument", action="append", choices=("MNQ", "MES"))
    parser.add_argument("--out", default="scripts/four_hr_retrigger_stop_study_results.json")
    args = parser.parse_args()
    instruments = args.instrument or ["MNQ", "MES"]

    report_by_instrument = {
        instrument: run_instrument(args.candles, instrument) for instrument in instruments
    }
    verdict = classify(report_by_instrument)

    output = {
        "assumptions": {
            "commission_round_trip_usd": COMMISSION_ROUND_TRIP,
            "baseline_slippage_ticks": BASELINE_SLIPPAGE_TICKS,
            "sensitivity_slippage_ticks": list(SENSITIVITY_SLIPPAGE_TICKS),
            "pessimistic_both_hit": True,
            "entry_fill_model": "market",
            "stop_definition": "fixed_at_entry_completed_1h_candle (canonical executable stop; NOT the documented ratcheting variant, which is unimplemented)",
            "resolution": "5m bars, strictly-prior closed bars only, no lookahead",
            "day_only_exit": "execution.day_only_exit (PR #318), 15:55-16:00 ET exact bar, stop/target take precedence, missing EOD bar fails closed (excluded)",
        },
        "instruments": report_by_instrument,
        "classification": verdict,
    }
    Path(args.out).write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(json.dumps(verdict, indent=2))
    print(f"\nFull results written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
