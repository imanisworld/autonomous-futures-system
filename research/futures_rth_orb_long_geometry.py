"""Futures RTH ORB_BREAKOUT_LONG geometry + fill study (fng-v0.1).

Research-only runner for the preregistered study:
docs/prereg-futures-rth-orb-long-geometry-2026-09-21.md

Reads only gitignored Polygon replay bars. It does not change strategy/risk/runtime
state and explicitly disables the Webull futures mirror in this process before
constructing PaperBroker.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.non_strat_coverage import observe_session  # noqa: E402
from config.futures_contracts import TICK_SIZE, TICK_VALUE  # noqa: E402
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

STUDY_ID = "FUTURES_RTH_ORB_LONG_GEOMETRY"
STUDY_VERSION = "fng-v0.1"
FAMILY = "ORB_BREAKOUT_LONG"
COMMISSION_RT = 1.24
ORB_STOP_TICKS = {"MNQ": 48.0, "MES": 16.0}
MAX_STOP_TICKS = {"MNQ": 120.0, "MES": 60.0}
SLIPPAGE_CELLS = {"base": 1.0, "stress": 2.0}
GEOMETRIES = ("G1_CANONICAL_OFFSET", "G2_TRIGGER_LOW", "G3_ORB_MIDPOINT")


@dataclass(frozen=True)
class Candidate:
    instrument: str
    session_date: str
    episode_id: str
    trigger_bar_start: str
    trigger_idx: int
    trigger_close: float
    trigger_low: float
    orb_high: float
    orb_low: float
    payload_orb_high: float | None


@dataclass(frozen=True)
class TradeRow:
    instrument: str
    session_date: str
    half: str
    episode_id: str
    geometry: str
    slippage_label: str
    slippage_ticks: float
    trigger_bar_start: str
    trigger_close: float
    decision_open: float | None
    fill_entry: float | None
    orb_high: float
    orb_low: float
    stop: float | None
    target: float | None
    stop_ticks: float | None
    over_stop_cap: bool | None
    detachment_ticks: float | None
    status: str
    result: str | None
    exit_reason: str | None
    exit_price: float | None
    gross_pnl: float | None
    net_pnl: float | None
    bars_held: int | None
    payload_orb_high: float | None
    payload_orb_equal: bool | None
    legacy_boundary_same_bar: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _raw_rows(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            ts = row.get("timestamp")
            if ts:
                rows[datetime.fromisoformat(str(ts)).isoformat()] = row
    return rows


def collect_candidates(instrument: str) -> tuple[list[Candidate], dict[str, int]]:
    files = session_files(instrument)
    if not files:
        raise SystemExit(f"no replay files for {instrument}")
    days = sorted(files)
    excluded_roll = roll_excluded_sessions(days)

    loaded: dict[date, tuple[list, dict[str, Any] | None]] = {}
    raw_by_day: dict[date, dict[str, dict[str, Any]]] = {}
    skipped = {"incomplete": 0, "roll": 0, "no_prior": 0}
    for day in days:
        bars, payload = load_rth_session(files[day])
        if len(bars) != RTH_BARS:
            skipped["incomplete"] += 1
            continue
        loaded[day] = (bars, payload)
        raw_by_day[day] = _raw_rows(files[day])

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

        bars = loaded[day][0]
        prior_bars = loaded[prior_days[-1]][0]
        history = [bar for d in prior_days for bar in loaded[d][0]]
        events = observe_session(
            symbol=instrument,
            session_date=day.isoformat(),
            prior_session_bars=prior_bars,
            session_bars=bars,
            history_bars=history,
        )
        first: dict[str, Any] = {}
        for event in sorted(events, key=lambda e: e.bar_start):
            if event.family == FAMILY:
                first.setdefault(event.episode_id, event)

        by_start = {bar.start_utc.isoformat(): i for i, bar in enumerate(bars)}
        raw = raw_by_day[day]
        orb_high = max(bar.high for bar in bars[:6])
        orb_low = min(bar.low for bar in bars[:6])
        for episode_id, event in first.items():
            event_start = datetime.fromisoformat(event.bar_start.replace("Z", "+00:00")).isoformat()
            bar_idx = by_start.get(event_start)
            if bar_idx is None:
                continue
            trigger = bars[bar_idx]
            payload = raw.get(event_start, {})
            payload_orb = _safe_float(payload.get("orb_high"))
            out.append(
                Candidate(
                    instrument=instrument,
                    session_date=day.isoformat(),
                    episode_id=episode_id,
                    trigger_bar_start=event_start,
                    trigger_idx=bar_idx,
                    trigger_close=float(trigger.close),
                    trigger_low=float(trigger.low),
                    orb_high=float(orb_high),
                    orb_low=float(orb_low),
                    payload_orb_high=payload_orb,
                )
            )
    return out, skipped


def geometry_prices(
    candidate: Candidate,
    decision_open: float,
    geometry: str,
) -> tuple[float, float]:
    tick = TICK_SIZE[candidate.instrument]
    if geometry == "G1_CANONICAL_OFFSET":
        stop = candidate.orb_high - ORB_STOP_TICKS[candidate.instrument] * tick
        rr = 2.2
    elif geometry == "G2_TRIGGER_LOW":
        stop = candidate.trigger_low - tick
        rr = 2.0
    elif geometry == "G3_ORB_MIDPOINT":
        stop = ((candidate.orb_high + candidate.orb_low) / 2.0) - tick
        rr = 2.0
    else:
        raise ValueError(f"unknown geometry {geometry!r}")
    risk = decision_open - stop
    if risk <= 0:
        return stop, float("nan")
    target = decision_open + rr * risk
    return stop, target


def _classify(entry: float, exit_price: float) -> str:
    if exit_price > entry:
        return "WIN"
    if exit_price < entry:
        return "LOSS"
    return "BREAKEVEN"


def simulate_candidate(
    candidate: Candidate,
    bars: Sequence,
    *,
    geometry: str,
    slippage_label: str,
    slippage_ticks: float,
) -> TradeRow:
    half = "H1" if date.fromisoformat(candidate.session_date) < HALF_SPLIT else "H2"
    tick = TICK_SIZE[candidate.instrument]
    payload_equal = (
        None
        if candidate.payload_orb_high is None
        else abs(candidate.payload_orb_high - candidate.orb_high) < 1e-9
    )
    legacy_same_bar = (
        None
        if candidate.payload_orb_high is None
        else candidate.trigger_close > candidate.payload_orb_high
    )

    next_idx = candidate.trigger_idx + 1
    if next_idx >= len(bars):
        return TradeRow(
            candidate.instrument, candidate.session_date, half, candidate.episode_id,
            geometry, slippage_label, slippage_ticks, candidate.trigger_bar_start,
            candidate.trigger_close, None, None, candidate.orb_high, candidate.orb_low,
            None, None, None, None, None, "NO_DATA", None, None, None, None, None,
            None, candidate.payload_orb_high, payload_equal, legacy_same_bar,
        )

    decision_open = float(bars[next_idx].open)
    stop, target = geometry_prices(candidate, decision_open, geometry)
    stop_ticks = (decision_open - stop) / tick if math.isfinite(target) else None
    over_cap = (
        None if stop_ticks is None else stop_ticks > MAX_STOP_TICKS[candidate.instrument]
    )
    detachment = (decision_open - candidate.orb_high) / tick

    if not math.isfinite(target) or not (stop < decision_open < target):
        return TradeRow(
            candidate.instrument, candidate.session_date, half, candidate.episode_id,
            geometry, slippage_label, slippage_ticks, candidate.trigger_bar_start,
            candidate.trigger_close, decision_open, None, candidate.orb_high,
            candidate.orb_low, stop, target if math.isfinite(target) else None,
            stop_ticks, over_cap, detachment, "BRACKET_INVALID", None, None, None,
            None, None, None, candidate.payload_orb_high, payload_equal, legacy_same_bar,
        )

    # Research must never fan out into the default-off Webull mirror even if the
    # caller's shell happens to have that flag enabled.
    os.environ["WEBULL_FUTURES_MIRROR_ENABLED"] = "false"

    broker = PaperBroker(
        starting_balance=100_000.0,
        slippage_ticks=slippage_ticks,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="market",
    )
    order = BracketOrder(
        instrument=candidate.instrument,
        direction="LONG",
        entry=decision_open,
        stop=stop,
        target=target,
        rr_ratio=(target - decision_open) / (decision_open - stop),
        strategy="fng_rth_orb_long",
        contracts=1,
        force_market_entry=True,
    )
    opened = broker.execute_bracket(order, market_price=decision_open)
    if opened.result == "CANCELLED":
        return TradeRow(
            candidate.instrument, candidate.session_date, half, candidate.episode_id,
            geometry, slippage_label, slippage_ticks, candidate.trigger_bar_start,
            candidate.trigger_close, decision_open, float(opened.entry_price),
            candidate.orb_high, candidate.orb_low, stop, target, stop_ticks, over_cap,
            detachment, "BRACKET_INVALID", None, opened.exit_reason, None, None, None,
            None, candidate.payload_orb_high, payload_equal, legacy_same_bar,
        )

    fill_entry = float(opened.entry_price)
    for j in range(next_idx, len(bars)):
        bar = bars[j]
        fill = broker.resolve_position(
            NextBarOHLC(
                high=float(bar.high),
                low=float(bar.low),
                open=float(bar.open),
            )
        )
        if fill is not None:
            gross = float(fill.pnl_dollars or 0.0)
            return TradeRow(
                candidate.instrument, candidate.session_date, half, candidate.episode_id,
                geometry, slippage_label, slippage_ticks, candidate.trigger_bar_start,
                candidate.trigger_close, decision_open, fill_entry, candidate.orb_high,
                candidate.orb_low, stop, target, stop_ticks, over_cap, detachment,
                "RESOLVED", fill.result, fill.exit_reason,
                float(fill.exit_price) if fill.exit_price is not None else None,
                gross, gross - COMMISSION_RT, j - next_idx,
                candidate.payload_orb_high, payload_equal, legacy_same_bar,
            )

    # RTH-only forced flatten. force_resolve itself does not slip, so pass an
    # already-adversely-adjusted close.
    final_close = float(bars[-1].close)
    exit_price = final_close - slippage_ticks * tick
    fill = broker.force_resolve(_classify(fill_entry, exit_price), exit_price)
    gross = float(fill.pnl_dollars or 0.0)
    return TradeRow(
        candidate.instrument, candidate.session_date, half, candidate.episode_id,
        geometry, slippage_label, slippage_ticks, candidate.trigger_bar_start,
        candidate.trigger_close, decision_open, fill_entry, candidate.orb_high,
        candidate.orb_low, stop, target, stop_ticks, over_cap, detachment,
        "RESOLVED", fill.result, "RTH_EOD_FLATTEN", float(fill.exit_price or exit_price),
        gross, gross - COMMISSION_RT, len(bars) - 1 - next_idx,
        candidate.payload_orb_high, payload_equal, legacy_same_bar,
    )


def run_instrument(instrument: str) -> tuple[list[TradeRow], dict[str, Any]]:
    candidates, skipped = collect_candidates(instrument)
    files = session_files(instrument)
    cache: dict[str, Sequence] = {}
    rows: list[TradeRow] = []
    for candidate in candidates:
        if candidate.session_date not in cache:
            bars, _ = load_rth_session(files[date.fromisoformat(candidate.session_date)])
            cache[candidate.session_date] = bars
        bars = cache[candidate.session_date]
        for geometry in GEOMETRIES:
            for label, slip in SLIPPAGE_CELLS.items():
                rows.append(
                    simulate_candidate(
                        candidate,
                        bars,
                        geometry=geometry,
                        slippage_label=label,
                        slippage_ticks=slip,
                    )
                )
    return rows, {"candidates": len(candidates), "skipped": skipped}


def _pf(values: Sequence[float]) -> float | None:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses == 0:
        return None
    return wins / losses


def _max_drawdown(values: Sequence[float]) -> float:
    equity = peak = 0.0
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def _monthly_concentration(rows: Sequence[TradeRow]) -> float | None:
    monthly: dict[str, float] = defaultdict(float)
    for row in rows:
        if row.net_pnl is not None:
            monthly[row.session_date[:7]] += row.net_pnl
    positive = [v for v in monthly.values() if v > 0]
    if not positive:
        return None
    return max(positive) / sum(positive)


def summarize_cell(rows: Sequence[TradeRow]) -> dict[str, Any]:
    resolved = [r for r in rows if r.status == "RESOLVED" and r.net_pnl is not None]
    pnl = [float(r.net_pnl) for r in resolved]
    stop_ticks = [float(r.stop_ticks) for r in rows if r.stop_ticks is not None]
    detach = [float(r.detachment_ticks) for r in rows if r.detachment_ticks is not None]

    by_half: dict[str, Any] = {}
    for half in ("H1", "H2"):
        half_rows = [r for r in resolved if r.half == half]
        hp = [float(r.net_pnl) for r in half_rows if r.net_pnl is not None]
        by_half[half] = {
            "n": len(hp),
            "net": round(sum(hp), 2),
            "expectancy": round(statistics.fmean(hp), 4) if hp else None,
            "pf": round(_pf(hp), 4) if _pf(hp) is not None else None,
        }

    return {
        "candidates": len(rows),
        "resolved": len(resolved),
        "no_data": sum(r.status == "NO_DATA" for r in rows),
        "bracket_invalid": sum(r.status == "BRACKET_INVALID" for r in rows),
        "wins": sum(r.result == "WIN" for r in resolved),
        "losses": sum(r.result == "LOSS" for r in resolved),
        "breakeven": sum(r.result == "BREAKEVEN" for r in resolved),
        "net": round(sum(pnl), 2),
        "expectancy": round(statistics.fmean(pnl), 4) if pnl else None,
        "pf": round(_pf(pnl), 4) if _pf(pnl) is not None else None,
        "max_drawdown": round(_max_drawdown(pnl), 2),
        "stop_ticks_median": round(statistics.median(stop_ticks), 2) if stop_ticks else None,
        "stop_ticks_p90": round(_quantile(stop_ticks, 0.90), 2) if stop_ticks else None,
        "stop_ticks_max": round(max(stop_ticks), 2) if stop_ticks else None,
        "over_stop_cap": sum(r.over_stop_cap is True for r in rows),
        "detachment_ticks_median": round(statistics.median(detach), 2) if detach else None,
        "detachment_ticks_p90": round(_quantile(detach, 0.90), 2) if detach else None,
        "top_positive_month_share": (
            round(_monthly_concentration(resolved), 4)
            if _monthly_concentration(resolved) is not None
            else None
        ),
        "halves": by_half,
    }


def pass_rule(report: dict[str, Any], geometry: str) -> dict[str, Any]:
    reasons: list[str] = []
    for instrument in ("MNQ", "MES"):
        cells = report["instruments"][instrument]["cells"]
        base = cells[f"{geometry}:base"]
        stress = cells[f"{geometry}:stress"]
        for half in ("H1", "H2"):
            b = base["halves"][half]
            s = stress["halves"][half]
            if b["n"] < 100:
                reasons.append(f"{instrument}:{half}:base_n<{100}")
            if not (b["net"] > 0 and b["pf"] is not None and b["pf"] > 1.10):
                reasons.append(f"{instrument}:{half}:base_not_positive_pf")
            if not (s["net"] > 0):
                reasons.append(f"{instrument}:{half}:stress_net_not_positive")
        share = base["top_positive_month_share"]
        if share is None or share >= 0.60:
            reasons.append(f"{instrument}:month_concentration")
    return {
        "geometry": geometry,
        "passes": not reasons,
        "classification": "PROMISING_BUT_UNPROVEN" if not reasons else "WAIT",
        "reasons": reasons,
    }


def build_report(all_rows: dict[str, list[TradeRow]], meta: dict[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "study_id": STUDY_ID,
        "study_version": STUDY_VERSION,
        "family": FAMILY,
        "commission_rt": COMMISSION_RT,
        "geometries": list(GEOMETRIES),
        "slippage_cells": SLIPPAGE_CELLS,
        "instruments": {},
    }
    for instrument, rows in all_rows.items():
        cells: dict[str, Any] = {}
        for geometry in GEOMETRIES:
            for label in SLIPPAGE_CELLS:
                selected = [
                    r for r in rows
                    if r.geometry == geometry and r.slippage_label == label
                ]
                cells[f"{geometry}:{label}"] = summarize_cell(selected)

        unique = {}
        for row in rows:
            unique.setdefault(row.episode_id, row)
        identity_rows = list(unique.values())
        with_payload = [r for r in identity_rows if r.payload_orb_high is not None]
        risk_feasible_cells: dict[str, Any] = {}
        for geometry in GEOMETRIES:
            for label in SLIPPAGE_CELLS:
                selected = [
                    r for r in rows
                    if r.geometry == geometry
                    and r.slippage_label == label
                    and r.over_stop_cap is False
                ]
                risk_feasible_cells[f"{geometry}:{label}"] = summarize_cell(selected)

        report["instruments"][instrument] = {
            **meta[instrument],
            "cells": cells,
            "risk_feasible_cells": risk_feasible_cells,
            "identity": {
                "events": len(identity_rows),
                "payload_orb_available": len(with_payload),
                "payload_orb_equal": sum(r.payload_orb_equal is True for r in with_payload),
                "legacy_boundary_same_bar": sum(
                    r.legacy_boundary_same_bar is True for r in with_payload
                ),
            },
        }

    report["gate"] = [pass_rule(report, geometry) for geometry in GEOMETRIES]
    return report


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {STUDY_ID} {STUDY_VERSION}",
        "",
        "**Research only. No runtime/paper/promotion authority.**",
        "",
    ]
    for instrument in ("MNQ", "MES"):
        block = report["instruments"][instrument]
        ident = block["identity"]
        lines += [
            f"## {instrument}",
            "",
            f"population: {block['candidates']} candidates; skipped {block['skipped']}",
            (
                "identity: payload ORB available "
                f"{ident['payload_orb_available']}/{ident['events']}; equal frozen ORB "
                f"{ident['payload_orb_equal']}; same-bar legacy boundary "
                f"{ident['legacy_boundary_same_bar']}"
            ),
            "",
            "| cell | n | net | exp | PF | maxDD | stop med/p90/max | >cap | detach med/p90 | H1 n/net/PF | H2 n/net/PF | top month |",
            "|---|---:|---:|---:|---:|---:|---|---:|---|---|---|---:|",
        ]
        for geometry in GEOMETRIES:
            for label in SLIPPAGE_CELLS:
                cell = block["cells"][f"{geometry}:{label}"]
                h1, h2 = cell["halves"]["H1"], cell["halves"]["H2"]
                lines.append(
                    f"| {geometry}:{label} | {cell['resolved']} | {cell['net']} | "
                    f"{cell['expectancy']} | {cell['pf']} | {cell['max_drawdown']} | "
                    f"{cell['stop_ticks_median']}/{cell['stop_ticks_p90']}/{cell['stop_ticks_max']} | "
                    f"{cell['over_stop_cap']} | "
                    f"{cell['detachment_ticks_median']}/{cell['detachment_ticks_p90']} | "
                    f"{h1['n']}/{h1['net']}/{h1['pf']} | "
                    f"{h2['n']}/{h2['net']}/{h2['pf']} | "
                    f"{cell['top_positive_month_share']} |"
                )
        lines += [
            "",
            "### Risk-feasible view (secondary; raw gate remains authoritative)",
            "",
            "| cell | n | net | exp | PF | maxDD | H1 n/net/PF | H2 n/net/PF |",
            "|---|---:|---:|---:|---:|---:|---|---|",
        ]
        for geometry in GEOMETRIES:
            for label in SLIPPAGE_CELLS:
                cell = block["risk_feasible_cells"][f"{geometry}:{label}"]
                h1, h2 = cell["halves"]["H1"], cell["halves"]["H2"]
                lines.append(
                    f"| {geometry}:{label} | {cell['resolved']} | {cell['net']} | "
                    f"{cell['expectancy']} | {cell['pf']} | {cell['max_drawdown']} | "
                    f"{h1['n']}/{h1['net']}/{h1['pf']} | "
                    f"{h2['n']}/{h2['net']}/{h2['pf']} |"
                )
        lines.append("")
    lines += ["## Pre-registered gate", ""]
    for gate in report["gate"]:
        lines.append(
            f"- {gate['geometry']}: **{gate['classification']}** — "
            + ("PASS" if gate["passes"] else "; ".join(gate["reasons"]))
        )
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="fng-v0.1 RTH ORB long geometry study")
    parser.add_argument("--instrument", choices=("MNQ", "MES", "ALL"), default="ALL")
    parser.add_argument("--out", default=str(ROOT / "logs" / "fng_v01"))
    args = parser.parse_args(argv)

    os.environ["WEBULL_FUTURES_MIRROR_ENABLED"] = "false"

    instruments = ("MNQ", "MES") if args.instrument == "ALL" else (args.instrument,)
    rows_by_instrument: dict[str, list[TradeRow]] = {}
    meta: dict[str, Any] = {}
    for instrument in instruments:
        rows, info = run_instrument(instrument)
        rows_by_instrument[instrument] = rows
        meta[instrument] = info

    if set(rows_by_instrument) != {"MNQ", "MES"}:
        for instrument in ("MNQ", "MES"):
            if instrument not in rows_by_instrument:
                rows, info = run_instrument(instrument)
                rows_by_instrument[instrument] = rows
                meta[instrument] = info

    report = build_report(rows_by_instrument, meta)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "fng_v01_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    for instrument, rows in rows_by_instrument.items():
        with (out / f"fng_v01_{instrument}_rows.jsonl").open("w") as handle:
            for row in rows:
                handle.write(json.dumps(row.to_dict(), sort_keys=True) + "\n")
    markdown = to_markdown(report)
    (out / "fng_v01_report.md").write_text(markdown)
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())