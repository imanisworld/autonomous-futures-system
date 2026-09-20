"""Read-only 4H sequence attribution for the 4HR pre-armed evidence lane."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable

from context.bar_history import _parse_dt
from strategy.four_hr_retrigger import aggregate_et_bars
from strategy.strat_classifier import StratBar, classify_bar, classify_sequence

DEFINITION = "completed_et_wall_clock_4h_sequence_v1"
TREATMENT = "4hr_prearmed_4h22_continuation_v1"
CONTINUATION = "strat_22_continuation"


def _completed_four_hour_bars(
    bars_5m: Iterable[dict[str, Any]], at_time: datetime
) -> list[dict[str, Any]]:
    available = []
    for raw in bars_5m:
        ts = _parse_dt(str(raw.get("ts") or raw.get("timestamp") or ""))
        if ts is not None and ts + timedelta(minutes=5) <= at_time:
            available.append(raw)
    four_hour = aggregate_et_bars(available, 240)
    return [
        bar
        for bar in four_hour
        if bar["ts"] + timedelta(hours=4) <= at_time
    ]
def _sequence_from_completed_four_hour(
    completed: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(completed) < 4:
        return {
            "definition": DEFINITION,
            "treatment": TREATMENT,
            "status": "INSUFFICIENT_CONTEXT",
            "sequence": None,
            "treatment_eligible": None,
            "bar_types": [],
            "bar_starts": [],
            "bar_counts": [],
        }

    bars = completed[-4:]
    bar_types = [
        classify_bar(
            StratBar(high=float(bars[idx]["high"]), low=float(bars[idx]["low"])),
            StratBar(
                high=float(bars[idx - 1]["high"]),
                low=float(bars[idx - 1]["low"]),
            ),
        )
        for idx in range(1, 4)
    ]
    strat = classify_sequence(bar_types[0], bar_types[1], bar_types[2])
    return {
        "definition": DEFINITION,
        "treatment": TREATMENT,
        "status": "OK",
        "sequence": strat.strat_sequence,
        "treatment_eligible": strat.strat_sequence == CONTINUATION,
        "bar_types": bar_types,
        "bar_starts": [bar["ts"].isoformat() for bar in bars],
        "bar_counts": [int(bar.get("count") or 0) for bar in bars],
    }


def completed_four_hour_sequence_context(
    bars_5m: Iterable[dict[str, Any]], at_time: datetime
) -> dict[str, Any]:
    """Return evidence metadata; this function has no execution authority."""
    return _sequence_from_completed_four_hour(
        _completed_four_hour_bars(bars_5m, at_time)
    )
