"""Read-only reducer: 30m coverage bar-events → deduplicated setup episodes.

The coverage observer (:mod:`alert_ranker.coverage_observer`) records one row
per completed 30m bar that carried a directional Strat structure.  In a
trending move that is one row per bar: each new directional bar forms a new
2-2-2 (or 2-2) whose trigger is the previous bar's extreme, so the raw count
overstates *opportunities* by the length of the run.  This module collapses
those rows into episodes with a rule derived from the structure itself, not
from clock proximity:

    Two events belong to the same episode when they share symbol, session,
    family and direction AND their bars are contiguous on the 30m grid.

A gap of one non-event bar, a change of family, or a change of direction
starts a new episode.  Contiguity is the structural identity: the later
event's "previous bar" *is* the earlier event's breakout bar, so the setup
is the continuation of the same directional run, not a new mechanical
trigger.  The stricter "direction run" (contiguous bars, same direction,
any family -- a 3-2-2C rolling into 2-2-2C is one move) is reported as a
second denominator; it is not used for the family tables.

Episode fields are taken from the FIRST event -- the first mechanical
opportunity, which is how V1 defines an episode (#575 ruling) -- and the raw
event count is preserved on every episode.  Nothing here selects, promotes,
alerts, prices, or changes any rule.

R:R quality flags are reported, never clipped:

* ``structural_risk_tiny`` -- |trigger − invalidation| below
  :data:`TINY_RISK_FRACTION` of the trigger price (an inside bar so narrow that
  any level looks like many R);
* ``first_sight_denominator_small`` -- |first-sight price − invalidation|
  below :data:`SMALL_DENOMINATOR_FRACTION` of the structural risk (price
  sitting on the stop makes remaining R explode: MAR 09-10 18:00 → 500R);
* ``remaining_rr_implausible`` -- a remaining R that is non-finite or larger
  than :data:`IMPLAUSIBLE_REMAINING_RR` in magnitude.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from .coverage_observer import MIN_REMAINING_RR, OBSERVED_TIMEFRAME

__all__ = [
    "REDUCER_VERSION",
    "Episode",
    "reduce_events",
    "direction_runs",
    "summarize",
    "answer_questions",
]

REDUCER_VERSION = "ep-v0.1"
BAR_DELTA = timedelta(minutes=30)
TINY_RISK_FRACTION = 0.001  # 0.1% of price
SMALL_DENOMINATOR_FRACTION = 0.10  # first-sight risk < 10% of structural risk
IMPLAUSIBLE_REMAINING_RR = 20.0


@dataclass
class Episode:
    reducer_version: str
    symbol: str
    session_date: str
    family: str
    direction: str
    v1_supported: bool
    requested_family: bool
    n_events: int
    first_bar_start: str
    first_bar_close: str
    last_bar_start: str
    # first-opportunity gate fields (from the first event)
    entry_trigger: float
    invalidation: float
    risk: float
    nearest_target_1: float | None
    nearest_rr_1: float | None
    nearest_reason: str
    nearest_geometry_ok: bool
    floor_target_1: float | None
    floor_rr_1: float | None
    floor_reason: str
    floor_geometry_ok: bool
    floor_rescued: bool
    spy_trend: str | None
    qqq_trend: str | None
    hourly_candle_type: str | None
    daily_candle_type: str | None
    spy_aligned: bool
    qqq_aligned: bool
    hourly_aligned: bool
    daily_aligned: bool
    alignment_ok: bool
    alignment_failures: str
    first_sight_at: str
    first_sight_after_close: bool
    first_sight_price: float | None
    nearest_remaining_rr: float | None
    floor_remaining_rr: float | None
    late_nearest: bool | None
    late_floor: bool | None
    would_qualify_v1_rule: bool | None
    would_qualify_floor_rule: bool | None
    # rejection reasons, in gate order, for each rule
    rejections_v1_rule: list[str] = field(default_factory=list)
    rejections_floor_rule: list[str] = field(default_factory=list)
    # evidence-quality flags (never clipped)
    rr_quality_flags: list[str] = field(default_factory=list)
    # run identity (contiguous same-direction bars, any family)
    run_id: str = ""

    @property
    def rr_quality_flagged(self) -> bool:
        return bool(self.rr_quality_flags)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["rr_quality_flagged"] = self.rr_quality_flagged
        return row


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _rejections(event: dict[str, Any], geometry_key: str, geometry_reason_key: str, late_key: str) -> list[str]:
    out: list[str] = []
    if not event.get("v1_supported"):
        out.append("unsupported_setup_family")
    if not event.get(geometry_key):
        out.append(f"target_geometry:{event.get(geometry_reason_key) or 'below_1R'}")
    if not event.get("alignment_ok"):
        out.append(f"market_alignment:{event.get('alignment_failures') or 'unknown'}")
    if event.get("first_sight_after_close"):
        out.append("first_sight_after_close")
    late = event.get(late_key)
    if late is None and not event.get("first_sight_after_close"):
        out.append("first_sight_unpriced")
    elif late:
        out.append("late_at_first_sight")
    return out


def _quality_flags(event: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    trigger = event.get("entry_trigger")
    invalidation = event.get("invalidation")
    risk = event.get("risk")
    price = event.get("first_sight_price")
    if _finite(trigger) and _finite(risk) and trigger and risk < TINY_RISK_FRACTION * abs(trigger):
        flags.append("structural_risk_tiny")
    if _finite(price) and _finite(invalidation) and _finite(risk) and risk:
        denominator = abs(price - invalidation)
        if denominator < SMALL_DENOMINATOR_FRACTION * risk:
            flags.append("first_sight_denominator_small")
    for key in ("nearest_remaining_rr", "floor_remaining_rr"):
        value = event.get(key)
        if value is None:
            continue
        if not _finite(value) or abs(float(value)) > IMPLAUSIBLE_REMAINING_RR:
            flags.append(f"remaining_rr_implausible:{key}")
    return flags


def _episode_from(first: dict[str, Any], events: Sequence[dict[str, Any]]) -> Episode:
    failures = set((first.get("alignment_failures") or "").split(",")) - {""}
    return Episode(
        reducer_version=REDUCER_VERSION,
        symbol=first["symbol"],
        session_date=first["session_date"],
        family=first["family"],
        direction=first["direction"],
        v1_supported=bool(first.get("v1_supported")),
        requested_family=bool(first.get("requested_family")),
        n_events=len(events),
        first_bar_start=first["bar_start"],
        first_bar_close=first["bar_close"],
        last_bar_start=events[-1]["bar_start"],
        entry_trigger=first["entry_trigger"],
        invalidation=first["invalidation"],
        risk=first["risk"],
        nearest_target_1=first.get("nearest_target_1"),
        nearest_rr_1=first.get("nearest_rr_1"),
        nearest_reason=first.get("nearest_reason") or "",
        nearest_geometry_ok=bool(first.get("nearest_geometry_ok")),
        floor_target_1=first.get("floor_target_1"),
        floor_rr_1=first.get("floor_rr_1"),
        floor_reason=first.get("floor_reason") or "",
        floor_geometry_ok=bool(first.get("floor_geometry_ok")),
        floor_rescued=bool(first.get("floor_rescued")),
        spy_trend=first.get("spy_trend"),
        qqq_trend=first.get("qqq_trend"),
        hourly_candle_type=first.get("hourly_candle_type"),
        daily_candle_type=first.get("daily_candle_type"),
        spy_aligned="spy" not in failures,
        qqq_aligned="qqq" not in failures,
        hourly_aligned="hourly" not in failures,
        daily_aligned="daily" not in failures,
        alignment_ok=bool(first.get("alignment_ok")),
        alignment_failures=first.get("alignment_failures") or "",
        first_sight_at=first["first_sight_at"],
        first_sight_after_close=bool(first.get("first_sight_after_close")),
        first_sight_price=first.get("first_sight_price"),
        nearest_remaining_rr=first.get("nearest_remaining_rr"),
        floor_remaining_rr=first.get("floor_remaining_rr"),
        late_nearest=first.get("late_nearest"),
        late_floor=first.get("late_floor"),
        would_qualify_v1_rule=first.get("would_qualify_v1_rule"),
        would_qualify_floor_rule=first.get("would_qualify_floor_rule"),
        rejections_v1_rule=_rejections(first, "nearest_geometry_ok", "nearest_reason", "late_nearest"),
        rejections_floor_rule=_rejections(first, "floor_geometry_ok", "floor_reason", "late_floor"),
        rr_quality_flags=_quality_flags(first),
    )


def reduce_events(events: Iterable[dict[str, Any]]) -> list[Episode]:
    """Collapse contiguous same-(symbol, session, family, direction) bars."""
    rows = [dict(e) for e in events if e.get("timeframe") == OBSERVED_TIMEFRAME]
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["symbol"], row["session_date"], row["family"], row["direction"])].append(row)

    episodes: list[Episode] = []
    for key in sorted(groups):
        ordered = sorted(groups[key], key=lambda r: _ts(r["bar_start"]))
        current: list[dict[str, Any]] = []
        for row in ordered:
            if current and _ts(row["bar_start"]) - _ts(current[-1]["bar_start"]) == BAR_DELTA:
                current.append(row)
                continue
            if current:
                episodes.append(_episode_from(current[0], current))
            current = [row]
        if current:
            episodes.append(_episode_from(current[0], current))

    _assign_runs(episodes, rows)
    episodes.sort(key=lambda e: (e.session_date, e.symbol, e.first_bar_start, e.family))
    return episodes


def _assign_runs(episodes: list[Episode], rows: Sequence[dict[str, Any]]) -> None:
    """Label each episode with its direction run (contiguous bars, same direction, any family)."""
    by_symbol_session: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_symbol_session[(row["symbol"], row["session_date"])].append(row)
    run_of_bar: dict[tuple[str, str, str], str] = {}
    for key, group in by_symbol_session.items():
        ordered = sorted(group, key=lambda r: (_ts(r["bar_start"]), r["family"]))
        last_bar: datetime | None = None
        last_direction: str | None = None
        run_index = 0
        for row in ordered:
            start = _ts(row["bar_start"])
            contiguous = last_bar is not None and (start - last_bar) in (timedelta(0), BAR_DELTA)
            if not (contiguous and row["direction"] == last_direction):
                run_index += 1
            run_of_bar[(key[0], key[1], row["bar_start"])] = f"{key[0]}|{key[1]}|{row['direction']}|run{run_index}"
            last_bar, last_direction = start, row["direction"]
    for episode in episodes:
        episode.run_id = run_of_bar.get((episode.symbol, episode.session_date, episode.first_bar_start), "")


def direction_runs(episodes: Sequence[Episode]) -> int:
    return len({e.run_id for e in episodes if e.run_id})


# --------------------------------------------------------------------------- #
# summaries
# --------------------------------------------------------------------------- #

COLUMNS = (
    "raw_events",
    "episodes",
    "direction_runs",
    "episodes_with_valid_targets",
    "episodes_with_floor_targets",
    "episodes_market_aligned",
    "episodes_ge1R_at_first_sight_nearest",
    "episodes_ge1R_at_first_sight_floor",
    "episodes_would_otherwise_qualify_v1_rule",
    "episodes_would_otherwise_qualify_floor_rule",
    "after_close_episodes",
    "rr_quality_flagged",
)


def _count(episodes: Sequence[Episode]) -> dict[str, int]:
    return {
        "raw_events": sum(e.n_events for e in episodes),
        "episodes": len(episodes),
        "direction_runs": direction_runs(episodes),
        "episodes_with_valid_targets": sum(1 for e in episodes if e.nearest_geometry_ok),
        "episodes_with_floor_targets": sum(1 for e in episodes if e.floor_geometry_ok),
        "episodes_market_aligned": sum(1 for e in episodes if e.alignment_ok),
        "episodes_ge1R_at_first_sight_nearest": sum(1 for e in episodes if e.late_nearest is False and not e.first_sight_after_close),
        "episodes_ge1R_at_first_sight_floor": sum(1 for e in episodes if e.late_floor is False and not e.first_sight_after_close),
        # "would otherwise qualify" = every gate except family support
        "episodes_would_otherwise_qualify_v1_rule": sum(1 for e in episodes if e.nearest_geometry_ok and e.alignment_ok and not e.first_sight_after_close and e.late_nearest is False),
        "episodes_would_otherwise_qualify_floor_rule": sum(1 for e in episodes if e.floor_geometry_ok and e.alignment_ok and not e.first_sight_after_close and e.late_floor is False),
        "after_close_episodes": sum(1 for e in episodes if e.first_sight_after_close),
        "rr_quality_flagged": sum(1 for e in episodes if e.rr_quality_flagged),
    }


def summarize(episodes: Sequence[Episode]) -> dict[str, Any]:
    by_family: dict[str, dict[str, Any]] = {}
    for family in sorted({e.family for e in episodes}):
        subset = [e for e in episodes if e.family == family]
        by_family[family] = {
            "v1_supported": subset[0].v1_supported,
            "requested": subset[0].requested_family,
            **_count(subset),
            "refire_ratio": round(sum(e.n_events for e in subset) / len(subset), 2),
            "episodes_len_ge2": sum(1 for e in subset if e.n_events >= 2),
        }
    by_symbol = {s: _count([e for e in episodes if e.symbol == s]) for s in sorted({e.symbol for e in episodes})}
    by_session = {d: _count([e for e in episodes if e.session_date == d]) for d in sorted({e.session_date for e in episodes})}
    flags: dict[str, int] = defaultdict(int)
    for e in episodes:
        for flag in e.rr_quality_flags:
            flags[flag] += 1
    return {
        "reducer_version": REDUCER_VERSION,
        "total": _count(list(episodes)),
        "by_family": by_family,
        "by_symbol": by_symbol,
        "by_session": by_session,
        "rr_quality_flag_counts": dict(flags),
    }


def answer_questions(summary: dict[str, Any], raw_family_counts: dict[str, int]) -> dict[str, Any]:
    """Evidence-only answers to the operator's A-D; no cutoff invented."""
    fam = summary["by_family"]
    ranked_raw = sorted(raw_family_counts.items(), key=lambda kv: -kv[1])
    ranked_ep = sorted(fam.items(), key=lambda kv: -kv[1]["episodes"])
    refire = {f: fam[f]["refire_ratio"] for f in fam}
    return {
        "A_common_after_dedupe": [(f, fam[f]["episodes"]) for f, _ in ranked_ep],
        "B_inflated_by_refire": sorted(((f, r, raw_family_counts.get(f, 0), fam[f]["episodes"]) for f, r in refire.items()), key=lambda t: -t[1]),
        "C_clean_episodes": {
            f: {
                "episodes": fam[f]["episodes"],
                "clean_episodes": fam[f]["episodes"] - fam[f]["rr_quality_flagged"],
                "with_floor_targets": fam[f]["episodes_with_floor_targets"],
                "aligned": fam[f]["episodes_market_aligned"],
                "would_qualify_floor_rule": fam[f]["episodes_would_otherwise_qualify_floor_rule"],
            }
            for f in fam
        },
        "D_rank_order_raw": [f for f, _ in ranked_raw],
        "D_rank_order_episodes": [f for f, _ in ranked_ep],
    }
