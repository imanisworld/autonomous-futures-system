"""MNQ true PDL sweep→reclaim honest-fill execution study.

Research only. Prereg:
docs/prereg-mnq-pdl-sweep-reclaim-fill-2026-09-24.md
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.non_strat_coverage import observe_session  # noqa: E402
from config.futures_contracts import TICK_SIZE  # noqa: E402
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.paper_broker import NextBarOHLC, PaperBroker  # noqa: E402
from research.futures_non_strat_coverage import (  # noqa: E402
    HALF_SPLIT,
    HISTORY_SESSIONS,
    RTH_BARS,
    load_rth_session,
    roll_excluded_sessions,
    session_files,
)

STUDY_ID = "MNQ_PDL_SWEEP_RECLAIM_EXECUTION"
STUDY_VERSION = "pdl-sr-b-v0.1"
FAMILY = "PDL_REJECTION_LONG"
INSTRUMENT = "MNQ"
IOC_TOLERANCE_TICKS = 32.0
SLIPPAGE_TICKS = (1.0, 2.0, 3.0)
COMMISSION_RT = 1.48
MAX_STOP_TICKS = 120.0
MIN_RR = 2.0
TARGETS = ("TARGET_VWAP_AT_SIGNAL", "TARGET_PRIOR_DAY_MIDPOINT")


@dataclass(frozen=True)
class Candidate:
    session_date: str
    half: str
    episode_id: str
    event_bar_start: str
    event_idx: int
    session_rank: int
    planned_entry: float
    sweep_low: float
    pdl: float
    pdh: float
    vwap_at_signal: float | None
    volume_ratio: float | None


@dataclass(frozen=True)
class TradeRow:
    session_date: str
    half: str
    episode_id: str
    session_rank: int
    target_model: str
    slippage_ticks: float
    event_bar_start: str
    planned_entry: float
    decision_open: float | None
    fill_entry: float | None
    stop: float
    target: float | None
    stop_ticks_actual: float | None
    rr_actual: float | None
    risk_feasible: bool | None
    status: str
    result: str | None
    exit_reason: str | None
    gross_pnl: float | None
    net_pnl: float | None
    bars_held: int | None
    volume_ratio: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _floor_to_tick(value: float, tick: float) -> float:
    return round(math.floor((float(value) + 1e-10) / tick) * tick, 10)


def collect_candidates() -> tuple[list[Candidate], dict[str, int]]:
    files = session_files(INSTRUMENT)
    if not files:
        raise SystemExit(f"no replay files for {INSTRUMENT}")
    days = sorted(files)
    excluded_roll = roll_excluded_sessions(days)
    loaded: dict[date, list] = {}
    skipped = {"incomplete": 0, "roll": 0, "no_prior": 0}

    for day in days:
        bars, _ = load_rth_session(files[day])
        if len(bars) != RTH_BARS:
            skipped["incomplete"] += 1
            continue
        loaded[day] = bars

    complete = sorted(loaded)
    out: list[Candidate] = []
    for idx, day in enumerate(complete):
        if day in excluded_roll:
            skipped["roll"] += 1
            continue
        prior_days = complete[max(0, idx - HISTORY_SESSIONS):idx]
        if not prior_days or prior_days[-1] in excluded_roll:
            skipped["no_prior"] += 1
            continue

        bars = loaded[day]
        prior_bars = loaded[prior_days[-1]]
        history = [bar for d in prior_days for bar in loaded[d]]
        events = observe_session(
            symbol=INSTRUMENT,
            session_date=day.isoformat(),
            prior_session_bars=prior_bars,
            session_bars=bars,
            history_bars=history,
        )
        first_by_episode: dict[str, Any] = {}
        for event in sorted(events, key=lambda e: e.bar_start):
            if event.family == FAMILY:
                first_by_episode.setdefault(event.episode_id, event)

        by_start = {bar.start_utc.isoformat(): i for i, bar in enumerate(bars)}
        pdl = min(float(b.low) for b in prior_bars)
        pdh = max(float(b.high) for b in prior_bars)
        ranked = sorted(first_by_episode.values(), key=lambda e: e.bar_start)
        for rank, event in enumerate(ranked, start=1):
            start = datetime.fromisoformat(event.bar_start.replace("Z", "+00:00")).isoformat()
            bar_idx = by_start.get(start)
            if bar_idx is None:
                continue
            trigger = bars[bar_idx]
            out.append(
                Candidate(
                    session_date=day.isoformat(),
                    half="H1" if day < HALF_SPLIT else "H2",
                    episode_id=event.episode_id,
                    event_bar_start=start,
                    event_idx=bar_idx,
                    session_rank=rank,
                    planned_entry=float(event.trigger_price),
                    sweep_low=float(trigger.low),
                    pdl=pdl,
                    pdh=pdh,
                    vwap_at_signal=float(event.vwap) if event.vwap is not None else None,
                    volume_ratio=float(event.volume_ratio) if event.volume_ratio is not None else None,
                )
            )
    return out, skipped


def geometry(candidate: Candidate, target_model: str) -> tuple[float, float | None]:
    tick = TICK_SIZE[INSTRUMENT]
    stop = _floor_to_tick(candidate.sweep_low, tick) - tick
    stop = round(stop, 10)
    if target_model == "TARGET_VWAP_AT_SIGNAL":
        raw = candidate.vwap_at_signal
    elif target_model == "TARGET_PRIOR_DAY_MIDPOINT":
        raw = (candidate.pdh + candidate.pdl) / 2.0
    else:
        raise ValueError(target_model)
    target = None if raw is None else _floor_to_tick(float(raw), tick)
    return stop, target


def simulate_candidate(
    candidate: Candidate,
    bars: Sequence,
    *,
    target_model: str,
    slippage_ticks: float,
) -> TradeRow:
    stop, target = geometry(candidate, target_model)
    tick = TICK_SIZE[INSTRUMENT]
    next_idx = candidate.event_idx + 1
    if next_idx >= len(bars):
        return TradeRow(
            candidate.session_date, candidate.half, candidate.episode_id, candidate.session_rank,
            target_model, slippage_ticks, candidate.event_bar_start, candidate.planned_entry,
            None, None, stop, target, None, None, None, "NO_DATA", None, None, None, None, None,
            candidate.volume_ratio,
        )
    decision_open = float(bars[next_idx].open)
    if target is None or not (stop < candidate.planned_entry < target):
        return TradeRow(
            candidate.session_date, candidate.half, candidate.episode_id, candidate.session_rank,
            target_model, slippage_ticks, candidate.event_bar_start, candidate.planned_entry,
            decision_open, None, stop, target, None, None, None, "GEOMETRY_INVALID", None, None,
            None, None, None, candidate.volume_ratio,
        )

    plan_risk = candidate.planned_entry - stop
    plan_rr = (target - candidate.planned_entry) / plan_risk if plan_risk > 0 else 0.0

    os.environ["WEBULL_FUTURES_MIRROR_ENABLED"] = "false"
    broker = PaperBroker(
        starting_balance=100_000.0,
        slippage_ticks=slippage_ticks,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={INSTRUMENT: IOC_TOLERANCE_TICKS},
    )
    order = BracketOrder(
        instrument=INSTRUMENT,
        direction="LONG",
        entry=candidate.planned_entry,
        stop=stop,
        target=target,
        rr_ratio=plan_rr,
        strategy="mnq_pdl_sweep_reclaim_research",
        contracts=1,
        min_rr_ratio=0.0,
    )
    opened = broker.execute_bracket(order, market_price=decision_open)
    if opened.result == "CANCELLED":
        reason = opened.exit_reason or opened.no_fill_reason
        status = "BRACKET_INVALID_AT_FILL" if reason == "ENTRY_BRACKET_INVALID_AT_FILL" else "ENTRY_NOT_FILLED"
        return TradeRow(
            candidate.session_date, candidate.half, candidate.episode_id, candidate.session_rank,
            target_model, slippage_ticks, candidate.event_bar_start, candidate.planned_entry,
            decision_open, float(opened.entry_price), stop, target, None, None, None, status,
            None, reason, None, None, None, candidate.volume_ratio,
        )

    fill_entry = float(opened.entry_price)
    stop_ticks = (fill_entry - stop) / tick
    actual_rr = (target - fill_entry) / (fill_entry - stop) if fill_entry > stop else None
    risk_feasible = bool(
        actual_rr is not None
        and stop_ticks <= MAX_STOP_TICKS
        and actual_rr >= MIN_RR
        and stop < fill_entry < target
    )

    for j in range(next_idx, len(bars)):
        bar = bars[j]
        fill = broker.resolve_position(
            NextBarOHLC(high=float(bar.high), low=float(bar.low), open=float(bar.open))
        )
        if fill is None:
            continue
        gross = float(fill.pnl_dollars or 0.0)
        return TradeRow(
            candidate.session_date, candidate.half, candidate.episode_id, candidate.session_rank,
            target_model, slippage_ticks, candidate.event_bar_start, candidate.planned_entry,
            decision_open, fill_entry, stop, target, round(stop_ticks, 3),
            round(actual_rr, 4) if actual_rr is not None else None, risk_feasible, "RESOLVED",
            fill.result, fill.exit_reason, round(gross, 2), round(gross - COMMISSION_RT, 2),
            j - next_idx + 1, candidate.volume_ratio,
        )

    return TradeRow(
        candidate.session_date, candidate.half, candidate.episode_id, candidate.session_rank,
        target_model, slippage_ticks, candidate.event_bar_start, candidate.planned_entry,
        decision_open, fill_entry, stop, target, round(stop_ticks, 3),
        round(actual_rr, 4) if actual_rr is not None else None, risk_feasible, "OPEN",
        "OPEN", None, None, None, None, candidate.volume_ratio,
    )


def _max_drawdown(values: Sequence[float]) -> float:
    equity = peak = worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return round(worst, 2)


def _metrics(rows: Sequence[TradeRow]) -> dict[str, Any]:
    resolved = [r for r in rows if r.status == "RESOLVED" and r.net_pnl is not None]
    net = [float(r.net_pnl) for r in resolved]
    wins = [v for v in net if v > 0]
    losses = [v for v in net if v < 0]
    positives = sorted(wins, reverse=True)
    positive_sum = sum(positives)
    return {
        "attempts": len(rows),
        "filled_or_opened": sum(r.status in {"RESOLVED", "OPEN"} for r in rows),
        "entry_not_filled": sum(r.status == "ENTRY_NOT_FILLED" for r in rows),
        "bracket_invalid_at_fill": sum(r.status == "BRACKET_INVALID_AT_FILL" for r in rows),
        "geometry_invalid": sum(r.status == "GEOMETRY_INVALID" for r in rows),
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl": round(sum(net), 2),
        "expectancy_per_attempt": round(sum(net) / len(rows), 4) if rows else None,
        "expectancy_per_resolved": round(statistics.fmean(net), 4) if net else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if wins and losses else None,
        "max_drawdown": _max_drawdown(net),
        "median_stop_ticks": round(statistics.median([r.stop_ticks_actual for r in resolved if r.stop_ticks_actual is not None]), 3)
        if any(r.stop_ticks_actual is not None for r in resolved) else None,
        "median_actual_rr": round(statistics.median([r.rr_actual for r in resolved if r.rr_actual is not None]), 4)
        if any(r.rr_actual is not None for r in resolved) else None,
        "risk_feasible_resolved": sum(r.risk_feasible is True for r in resolved),
        "top3_winner_share": round(sum(positives[:3]) / positive_sum, 4) if positive_sum > 0 else None,
    }


def summarize(rows: Sequence[TradeRow]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for target in TARGETS:
        for slip in SLIPPAGE_TICKS:
            key = f"{target}|slip{int(slip)}"
            cell = [r for r in rows if r.target_model == target and r.slippage_ticks == slip]
            account = [r for r in cell if r.session_rank <= 3]
            risk = [r for r in account if r.risk_feasible is True]
            result[key] = {
                "all": _metrics(cell),
                "account_first3": _metrics(account),
                "risk_feasible_account": _metrics(risk),
                "H1_account": _metrics([r for r in account if r.half == "H1"]),
                "H2_account": _metrics([r for r in account if r.half == "H2"]),
            }
    return result


def run() -> dict[str, Any]:
    candidates, skipped = collect_candidates()
    files = session_files(INSTRUMENT)
    loaded: dict[str, list] = {}
    rows: list[TradeRow] = []
    for candidate in candidates:
        if candidate.session_date not in loaded:
            bars, _ = load_rth_session(files[date.fromisoformat(candidate.session_date)])
            loaded[candidate.session_date] = bars
        bars = loaded[candidate.session_date]
        for target in TARGETS:
            for slip in SLIPPAGE_TICKS:
                rows.append(
                    simulate_candidate(candidate, bars, target_model=target, slippage_ticks=slip)
                )
    return {
        "study_id": STUDY_ID,
        "study_version": STUDY_VERSION,
        "instrument": INSTRUMENT,
        "family": FAMILY,
        "execution": {
            "ioc_tolerance_ticks": IOC_TOLERANCE_TICKS,
            "slippage_ticks": SLIPPAGE_TICKS,
            "commission_rt": COMMISSION_RT,
            "max_stop_ticks": MAX_STOP_TICKS,
            "min_rr": MIN_RR,
        },
        "candidate_count": len(candidates),
        "skipped": skipped,
        "summary": summarize(rows),
        "candidates": [asdict(c) for c in candidates],
        "rows": [r.to_dict() for r in rows],
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['study_id']} {report['study_version']}",
        "",
        f"Candidates: {report['candidate_count']} · skipped: {report['skipped']}",
        "",
        "| cell | attempts | fills/resolved | net | PF | H1 net/PF | H2 net/PF | risk-feasible resolved | top3 share |",
        "|---|---:|---:|---:|---:|---|---|---:|---:|",
    ]
    for key, cell in report["summary"].items():
        a = cell["account_first3"]
        h1, h2 = cell["H1_account"], cell["H2_account"]
        rf = cell["risk_feasible_account"]
        lines.append(
            f"| {key} | {a['attempts']} | {a['filled_or_opened']}/{a['resolved']} | "
            f"{a['net_pnl']} | {a['profit_factor']} | {h1['net_pnl']}/{h1['profit_factor']} | "
            f"{h2['net_pnl']}/{h2['profit_factor']} | {rf['resolved']} | {a['top3_winner_share']} |"
        )
    lines += [
        "",
        "Research only. Prior raw signal is exposed; no historical cell can produce VALIDATED status.",
        "No volume/ATR/session/regime filter is applied.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "logs" / "mnq_pdl_sweep_reclaim_execution"))
    args = parser.parse_args(argv)
    report = run()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    md = to_markdown(report)
    (out / "result.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
