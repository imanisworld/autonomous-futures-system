"""MNQ VWAP failed-reclaim within 3 bars raw-signal study.

Research only. Prereg:
docs/prereg-mnq-vwap-failed-reclaim-3bar-2026-09-24.md
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

from alert_ranker.causal_bars import Bar, session_vwap  # noqa: E402
from research.futures_non_strat_coverage import (  # noqa: E402
    HALF_SPLIT,
    HISTORY_SESSIONS,
    RTH_BARS,
    load_rth_session,
    roll_excluded_sessions,
    session_files,
)

STUDY_ID = "MNQ_VWAP_FAILED_RECLAIM_3BAR"
STUDY_VERSION = "vwap-fr3-a-v0.1"
INSTRUMENT = "MNQ"
MAX_FAILURE_LAG_BARS = 3
VOLUME_LOOKBACK = 20
HORIZONS = (15, 30, 60)
MIN_TOTAL = 100
MIN_HALF = 40


@dataclass(frozen=True)
class Event:
    session_date: str
    reclaim_bar_start: str
    bar_start: str
    bar_close: str
    failure_lag_bars: int
    trigger_price: float
    vwap: float
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


def _close_time(bar: Bar) -> datetime:
    return bar.start_utc + timedelta(minutes=5)


def _volume_ratio(prior: Sequence[Bar], current: Bar) -> float | None:
    vols = [float(b.volume) for b in prior[-VOLUME_LOOKBACK:]]
    if len(vols) < VOLUME_LOOKBACK:
        return None
    baseline = statistics.fmean(vols)
    if baseline <= 0:
        return None
    return float(current.volume) / baseline


def _volume_bin(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    if value < 0.8:
        return "LOW"
    if value < 1.2:
        return "NORMAL"
    return "HIGH"


def detect_events(
    *,
    day: date,
    bars: Sequence[Bar],
    history: Sequence[Bar] = (),
) -> list[Event]:
    current = list(bars)
    events: list[Event] = []
    armed_idx: int | None = None
    reclaim_start: str | None = None

    for idx in range(1, len(current)):
        bar = current[idx]
        prev = current[idx - 1]
        current_vwap = session_vwap(current[: idx + 1])
        previous_vwap = session_vwap(current[:idx])
        if current_vwap is None or previous_vwap is None:
            continue

        # Existing reclaim arm gets first chance to fail on this completed bar.
        if armed_idx is not None and reclaim_start is not None:
            lag = idx - armed_idx
            if 1 <= lag <= MAX_FAILURE_LAG_BARS and float(bar.close) < float(current_vwap):
                prior_for_volume = [*history, *current[:idx]]
                vr = _volume_ratio(prior_for_volume, bar)
                events.append(
                    Event(
                        session_date=day.isoformat(),
                        reclaim_bar_start=reclaim_start,
                        bar_start=bar.start_utc.isoformat(),
                        bar_close=_close_time(bar).isoformat(),
                        failure_lag_bars=lag,
                        trigger_price=float(bar.close),
                        vwap=float(current_vwap),
                        volume_ratio=round(vr, 6) if vr is not None else None,
                        volume_bin=_volume_bin(vr),
                    )
                )
                armed_idx = None
                reclaim_start = None
            elif lag >= MAX_FAILURE_LAG_BARS:
                armed_idx = None
                reclaim_start = None

        # A fresh reclaim is armed only from information known at this bar close.
        reclaimed = float(prev.close) <= float(previous_vwap) and float(bar.close) > float(current_vwap)
        if reclaimed:
            armed_idx = idx
            reclaim_start = bar.start_utc.isoformat()

    return events


def measure(event: Event, bars: Sequence[Bar]) -> list[Outcome]:
    event_close = datetime.fromisoformat(event.bar_close)
    entry = float(event.trigger_price)
    future = [b for b in bars if b.start_utc >= event_close]
    views: list[tuple[str, list[Bar]]] = []
    for minutes in HORIZONS:
        cutoff = event_close + timedelta(minutes=minutes)
        views.append((f"{minutes}m", [b for b in future if _close_time(b) <= cutoff]))
    views.append(("EOD", future))

    rows: list[Outcome] = []
    for horizon, path in views:
        if not path:
            rows.append(Outcome(event, horizon, 0, None, None, None))
            continue
        close_ret = (entry - float(path[-1].close)) / entry * 10_000.0
        mfe = (entry - min(float(b.low) for b in path)) / entry * 10_000.0
        mae = (max(float(b.high) for b in path) - entry) / entry * 10_000.0
        rows.append(
            Outcome(
                event=event,
                horizon=horizon,
                bars_observed=len(path),
                signed_close_bps=round(close_ret, 6),
                mfe_bps=round(max(0.0, mfe), 6),
                mae_bps=round(max(0.0, mae), 6),
            )
        )
    return rows


def _stats(rows: Sequence[Outcome]) -> dict[str, Any]:
    valid = [r for r in rows if r.signed_close_bps is not None]
    ret = [float(r.signed_close_bps) for r in valid]
    mfe = [float(r.mfe_bps) for r in valid if r.mfe_bps is not None]
    mae = [float(r.mae_bps) for r in valid if r.mae_bps is not None]
    return {
        "n": len(valid),
        "mean_signed_close_bps": round(statistics.fmean(ret), 6) if ret else None,
        "median_signed_close_bps": round(statistics.median(ret), 6) if ret else None,
        "median_mfe_bps": round(statistics.median(mfe), 6) if mfe else None,
        "median_mae_bps": round(statistics.median(mae), 6) if mae else None,
    }


def summarize(events: Sequence[Event], outcomes: Sequence[Outcome]) -> dict[str, Any]:
    rows60 = [o for o in outcomes if o.horizon == "60m"]
    h1 = [o for o in rows60 if date.fromisoformat(o.event.session_date) < HALF_SPLIT]
    h2 = [o for o in rows60 if date.fromisoformat(o.event.session_date) >= HALF_SPLIT]
    overall, h1s, h2s = _stats(rows60), _stats(h1), _stats(h2)
    advance = (
        overall["n"] >= MIN_TOTAL
        and h1s["n"] >= MIN_HALF
        and h2s["n"] >= MIN_HALF
        and (h1s["mean_signed_close_bps"] or 0) > 0
        and (h2s["mean_signed_close_bps"] or 0) > 0
        and (h1s["median_signed_close_bps"] or 0) >= 0
        and (h2s["median_signed_close_bps"] or 0) >= 0
        and (h1s["median_mfe_bps"] or 0) > (h1s["median_mae_bps"] or 0)
        and (h2s["median_mfe_bps"] or 0) > (h2s["median_mae_bps"] or 0)
    )
    lag = {
        str(k): _stats([o for o in rows60 if o.event.failure_lag_bars == k])
        for k in (1, 2, 3)
    }
    volume = {
        vb: _stats([o for o in rows60 if o.event.volume_bin == vb])
        for vb in ("LOW", "NORMAL", "HIGH", "UNKNOWN")
    }
    return {
        "overall_60m": overall,
        "H1_60m": h1s,
        "H2_60m": h2s,
        "stage_b_eligible_metrics_only": advance,
        "distinct_sessions": len({e.session_date for e in events}),
        "lag_descriptive_60m": lag,
        "volume_descriptive_60m": volume,
    }


def run() -> dict[str, Any]:
    files = session_files(INSTRUMENT)
    if not files:
        raise SystemExit(f"no replay files for {INSTRUMENT}")
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
    for idx, day in enumerate(complete):
        if day in excluded_roll:
            skipped["roll"] += 1
            continue
        prior_days = complete[max(0, idx - HISTORY_SESSIONS):idx]
        if not prior_days or prior_days[-1] in excluded_roll:
            skipped["no_prior"] += 1
            continue
        history = [b for d in prior_days for b in loaded[d]]
        evs = detect_events(day=day, bars=loaded[day], history=history)
        events.extend(evs)
        for event in evs:
            outcomes.extend(measure(event, loaded[day]))

    return {
        "study_id": STUDY_ID,
        "study_version": STUDY_VERSION,
        "instrument": INSTRUMENT,
        "event_definition": "VWAP reclaim then first close below cumulative RTH VWAP within <=3 completed 5m bars",
        "event_count": len(events),
        "skipped": skipped,
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


def to_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    return (
        f"# {report['study_id']} {report['study_version']}\n\n"
        f"Events: {report['event_count']} · distinct sessions: {s['distinct_sessions']}\n\n"
        "| view | n | mean 60m bps | median 60m bps | median MFE | median MAE |\n"
        "|---|---:|---:|---:|---:|---:|\n"
        f"| overall | {s['overall_60m']['n']} | {s['overall_60m']['mean_signed_close_bps']} | "
        f"{s['overall_60m']['median_signed_close_bps']} | {s['overall_60m']['median_mfe_bps']} | {s['overall_60m']['median_mae_bps']} |\n"
        f"| H1 | {s['H1_60m']['n']} | {s['H1_60m']['mean_signed_close_bps']} | "
        f"{s['H1_60m']['median_signed_close_bps']} | {s['H1_60m']['median_mfe_bps']} | {s['H1_60m']['median_mae_bps']} |\n"
        f"| H2 | {s['H2_60m']['n']} | {s['H2_60m']['mean_signed_close_bps']} | "
        f"{s['H2_60m']['median_signed_close_bps']} | {s['H2_60m']['median_mfe_bps']} | {s['H2_60m']['median_mae_bps']} |\n\n"
        f"Stage-B metrics gate: {'PASS' if s['stage_b_eligible_metrics_only'] else 'FAIL'}\n\n"
        "Lag and volume splits are descriptive only; they cannot rescue the primary combined population.\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "logs" / "mnq_vwap_failed_reclaim_3bar"))
    args = parser.parse_args(argv)
    report = run()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    md = to_markdown(report)
    (out / "result.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
