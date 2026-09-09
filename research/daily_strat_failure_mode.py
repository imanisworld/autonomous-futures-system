#!/usr/bin/env python3
"""Causal Daily STRAT failure-mode study for MES/MNQ.

RESEARCH ONLY. This module never imports the live runner, never submits orders,
and never changes runtime/config/risk state.

Purpose
-------
Establish the untouched Daily-timeframe baseline before testing repairs. The
baseline deliberately reuses the existing shadow STRAT bracket contract:

* entry: one tick beyond the previous completed Daily bar high/low;
* stop: one tick beyond the opposite side of that same completed Daily bar;
* target: 2R from the planned entry;
* one contract;
* one adverse tick on the simulated entry/stop market fills;
* pessimistic stop-first handling when OHLC cannot establish intrabar order;
* $1.48 round-turn commission at the analysis layer.

Daily bars are CME-session bars, 18:00 ET through 17:00 ET next calendar day.
The current Daily bar is NEVER classified from its final OHLC to back-date an
entry. A stop-entry is considered only when a completed 5-minute bar first
reaches the already-known previous-Day boundary. That is the no-lookahead
contract for this study.

Canonical populations
---------------------
``daily_22_continuation``
    Previous completed Daily bar is directional; current day first breaks in
    the same direction.
``daily_22_reversal``
    Generic 2-2 reversal only. 1-2-2 and 3-2-2 are excluded because the shared
    classifier gives those more-specific identities.
``daily_32``
    Previous completed Daily bar is outside; current day first chooses one
    side. A single 5-minute bar that breaks both sides is ambiguous and skipped.
``daily_322_reversal``
    Two-bars-back Daily bar is outside, previous Daily bar is directional, and
    the current day breaks the opposite side.
``daily_222_slice``
    Research slice of ``daily_22_continuation`` where the two prior completed
    Daily bars are directional in the same direction. This is NOT a new
    canonical strategy identity: the production classifier correctly labels a
    2-2-2 same-direction sequence as ``strat_22_continuation``.

The first run is baseline-only. Do not add entry/stop/target/filter variants
until this report identifies the binding failure mode. That keeps the repair
sequence one-variable-at-a-time instead of tuning a grid after seeing results.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import yaml

from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker, TICK_SIZE
from strategy.strat_classifier import (
    OUTSIDE_BAR,
    TWO_DOWN,
    TWO_UP,
    StratBar,
    classify_bar,
)


ET = ZoneInfo("America/New_York")
COMMISSION_RT = 1.48
MIN_SESSION_5M_BARS = 200
FAMILIES = (
    "daily_22_continuation",
    "daily_22_reversal",
    "daily_32",
    "daily_322_reversal",
    "daily_222_slice",
)


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class DailyBar:
    trading_day: date
    open: float
    high: float
    low: float
    close: float
    n_sub_bars: int
    complete: bool


@dataclass(frozen=True)
class DailyCandidate:
    family: str
    instrument: str
    trading_day: date
    direction: str
    trigger_ts: datetime
    planned_entry: float
    stop: float
    target: float
    stop_ticks: float
    previous_type: str
    two_back_type: str
    trigger_kind: str


@dataclass(frozen=True)
class TradeResult:
    family: str
    instrument: str
    trading_day: str
    direction: str
    trigger_ts: str
    status: str
    result: str
    planned_entry: float
    fill_entry: float | None
    stop: float
    target: float
    stop_ticks: float
    gross_pnl: float
    net_pnl: float
    bars_to_exit: int | None
    mae_ticks: float | None
    mfe_ticks: float | None
    current_stop_cap_ticks: float | None
    stop_cap_pass: bool | None


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("UTC"))
    return ts.astimezone(ET)


def load_5m_corpus(root: Path, instrument: str) -> list[Bar]:
    """Read + dedupe the gitignored Polygon 5m cache for one instrument."""
    directory = root / instrument
    paths = sorted(directory.glob(f"{instrument}_*.jsonl"))
    if not paths:
        raise RuntimeError(
            f"No 5m corpus found under {directory}. "
            "This study requires the local gitignored replay_polygon_5m cache."
        )
    deduped: dict[datetime, Bar] = {}
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                ts = _parse_ts(str(row["timestamp"]))
                deduped[ts] = Bar(
                    ts=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                )
    return [deduped[key] for key in sorted(deduped)]


def trading_day_for_bar(ts: datetime) -> date | None:
    """Map a Globex 5m bar to the 18:00-17:00 ET CME trading day."""
    local = ts.astimezone(ET)
    clock = local.time().replace(tzinfo=None)
    if clock >= time(18, 0):
        return local.date() + timedelta(days=1)
    if clock < time(17, 0):
        return local.date()
    return None  # 17:00-18:00 ET maintenance window


def group_sessions(bars: Iterable[Bar]) -> dict[date, list[Bar]]:
    grouped: dict[date, list[Bar]] = defaultdict(list)
    for bar in bars:
        key = trading_day_for_bar(bar.ts)
        if key is not None:
            grouped[key].append(bar)
    return {day: sorted(rows, key=lambda b: b.ts) for day, rows in sorted(grouped.items())}


def build_daily_bars(sessions: dict[date, list[Bar]]) -> list[DailyBar]:
    out: list[DailyBar] = []
    for day, rows in sorted(sessions.items()):
        if not rows:
            continue
        out.append(
            DailyBar(
                trading_day=day,
                open=rows[0].open,
                high=max(b.high for b in rows),
                low=min(b.low for b in rows),
                close=rows[-1].close,
                n_sub_bars=len(rows),
                complete=len(rows) >= MIN_SESSION_5M_BARS,
            )
        )
    return out


def _daily_type(current: DailyBar, previous: DailyBar) -> str:
    return classify_bar(
        StratBar(high=current.high, low=current.low),
        StratBar(high=previous.high, low=previous.low),
    )


def baseline_bracket(previous: DailyBar, direction: str, tick: float) -> tuple[float, float, float]:
    """Exact existing shadow-family prior-bar-break / opposite-side / 2R contract."""
    if direction == "LONG":
        entry = previous.high + tick
        stop = previous.low - tick
        risk = entry - stop
        target = entry + 2.0 * risk
    elif direction == "SHORT":
        entry = previous.low - tick
        stop = previous.high + tick
        risk = stop - entry
        target = entry - 2.0 * risk
    else:
        raise ValueError(direction)
    return entry, stop, target


def _first_directional_trigger(
    bars: list[Bar], previous: DailyBar, direction: str, tick: float
) -> tuple[datetime, str] | None:
    entry, _, _ = baseline_bracket(previous, direction, tick)
    for bar in bars:
        if direction == "LONG" and bar.high >= entry:
            return bar.ts, "gap" if bar.open >= entry else "touch"
        if direction == "SHORT" and bar.low <= entry:
            return bar.ts, "gap" if bar.open <= entry else "touch"
    return None


def _first_outside_choice(
    bars: list[Bar], previous: DailyBar, tick: float
) -> tuple[str, datetime, str] | None:
    long_entry, _, _ = baseline_bracket(previous, "LONG", tick)
    short_entry, _, _ = baseline_bracket(previous, "SHORT", tick)
    for bar in bars:
        long_hit = bar.high >= long_entry
        short_hit = bar.low <= short_entry
        if long_hit and short_hit:
            return None  # intrabar order unknown; fail closed
        if long_hit:
            return "LONG", bar.ts, "gap" if bar.open >= long_entry else "touch"
        if short_hit:
            return "SHORT", bar.ts, "gap" if bar.open <= short_entry else "touch"
    return None


def detect_candidates(
    instrument: str,
    sessions: dict[date, list[Bar]],
    daily: list[DailyBar],
) -> list[DailyCandidate]:
    tick = float(TICK_SIZE.get(instrument, 0.25))
    by_day = {d.trading_day: d for d in daily}
    days = [d.trading_day for d in daily]
    candidates: list[DailyCandidate] = []

    for i in range(3, len(days)):
        day = days[i]
        cur = by_day[day]
        prev = by_day[days[i - 1]]
        two_back = by_day[days[i - 2]]
        three_back = by_day[days[i - 3]]
        if not (cur.complete and prev.complete and two_back.complete and three_back.complete):
            continue

        prev_type = _daily_type(prev, two_back)
        two_back_type = _daily_type(two_back, three_back)
        rows = sessions.get(day, [])
        if not rows:
            continue

        specs: list[tuple[str, str]] = []
        if prev_type in {TWO_UP, TWO_DOWN}:
            same = "LONG" if prev_type == TWO_UP else "SHORT"
            reverse = "SHORT" if prev_type == TWO_UP else "LONG"
            specs.append(("daily_22_continuation", same))
            # Preserve canonical precedence: 1-2-2 and 3-2-2 are not generic 2-2 reversal.
            if two_back_type not in {OUTSIDE_BAR, "inside_bar"}:
                specs.append(("daily_22_reversal", reverse))
            if two_back_type == OUTSIDE_BAR:
                specs.append(("daily_322_reversal", reverse))
            if two_back_type == prev_type:
                specs.append(("daily_222_slice", same))

        if prev_type == OUTSIDE_BAR:
            first = _first_outside_choice(rows, prev, tick)
            if first is not None:
                direction, trigger_ts, trigger_kind = first
                entry, stop, target = baseline_bracket(prev, direction, tick)
                candidates.append(
                    DailyCandidate(
                        family="daily_32",
                        instrument=instrument,
                        trading_day=day,
                        direction=direction,
                        trigger_ts=trigger_ts,
                        planned_entry=entry,
                        stop=stop,
                        target=target,
                        stop_ticks=abs(entry - stop) / tick,
                        previous_type=prev_type,
                        two_back_type=two_back_type,
                        trigger_kind=trigger_kind,
                    )
                )

        for family, direction in specs:
            trigger = _first_directional_trigger(rows, prev, direction, tick)
            if trigger is None:
                continue
            trigger_ts, trigger_kind = trigger
            entry, stop, target = baseline_bracket(prev, direction, tick)
            candidates.append(
                DailyCandidate(
                    family=family,
                    instrument=instrument,
                    trading_day=day,
                    direction=direction,
                    trigger_ts=trigger_ts,
                    planned_entry=entry,
                    stop=stop,
                    target=target,
                    stop_ticks=abs(entry - stop) / tick,
                    previous_type=prev_type,
                    two_back_type=two_back_type,
                    trigger_kind=trigger_kind,
                )
            )
    return sorted(candidates, key=lambda c: (c.family, c.trigger_ts))


def _stop_cap_ticks(repo: Path, instrument: str) -> float | None:
    try:
        data = yaml.safe_load((repo / "risk_rules.yaml").read_text(encoding="utf-8"))
        return float(data["daily_limits"]["max_stop_ticks"][instrument])
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return None


def _trigger_fill_reference(candidate: DailyCandidate, trigger_bar: Bar) -> float:
    if candidate.direction == "LONG":
        return max(candidate.planned_entry, trigger_bar.open)
    return min(candidate.planned_entry, trigger_bar.open)


def resolve_candidate(
    candidate: DailyCandidate,
    day_bars: list[Bar],
    *,
    current_stop_cap_ticks: float | None,
) -> TradeResult:
    """Resolve one same-session stop-entry through the real PaperBroker exit model."""
    try:
        trigger_idx = next(i for i, b in enumerate(day_bars) if b.ts == candidate.trigger_ts)
    except StopIteration as exc:
        raise RuntimeError(f"trigger bar missing: {candidate.trigger_ts}") from exc

    trigger_bar = day_bars[trigger_idx]
    fill_reference = _trigger_fill_reference(candidate, trigger_bar)
    broker = PaperBroker(
        starting_balance=1500.0,
        slippage_ticks=1.0,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="market",
    )
    fill = broker.execute_bracket(
        BracketOrder(
            instrument=candidate.instrument,
            direction=candidate.direction,
            entry=fill_reference,
            stop=candidate.stop,
            target=candidate.target,
            rr_ratio=2.0,
            strategy=candidate.family,
            contracts=1,
        )
    )
    cap_pass = None if current_stop_cap_ticks is None else candidate.stop_ticks <= current_stop_cap_ticks
    if fill.result == "CANCELLED":
        return TradeResult(
            family=candidate.family,
            instrument=candidate.instrument,
            trading_day=candidate.trading_day.isoformat(),
            direction=candidate.direction,
            trigger_ts=candidate.trigger_ts.isoformat(),
            status="CANCELLED_AT_FILL",
            result="CANCELLED",
            planned_entry=candidate.planned_entry,
            fill_entry=float(fill.entry_price),
            stop=candidate.stop,
            target=candidate.target,
            stop_ticks=candidate.stop_ticks,
            gross_pnl=0.0,
            net_pnl=0.0,
            bars_to_exit=None,
            mae_ticks=None,
            mfe_ticks=None,
            current_stop_cap_ticks=current_stop_cap_ticks,
            stop_cap_pass=cap_pass,
        )

    fill_entry = float(fill.entry_price)
    tick = float(TICK_SIZE.get(candidate.instrument, 0.25))
    mae = 0.0
    mfe = 0.0

    # Fill-bar rule: a target-only touch is not credited because OHLC cannot
    # prove target came after the stop entry. Any stop touch is allowed to
    # resolve pessimistically, matching the existing shadow resolver contract.
    start = trigger_idx
    for j in range(start, len(day_bars)):
        bar = day_bars[j]
        adverse = (fill_entry - bar.low) / tick if candidate.direction == "LONG" else (bar.high - fill_entry) / tick
        favorable = (bar.high - fill_entry) / tick if candidate.direction == "LONG" else (fill_entry - bar.low) / tick
        mae = max(mae, adverse)
        mfe = max(mfe, favorable)

        if j == start:
            stop_hit = bar.low <= candidate.stop if candidate.direction == "LONG" else bar.high >= candidate.stop
            if not stop_hit:
                continue

        outcome = broker.resolve_position(NextBarOHLC(open=bar.open, high=bar.high, low=bar.low))
        if outcome is None:
            continue
        gross = float(outcome.pnl_dollars or 0.0)
        net = gross - COMMISSION_RT if outcome.result in {"WIN", "LOSS", "BREAKEVEN"} else gross
        return TradeResult(
            family=candidate.family,
            instrument=candidate.instrument,
            trading_day=candidate.trading_day.isoformat(),
            direction=candidate.direction,
            trigger_ts=candidate.trigger_ts.isoformat(),
            status="RESOLVED",
            result=str(outcome.result),
            planned_entry=candidate.planned_entry,
            fill_entry=fill_entry,
            stop=candidate.stop,
            target=candidate.target,
            stop_ticks=candidate.stop_ticks,
            gross_pnl=round(gross, 2),
            net_pnl=round(net, 2),
            bars_to_exit=j - start + 1,
            mae_ticks=round(mae, 2),
            mfe_ticks=round(mfe, 2),
            current_stop_cap_ticks=current_stop_cap_ticks,
            stop_cap_pass=cap_pass,
        )

    return TradeResult(
        family=candidate.family,
        instrument=candidate.instrument,
        trading_day=candidate.trading_day.isoformat(),
        direction=candidate.direction,
        trigger_ts=candidate.trigger_ts.isoformat(),
        status="EOD_OPEN",
        result="OPEN",
        planned_entry=candidate.planned_entry,
        fill_entry=fill_entry,
        stop=candidate.stop,
        target=candidate.target,
        stop_ticks=candidate.stop_ticks,
        gross_pnl=0.0,
        net_pnl=0.0,
        bars_to_exit=None,
        mae_ticks=round(mae, 2),
        mfe_ticks=round(mfe, 2),
        current_stop_cap_ticks=current_stop_cap_ticks,
        stop_cap_pass=cap_pass,
    )


def _max_drawdown(values: list[float]) -> float:
    equity = peak = max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def summarize(rows: list[TradeResult]) -> dict:
    resolved = [r for r in rows if r.result in {"WIN", "LOSS", "BREAKEVEN"}]
    nets = [r.net_pnl for r in resolved]
    grosses = [r.gross_pnl for r in resolved]
    wins = [p for p in nets if p > 0]
    losses = [p for p in nets if p < 0]
    months: dict[str, float] = defaultdict(float)
    for r in resolved:
        months[r.trading_day[:7]] += r.net_pnl
    stop_widths = [r.stop_ticks for r in rows]
    cap_rows = [r for r in rows if r.stop_cap_pass is not None]
    return {
        "candidates": len(rows),
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "open": sum(r.result == "OPEN" for r in rows),
        "cancelled_at_fill": sum(r.result == "CANCELLED" for r in rows),
        "win_rate": round(len(wins) / len(resolved), 4) if resolved else None,
        "gross_pnl_before_commission": round(sum(grosses), 2),
        "net_pnl_after_commission": round(sum(nets), 2),
        "expectancy_after_commission": round(statistics.fmean(nets), 2) if nets else None,
        "profit_factor_after_commission": round(sum(wins) / abs(sum(losses)), 3) if wins and losses else None,
        "max_drawdown_after_commission": round(_max_drawdown(nets), 2),
        "median_stop_ticks": round(statistics.median(stop_widths), 1) if stop_widths else None,
        "p90_stop_ticks": round(sorted(stop_widths)[max(0, math.ceil(0.9 * len(stop_widths)) - 1)], 1) if stop_widths else None,
        "current_stop_cap_pass_rate": round(sum(bool(r.stop_cap_pass) for r in cap_rows) / len(cap_rows), 4) if cap_rows else None,
        "monthly_net": {k: round(v, 2) for k, v in sorted(months.items())},
        "positive_months": sum(v > 0 for v in months.values()),
        "negative_months": sum(v < 0 for v in months.values()),
    }


def split_summary(rows: list[TradeResult]) -> dict:
    ordered = sorted(rows, key=lambda r: r.trigger_ts)
    mid = len(ordered) // 2
    return {
        "full": summarize(ordered),
        "half1": summarize(ordered[:mid]),
        "half2": summarize(ordered[mid:]),
        "long": summarize([r for r in ordered if r.direction == "LONG"]),
        "short": summarize([r for r in ordered if r.direction == "SHORT"]),
    }


def infer_failure_mode(summary: dict) -> list[str]:
    """Mechanical diagnostics only; never promotes a strategy."""
    reasons: list[str] = []
    full = summary["full"]
    h1 = summary["half1"]
    h2 = summary["half2"]
    gross = full.get("gross_pnl_before_commission")
    net = full.get("net_pnl_after_commission")
    if gross is not None and gross <= 0:
        reasons.append("NO_GROSS_DIRECTIONAL_EDGE")
    elif net is not None and net <= 0:
        reasons.append("COST_DRAG_ERASES_GROSS_EDGE")
    h1n = h1.get("net_pnl_after_commission")
    h2n = h2.get("net_pnl_after_commission")
    if h1n is not None and h2n is not None and h1n * h2n <= 0:
        reasons.append("WALK_FORWARD_SIGN_FLIP")
    cap_rate = full.get("current_stop_cap_pass_rate")
    if cap_rate is not None and cap_rate < 0.5:
        reasons.append("MOST_STOPS_EXCEED_CURRENT_SYSTEM_CAP")
    if full.get("resolved", 0) < 30:
        reasons.append("INSUFFICIENT_RESOLVED_SAMPLE")
    if full.get("open", 0) > full.get("resolved", 0):
        reasons.append("SAME_SESSION_EXIT_CONTRACT_LEAVES_MOST_TRADES_OPEN")
    return reasons or ["NO_SINGLE_BINDING_FAILURE_IDENTIFIED"]


def run_instrument(repo: Path, corpus: Path, instrument: str) -> dict:
    bars = load_5m_corpus(corpus, instrument)
    sessions = group_sessions(bars)
    daily = build_daily_bars(sessions)
    candidates = detect_candidates(instrument, sessions, daily)
    cap = _stop_cap_ticks(repo, instrument)

    by_family: dict[str, list[TradeResult]] = {family: [] for family in FAMILIES}
    for candidate in candidates:
        result = resolve_candidate(candidate, sessions[candidate.trading_day], current_stop_cap_ticks=cap)
        by_family[candidate.family].append(result)

    report: dict[str, dict] = {}
    for family in FAMILIES:
        summary = split_summary(by_family[family])
        report[family] = {
            "summary": summary,
            "failure_mode": infer_failure_mode(summary),
            "rows": [asdict(r) for r in by_family[family]],
        }
    return {
        "instrument": instrument,
        "source_5m_bars": len(bars),
        "daily_sessions": len(daily),
        "complete_daily_sessions": sum(d.complete for d in daily),
        "current_stop_cap_ticks": cap,
        "families": report,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--instrument", action="append", choices=["MNQ", "MES"])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "scripts" / "daily_strat_failure_mode_results.json",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    corpus = (args.corpus or (repo / "data" / "replay_polygon_5m")).resolve()
    instruments = args.instrument or ["MNQ", "MES"]

    payload = {
        "study": "daily_strat_failure_mode_baseline_v1",
        "research_only": True,
        "baseline_only": True,
        "corpus": str(corpus),
        "commission_rt": COMMISSION_RT,
        "entry_slippage_ticks": 1.0,
        "pessimistic_same_bar": True,
        "position_size_contracts": 1,
        "daily_session": "18:00 ET to 17:00 ET",
        "minimum_5m_bars_for_data_quality": MIN_SESSION_5M_BARS,
        "instruments": {},
    }
    for instrument in instruments:
        payload["instruments"][instrument] = run_instrument(repo, corpus, instrument)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        instrument: {
            family: data["summary"]["full"]
            for family, data in inst["families"].items()
        }
        for instrument, inst in payload["instruments"].items()
    }, indent=2, sort_keys=True))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
