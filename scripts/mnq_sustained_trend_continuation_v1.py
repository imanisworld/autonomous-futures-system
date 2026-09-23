#!/usr/bin/env python3
"""Offline one-pass evaluator for frozen MNQ Sustained Trend Continuation v1.

No broker adapter, RiskEngine, webhook route, runtime config, or live state is
imported or touched.  The script fails closed if the 15m structural corpus and
5m trigger corpus do not overlap causally for every measured 15m bar.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research.mnq_sustained_trend_continuation_v1 import (  # noqa: E402
    CapacityGate,
    MAX_STOP_TICKS,
    ResearchTrade,
    ROUND_TURN_COMMISSION,
    SustainedTrendContinuationV1,
    observation_day,
    resolve_trade_on_bar,
)

DEFAULT_15M = REPO / "data/replay_corpus_v1_market_condition_fixed/MNQ"
DEFAULT_5M = REPO / "data/replay_corpus_v1_5m/MNQ"
DEFAULT_OUT = REPO / "logs/mnq_sustained_trend_continuation_v1.json"
MOVE_WINDOW_BARS = 4
MOVE_THRESHOLD_POINTS = 60.0
PF_HURDLE = 1.94
MAX_DRAWDOWN_HURDLE = 450.0


def _parse_ts(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("missing timestamp")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"naive timestamp {text!r}")
    return parsed


def _raw_ts(row: dict[str, Any]) -> str:
    value = row.get("timestamp", row.get("ts"))
    if value is None:
        raise ValueError("bar missing timestamp/ts")
    return str(value)


def _close_time(row: dict[str, Any], minutes: int) -> datetime:
    return _parse_ts(_raw_ts(row)) + timedelta(minutes=minutes)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text().splitlines(), 1):
        text = line.strip()
        if not text:
            continue
        obj = json.loads(text)
        if not isinstance(obj, dict):
            raise RuntimeError(f"{path}:{line_no}: expected object")
        rows.append(obj)
    rows.sort(key=lambda row: _parse_ts(_raw_ts(row)))
    return rows


def _day(path: Path) -> str:
    prefix = "MNQ_"
    if not path.stem.startswith(prefix):
        raise RuntimeError(f"unexpected corpus filename {path.name}")
    day = path.stem[len(prefix):]
    datetime.fromisoformat(day)
    return day


def _corpus_files(root: Path) -> list[Path]:
    files = sorted(root.glob("MNQ_*.jsonl"))
    if not files:
        raise RuntimeError(f"no MNQ jsonl files under {root}")
    return files


def _fingerprint(files: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_5m_coverage(
    files15: list[Path],
    by_day5: dict[str, Path],
) -> dict[str, Any]:
    missing_files: list[str] = []
    uncovered: list[dict[str, str]] = []
    total_15m_bars = 0
    covered_15m_bars = 0

    for path15 in files15:
        day = _day(path15)
        path5 = by_day5.get(day)
        if path5 is None:
            missing_files.append(day)
            continue
        bars15 = _load_jsonl(path15)
        bars5 = _load_jsonl(path5)
        close5 = [_close_time(row, 5) for row in bars5]
        for row15 in bars15:
            total_15m_bars += 1
            open15 = _parse_ts(_raw_ts(row15))
            close15 = open15 + timedelta(minutes=15)
            left = bisect.bisect_right(close5, open15)
            right = bisect.bisect_right(close5, close15)
            if left < right:
                covered_15m_bars += 1
            else:
                if len(uncovered) < 50:
                    uncovered.append(
                        {
                            "day": day,
                            "bar_ts": _raw_ts(row15),
                            "session": str(row15.get("session") or ""),
                        }
                    )

    if missing_files or covered_15m_bars != total_15m_bars:
        raise RuntimeError(
            "5m trigger coverage is incomplete; refusing to score. "
            f"missing_day_files={len(missing_files)} "
            f"uncovered_15m_bars={total_15m_bars-covered_15m_bars} "
            f"examples={uncovered[:5]}"
        )

    return {
        "missing_day_files": 0,
        "total_15m_bars": total_15m_bars,
        "covered_15m_bars": covered_15m_bars,
        "coverage_fraction": 1.0 if total_15m_bars else None,
    }


def _close_open_eod(trade: ResearchTrade, gate: CapacityGate) -> None:
    trade.result = "OPEN_EOD"
    gate.mark_closed()


def _max_drawdown(rows: list[ResearchTrade]) -> float:
    equity = peak = drawdown = 0.0
    for trade in rows:
        if trade.net_pnl is None:
            continue
        equity += float(trade.net_pnl)
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return round(drawdown, 2)


def _profit_factor(pnls: list[float]) -> tuple[float | None, bool]:
    positive = sum(p for p in pnls if p > 0)
    negative = -sum(p for p in pnls if p < 0)
    if negative <= 0:
        return (None, positive > 0)
    return (round(positive / negative, 4), False)


def _summary(trades: list[ResearchTrade]) -> dict[str, Any]:
    terminal = [trade for trade in trades if trade.result in {"WIN", "LOSS"}]
    pnls = [float(trade.net_pnl or 0.0) for trade in terminal]
    pf, pf_inf = _profit_factor(pnls)
    net = round(sum(pnls), 2)

    by_day: dict[str, float] = defaultdict(float)
    for trade in terminal:
        by_day[trade.observation_day] += float(trade.net_pnl or 0.0)
    ranked = sorted(by_day.items(), key=lambda item: item[1], reverse=True)
    positive_days = [value for _, value in ranked if value > 0]
    total_positive_days = sum(positive_days)
    top3_positive = sum(positive_days[:3])
    top3_share = (
        round(top3_positive / total_positive_days, 4)
        if total_positive_days > 0
        else None
    )
    best = ranked[0] if ranked else (None, 0.0)
    best3 = sum(value for _, value in ranked[:3]) if ranked else 0.0

    streak = max_streak = 0
    for trade in terminal:
        if trade.result == "LOSS":
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    return {
        "fills": len(trades),
        "terminal": len(terminal),
        "wins": sum(trade.result == "WIN" for trade in terminal),
        "losses": sum(trade.result == "LOSS" for trade in terminal),
        "open_eod": sum(trade.result == "OPEN_EOD" for trade in trades),
        "net_pnl": net,
        "expectancy_per_terminal": round(net / len(terminal), 2) if terminal else None,
        "profit_factor": pf,
        "profit_factor_infinite": pf_inf,
        "max_drawdown": _max_drawdown(terminal),
        "distinct_filled_days": len({trade.observation_day for trade in trades}),
        "best_day": {"date": best[0], "net_pnl": round(best[1], 2)},
        "leave_best_day_out_net": round(net - best[1], 2) if ranked else None,
        "leave_best_3_days_out_net": round(net - best3, 2) if ranked else None,
        "top3_positive_day_contribution_to_positive_net": top3_share,
        "max_consecutive_losses": max_streak,
    }


def _group_summary(trades: list[ResearchTrade], key) -> dict[str, Any]:
    groups: dict[str, list[ResearchTrade]] = defaultdict(list)
    for trade in trades:
        groups[str(key(trade))].append(trade)
    return {name: _summary(rows) for name, rows in sorted(groups.items())}


def _move_windows(
    bars15_by_day: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for day, bars in sorted(bars15_by_day.items()):
        for i in range(0, len(bars), MOVE_WINDOW_BARS):
            block = bars[i:i + MOVE_WINDOW_BARS]
            if len(block) < 2:
                continue
            hi = max(float(row["high"]) for row in block)
            lo = min(float(row["low"]) for row in block)
            rng = hi - lo
            if rng < MOVE_THRESHOLD_POINTS:
                continue
            start = _close_time(block[0], 15)
            end = _close_time(block[-1], 15)
            windows.append(
                {
                    "day": day,
                    "start": start,
                    "end": end,
                    "range_points": round(rng, 2),
                    "up_window": float(block[-1]["close"]) > float(block[0]["open"]),
                }
            )
    return windows


def _between(ts_text: str, start: datetime, end: datetime) -> bool:
    ts = _parse_ts(ts_text)
    return start <= ts <= end


def _coverage(
    windows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    trades: list[ResearchTrade],
) -> dict[str, Any]:
    armed = [event for event in events if event["event"] == "ARMED"]
    triggers = [
        event
        for event in events
        if event["event"] in {"TRIGGERED", "STOP_CAP_REJECTED"}
    ]

    def summarize(selected: list[dict[str, Any]]) -> dict[str, Any]:
        with_arm = with_trigger = with_fill = 0
        for window in selected:
            start, end = window["start"], window["end"]
            if any(_between(event["event_ts"], start, end) for event in armed):
                with_arm += 1
            if any(_between(event["event_ts"], start, end) for event in triggers):
                with_trigger += 1
            if any(_between(trade.trigger_ts, start, end) for trade in trades):
                with_fill += 1
        n = len(selected)
        return {
            "windows": n,
            "with_arm": with_arm,
            "with_trigger": with_trigger,
            "with_fill": with_fill,
            "fill_coverage_fraction": round(with_fill / n, 4) if n else None,
        }

    up = [window for window in windows if window["up_window"]]
    return {
        "definition": {
            "window_bars": MOVE_WINDOW_BARS,
            "threshold_points": MOVE_THRESHOLD_POINTS,
            "non_overlapping": True,
            "source": "descriptive reuse of missed_move_gate_sweep_622d methodology",
            "pass_fail_authority": False,
        },
        "all_large_range_windows": summarize(windows),
        "up_large_range_windows": summarize(up),
    }


def run(root15: Path, root5: Path) -> dict[str, Any]:
    files15 = _corpus_files(root15)
    files5_all = _corpus_files(root5)
    by_day5 = {_day(path): path for path in files5_all}
    coverage_check = _validate_5m_coverage(files15, by_day5)
    files5 = [by_day5[_day(path)] for path in files15]

    dates = [_day(path) for path in files15]
    midpoint = dates[len(dates) // 2]

    detector = SustainedTrendContinuationV1()
    capacity = CapacityGate()
    active: ResearchTrade | None = None
    trades: list[ResearchTrade] = []
    events: list[dict[str, Any]] = []
    bars15_by_day: dict[str, list[dict[str, Any]]] = {}

    for path15, path5 in zip(files15, files5):
        day = _day(path15)
        bars15 = _load_jsonl(path15)
        bars5 = _load_jsonl(path5)
        bars15_by_day[day] = bars15

        stream: list[tuple[datetime, int, str, dict[str, Any]]] = []
        stream.extend((_close_time(row, 15), 0, "15m", row) for row in bars15)
        stream.extend((_close_time(row, 5), 1, "5m", row) for row in bars5)
        stream.sort(key=lambda item: (item[0], item[1]))

        for close_ts, _, timeframe, row in stream:
            current_day = observation_day(close_ts)
            if active is not None and active.observation_day != current_day:
                _close_open_eod(active, capacity)
                active = None

            if timeframe == "15m":
                produced = detector.on_15m(row, close_time=close_ts)
            else:
                if active is not None and close_ts > _parse_ts(active.trigger_ts):
                    resolved = resolve_trade_on_bar(active, row, close_time=close_ts)
                    if resolved is not None:
                        capacity.mark_closed()
                        active = None
                produced = detector.on_5m(row, close_time=close_ts)

            for item in produced:
                event = item.to_dict()
                event["observation_day"] = current_day
                events.append(event)

                if event["event"] != "TRIGGERED":
                    continue
                status = capacity.classify_trigger(current_day)
                if status != "FILLED":
                    skipped = dict(event)
                    skipped["event"] = status
                    events.append(skipped)
                    continue

                trade = ResearchTrade(
                    observation_day=current_day,
                    session=str(event.get("session") or row.get("session") or "unknown"),
                    arm_bar_ts=str(event["arm_bar_ts"]),
                    trigger_ts=str(event["event_ts"]),
                    entry=float(event["modeled_fill"]),
                    stop=float(event["stop"]),
                    target=float(event["target"]),
                    stop_ticks=float(event["stop_ticks"]),
                )
                trades.append(trade)
                active = trade
                filled = dict(event)
                filled["event"] = "FILLED"
                events.append(filled)

    if active is not None:
        _close_open_eod(active, capacity)
        active = None

    all_summary = _summary(trades)
    first = _summary([trade for trade in trades if trade.observation_day < midpoint])
    second = _summary([trade for trade in trades if trade.observation_day >= midpoint])

    pf_ok = bool(
        all_summary["profit_factor_infinite"]
        or (
            all_summary["profit_factor"] is not None
            and float(all_summary["profit_factor"]) >= PF_HURDLE
        )
    )
    checks = {
        "terminal_fills_gte_40": all_summary["terminal"] >= 40,
        "distinct_filled_days_gte_20": all_summary["distinct_filled_days"] >= 20,
        "net_positive": all_summary["net_pnl"] > 0,
        "profit_factor_gte_1_94": pf_ok,
        "first_half_net_positive": first["net_pnl"] > 0,
        "second_half_net_positive": second["net_pnl"] > 0,
        "leave_best_day_out_positive": (
            all_summary["leave_best_day_out_net"] is not None
            and all_summary["leave_best_day_out_net"] > 0
        ),
        "leave_best_3_days_out_positive": (
            all_summary["leave_best_3_days_out_net"] is not None
            and all_summary["leave_best_3_days_out_net"] > 0
        ),
        "top3_positive_day_share_lt_50pct": (
            all_summary["top3_positive_day_contribution_to_positive_net"] is not None
            and all_summary["top3_positive_day_contribution_to_positive_net"] < 0.50
        ),
        "max_drawdown_lte_450": all_summary["max_drawdown"] <= MAX_DRAWDOWN_HURDLE,
    }
    verdict = (
        "PROMISING_BUT_UNPROVEN"
        if all(checks.values())
        else "DOES_NOT_CLEAR_RETROSPECTIVE_GATE"
    )

    event_counts = Counter(event["event"] for event in events)
    report = {
        "study": "mnq_sustained_trend_continuation_v1",
        "mode": "RESEARCH_ONLY",
        "verdict": verdict,
        "data": {
            "structural_15m_root": str(root15),
            "trigger_5m_root": str(root5),
            "files_15m": len(files15),
            "files_5m_selected": len(files5),
            "first_date": dates[0],
            "last_date": dates[-1],
            "midpoint_date": midpoint,
            "sha256_15m": _fingerprint(files15, root15),
            "sha256_5m_selected": _fingerprint(files5, root5),
            "coverage_check": coverage_check,
        },
        "frozen_rules": {
            "instrument": "MNQ",
            "direction": "LONG",
            "arm_timeframe": "15m",
            "trigger_timeframe": "5m",
            "max_stop_ticks": MAX_STOP_TICKS,
            "target_r": 2.0,
            "round_turn_commission": ROUND_TURN_COMMISSION,
            "max_fills_per_observation_day": 3,
            "one_open_position": True,
            "pessimistic_stop_first": True,
        },
        "event_counts": dict(sorted(event_counts.items())),
        "all": all_summary,
        "first_half": first,
        "second_half": second,
        "session_breakdown": _group_summary(trades, lambda trade: trade.session),
        "monthly_breakdown": _group_summary(
            trades, lambda trade: trade.trigger_ts[:7]
        ),
        "gate_checks": checks,
        "large_move_coverage": _coverage(
            _move_windows(bars15_by_day), events, trades
        ),
        "notes": [
            "The canonical development corpus is not untouched OOS validation.",
            "2026-09-21 motivated the research and is not validation data.",
            "Large-move coverage is descriptive only and has no pass/fail authority.",
        ],
    }
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars-15m", type=Path, default=DEFAULT_15M)
    parser.add_argument("--bars-5m", type=Path, default=DEFAULT_5M)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    report = run(args.bars_15m, args.bars_5m)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
