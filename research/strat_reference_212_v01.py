"""TheStrat 60m 2-1-2 reversal reference-translation study v0.1.

Research only. No broker, execution, risk, scanner, scheduler, or alert imports.

Preregistration: docs/prereg-strat-reference-212-v01.md

This module intentionally keeps the primary result structural.  It asks whether
an already-completed directional 2 -> inside 1 produces the documented reversal
break and reaches the parent 2's far extreme.  It does NOT replace that
structural magnitude with a fixed-R target.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.causal_bars import Bar, HOUR_1, MINUTE_5, build_session_timeframe
from research.futures_non_strat_coverage import roll_excluded_sessions
from strategy.strat_classifier import (
    INSIDE_BAR,
    TWO_DOWN,
    TWO_UP,
    StratBar,
    classify_bar,
    classify_sequence,
)

STUDY_ID = "STRAT_REFERENCE_212_REVERSAL"
STUDY_VERSION = "strat-ref-212-v0.1"
REPLAY_ROOT = ROOT / "data" / "replay_polygon_5m"
ET = ZoneInfo("America/New_York")
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
RTH_BARS = 78
WATCH_BARS_5M = 12

FTFC_UP = "UP"
FTFC_DOWN = "DOWN"
FTFC_CONFLICT = "CONFLICT"
FTFC_UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class Event:
    event_id: str
    instrument: str
    session_date: str
    session: str
    source_timeframe: str
    population_scope: str
    parent_start: str
    inside_start: str
    parent_type: str
    inside_type: str
    source_c_start: str | None
    source_c_type: str | None
    afs_classifier_sequence: str | None
    afs_classifier_direction: str | None
    direction: str
    boundary_high: float
    boundary_low: float
    trigger_time: str | None
    trigger_price: float | None
    ambiguous: bool
    magnitude: float
    magnitude_reached: bool
    opposite_boundary_first: bool
    structural_failure_after_trigger: bool
    resolution_ambiguous: bool
    time_to_magnitude_minutes: int | None
    mae_points: float | None
    mfe_points: float | None
    trigger_bar_excursion_excluded: bool
    watch_window_unresolved: bool
    data_complete: bool
    watch_window_complete: bool
    ftfc_state: str
    afs_ema_trend_state: str
    afs_comparison_status: str
    half: str


def _classify(current: Bar, previous: Bar) -> str:
    return classify_bar(
        StratBar(high=current.high, low=current.low),
        StratBar(high=previous.high, low=previous.low),
    )


def _session_files(instrument: str) -> dict[date, Path]:
    result: dict[date, Path] = {}
    for path in sorted((REPLAY_ROOT / instrument).glob(f"{instrument}_*.jsonl")):
        result[date.fromisoformat(path.stem.split("_", 1)[1])] = path
    return result


def _load(path: Path) -> list[Bar]:
    bars: list[Bar] = []
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("session") != "new_york":
                continue
            ts = datetime.fromisoformat(row["timestamp"]).astimezone(timezone.utc)
            local = ts.astimezone(ET).time()
            if not (RTH_OPEN <= local < RTH_CLOSE):
                continue
            bars.append(
                Bar(
                    start=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                    vwap=float(row["vwap"]) if row.get("vwap") is not None else None,
                )
            )
    return sorted(bars, key=lambda b: b.start_utc)


def _half(day: date, midpoint: date) -> str:
    return "H1" if day < midpoint else "H2"


def _is_exact_5m_window(
    bars: Sequence[Bar],
    *,
    start: datetime,
    count: int,
) -> bool:
    """True only for unique, contiguous 5m starts from the requested boundary."""
    start_utc = start.astimezone(timezone.utc)
    expected = [start_utc + MINUTE_5.delta * i for i in range(count)]
    starts = [bar.start_utc for bar in sorted(bars, key=lambda b: b.start_utc)]
    return len(starts) == count and len(set(starts)) == count and starts == expected


def classify_ftfc(
    *,
    last_price: float,
    monthly_open: float | None,
    weekly_open: float | None,
    daily_open: float | None,
    current_60m_open: float | None,
) -> str:
    """Pure FTFC state; missing required opens fail closed to UNAVAILABLE."""
    opens = (monthly_open, weekly_open, daily_open, current_60m_open)
    if any(value is None for value in opens):
        return FTFC_UNAVAILABLE
    required = tuple(float(value) for value in opens if value is not None)
    price = float(last_price)
    if not math.isfinite(price) or any(not math.isfinite(value) for value in required):
        return FTFC_UNAVAILABLE
    if all(price > value for value in required):
        return FTFC_UP
    if all(price < value for value in required):
        return FTFC_DOWN
    return FTFC_CONFLICT


def observe_candidate(
    *,
    instrument: str,
    day: date,
    parent: Bar,
    inside: Bar,
    parent_type: str,
    watch: Sequence[Bar],
    midpoint: date,
) -> Event:
    if parent_type not in {TWO_UP, TWO_DOWN}:
        raise ValueError("parent_type must be directional")
    if _classify(inside, parent) != INSIDE_BAR:
        raise ValueError("inside must classify as 1 relative to parent")

    direction = "SHORT" if parent_type == TWO_UP else "LONG"
    magnitude = float(parent.low if direction == "SHORT" else parent.high)
    event_id = (
        f"{STUDY_VERSION}:{instrument}:{day.isoformat()}:"
        f"{inside.start_utc.isoformat()}:{direction}"
    )

    watch_start = inside.start_utc + HOUR_1.delta
    watch_end = watch_start + HOUR_1.delta
    watched = sorted(
        (bar for bar in watch if watch_start <= bar.start_utc < watch_end),
        key=lambda b: b.start_utc,
    )

    watch_complete = _is_exact_5m_window(
        watched, start=watch_start, count=WATCH_BARS_5M
    )
    source_c: Bar | None = None
    source_c_type: str | None = None
    afs_classifier_sequence: str | None = None
    afs_classifier_direction: str | None = None
    if watch_complete:
        built = build_session_timeframe(watched, MINUTE_5, HOUR_1, watch_start)
        if len(built) == 1:
            source_c = built[0]
            source_c_type = _classify(source_c, inside)
            afs_context = classify_sequence(parent_type, INSIDE_BAR, source_c_type)
            afs_classifier_sequence = afs_context.strat_sequence
            afs_classifier_direction = afs_context.strat_direction

    trigger_bar: Bar | None = None
    ambiguous = False
    opposite_first = False
    trigger_price: float | None = None

    for bar in watched:
        high_break = bar.high > inside.high
        low_break = bar.low < inside.low
        if high_break and low_break:
            ambiguous = True
            break
        wanted = high_break if direction == "LONG" else low_break
        opposite = low_break if direction == "LONG" else high_break
        if opposite:
            opposite_first = True
            break
        if wanted:
            trigger_bar = bar
            trigger_price = float(inside.high if direction == "LONG" else inside.low)
            break

    if trigger_bar is None:
        return Event(
            event_id=event_id,
            instrument=instrument,
            session_date=day.isoformat(),
            session="RTH",
            source_timeframe="60m_RTH_session_aligned",
            population_scope="A_B_C_same_RTH_session_only_full_RTH_non_roll",
            parent_start=parent.start_utc.isoformat(),
            inside_start=inside.start_utc.isoformat(),
            parent_type=parent_type,
            inside_type=INSIDE_BAR,
            source_c_start=source_c.start_utc.isoformat() if source_c is not None else None,
            source_c_type=source_c_type,
            afs_classifier_sequence=afs_classifier_sequence,
            afs_classifier_direction=afs_classifier_direction,
            direction=direction,
            boundary_high=float(inside.high),
            boundary_low=float(inside.low),
            trigger_time=None,
            trigger_price=None,
            ambiguous=ambiguous,
            magnitude=magnitude,
            magnitude_reached=False,
            opposite_boundary_first=opposite_first,
            structural_failure_after_trigger=False,
            resolution_ambiguous=False,
            time_to_magnitude_minutes=None,
            mae_points=None,
            mfe_points=None,
            trigger_bar_excursion_excluded=False,
            watch_window_unresolved=not (ambiguous or opposite_first),
            data_complete=watch_complete,
            watch_window_complete=watch_complete,
            ftfc_state=FTFC_UNAVAILABLE,
            afs_ema_trend_state="UNAVAILABLE",
            afs_comparison_status=(
                "CLASSIFIER_ONLY_EXECUTABLE_PATH_NOT_COMPARED"
                if source_c is not None
                else "UNAVAILABLE_INCOMPLETE_SOURCE_C"
            ),
            half=_half(day, midpoint),
        )

    after = [bar for bar in watched if bar.start_utc >= trigger_bar.start_utc]
    magnitude_bar: Bar | None = None
    structural_failure = False
    resolution_ambiguous = False
    resolution_slice: list[Bar] = []

    for bar in after:
        resolution_slice.append(bar)
        if direction == "LONG":
            magnitude_hit = bar.high >= magnitude
            failure_hit = bar.low < inside.low
        else:
            magnitude_hit = bar.low <= magnitude
            failure_hit = bar.high > inside.high

        if magnitude_hit and failure_hit:
            # Lowest available resolution cannot prove which happened first.
            # Fail closed: do not credit the structural magnitude.
            resolution_ambiguous = True
            break
        if failure_hit:
            structural_failure = True
            break
        if magnitude_hit:
            magnitude_bar = bar
            break

    # The trigger occurs somewhere inside trigger_bar. Full trigger-bar high/low
    # cannot be causally assigned to the post-trigger path, so excursion starts
    # on the first subsequent 5m bar. Structural terminal-state detection above
    # still uses the trigger bar conservatively.
    excursion_slice = resolution_slice[1:]
    if direction == "LONG":
        favorable = [max(0.0, bar.high - trigger_price) for bar in excursion_slice]
        adverse = [max(0.0, trigger_price - bar.low) for bar in excursion_slice]
    else:
        favorable = [max(0.0, trigger_price - bar.low) for bar in excursion_slice]
        adverse = [max(0.0, bar.high - trigger_price) for bar in excursion_slice]

    minutes = None
    if magnitude_bar is not None:
        minutes = int(
            (magnitude_bar.start_utc - trigger_bar.start_utc).total_seconds() // 60
        )

    return Event(
        event_id=event_id,
        instrument=instrument,
        session_date=day.isoformat(),
        session="RTH",
        source_timeframe="60m_RTH_session_aligned",
        population_scope="A_B_C_same_RTH_session_only_full_RTH_non_roll",
        parent_start=parent.start_utc.isoformat(),
        inside_start=inside.start_utc.isoformat(),
        parent_type=parent_type,
        inside_type=INSIDE_BAR,
        source_c_start=source_c.start_utc.isoformat() if source_c is not None else None,
        source_c_type=source_c_type,
        afs_classifier_sequence=afs_classifier_sequence,
        afs_classifier_direction=afs_classifier_direction,
        direction=direction,
        boundary_high=float(inside.high),
        boundary_low=float(inside.low),
        trigger_time=trigger_bar.start_utc.isoformat(),
        trigger_price=trigger_price,
        ambiguous=False,
        magnitude=magnitude,
        magnitude_reached=magnitude_bar is not None,
        opposite_boundary_first=False,
        structural_failure_after_trigger=structural_failure,
        resolution_ambiguous=resolution_ambiguous,
        time_to_magnitude_minutes=minutes,
        mae_points=max(adverse) if adverse else None,
        mfe_points=max(favorable) if favorable else None,
        trigger_bar_excursion_excluded=True,
        watch_window_unresolved=(
            magnitude_bar is None
            and not structural_failure
            and not resolution_ambiguous
        ),
        data_complete=watch_complete,
        watch_window_complete=watch_complete,
        ftfc_state=FTFC_UNAVAILABLE,
        afs_ema_trend_state="UNAVAILABLE",
        afs_comparison_status=(
            "CLASSIFIER_ONLY_EXECUTABLE_PATH_NOT_COMPARED"
            if source_c is not None
            else "UNAVAILABLE_INCOMPLETE_SOURCE_C"
        ),
        half=_half(day, midpoint),
    )


def run_instrument(instrument: str) -> dict[str, Any]:
    files = _session_files(instrument)
    if not files:
        raise SystemExit(f"no replay files under {REPLAY_ROOT / instrument}")

    loaded: dict[date, list[Bar]] = {}
    skipped = {
        "incomplete_session": 0,
        "misaligned_or_duplicate_session": 0,
        "roll_excluded": 0,
        "incomplete_watch_window": 0,
    }
    for day, path in files.items():
        bars = _load(path)
        if len(bars) != RTH_BARS:
            skipped["incomplete_session"] += 1
            continue
        session_open = datetime.combine(day, RTH_OPEN, ET)
        if not _is_exact_5m_window(bars, start=session_open, count=RTH_BARS):
            skipped["misaligned_or_duplicate_session"] += 1
            continue
        loaded[day] = bars

    complete_days = sorted(loaded)
    if not complete_days:
        raise SystemExit(f"no complete sessions for {instrument}")

    roll_excluded = roll_excluded_sessions(complete_days)
    days = [day for day in complete_days if day not in roll_excluded]
    skipped["roll_excluded"] = len(roll_excluded & set(complete_days))
    if not days:
        raise SystemExit(f"no eligible non-roll sessions for {instrument}")
    midpoint = days[len(days) // 2]

    events: list[Event] = []
    for day in days:
        bars5 = loaded[day]
        session_open = datetime.combine(day, RTH_OPEN, ET)
        hourly = build_session_timeframe(bars5, MINUTE_5, HOUR_1, session_open)
        # Only whole 60m source bars are emitted. The final 30m RTH fragment is
        # deliberately excluded by build_session_timeframe.
        for index in range(2, len(hourly)):
            parent = hourly[index - 1]
            prior = hourly[index - 2]
            inside = hourly[index]
            parent_type = _classify(parent, prior)
            if parent_type not in {TWO_UP, TWO_DOWN}:
                continue
            if _classify(inside, parent) != INSIDE_BAR:
                continue

            watch_start = inside.start_utc + HOUR_1.delta
            watch_end = watch_start + HOUR_1.delta
            watch = [
                bar
                for bar in bars5
                if watch_start <= bar.start_utc < watch_end
            ]
            if not _is_exact_5m_window(
                watch,
                start=watch_start,
                count=WATCH_BARS_5M,
            ):
                skipped["incomplete_watch_window"] += 1
                continue
            events.append(
                observe_candidate(
                    instrument=instrument,
                    day=day,
                    parent=parent,
                    inside=inside,
                    parent_type=parent_type,
                    watch=watch,
                    midpoint=midpoint,
                )
            )

    return summarize(instrument, days, midpoint, events, skipped)


def _median(values: Iterable[float]) -> float | None:
    values = list(values)
    return round(statistics.median(values), 4) if values else None


def _bucket(events: Sequence[Event]) -> dict[str, Any]:
    triggered = [e for e in events if e.trigger_time is not None]
    reached = [e for e in triggered if e.magnitude_reached]
    return {
        "candidates": len(events),
        "triggered": len(triggered),
        "ambiguous": sum(e.ambiguous for e in events),
        "opposite_boundary_first": sum(e.opposite_boundary_first for e in events),
        "structural_failure_after_trigger": sum(
            e.structural_failure_after_trigger for e in events
        ),
        "resolution_ambiguous": sum(e.resolution_ambiguous for e in events),
        "watch_window_unresolved": sum(e.watch_window_unresolved for e in events),
        "magnitude_reached": len(reached),
        "magnitude_hit_rate_triggered": round(len(reached) / len(triggered), 4)
        if triggered
        else None,
        "median_time_to_magnitude_minutes": _median(
            e.time_to_magnitude_minutes
            for e in reached
            if e.time_to_magnitude_minutes is not None
        ),
        "trigger_bar_excursion_excluded": sum(
            e.trigger_bar_excursion_excluded for e in triggered
        ),
        "median_mae_points": _median(
            e.mae_points for e in triggered if e.mae_points is not None
        ),
        "median_mfe_points": _median(
            e.mfe_points for e in triggered if e.mfe_points is not None
        ),
    }


def summarize(
    instrument: str,
    days: Sequence[date],
    midpoint: date,
    events: Sequence[Event],
    skipped: dict[str, int],
) -> dict[str, Any]:
    return {
        "study_id": STUDY_ID,
        "study_version": STUDY_VERSION,
        "research_only": True,
        "study_complete": False,
        "outstanding_before_final_interpretation": [
            "FTFC_DATA_CAPABILITY",
            "AFS_EMA_TREND_SIDE_BY_SIDE",
            "EXECUTION_OVERLAYS",
            "COST_MODEL_CONTRACT",
            "SOURCE_ALIGNMENT_EXTERNAL_CANONICALITY",
        ],
        "instrument": instrument,
        "source_timeframe": "60m_RTH_session_aligned",
        "population_scope": "A_B_C_same_RTH_session_only_full_RTH_non_roll",
        "session_eligibility": "78_exact_5m_RTH_bars_only_early_close_ineligible",
        "roll_policy": "existing_futures_research_third_friday_week_plus_next_session_excluded",
        "source_alignment_status": "EXPLICIT_AFS_TRANSLATION_NOT_PUBLIC_CANONICAL",
        "trigger_resolution": "5m",
        "trigger_watch_window": "immediately_following_60m_source_bar_only",
        "excursion_horizon": "first_5m_bar_after_trigger_to_first_structural_terminal_event",
        "trigger_bar_excursion_policy": "EXCLUDE_UNORDERED_TRIGGER_BAR_OHLC",
        "first_session": days[0].isoformat(),
        "last_session": days[-1].isoformat(),
        "midpoint_date": midpoint.isoformat(),
        "skipped": skipped,
        "overall": _bucket(events),
        "by_half": {
            half: _bucket([e for e in events if e.half == half])
            for half in ("H1", "H2")
        },
        "by_direction": {
            direction: _bucket([e for e in events if e.direction == direction])
            for direction in ("LONG", "SHORT")
        },
        "events": [asdict(e) for e in events],
    }


def to_markdown(report: dict[str, Any]) -> str:
    o = report["overall"]
    return (
        f"# {report['study_id']} {report['study_version']} — {report['instrument']}\n\n"
        f"RESEARCH ONLY. 60m RTH session-aligned source bars; same-session A/B/C only; "
        f"5m causal trigger resolution; next-source-bar watch only.\n\n"
        f"Sessions: {report['first_session']} → {report['last_session']} · midpoint {report['midpoint_date']}\n\n"
        f"Candidates {o['candidates']} · triggered {o['triggered']} · pre-trigger ambiguous {o['ambiguous']} · "
        f"opposite-first {o['opposite_boundary_first']} · post-trigger failure {o['structural_failure_after_trigger']} · "
        f"resolution ambiguous {o['resolution_ambiguous']} · unresolved {o['watch_window_unresolved']} · "
        f"magnitude reached {o['magnitude_reached']} · hit rate among triggers {o['magnitude_hit_rate_triggered']}\n\n"
        f"Median time to magnitude {o['median_time_to_magnitude_minutes']} min · "
        f"median MAE {o['median_mae_points']} pts · median MFE {o['median_mfe_points']} pts\n\n"
        "MAE/MFE exclude the unordered trigger-bar OHLC and stop at the first structural terminal event; "
        "later price action is excluded.\n\n"
        "Study is incomplete: FTFC data capability, AFS EMA side-by-side, execution overlays, "
        "a frozen cost model, and external source-alignment canonicality remain outstanding. "
        "No P&L, PF, fixed-R target, "
        "or promotion claim is produced by this structural pass.\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TheStrat 60m 2-1-2 reversal reference study")
    parser.add_argument("--instrument", required=True, choices=("MNQ", "MES"))
    parser.add_argument("--out", default=str(ROOT / "logs" / "strat_ref_212_v01"))
    args = parser.parse_args(argv)
    report = run_instrument(args.instrument)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"strat_ref_212_v01_{args.instrument}"
    (out / f"{stem}.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    md = to_markdown(report)
    (out / f"{stem}.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
