"""Lane-generic read-only monitor for MNQ Strat evidence JSONL files."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from execution.mnq_strat_evidence import LANES, evidence_path


def read_events(log_dir: str | Path, lane: str) -> list[dict[str, Any]]:
    path = evidence_path(log_dir, lane)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _profit_factor(values: Iterable[float]) -> float | str | None:
    values = list(values)
    gains = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    if losses:
        return round(gains / losses, 3)
    return "infinite" if gains else None


def _max_drawdown(values: Iterable[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return round(drawdown, 2)


def _result_bucket(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row.get("net_dollars") or 0.0) for row in rows]
    return {
        "resolved": len(rows),
        "wins": sum(row.get("result") == "WIN" for row in rows),
        "losses": sum(row.get("result") == "LOSS" for row in rows),
        "net_dollars": round(sum(values), 2),
        "net_ticks": round(sum(float(row.get("net_ticks") or 0.0) for row in rows), 2),
        "profit_factor": _profit_factor(values),
        "expectancy_dollars": round(statistics.mean(values), 2) if values else None,
    }


def _group(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field) or "unknown")].append(row)
    return {key: _result_bucket(sample) for key, sample in sorted(grouped.items())}


def _consistency(groups: dict[str, Any]) -> dict[str, Any]:
    values = [float(item.get("net_dollars") or 0.0) for item in groups.values()]
    return {
        "positive": sum(value > 0 for value in values),
        "negative": sum(value < 0 for value in values),
        "flat": sum(value == 0 for value in values),
        "total": len(values),
    }


def summarize_lane(log_dir: str | Path, lane: str) -> dict[str, Any]:
    if lane not in LANES:
        raise ValueError(f"unknown MNQ Strat lane: {lane}")
    rows = read_events(log_dir, lane)
    candidates = [row for row in rows if row.get("event") == "CANDIDATE"]
    outcomes = [row for row in rows if row.get("event") == "OUTCOME"]
    values = [float(row.get("net_dollars") or 0.0) for row in outcomes]
    tick_values = [float(row.get("net_ticks") or 0.0) for row in outcomes]
    winner_values = [value for value in values if value > 0]
    loss_values = [value for value in values if value < 0]
    winner_ticks = [value for value in tick_values if value > 0]
    loss_ticks = [value for value in tick_values if value < 0]
    # Collapse full timestamps to YYYY-MM without mutating evidence rows.
    month_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in outcomes:
        month_rows[str(row.get("entry_ts") or "unknown")[:7]].append(row)
    monthly = {key: _result_bucket(sample) for key, sample in sorted(month_rows.items())}
    sessions = _group(outcomes, "session")
    sorted_winners = sorted(
        (row for row in outcomes if float(row.get("net_dollars") or 0.0) > 0),
        key=lambda row: float(row.get("net_dollars") or 0.0),
        reverse=True,
    )

    def excluding_top_winners(count: int) -> dict[str, Any]:
        excluded = sorted_winners[:count]
        return {
            "net_dollars": round(
                sum(values)
                - sum(float(row.get("net_dollars") or 0.0) for row in excluded),
                2,
            ),
            "net_ticks": round(
                sum(tick_values)
                - sum(float(row.get("net_ticks") or 0.0) for row in excluded),
                2,
            ),
            "resolved": len(values) - len(excluded),
        }

    return {
        "lane": lane,
        "evidence_path": str(evidence_path(log_dir, lane)),
        "candidate_count": len(candidates),
        "accepted_count": sum(row.get("accepted") is True for row in candidates),
        "rejected_count": sum(row.get("accepted") is False for row in candidates),
        "fill_count": sum(row.get("fill_status") == "FILLED" for row in candidates),
        "no_fill_count": sum(row.get("fill_status") == "NO_FILL" for row in candidates),
        "wins": sum(row.get("result") == "WIN" for row in outcomes),
        "losses": sum(row.get("result") == "LOSS" for row in outcomes),
        "breakevens": sum(row.get("result") == "BREAKEVEN" for row in outcomes),
        "net_ticks": round(sum(float(row.get("net_ticks") or 0.0) for row in outcomes), 2),
        "net_dollars": round(sum(values), 2),
        "profit_factor": _profit_factor(values),
        "expectancy_dollars": round(statistics.mean(values), 2) if values else None,
        "expectancy_ticks": round(statistics.mean(tick_values), 2) if tick_values else None,
        "average_winner_dollars": (
            round(statistics.mean(winner_values), 2) if winner_values else None
        ),
        "median_winner_dollars": (
            round(statistics.median(winner_values), 2) if winner_values else None
        ),
        "average_loss_dollars": (
            round(statistics.mean(loss_values), 2) if loss_values else None
        ),
        "median_loss_dollars": (
            round(statistics.median(loss_values), 2) if loss_values else None
        ),
        "average_winner_ticks": (
            round(statistics.mean(winner_ticks), 2) if winner_ticks else None
        ),
        "median_winner_ticks": (
            round(statistics.median(winner_ticks), 2) if winner_ticks else None
        ),
        "average_loss_ticks": round(statistics.mean(loss_ticks), 2) if loss_ticks else None,
        "median_loss_ticks": round(statistics.median(loss_ticks), 2) if loss_ticks else None,
        "maximum_drawdown_dollars": _max_drawdown(values),
        "maximum_drawdown_ticks": _max_drawdown(tick_values),
        "long_vs_short": _group(outcomes, "direction"),
        "ny_vs_london": {
            key: value
            for key, value in sessions.items()
            if key in {"new_york", "london"}
        },
        "by_session": sessions,
        "by_month": monthly,
        "session_consistency": _consistency(sessions),
        "monthly_consistency": _consistency(monthly),
        "excluding_largest_winner": excluding_top_winners(1),
        "excluding_top_five_winners": excluding_top_winners(5),
    }


def summarize_all(log_dir: str | Path) -> dict[str, Any]:
    return {lane: summarize_lane(log_dir, lane) for lane in LANES}
