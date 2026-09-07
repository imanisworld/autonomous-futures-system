#!/usr/bin/env python3
"""Build an offline missed-move report for transition/reclaim shadow candidates.

The input is a TradingView OHLCV CSV export. The output is evidence-only: it
does not create replay orders, paper orders, broker orders, or config changes.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from context.market_context import (
    MarketState,
    OHLCData,
    ORBData,
    PreviousDayData,
    PriceData,
    TrendData,
    VWAPData,
    VolumeData,
)
from strategy.shadow_setups import evaluate_shadow_setups, resolve_shadow_candidate


TARGET_STRATEGY = "transition_failed_breakdown_reclaim"
TICK_SIZE = {"MNQ": 0.25, "MES": 0.25, "NQ": 0.25, "ES": 0.25}
TICK_VALUE = {"MNQ": 0.50, "MES": 1.25, "NQ": 5.00, "ES": 12.50}
DEFAULT_COMMISSION_ROUND_TRIP = 1.48
DEFAULT_SLIPPAGE_TICKS_PER_LEG = 1.0


@dataclass(frozen=True)
class CsvBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_recent_dict(self) -> dict:
        return {
            "ts": self.ts.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def _load_csv(path: Path) -> list[CsvBar]:
    rows: list[CsvBar] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                CsvBar(
                    ts=_parse_dt(row["time"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("Volume") or row.get("volume") or 0),
                )
            )
    return rows


def _infer_instrument(path: Path, fallback: str | None) -> str:
    if fallback:
        return fallback.upper()
    name = path.name.upper()
    if "MNQ" in name:
        return "MNQ"
    if "MES" in name:
        return "MES"
    if "NQ" in name:
        return "MNQ"
    if "ES" in name:
        return "MES"
    return "MNQ"


def _infer_timeframe(path: Path, fallback: str | None) -> str:
    if fallback:
        return fallback
    match = re.search(r",\s*(\d+)", path.name)
    return f"{match.group(1)}m" if match else "5m"


def _timeframe_minutes(timeframe: str) -> int | None:
    match = re.search(r"(\d+)", timeframe)
    return int(match.group(1)) if match else None


def _session_bucket(ts: datetime) -> str:
    """Coarse session buckets for research splits, not execution gating."""
    minutes = ts.hour * 60 + ts.minute
    if 3 * 60 <= minutes < 9 * 60 + 30:
        return "london"
    if 9 * 60 + 30 <= minutes < 16 * 60:
        return "ny"
    return "after_hours"


def _filter_window(
    bars: Iterable[CsvBar],
    *,
    start: datetime | None,
    end: datetime | None,
) -> list[CsvBar]:
    out = []
    for bar in bars:
        if start and bar.ts < start:
            continue
        if end and bar.ts > end:
            continue
        out.append(bar)
    return out


def _avg_volume(bars: list[CsvBar], idx: int, lookback: int = 20) -> float:
    prior = bars[max(0, idx - lookback):idx]
    if not prior:
        return max(bars[idx].volume, 1.0)
    return max(sum(bar.volume for bar in prior) / len(prior), 1.0)


def _state_for(
    bars: list[CsvBar],
    idx: int,
    *,
    instrument: str,
    timeframe: str,
) -> MarketState:
    bar = bars[idx]
    previous = bars[max(0, idx - 1)]
    direction = "UP" if bar.close > previous.close else "DOWN" if bar.close < previous.close else "SIDEWAYS"
    avg_volume = _avg_volume(bars, idx)
    return MarketState(
        timestamp=bar.ts,
        instrument=instrument,
        session="offline_csv",
        price=PriceData(last=bar.close, bid=bar.close, ask=bar.close),
        ohlc=OHLCData(
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            timeframe=timeframe,
            bar_start=bar.ts.isoformat(),
        ),
        vwap=VWAPData(value=bar.close, price_vs_vwap="at", reclaimed=None, holding=None),
        orb=ORBData(high=bar.high, low=bar.low, timeframe_minutes=15, status="inside"),
        previous_day=PreviousDayData(high=bar.high, low=bar.low, close=bar.close),
        volume=VolumeData(
            current_bar=int(bar.volume),
            avg_bar=int(avg_volume),
            relative=bar.volume / avg_volume,
        ),
        market_condition="RANGE_BOUND",
        trend=TrendData(direction=direction, strength="MODERATE"),
        raw={},
    )


def _span_excursion(
    candidate,
    bars: list[tuple[float, float]],
    *,
    tick: float,
) -> dict:
    entry = candidate.entry
    is_long = candidate.direction == "LONG"
    if not bars:
        mfe = 0.0
        mae = 0.0
    elif is_long:
        mfe = max(high - entry for high, _ in bars)
        mae = min(low - entry for _, low in bars)
    else:
        mfe = max(entry - low for _, low in bars)
        mae = min(entry - high for high, _ in bars)
    return {
        "mfe_points": round(mfe, 2),
        "mae_points": round(mae, 2),
        "mfe_ticks": round(mfe / tick, 2),
        "mae_ticks": round(mae / tick, 2),
    }


def _mfe_mae(candidate, forward_bars: list[tuple[float, float]], instrument: str, outcome: dict) -> dict:
    tick = {"MNQ": 0.25, "MES": 0.25, "NQ": 0.25, "ES": 0.25}.get(instrument, 0.25)
    entry = candidate.entry
    fill_idx = None
    for idx, (high, low) in enumerate(forward_bars):
        if low <= entry <= high:
            fill_idx = idx
            break
    if fill_idx is None:
        return {
            "mfe_points": None,
            "mae_points": None,
            "mfe_ticks": None,
            "mae_ticks": None,
            "lookahead_mfe_points": None,
            "lookahead_mae_points": None,
            "lookahead_mfe_ticks": None,
            "lookahead_mae_ticks": None,
        }

    lookahead_span = forward_bars[fill_idx + 1:]
    exit_bars = outcome.get("bars_to_exit")
    if exit_bars is None:
        path_span = lookahead_span
    else:
        path_span = forward_bars[fill_idx + 1:exit_bars]
    path = _span_excursion(candidate, path_span, tick=tick)
    lookahead = _span_excursion(candidate, lookahead_span, tick=tick)
    return {
        **path,
        "lookahead_mfe_points": lookahead["mfe_points"],
        "lookahead_mae_points": lookahead["mae_points"],
        "lookahead_mfe_ticks": lookahead["mfe_ticks"],
        "lookahead_mae_ticks": lookahead["mae_ticks"],
    }


def _risk_metrics(candidate, mfe_mae: dict) -> dict:
    risk = (
        candidate.entry - candidate.stop
        if candidate.direction == "LONG"
        else candidate.stop - candidate.entry
    )
    if risk <= 0:
        return {
            "planned_risk_points": None,
            "mfe_r": None,
            "mae_r": None,
            "stop_survived_by_mae": None,
        }
    mfe = mfe_mae["mfe_points"]
    mae = mfe_mae["mae_points"]
    return {
        "planned_risk_points": round(risk, 2),
        "mfe_r": round(mfe / risk, 2) if mfe is not None else None,
        "mae_r": round(mae / risk, 2) if mae is not None else None,
        "stop_survived_by_mae": mae is not None and mae > -risk,
    }


def _scan(
    bars: list[CsvBar],
    *,
    instrument: str,
    timeframe: str,
    max_forward_bars: int,
    commission_round_trip: float = DEFAULT_COMMISSION_ROUND_TRIP,
    slippage_ticks_per_leg: float = DEFAULT_SLIPPAGE_TICKS_PER_LEG,
) -> list[dict]:
    found: list[dict] = []
    for idx, _bar in enumerate(bars):
        state = _state_for(bars, idx, instrument=instrument, timeframe=timeframe)
        recent = [bar.to_recent_dict() for bar in bars[max(0, idx - 7):idx + 1]]
        for candidate in evaluate_shadow_setups(state, recent):
            if candidate.strategy != TARGET_STRATEGY:
                continue
            forward = [
                (bar.high, bar.low)
                for bar in bars[idx + 1:idx + 1 + max_forward_bars]
            ]
            forward_closes = [
                bar.close for bar in bars[idx + 1:idx + 1 + max_forward_bars]
            ]
            outcome = resolve_shadow_candidate(
                candidate, forward, instrument=instrument
            ).to_dict()
            excursions = _mfe_mae(candidate, forward, instrument, outcome)
            costs = _cost_metrics(
                outcome,
                instrument=instrument,
                commission_round_trip=commission_round_trip,
                slippage_ticks_per_leg=slippage_ticks_per_leg,
            )
            baseline = _matched_baseline(
                candidate,
                signal_close=bars[idx].close,
                forward_closes=forward_closes,
                outcome=outcome,
                instrument=instrument,
                commission_round_trip=commission_round_trip,
                slippage_ticks_per_leg=slippage_ticks_per_leg,
            )
            found.append(
                {
                    "ts": bars[idx].ts.isoformat(),
                    "session_bucket": _session_bucket(bars[idx].ts),
                    "instrument": instrument,
                    "timeframe": timeframe,
                    "candidate": candidate.to_dict(),
                    "outcome": outcome,
                    **excursions,
                    **_risk_metrics(candidate, excursions),
                    **costs,
                    "matched_baseline": baseline,
                }
            )
    return found


def _cost_metrics(
    outcome: dict,
    *,
    instrument: str,
    commission_round_trip: float,
    slippage_ticks_per_leg: float,
) -> dict:
    """Apply explicit round-trip costs to resolved shadow outcomes only."""
    if not outcome["entry_filled"] or outcome["pnl_ticks"] is None:
        return {
            "gross_pnl_dollars": None,
            "commission_dollars": None,
            "slippage_dollars": None,
            "net_pnl_dollars": None,
        }
    tick_value = TICK_VALUE.get(instrument, 1.0)
    slippage_dollars = 2.0 * slippage_ticks_per_leg * tick_value
    gross = outcome["pnl_ticks"] * tick_value
    return {
        "gross_pnl_dollars": round(gross, 2),
        "commission_dollars": round(commission_round_trip, 2),
        "slippage_dollars": round(slippage_dollars, 2),
        "net_pnl_dollars": round(gross - commission_round_trip - slippage_dollars, 2),
    }


def _matched_baseline(
    candidate,
    *,
    signal_close: float,
    forward_closes: list[float],
    outcome: dict,
    instrument: str,
    commission_round_trip: float,
    slippage_ticks_per_leg: float,
) -> dict:
    """Return a same-timestamp, same-horizon passive control result.

    The control enters at the signal bar close and exits on the strategy's
    observed terminal bar. It does not use the candidate's stop or target.
    """
    exit_bars = outcome.get("bars_to_exit")
    if not outcome["entry_filled"] or exit_bars is None or exit_bars < 1:
        return {"gross_pnl_dollars": None, "net_pnl_dollars": None}
    exit_index = exit_bars - 1
    if exit_index >= len(forward_closes):
        return {"gross_pnl_dollars": None, "net_pnl_dollars": None}
    tick_value = TICK_VALUE.get(instrument, 1.0)
    ticks = (
        (forward_closes[exit_index] - signal_close)
        / TICK_SIZE.get(instrument, 0.25)
        if candidate.direction == "LONG"
        else (signal_close - forward_closes[exit_index])
        / TICK_SIZE.get(instrument, 0.25)
    )
    gross = ticks * tick_value
    costs = commission_round_trip + 2.0 * slippage_ticks_per_leg * tick_value
    return {
        "gross_pnl_dollars": round(gross, 2),
        "net_pnl_dollars": round(gross - costs, 2),
    }


def _result_counts(rows: list[dict]) -> dict:
    counts = Counter(row["outcome"]["result"] for row in rows)
    return {key: counts.get(key, 0) for key in ("WIN", "LOSS", "NO_FILL", "OPEN")}


def _series_stats(values: list[float]) -> dict:
    if not values:
        return {"avg": None, "median": None, "min": None, "max": None}
    return {
        "avg": round(mean(values), 2),
        "median": round(median(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def _split_counts(rows: list[dict], key: str) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row[key])].append(row)
    out: dict[str, dict] = {}
    for name, items in sorted(grouped.items()):
        counts = _result_counts(items)
        filled = [row for row in items if row["outcome"]["entry_filled"]]
        terminal = counts["WIN"] + counts["LOSS"]
        out[name] = {
            "candidates": len(items),
            "filled_count": len(filled),
            "fill_rate": round(len(filled) / len(items), 4) if items else None,
            "wins": counts["WIN"],
            "losses": counts["LOSS"],
            "no_fill": counts["NO_FILL"],
            "open": counts["OPEN"],
            "win_rate": round(counts["WIN"] / terminal, 4) if terminal else None,
            "win_loss_ratio": round(counts["WIN"] / counts["LOSS"], 4) if counts["LOSS"] else None,
        }
    return out


def _distribution(
    rows: list[dict],
    timeframe: str,
    *,
    commission_round_trip: float = DEFAULT_COMMISSION_ROUND_TRIP,
    slippage_ticks_per_leg: float = DEFAULT_SLIPPAGE_TICKS_PER_LEG,
) -> dict:
    filled = [row for row in rows if row["outcome"]["entry_filled"]]
    terminal = [row for row in rows if row["outcome"]["result"] in {"WIN", "LOSS"}]
    exit_bars = [
        row["outcome"]["bars_to_exit"]
        for row in terminal
        if row["outcome"]["bars_to_exit"] is not None
    ]
    target_exit_bars = [
        row["outcome"]["bars_to_exit"]
        for row in rows
        if row["outcome"]["result"] == "WIN" and row["outcome"]["bars_to_exit"] is not None
    ]
    stop_exit_bars = [
        row["outcome"]["bars_to_exit"]
        for row in rows
        if row["outcome"]["result"] == "LOSS" and row["outcome"]["bars_to_exit"] is not None
    ]
    tf_minutes = _timeframe_minutes(timeframe)
    stop_survived = [
        row["stop_survived_by_mae"]
        for row in filled
        if row["stop_survived_by_mae"] is not None
    ]
    counts = _result_counts(rows)
    candidate_count = len(rows)
    filled_count = len(filled)
    terminal_count = counts["WIN"] + counts["LOSS"]
    net_values = [
        row["net_pnl_dollars"]
        for row in rows
        if row["net_pnl_dollars"] is not None
    ]
    baseline_values = [
        row["matched_baseline"]["net_pnl_dollars"]
        for row in rows
        if row["matched_baseline"]["net_pnl_dollars"] is not None
    ]
    return {
        "candidate_count": candidate_count,
        "result_counts": counts,
        "fill_rate": round(filled_count / candidate_count, 4) if candidate_count else None,
        "filled_count": filled_count,
        "terminal_count": terminal_count,
        "win_rate": round(counts["WIN"] / terminal_count, 4) if terminal_count else None,
        "win_loss_ratio": round(counts["WIN"] / counts["LOSS"], 4) if counts["LOSS"] else None,
        "stop_survival_count": sum(1 for ok in stop_survived if ok),
        "stop_survival_rate": (
            round(sum(1 for ok in stop_survived if ok) / len(stop_survived), 4)
            if stop_survived
            else None
        ),
        "mfe_points": _series_stats(
            [row["mfe_points"] for row in filled if row["mfe_points"] is not None]
        ),
        "mae_points": _series_stats(
            [row["mae_points"] for row in filled if row["mae_points"] is not None]
        ),
        "mfe_r": _series_stats([row["mfe_r"] for row in filled if row["mfe_r"] is not None]),
        "mae_r": _series_stats([row["mae_r"] for row in filled if row["mae_r"] is not None]),
        "lookahead_mfe_points": _series_stats(
            [
                row["lookahead_mfe_points"]
                for row in filled
                if row["lookahead_mfe_points"] is not None
            ]
        ),
        "lookahead_mae_points": _series_stats(
            [
                row["lookahead_mae_points"]
                for row in filled
                if row["lookahead_mae_points"] is not None
            ]
        ),
        "max_adverse_excursion_points": (
            min(row["mae_points"] for row in filled if row["mae_points"] is not None)
            if filled
            else None
        ),
        "bars_to_exit": _series_stats(exit_bars),
        "minutes_to_exit": _series_stats(
            [bars * tf_minutes for bars in exit_bars if tf_minutes is not None]
        ),
        "minutes_to_target": _series_stats(
            [bars * tf_minutes for bars in target_exit_bars if tf_minutes is not None]
        ),
        "minutes_to_stop": _series_stats(
            [bars * tf_minutes for bars in stop_exit_bars if tf_minutes is not None]
        ),
        "session_split": _split_counts(rows, "session_bucket"),
        "cost_model": {
            "commission_round_trip_dollars": commission_round_trip,
            "slippage_ticks_per_leg": slippage_ticks_per_leg,
        },
        "strategy_net_pnl_dollars": round(sum(net_values), 2),
        "strategy_net_expectancy_dollars": round(mean(net_values), 2) if net_values else None,
        "strategy_net_profitable_count": sum(value > 0 for value in net_values),
        "matched_baseline_net_pnl_dollars": round(sum(baseline_values), 2),
        "matched_baseline_net_expectancy_dollars": round(mean(baseline_values), 2) if baseline_values else None,
        "matched_baseline_net_profitable_count": sum(value > 0 for value in baseline_values),
        "matched_baseline_count": len(baseline_values),
    }


def _summary(bars: list[CsvBar]) -> dict:
    if not bars:
        return {}
    return {
        "start": bars[0].ts.isoformat(),
        "end": bars[-1].ts.isoformat(),
        "bars": len(bars),
        "open": bars[0].open,
        "close": bars[-1].close,
        "net_points": round(bars[-1].close - bars[0].open, 2),
        "high": max(bar.high for bar in bars),
        "low": min(bar.low for bar in bars),
        "range_points": round(max(bar.high for bar in bars) - min(bar.low for bar in bars), 2),
        "total_volume": int(sum(bar.volume for bar in bars)),
        "max_bar_volume": int(max(bar.volume for bar in bars)),
    }


def _markdown(source: Path, summary: dict, rows: list[dict], distribution: dict) -> str:
    lines = [
        f"# Missed-Move Transition Report - {source.name}",
        "",
        "Evidence-only scan. No live, paper, or broker orders are created.",
        "",
        "This command surfaces fill rate, win/loss, MFE/MAE, and session splits "
        "for `transition_failed_breakdown_reclaim` so you can judge whether the "
        "lane is earning promotion evidence without touching execution behavior.",
        "",
        "## Window Summary",
        "",
    ]
    if summary:
        for key, value in summary.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- No bars in requested window.")
    lines.extend(["", "## Distribution", ""])
    if distribution:
        counts = distribution["result_counts"]
        fill_rate = distribution["fill_rate"]
        win_rate = distribution["win_rate"]
        win_loss_ratio = distribution["win_loss_ratio"]
        lines.extend(
            [
                f"- candidate_count: {distribution['candidate_count']}",
                f"- filled_count: {distribution['filled_count']}",
                f"- fill_rate: {_pct(fill_rate)}",
                f"- wins: {counts['WIN']}",
                f"- losses: {counts['LOSS']}",
                f"- no_fill: {counts['NO_FILL']}",
                f"- open_or_ambiguous: {counts['OPEN']}",
                f"- win_rate_on_terminal: {_pct(win_rate)}",
                f"- win_loss_ratio: {win_loss_ratio}",
                f"- stop_survival_count: {distribution['stop_survival_count']}",
                f"- stop_survival_rate: {_pct(distribution['stop_survival_rate'])}",
                f"- average_MFE_points: {distribution['mfe_points']['avg']}",
                f"- median_MFE_points: {distribution['mfe_points']['median']}",
                f"- average_MAE_points: {distribution['mae_points']['avg']}",
                f"- median_MAE_points: {distribution['mae_points']['median']}",
                f"- average_lookahead_MFE_points: {distribution['lookahead_mfe_points']['avg']}",
                f"- average_lookahead_MAE_points: {distribution['lookahead_mae_points']['avg']}",
                f"- max_adverse_excursion_points: {distribution['max_adverse_excursion_points']}",
                f"- average_time_to_exit_minutes: {distribution['minutes_to_exit']['avg']}",
                f"- median_time_to_exit_minutes: {distribution['minutes_to_exit']['median']}",
                f"- average_time_to_target_minutes: {distribution['minutes_to_target']['avg']}",
                f"- median_time_to_target_minutes: {distribution['minutes_to_target']['median']}",
                f"- average_time_to_stop_minutes: {distribution['minutes_to_stop']['avg']}",
                f"- median_time_to_stop_minutes: {distribution['minutes_to_stop']['median']}",
                f"- average_MFE_R: {distribution['mfe_r']['avg']}",
                f"- average_MAE_R: {distribution['mae_r']['avg']}",
                "",
                "### Costed strategy vs matched baseline",
                "",
                f"- commission_round_trip_dollars: {distribution['cost_model']['commission_round_trip_dollars']}",
                f"- slippage_ticks_per_leg: {distribution['cost_model']['slippage_ticks_per_leg']}",
                f"- strategy_net_pnl_dollars: {distribution['strategy_net_pnl_dollars']}",
                f"- strategy_net_expectancy_dollars: {distribution['strategy_net_expectancy_dollars']}",
                f"- strategy_net_profitable_count: {distribution['strategy_net_profitable_count']}",
                f"- matched_baseline_net_pnl_dollars: {distribution['matched_baseline_net_pnl_dollars']}",
                f"- matched_baseline_net_expectancy_dollars: {distribution['matched_baseline_net_expectancy_dollars']}",
                f"- matched_baseline_net_profitable_count: {distribution['matched_baseline_net_profitable_count']}",
                f"- matched_baseline_count: {distribution['matched_baseline_count']}",
            ]
        )
        lines.extend(["", "### Session Split", ""])
        lines.append("| session | candidates | fill_rate | wins | losses | no_fill | open | win_rate |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for session, counts in distribution["session_split"].items():
            lines.append(
                f"| {session} | {counts['candidates']} | {_pct(counts['fill_rate'])} | {counts['wins']} | {counts['losses']} | {counts['no_fill']} | {counts['open']} | {_pct(counts['win_rate'])} |"
            )
    lines.extend(["", "## Candidates", ""])
    if not rows:
        lines.append("No transition_failed_breakdown_reclaim candidates found.")
        return "\n".join(lines) + "\n"
    lines.append(
        "| ts | session | direction | entry | stop | target | risk pts | result | fill | path MFE pts | path MAE pts | MFE R | MAE R | bars to exit | notes |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|---|")
    for row in rows:
        cand = row["candidate"]
        out = row["outcome"]
        lines.append(
            "| {ts} | {session} | {direction} | {entry} | {stop} | {target} | {risk} | {result} | {filled} | {mfe} | {mae} | {mfe_r} | {mae_r} | {bars_to_exit} | {notes} |".format(
                ts=row["ts"],
                session=row["session_bucket"],
                direction=cand["direction"],
                entry=cand["entry"],
                stop=cand["stop"],
                target=cand["target"],
                risk=row["planned_risk_points"],
                result=out["result"],
                filled=out["entry_filled"],
                mfe=row["mfe_points"],
                mae=row["mae_points"],
                mfe_r=row["mfe_r"],
                mae_r=row["mae_r"],
                bars_to_exit=out["bars_to_exit"],
                notes=str(cand["notes"]).replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--instrument")
    parser.add_argument("--timeframe")
    parser.add_argument("--start", help="Inclusive ISO timestamp, e.g. 2026-07-08T18:00:00-04:00")
    parser.add_argument("--end", help="Inclusive ISO timestamp")
    parser.add_argument("--max-forward-bars", type=int, default=24)
    parser.add_argument(
        "--commission-round-trip",
        type=float,
        default=DEFAULT_COMMISSION_ROUND_TRIP,
        help="Round-trip commission/fees in dollars per resolved fill.",
    )
    parser.add_argument(
        "--slippage-ticks-per-leg",
        type=float,
        default=DEFAULT_SLIPPAGE_TICKS_PER_LEG,
        help="Adverse slippage in ticks applied to each entry and exit leg.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)

    all_bars = _load_csv(args.csv_path)
    bars = _filter_window(
        all_bars,
        start=_parse_dt(args.start) if args.start else None,
        end=_parse_dt(args.end) if args.end else None,
    )
    instrument = _infer_instrument(args.csv_path, args.instrument)
    timeframe = _infer_timeframe(args.csv_path, args.timeframe)
    rows = _scan(
        bars,
        instrument=instrument,
        timeframe=timeframe,
        max_forward_bars=max(1, args.max_forward_bars),
        commission_round_trip=args.commission_round_trip,
        slippage_ticks_per_leg=args.slippage_ticks_per_leg,
    )
    summary = _summary(bars)
    distribution = _distribution(
        rows,
        timeframe,
        commission_round_trip=args.commission_round_trip,
        slippage_ticks_per_leg=args.slippage_ticks_per_leg,
    )
    markdown = _markdown(args.csv_path, summary, rows, distribution)

    output = args.output or Path("logs") / f"missed_move_transition_{instrument}_{timeframe}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown)
    print(markdown, end="")

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(
                {
                    "summary": summary,
                    "distribution": distribution,
                    "candidates": rows,
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
