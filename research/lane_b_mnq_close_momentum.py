"""Literal Lane B reproduction of Baltussen et al. (2021) on MNQ.

Research-only. The frozen implementation contract is:
docs/strategy-rules/LANE_B_MNQ_CLOSE_MOMENTUM_PREREGISTRATION_2026-07-27.md

This module intentionally does not import runtime, risk, execution, or broker
code. It reads the local 5-minute Polygon cache and writes research artifacts.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo


REPO = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO / "data" / "replay_polygon_5m" / "MNQ"
RESULTS_PATH = REPO / "scripts" / "lane_b_mnq_close_momentum_results.json"
TRADES_PATH = REPO / "scripts" / "lane_b_mnq_close_momentum_trades.jsonl"
REPORT_PATH = REPO / "docs" / "strategy-rules" / "LANE_B_MNQ_CLOSE_MOMENTUM_RESULTS_2026-07-27.md"

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
TICK_SIZE = 0.25
POINT_VALUE = 2.0
COMMISSION_RT = 1.48
HOLDOUT_FRACTION = 0.25
SLIPPAGE_TICKS = (1, 2, 3, 4)


@dataclass(frozen=True)
class BoundaryBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    market_condition: str


@dataclass(frozen=True)
class Trade:
    day: date
    direction: str
    signal_return: float
    signal_price: float
    prior_close: float
    raw_entry: float
    raw_exit: float
    entry: float
    exit: float
    gross_pnl: float
    slippage_cost: float
    commission: float
    net_pnl: float
    market_condition: str
    slippage_ticks_per_side: int
    partition: str = ""


def _read_sessions() -> dict[date, dict[time, BoundaryBar]]:
    """Read all cached bars and group them by ET session date/time."""
    sessions: dict[date, dict[time, BoundaryBar]] = defaultdict(dict)
    for path in sorted(DATA_ROOT.glob("MNQ_*.jsonl")):
        with path.open() as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                ts = datetime.fromisoformat(row["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                ts = ts.astimezone(ET)
                key = ts.timetz().replace(tzinfo=None)
                bar = BoundaryBar(
                    ts=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                    market_condition=str(row.get("market_condition") or "UNKNOWN"),
                )
                existing = sessions[ts.date()].get(key)
                if existing is not None:
                    # Overlapping UTC-day cache files can repeat an ET bar. Exact
                    # duplicates are harmless; conflicting duplicates fail loud.
                    if existing != bar:
                        raise ValueError(f"conflicting duplicate bar at {ts.isoformat()}")
                    continue
                sessions[ts.date()][key] = bar
    return dict(sessions)


def _classify_sessions(
    sessions: dict[date, dict[time, BoundaryBar]],
) -> tuple[list[date], list[date], list[date], list[date]]:
    """Return full, shortened, missing-weekday, and weekend dates."""
    first, last = min(sessions), max(sessions)
    required = {time(15, 25), time(15, 30), time(15, 55)}
    full: list[date] = []
    shortened: list[date] = []
    missing: list[date] = []
    weekends: list[date] = []
    current = first
    while current <= last:
        bars = sessions.get(current, {})
        if current.weekday() >= 5:
            weekends.append(current)
        elif required.issubset(bars):
            full.append(current)
        elif any(time(9, 30) <= t <= time(16, 0) for t in bars):
            shortened.append(current)
        else:
            missing.append(current)
        current += timedelta(days=1)
    return full, shortened, missing, weekends


def _build_trades(
    sessions: dict[date, dict[time, BoundaryBar]],
    full_days: list[date],
    slippage_ticks: int,
) -> tuple[list[Trade], list[dict]]:
    """Build causal close-momentum trades; disclose every ineligible full day."""
    slip_points = slippage_ticks * TICK_SIZE
    trades: list[Trade] = []
    exclusions: list[dict] = []
    prior_full_close: BoundaryBar | None = None

    for day in full_days:
        bars = sessions[day]
        signal_bar = bars[time(15, 25)]  # known at 15:30
        entry_bar = bars[time(15, 30)]
        exit_bar = bars[time(15, 55)]  # closes at 16:00
        if prior_full_close is None:
            exclusions.append({"date": day.isoformat(), "reason": "NO_PRIOR_FULL_SESSION_CLOSE"})
            prior_full_close = exit_bar
            continue
        if prior_full_close.close <= 0 or signal_bar.close <= 0:
            exclusions.append({"date": day.isoformat(), "reason": "NON_POSITIVE_PRICE"})
            prior_full_close = exit_bar
            continue

        signal_return = signal_bar.close / prior_full_close.close - 1.0
        direction = "LONG" if signal_return > 0 else "SHORT"
        signed = 1.0 if direction == "LONG" else -1.0
        raw_entry = entry_bar.open
        raw_exit = exit_bar.close
        entry = raw_entry + signed * slip_points
        exit_price = raw_exit - signed * slip_points
        gross_pnl = signed * (raw_exit - raw_entry) * POINT_VALUE
        net_before_commission = signed * (exit_price - entry) * POINT_VALUE
        slippage_cost = gross_pnl - net_before_commission
        trades.append(
            Trade(
                day=day,
                direction=direction,
                signal_return=signal_return,
                signal_price=signal_bar.close,
                prior_close=prior_full_close.close,
                raw_entry=raw_entry,
                raw_exit=raw_exit,
                entry=entry,
                exit=exit_price,
                gross_pnl=gross_pnl,
                slippage_cost=slippage_cost,
                commission=COMMISSION_RT,
                net_pnl=net_before_commission - COMMISSION_RT,
                market_condition=signal_bar.market_condition,
                slippage_ticks_per_side=slippage_ticks,
            )
        )
        prior_full_close = exit_bar
    return trades, exclusions


def _max_drawdown(pnls: Iterable[float]) -> float:
    equity = peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def _longest_losing_streak(pnls: Iterable[float]) -> int:
    longest = current = 0
    for pnl in pnls:
        if pnl < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _metrics(rows: list[Trade]) -> dict:
    if not rows:
        return {
            "signals": 0, "fills": 0, "resolved": 0, "long": 0, "short": 0,
            "wins": 0, "losses": 0, "breakeven": 0, "gross_pnl": 0.0,
            "slippage_cost": 0.0, "commissions": 0.0, "net_pnl": 0.0,
            "expectancy": None, "profit_factor": None, "win_rate": None,
            "average_win": None, "average_loss": None, "max_drawdown": 0.0,
            "longest_losing_streak": 0, "average_trades_per_week": None,
        }
    pnls = [r.net_pnl for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    span_weeks = max((rows[-1].day - rows[0].day).days / 7.0, 1 / 7)
    return {
        "signals": len(rows),
        "fills": len(rows),
        "resolved": len(rows),
        "long": sum(r.direction == "LONG" for r in rows),
        "short": sum(r.direction == "SHORT" for r in rows),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(rows) - len(wins) - len(losses),
        "gross_pnl": round(sum(r.gross_pnl for r in rows), 2),
        "slippage_cost": round(sum(r.slippage_cost for r in rows), 2),
        "commissions": round(sum(r.commission for r in rows), 2),
        "net_pnl": round(sum(pnls), 2),
        "expectancy": round(statistics.fmean(pnls), 4),
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 4)
            if wins and losses else (math.inf if wins else 0.0)
        ),
        "win_rate": round(len(wins) / len(rows), 4),
        "average_win": round(statistics.fmean(wins), 4) if wins else None,
        "average_loss": round(statistics.fmean(losses), 4) if losses else None,
        "max_drawdown": round(_max_drawdown(pnls), 2),
        "longest_losing_streak": _longest_losing_streak(pnls),
        "average_trades_per_week": round(len(rows) / span_weeks, 3),
        "first_date": rows[0].day.isoformat(),
        "last_date": rows[-1].day.isoformat(),
    }


def _group(rows: list[Trade], key: Callable[[Trade], str]) -> dict[str, dict]:
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for row in rows:
        buckets[key(row)].append(row)
    return {name: _metrics(group) for name, group in sorted(buckets.items())}


def _concentration(rows: list[Trade]) -> dict:
    winners = sorted((r.net_pnl for r in rows if r.net_pnl > 0), reverse=True)
    net = sum(r.net_pnl for r in rows)
    top1 = sum(winners[:1])
    top5 = sum(winners[:5])
    return {
        "top_1_winner_contribution": round(top1, 2),
        "top_5_winner_contribution": round(top5, 2),
        "top_5_pct_of_total_net": round(top5 / net * 100, 2) if net else None,
        "net_with_top_1_removed": round(net - top1, 2),
        "net_with_top_5_removed": round(net - top5, 2),
    }


def _serialize_trade(row: Trade) -> dict:
    out = row.__dict__.copy()
    out["day"] = row.day.isoformat()
    return out


def _fmt_money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _fmt_metric_row(name: str, m: dict) -> str:
    pf = m.get("profit_factor")
    pf_text = "n/a" if pf is None else ("∞" if math.isinf(pf) else f"{pf:.2f}")
    wr = m.get("win_rate")
    return (
        f"| {name} | {m['resolved']} | {m['long']} / {m['short']} | "
        f"{m['wins']} / {m['losses']} | {'' if wr is None else f'{wr:.1%}'} | "
        f"{_fmt_money(m['gross_pnl'])} | {_fmt_money(m['net_pnl'])} | "
        f"{_fmt_money(m['expectancy'])} | {pf_text} | "
        f"{_fmt_money(m['max_drawdown'])} |"
    )


def _render_report(results: dict) -> str:
    base = results["baseline"]
    lines = [
        "# Lane B MNQ Close-Momentum — Literal Baseline Results",
        "",
        "## VERDICT",
        "",
        f"**{results['verdict']} — {results['recommendation']}**",
        "",
        results["verdict_reason"],
        "",
        "## SOURCE VERIFICATION",
        "",
        "| Claim | Primary source | Verified? | Exact definition |",
        "|---|---|---:|---|",
        "| NQ market/session | Baltussen et al. (2021), §§1–2, Table A1 | Yes | NQ futures, underlying cash-market hours 09:30–16:00 ET. |",
        "| Rest-of-day signal | Baltussen et al. (2021), §1 | Yes | Previous market close through 30 minutes before current close; overnight is included. |",
        "| Direction/holding period | Baltussen et al. (2021), Eq. (12), Table 6 | Yes | LONG if ROD return is positive, SHORT otherwise; hold only for 15:30–16:00 ET. |",
        "| Threshold/stops/targets | Baltussen et al. (2021), Eq. (12) | Yes | None. Exact zero follows the SHORT “otherwise” branch. |",
        "| Short sessions | Baltussen et al. (2021), §2 | Yes | Early-close days removed. |",
        "| Costs | Baltussen et al. (2021), §3.5 | Yes | Main results are gross; no NQ cost-adjusted strategy result is published. |",
        "| NQ evidence | Baltussen et al. (2021), Tables A1/B1 | Yes | 6,017 observations through 2020; positive significant ROD slope, but no NQ strategy P&L table. |",
        "| 24.3% / 1.67 / +6 bps / 38% / 2.25 | Baltussen et al. (2021) | **No** | Absent from the paper; later secondary-source figures for a modified Noise-Area strategy. |",
        "",
        "The claimed 24.3% return, 1.67 Sharpe, +6 bps/trade, 38% win rate, and",
        "2.25 payoff ratio are **not present in the primary paper**. They are",
        "secondary-source figures for a materially different, modified strategy.",
        "Primary sources: [publisher PDF](https://pure.eur.nl/ws/portalfiles/portal/58145484/1_s2.0_S0304405X21001598_main.pdf),",
        "[university record](https://repub.eur.nl/pub/131621), and the distinct",
        "[Quantitativo adaptation](https://www.quantitativo.com/p/intraday-momentum-for-es-and-nq).",
        "Full definitions are frozen in the preregistration.",
        "",
        "## PRE-REGISTERED RULE",
        "",
        "At 15:30 ET, compare the just-closed 15:25 five-minute bar with the prior",
        "full session's 16:00 close. Go LONG if the return is positive and SHORT",
        "otherwise; enter at the 15:30 bar open and exit at the 15:55 bar close",
        "(16:00 ET). No threshold, stop, target, or filter. Baseline costs are",
        "$1.48 round-trip commission plus one adverse tick per side.",
        "",
        "## DATASET / COVERAGE",
        "",
        f"- Raw cache coverage: {results['coverage']['raw_first_date']} through {results['coverage']['raw_last_date']}.",
        f"- Full sessions found: {results['coverage']['full_sessions']}.",
        f"- Shortened/incomplete RTH sessions excluded: {results['coverage']['shortened_sessions']}.",
        f"- Missing weekdays disclosed: {results['coverage']['missing_weekdays']}.",
        f"- Eligible signals after the required prior close: {base['resolved']}.",
        f"- Average cadence: {base['average_trades_per_week']:.2f} trades/week.",
        "",
        "## BASELINE RESULTS",
        "",
        "| Scope | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        _fmt_metric_row("All", base),
        "",
        f"Commissions were {_fmt_money(base['commissions'])}; adverse slippage cost was "
        f"{_fmt_money(base['slippage_cost'])}. Longest losing streak: "
        f"{base['longest_losing_streak']}. Average win: "
        f"{_fmt_money(base['average_win'])}; average loss: "
        f"{_fmt_money(base['average_loss'])}. Signals/fills/resolved were "
        f"{base['signals']}/{base['fills']}/{base['resolved']}.",
        "",
        "## H1 / H2",
        "",
        "| Half | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in results["chronology"].items():
        lines.append(_fmt_metric_row(name, metrics))
    lines += [
        "",
        "## LONG / SHORT",
        "",
        "| Direction | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in results["by_direction"].items():
        lines.append(_fmt_metric_row(name, metrics))
    lines += [
        "",
        "## COST SENSITIVITY",
        "",
        "| Adverse ticks/side | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in results["cost_sensitivity"].items():
        lines.append(_fmt_metric_row(name, metrics))
    c = results["concentration"]
    lines += [
        "",
        "## CONCENTRATION",
        "",
        f"- Top winner contribution: {_fmt_money(c['top_1_winner_contribution'])}.",
        f"- Top five winners: {_fmt_money(c['top_5_winner_contribution'])} "
        f"({c['top_5_pct_of_total_net']}% of total net).",
        f"- Net with top winner removed: {_fmt_money(c['net_with_top_1_removed'])}.",
        f"- Net with top five removed: {_fmt_money(c['net_with_top_5_removed'])}.",
        "- Because total net is negative, the top-five percentage has no positive-profit concentration interpretation.",
        "",
        "## HOLDOUT",
        "",
        "| Partition | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        _fmt_metric_row("Development 75%", results["holdout"]["development"]),
        _fmt_metric_row("Untouched final 25%", results["holdout"]["holdout"]),
        "",
        "The split index and 25% fraction were frozen before results. No rule was",
        "changed after the holdout was opened.",
        "",
        "## FAILURE MODES",
        "",
    ]
    lines.extend(f"- {item}" for item in results["failure_modes"])
    lines += [
        "",
        "## WHAT THE RESULT PROVES",
        "",
    ]
    lines.extend(f"- {item}" for item in results["what_it_proves"])
    lines += [
        "",
        "## WHAT IT DOES NOT PROVE",
        "",
    ]
    lines.extend(f"- {item}" for item in results["what_it_does_not_prove"])
    lines += [
        "",
        "## DESCRIPTIVE PERIOD / REGIME STABILITY",
        "",
        "| Quarter | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in results["by_quarter"].items():
        lines.append(_fmt_metric_row(name, metrics))
    lines += [
        "",
        "| Signal-time regime | Trades | L / S | W / L | WR | Gross | Net | Exp/trade | PF | Max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, metrics in results["by_market_condition_descriptive"].items():
        lines.append(_fmt_metric_row(name, metrics))
    lines += [
        "",
        "Month-level results are preserved in",
        "`scripts/lane_b_mnq_close_momentum_results.json`. These descriptive",
        "breakdowns were not used to create filters.",
        "",
        "## RECOMMENDATION",
        "",
        f"**{results['recommendation']}**",
        "",
        "No deploy, runtime change, execution change, sizing change, or parameter",
        "optimization was performed.",
        "",
    ]
    return "\n".join(lines)


def run() -> dict:
    sessions = _read_sessions()
    full_days, shortened_days, missing_days, weekend_days = _classify_sessions(sessions)
    baseline_rows, baseline_exclusions = _build_trades(sessions, full_days, 1)
    if not baseline_rows:
        raise RuntimeError("no eligible baseline trades")

    # Frozen untouched holdout: final 25% of chronologically ordered signals.
    split_index = int(len(baseline_rows) * (1.0 - HOLDOUT_FRACTION))
    if split_index <= 0 or split_index >= len(baseline_rows):
        raise RuntimeError("corpus too small for frozen holdout")
    baseline_rows = [
        replace(row, partition="development" if i < split_index else "holdout")
        for i, row in enumerate(baseline_rows)
    ]
    development = baseline_rows[:split_index]
    holdout = baseline_rows[split_index:]

    midpoint = len(baseline_rows) // 2
    chronology = {
        "H1": _metrics(baseline_rows[:midpoint]),
        "H2": _metrics(baseline_rows[midpoint:]),
    }

    cost_rows: dict[str, list[Trade]] = {"1 tick": baseline_rows}
    for ticks in SLIPPAGE_TICKS[1:]:
        rows, exclusions = _build_trades(sessions, full_days, ticks)
        if exclusions != baseline_exclusions:
            raise AssertionError("cost sensitivity changed session eligibility")
        cost_rows[f"{ticks} ticks"] = rows

    baseline_metrics = _metrics(baseline_rows)
    holdout_metrics = _metrics(holdout)
    h1, h2 = chronology["H1"], chronology["H2"]
    concentration = _concentration(baseline_rows)
    directions = _group(baseline_rows, lambda r: r.direction)

    positive_all_costs = all(_metrics(rows)["net_pnl"] > 0 for rows in cost_rows.values())
    positive_halves = h1["net_pnl"] > 0 and h2["net_pnl"] > 0
    positive_directions = all(m["net_pnl"] > 0 for m in directions.values())
    positive_holdout = holdout_metrics["net_pnl"] > 0
    unconcentrated = concentration["net_with_top_5_removed"] > 0
    useful_cadence = baseline_metrics["average_trades_per_week"] >= 3

    if baseline_metrics["net_pnl"] <= 0 or not positive_holdout:
        verdict = "BROKEN"
        recommendation = "REJECT"
    elif not positive_halves or not positive_directions or not unconcentrated:
        verdict = "PROMISING BUT UNPROVEN"
        recommendation = "KEEP RESEARCHING"
    elif positive_all_costs and useful_cadence:
        verdict = "PROMISING BUT UNPROVEN"
        recommendation = "PROMOTE TO PAPER CANDIDATE"
    else:
        verdict = "PROMISING BUT UNPROVEN"
        recommendation = "KEEP RESEARCHING"

    failure_modes: list[str] = []
    if not positive_all_costs:
        failure_modes.append("The edge does not remain positive through all 1–4 tick-per-side cost stresses.")
    if not positive_halves:
        failure_modes.append("The frozen result is not positive in both chronological halves.")
    if not positive_directions:
        failure_modes.append("At least one direction is not independently positive.")
    if not positive_holdout:
        failure_modes.append("The untouched final 25% holdout is not positive.")
    if baseline_metrics["net_pnl"] <= 0:
        failure_modes.append(
            "The baseline is already negative before concentration adjustment; "
            "removing the top five winners makes it still worse."
        )
    elif not unconcentrated:
        failure_modes.append("Removing the top five winners eliminates the total net profit.")
    if not useful_cadence:
        failure_modes.append("Cadence is below the pre-registered useful threshold.")
    if not failure_modes:
        failure_modes.append("Historical OHLC execution cannot prove live boundary fills or future persistence.")

    results = {
        "study": "Lane B MNQ literal close momentum",
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "preregistration_commit": "8b58788",
        "rule_frozen_before_results": True,
        "deployed_epoch_untouched": {
            "release": "0db7c0882b421bfaa3545eef8524291049f41b47",
            "boundary_utc": "2026-07-27 01:57:54 UTC",
        },
        "assumptions": {
            "instrument": "MNQ",
            "contracts": 1,
            "session_timezone": "America/New_York",
            "signal": "prior full-session 15:55 close to current 15:25 close",
            "entry": "15:30 bar open",
            "exit": "15:55 bar close",
            "commission_round_trip_usd": COMMISSION_RT,
            "tick_size": TICK_SIZE,
            "point_value": POINT_VALUE,
            "slippage_ticks_are_per_side": True,
            "holdout_fraction": HOLDOUT_FRACTION,
            "holdout_split_index": split_index,
        },
        "coverage": {
            "raw_first_date": min(sessions).isoformat(),
            "raw_last_date": max(sessions).isoformat(),
            "raw_calendar_dates": len(sessions),
            "full_sessions": len(full_days),
            "shortened_sessions": len(shortened_days),
            "missing_weekdays": len(missing_days),
            "weekend_dates": len(weekend_days),
            "shortened_session_dates": [d.isoformat() for d in shortened_days],
            "missing_weekday_dates": [d.isoformat() for d in missing_days],
            "baseline_exclusions": baseline_exclusions,
        },
        "baseline": baseline_metrics,
        "chronology": chronology,
        "by_direction": directions,
        "cost_sensitivity": {name: _metrics(rows) for name, rows in cost_rows.items()},
        "by_quarter": _group(baseline_rows, lambda r: f"{r.day.year}-Q{(r.day.month - 1) // 3 + 1}"),
        "by_month": _group(baseline_rows, lambda r: r.day.strftime("%Y-%m")),
        "by_market_condition_descriptive": _group(baseline_rows, lambda r: r.market_condition),
        "by_session_descriptive": {"new_york": baseline_metrics},
        "concentration": concentration,
        "holdout": {
            "development": _metrics(development),
            "holdout": holdout_metrics,
        },
        "verdict": verdict,
        "recommendation": recommendation,
        "verdict_reason": (
            "The frozen literal rule is classified solely from the pre-registered "
            "cadence, cost, chronology, direction, concentration, and holdout checks."
        ),
        "failure_modes": failure_modes,
        "what_it_proves": [
            "Whether the literal paper rule survives this local MNQ five-minute corpus under the frozen cost model.",
            "Whether its historical result is stable across chronology, direction, calendar blocks, and an untouched final 25% holdout.",
        ],
        "what_it_does_not_prove": [
            "That the academic NQ sample or its tick-level construction was exactly replicated.",
            "That five-minute boundary prices can be filled live at the modeled slippage.",
            "That any historical edge will persist prospectively.",
            "That the unrelated Quantitativo Noise-Area NQ statistics are reproducible.",
        ],
    }

    RESULTS_PATH.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    TRADES_PATH.write_text(
        "\n".join(json.dumps(_serialize_trade(row), separators=(",", ":")) for row in baseline_rows)
        + "\n"
    )
    REPORT_PATH.write_text(_render_report(results))
    return results


if __name__ == "__main__":
    result = run()
    print(json.dumps({
        "verdict": result["verdict"],
        "recommendation": result["recommendation"],
        "baseline": result["baseline"],
        "holdout": result["holdout"],
        "concentration": result["concentration"],
        "cost_sensitivity": result["cost_sensitivity"],
    }, indent=2))
