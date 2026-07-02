"""Causal 15-minute authorization -> 5-minute retest execution research.

This module intentionally contains no strategy discovery.  It consumes exact
brackets already authorized by the 15-minute engine, then applies the same
close-confirmed retest predicate used by the live five-minute lane.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from context.bar_history import _parse_dt
from context.five_min_feed import retest_triggered


@dataclass(frozen=True)
class RetestArm:
    instrument: str
    armed_at: datetime
    direction: str
    entry: float
    stop: float
    target: float
    strategy: str
    arm_id: str = ""


@dataclass(frozen=True)
class FineBar:
    timestamp: datetime
    instrument: str
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class RetestResult:
    arm: RetestArm
    status: str
    triggered_at: Optional[datetime] = None
    trigger_close: Optional[float] = None
    minutes_to_fill: Optional[float] = None
    bars_seen: int = 0


def _aware(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = _parse_dt(str(value))
    if parsed is None:
        raise ValueError(f"invalid timestamp: {value!r}")
    return parsed


def simulate_arm(
    arm: RetestArm,
    bars: Iterable[FineBar],
    *,
    ttl_minutes: int = 20,
    max_distance_ticks: int = 1,
    tick_size: float = 0.25,
) -> RetestResult:
    """Evaluate completed fine bars strictly after authorization, oldest first."""
    armed_at = _aware(arm.armed_at)
    expires_at = armed_at + timedelta(minutes=max(0, int(ttl_minutes)))
    seen = 0
    for bar in sorted(bars, key=lambda item: _aware(item.timestamp)):
        ts = _aware(bar.timestamp)
        if bar.instrument != arm.instrument or ts <= armed_at:
            continue
        if ts > expires_at:
            break
        seen += 1
        if retest_triggered(
            direction=arm.direction,
            entry=arm.entry,
            bar_high=bar.high,
            bar_low=bar.low,
            bar_close=bar.close,
            tick_size=tick_size,
            max_distance_ticks=max_distance_ticks,
        ):
            return RetestResult(
                arm=arm,
                status="TRIGGERED",
                triggered_at=ts,
                trigger_close=bar.close,
                minutes_to_fill=(ts - armed_at).total_seconds() / 60.0,
                bars_seen=seen,
            )
    return RetestResult(arm=arm, status="EXPIRED", bars_seen=seen)


def sensitivity_grid(
    arms: Iterable[RetestArm],
    bars: Iterable[FineBar],
    *,
    ttl_values: tuple[int, ...] = (15, 20, 30),
    distance_values: tuple[int, ...] = (1, 2, 4),
    tick_size: float = 0.25,
) -> list[dict]:
    """Return fill diagnostics for a small predefined, non-optimizing grid."""
    arm_list = list(arms)
    bar_list = list(bars)
    rows: list[dict] = []
    for ttl in ttl_values:
        for distance in distance_values:
            results = [
                simulate_arm(
                    arm,
                    bar_list,
                    ttl_minutes=ttl,
                    max_distance_ticks=distance,
                    tick_size=tick_size,
                )
                for arm in arm_list
            ]
            triggered = [r for r in results if r.status == "TRIGGERED"]
            rows.append(
                {
                    "ttl_minutes": ttl,
                    "max_distance_ticks": distance,
                    "arms": len(results),
                    "triggered": len(triggered),
                    "fill_rate": len(triggered) / len(results) if results else 0.0,
                    "avg_minutes_to_fill": (
                        sum(r.minutes_to_fill or 0.0 for r in triggered) / len(triggered)
                        if triggered else None
                    ),
                }
            )
    return rows
