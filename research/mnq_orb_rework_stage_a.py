"""MNQ ORB rework Stage A (orb-a-v0.1) — research only.

Prereg:
docs/prereg-mnq-orb-rework-stage-a-2026-09-23.md

This runner measures raw directional behavior only. It does not import or call
strategy evaluation, risk, broker, execution, webhook, scheduler, or deployment paths.
It reuses the repository's existing Bar type and read-only futures replay loaders.

Example:
    python research/mnq_orb_rework_stage_a.py --out logs/mnq_orb_stage_a
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.causal_bars import Bar  # noqa: E402
from research.futures_non_strat_coverage import (  # noqa: E402
    HALF_SPLIT,
    RTH_BARS,
    load_rth_session,
    roll_excluded_sessions,
    session_files,
)

STUDY_ID = "MNQ_ORB_REWORK_STAGE_A"
STUDY_VERSION = "orb-a-v0.1"
INSTRUMENT = "MNQ"
OR_DURATIONS = (15, 30, 60)
CONFIRM_MINUTES = (5, 15)
FAMILIES = ("BREAKOUT", "WICK_REJECTION", "BREAK_RETEST", "ACCEPTANCE", "FAILED_BREAKOUT_INVERSE")
DIRECTIONS = ("LONG", "SHORT")
HORIZONS = (15, 30, 60)
VOLUME_LOOKBACK = 20
INVERSE_WINDOW = timedelta(minutes=30)
MIN_TOTAL = 80
MIN_HALF = 30


@dataclass(frozen=True)
class Event:
    session_date: str
    or_minutes: int
    confirm_minutes: int
    family: str
    direction: str
    bar_start: str
    bar_close: str
    trigger_price: float
    orb_high: float
    orb_low: float
    volume_ratio: float | None
    volume_bin: str


@dataclass(frozen=True)
class Outcome:
    event: Event
    horizon: str
    bars_observed: int
    signed_close_bps: float | None
    mfe_bps: float | None
    mae_bps: float | None


def _close_time(bar: Bar, minutes: int) -> datetime:
    return bar.start_utc + timedelta(minutes=minutes)


def _aggregate_15m(bars: Sequence[Bar]) -> list[Bar]:
    if len(bars) % 3:
        raise ValueError("RTH 5m session does not aggregate cleanly to 15m")
    out: list[Bar] = []
    for i in range(0, len(bars), 3):
        chunk = bars[i : i + 3]
        out.append(
            Bar(
                start=chunk[0].start_utc,
                open=float(chunk[0].open),
                high=max(float(b.high) for b in chunk),
                low=min(float(b.low) for b in chunk),
                close=float(chunk[-1].close),
                volume=sum(float(b.volume) for b in chunk),
                vwap=None,
            )
        )
    return out


def _series_for_tf(bars: Sequence[Bar], minutes: int) -> list[Bar]:
    if minutes == 5:
        return list(bars)
    if minutes == 15:
        return _aggregate_15m(bars)
    raise ValueError(f"unsupported confirmation timeframe {minutes}")


def _volume_ratio(prior: Sequence[Bar], current: Bar) -> float | None:
    values = [float(b.volume) for b in prior[-VOLUME_LOOKBACK:]]
    if len(values) < VOLUME_LOOKBACK:
        return None
    baseline = statistics.fmean(values)
    if baseline <= 0:
        return None
    return float(current.volume) / baseline


def _volume_bin(ratio: float | None) -> str:
    if ratio is None:
        return "UNKNOWN"
    if ratio < 0.8:
        return "LOW"
    if ratio < 1.2:
        return "NORMAL"
    return "HIGH"


def _event(
    *,
    day: date,
    or_minutes: int,
    confirm_minutes: int,
    family: str,
    direction: str,
    bar: Bar,
    orb_high: float,
    orb_low: float,
    prior_for_volume: Sequence[Bar],
) -> Event:
    vr = _volume_ratio(prior_for_volume, bar)
    return Event(
        session_date=day.isoformat(),
        or_minutes=or_minutes,
        confirm_minutes=confirm_minutes,
        family=family,
        direction=direction,
        bar_start=bar.start_utc.isoformat(),
        bar_close=_close_time(bar, confirm_minutes).isoformat(),
        trigger_price=float(bar.close),
        orb_high=float(orb_high),
        orb_low=float(orb_low),
        volume_ratio=round(vr, 6) if vr is not None else None,
        volume_bin=_volume_bin(vr),
    )


def detect_events(
    *,
    day: date,
    native_bars: Sequence[Bar],
    history_native: Sequence[Bar],
    or_minutes: int,
    confirm_minutes: int,
) -> list[Event]:
    or_count = or_minutes // 5
    if len(native_bars) < or_count + 1:
        return []
    orb_high = max(float(b.high) for b in native_bars[:or_count])
    orb_low = min(float(b.low) for b in native_bars[:or_count])
    range_end = native_bars[0].start_utc + timedelta(minutes=or_minutes)

    confirm = _series_for_tf(native_bars, confirm_minutes)
    history_confirm = _series_for_tf(history_native, confirm_minutes) if history_native else []

    emitted: set[tuple[str, str]] = set()
    events: list[Event] = []
    retest_up = False
    retest_down = False
    breakout_up_close: datetime | None = None
    breakout_down_close: datetime | None = None

    for idx, bar in enumerate(confirm):
        close_at = _close_time(bar, confirm_minutes)
        if bar.start_utc < range_end or idx == 0:
            continue
        prev = confirm[idx - 1]
        prior_for_volume = [*history_confirm, *confirm[:idx]]

        def add(family: str, direction: str) -> None:
            key = (family, direction)
            if key in emitted:
                return
            emitted.add(key)
            events.append(
                _event(
                    day=day,
                    or_minutes=or_minutes,
                    confirm_minutes=confirm_minutes,
                    family=family,
                    direction=direction,
                    bar=bar,
                    orb_high=orb_high,
                    orb_low=orb_low,
                    prior_for_volume=prior_for_volume,
                )
            )

        # Expire failed-breakout inverse windows before evaluating this bar.
        if breakout_up_close is not None and close_at - breakout_up_close > INVERSE_WINDOW:
            breakout_up_close = None
        if breakout_down_close is not None and close_at - breakout_down_close > INVERSE_WINDOW:
            breakout_down_close = None

        # Existing armed states are evaluated before a new breakout is armed.
        if retest_up and float(bar.low) <= orb_high < float(bar.close):
            add("BREAK_RETEST", "LONG")
            retest_up = False
        if retest_down and float(bar.high) >= orb_low > float(bar.close):
            add("BREAK_RETEST", "SHORT")
            retest_down = False

        inside = orb_low <= float(bar.close) <= orb_high
        if breakout_up_close is not None and inside and close_at > breakout_up_close:
            add("FAILED_BREAKOUT_INVERSE", "SHORT")
            breakout_up_close = None
        if breakout_down_close is not None and inside and close_at > breakout_down_close:
            add("FAILED_BREAKOUT_INVERSE", "LONG")
            breakout_down_close = None

        # Same-bar sweep / rejection.
        if float(prev.close) <= orb_high and float(bar.high) > orb_high and float(bar.close) < orb_high:
            add("WICK_REJECTION", "SHORT")
        if float(prev.close) >= orb_low and float(bar.low) < orb_low and float(bar.close) > orb_low:
            add("WICK_REJECTION", "LONG")

        # Two-close acceptance.
        if float(prev.close) > orb_high and float(bar.close) > orb_high:
            add("ACCEPTANCE", "LONG")
        if float(prev.close) < orb_low and float(bar.close) < orb_low:
            add("ACCEPTANCE", "SHORT")

        # Fresh causal breakout.
        if float(prev.close) <= orb_high and float(bar.close) > orb_high:
            add("BREAKOUT", "LONG")
            retest_up = True
            breakout_up_close = close_at
        if float(prev.close) >= orb_low and float(bar.close) < orb_low:
            add("BREAKOUT", "SHORT")
            retest_down = True
            breakout_down_close = close_at

    return events


def measure(event: Event, native_bars: Sequence[Bar]) -> list[Outcome]:
    event_close = datetime.fromisoformat(event.bar_close)
    entry = float(event.trigger_price)
    sign = 1.0 if event.direction == "LONG" else -1.0
    future = [b for b in native_bars if b.start_utc >= event_close]
    rows: list[Outcome] = []

    views: list[tuple[str, list[Bar]]] = []
    for minutes in HORIZONS:
        cutoff = event_close + timedelta(minutes=minutes)
        path = [b for b in future if _close_time(b, 5) <= cutoff]
        views.append((f"{minutes}m", path))
    views.append(("EOD", future))

    for label, path in views:
        if not path:
            rows.append(Outcome(event, label, 0, None, None, None))
            continue
        close_ret = sign * (float(path[-1].close) - entry) / entry * 10_000.0
        if event.direction == "LONG":
            mfe = (max(float(b.high) for b in path) - entry) / entry * 10_000.0
            mae = (entry - min(float(b.low) for b in path)) / entry * 10_000.0
        else:
            mfe = (entry - min(float(b.low) for b in path)) / entry * 10_000.0
            mae = (max(float(b.high) for b in path) - entry) / entry * 10_000.0
        rows.append(
            Outcome(
                event=event,
                horizon=label,
                bars_observed=len(path),
                signed_close_bps=round(close_ret, 6),
                mfe_bps=round(mfe, 6),
                mae_bps=round(mae, 6),
            )
        )
    return rows


def _mean(values: Sequence[float]) -> float | None:
    return round(statistics.fmean(values), 6) if values else None


def _median(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def _stats(rows: Sequence[Outcome]) -> dict[str, Any]:
    valid = [r for r in rows if r.signed_close_bps is not None]
    ret = [float(r.signed_close_bps) for r in valid]
    mfe = [float(r.mfe_bps) for r in valid if r.mfe_bps is not None]
    mae = [float(r.mae_bps) for r in valid if r.mae_bps is not None]
    return {
        "n": len(valid),
        "mean_signed_close_bps": _mean(ret),
        "median_signed_close_bps": _median(ret),
        "median_mfe_bps": _median(mfe),
        "median_mae_bps": _median(mae),
    }


def summarize(events: Sequence[Event], outcomes: Sequence[Outcome]) -> dict[str, Any]:
    by_event = {
        (o.event.session_date, o.event.or_minutes, o.event.confirm_minutes, o.event.family,
         o.event.direction, o.event.bar_start, o.horizon): o
        for o in outcomes
    }
    primary: dict[str, Any] = {}
    volume: dict[str, Any] = {}

    cells = [
        (or_minutes, confirm_minutes, family, direction)
        for or_minutes in OR_DURATIONS
        for confirm_minutes in CONFIRM_MINUTES
        for family in FAMILIES
        for direction in DIRECTIONS
    ]
    for or_minutes, confirm_minutes, family, direction in cells:
        evs = [
            e for e in events
            if (e.or_minutes, e.confirm_minutes, e.family, e.direction)
            == (or_minutes, confirm_minutes, family, direction)
        ]
        rows60 = [
            by_event[(e.session_date, e.or_minutes, e.confirm_minutes, e.family,
                      e.direction, e.bar_start, "60m")]
            for e in evs
            if (e.session_date, e.or_minutes, e.confirm_minutes, e.family,
                e.direction, e.bar_start, "60m") in by_event
        ]
        h1 = [r for r in rows60 if date.fromisoformat(r.event.session_date) < HALF_SPLIT]
        h2 = [r for r in rows60 if date.fromisoformat(r.event.session_date) >= HALF_SPLIT]
        all_s = _stats(rows60)
        h1_s = _stats(h1)
        h2_s = _stats(h2)
        eligible = (
            all_s["n"] >= MIN_TOTAL
            and h1_s["n"] >= MIN_HALF
            and h2_s["n"] >= MIN_HALF
            and (h1_s["mean_signed_close_bps"] or 0) > 0
            and (h2_s["mean_signed_close_bps"] or 0) > 0
            and (h1_s["median_signed_close_bps"] or 0) >= 0
            and (h2_s["median_signed_close_bps"] or 0) >= 0
            and (h1_s["median_mfe_bps"] or 0) > (h1_s["median_mae_bps"] or 0)
            and (h2_s["median_mfe_bps"] or 0) > (h2_s["median_mae_bps"] or 0)
        )
        key = f"OR{or_minutes}|{confirm_minutes}m|{family}|{direction}"
        primary[key] = {
            "overall_60m": all_s,
            "H1_60m": h1_s,
            "H2_60m": h2_s,
            "stage_b_eligible_metrics_only": eligible,
            "distinct_sessions": len({e.session_date for e in evs}),
        }
        volume[key] = {}
        for vb in ("LOW", "NORMAL", "HIGH", "UNKNOWN"):
            vrows = [r for r in rows60 if r.event.volume_bin == vb]
            volume[key][vb] = _stats(vrows)

    return {"primary_cells": primary, "volume_decomposition_60m": volume}


def run() -> dict[str, Any]:
    files = session_files(INSTRUMENT)
    if not files:
        raise SystemExit(f"no replay files under data/replay_polygon_5m/{INSTRUMENT}")
    days = sorted(files)
    excluded_roll = roll_excluded_sessions(days)
    loaded: dict[date, list[Bar]] = {}
    skipped = {"incomplete": 0, "roll": 0, "no_prior": 0}

    for day in days:
        bars, _ = load_rth_session(files[day])
        if len(bars) != RTH_BARS:
            skipped["incomplete"] += 1
            continue
        loaded[day] = bars

    complete = sorted(loaded)
    events: list[Event] = []
    outcomes: list[Outcome] = []
    sessions_used = 0

    for idx, day in enumerate(complete):
        if day in excluded_roll:
            skipped["roll"] += 1
            continue
        prior_days = complete[max(0, idx - 4):idx]
        if not prior_days or prior_days[-1] in excluded_roll:
            skipped["no_prior"] += 1
            continue
        bars = loaded[day]
        history_native = [b for d in prior_days for b in loaded[d]]
        sessions_used += 1
        for or_minutes in OR_DURATIONS:
            for confirm_minutes in CONFIRM_MINUTES:
                evs = detect_events(
                    day=day,
                    native_bars=bars,
                    history_native=history_native,
                    or_minutes=or_minutes,
                    confirm_minutes=confirm_minutes,
                )
                events.extend(evs)
                for event in evs:
                    outcomes.extend(measure(event, bars))

    report = {
        "study_id": STUDY_ID,
        "study_version": STUDY_VERSION,
        "prereg": "docs/prereg-mnq-orb-rework-stage-a-2026-09-23.md",
        "instrument": INSTRUMENT,
        "or_durations": OR_DURATIONS,
        "confirmation_minutes": CONFIRM_MINUTES,
        "primary_cell_count_frozen": 60,
        "sessions_total": len(days),
        "sessions_used": sessions_used,
        "skipped": skipped,
        "event_count": len(events),
        "outcome_count": len(outcomes),
        "summary": summarize(events, outcomes),
        "events": [asdict(e) for e in events],
        "outcomes": [
            {
                "event": asdict(o.event),
                "horizon": o.horizon,
                "bars_observed": o.bars_observed,
                "signed_close_bps": o.signed_close_bps,
                "mfe_bps": o.mfe_bps,
                "mae_bps": o.mae_bps,
            }
            for o in outcomes
        ],
    }
    return report


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['study_id']} {report['study_version']}",
        "",
        f"MNQ sessions used: {report['sessions_used']} of {report['sessions_total']}; "
        f"skipped {report['skipped']}",
        f"events: {report['event_count']} · outcome rows: {report['outcome_count']}",
        "",
        "| cell | n | H1 n / mean / med | H2 n / mean / med | H1 MFE/MAE | H2 MFE/MAE | Stage-B metrics |",
        "|---|---:|---|---|---|---|---|",
    ]
    for key, row in report["summary"]["primary_cells"].items():
        a, h1, h2 = row["overall_60m"], row["H1_60m"], row["H2_60m"]
        lines.append(
            f"| {key} | {a['n']} | {h1['n']} / {h1['mean_signed_close_bps']} / {h1['median_signed_close_bps']} | "
            f"{h2['n']} / {h2['mean_signed_close_bps']} / {h2['median_signed_close_bps']} | "
            f"{h1['median_mfe_bps']}/{h1['median_mae_bps']} | "
            f"{h2['median_mfe_bps']}/{h2['median_mae_bps']} | "
            f"{'PASS' if row['stage_b_eligible_metrics_only'] else 'FAIL'} |"
        )
    lines += [
        "",
        "Stage-B metrics PASS is not a strategy verdict or promotion. Volume is descriptive only.",
        "No fills, stops, targets, PF, or execution expectancy are measured in Stage A.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MNQ ORB rework Stage A raw-signal screen")
    parser.add_argument("--out", default=str(ROOT / "logs" / "mnq_orb_stage_a"))
    args = parser.parse_args(argv)
    report = run()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "mnq_orb_stage_a.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    md = to_markdown(report)
    (out / "mnq_orb_stage_a.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
