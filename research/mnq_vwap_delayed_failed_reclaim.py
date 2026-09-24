"""MNQ delayed VWAP failed-reclaim Stage A — research only.

Prereg:
docs/prereg-mnq-vwap-delayed-failed-reclaim-2026-09-24.md

Tests a new delayed failure population only. Immediate next-bar failed reclaims
remain excluded because that population was already exposed and failed H2.
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

from alert_ranker.causal_bars import Bar, session_vwap
from research.futures_non_strat_coverage import (
    HALF_SPLIT,
    RTH_BARS,
    load_rth_session,
    roll_excluded_sessions,
    session_files,
)

TRIAL_ID = "T-2026-09-24-prereg-mnq-vwap-delayed-failed-reclaim-2026-09-24-01"
INSTRUMENT = "MNQ"


@dataclass(frozen=True)
class Event:
    session_date: str
    reclaim_bar_start: str
    failure_bar_start: str
    failure_lag_bars: int
    trigger_price: float
    vwap: float


@dataclass(frozen=True)
class Outcome:
    event: Event
    horizon: str
    signed_close_bps: float | None
    mfe_bps: float | None
    mae_bps: float | None


def detect_first_delayed_failure(day: date, bars: Sequence[Bar]) -> Event | None:
    arm_idx: int | None = None
    for idx in range(1, len(bars)):
        bar = bars[idx]
        prev = bars[idx - 1]
        current_vwap = session_vwap(bars[: idx + 1])
        previous_vwap = session_vwap(bars[:idx])
        if current_vwap is None or previous_vwap is None:
            continue

        if arm_idx is not None:
            lag = idx - arm_idx
            if lag == 1 and float(bar.close) < float(current_vwap):
                # Already-exposed immediate failure population: explicitly excluded.
                arm_idx = None
            elif lag in (2, 3) and float(bar.close) < float(current_vwap):
                return Event(
                    session_date=day.isoformat(),
                    reclaim_bar_start=bars[arm_idx].start_utc.isoformat(),
                    failure_bar_start=bar.start_utc.isoformat(),
                    failure_lag_bars=lag,
                    trigger_price=float(bar.close),
                    vwap=float(current_vwap),
                )
            elif lag >= 3:
                arm_idx = None

        if arm_idx is None:
            reclaimed = float(prev.close) <= float(previous_vwap) and float(bar.close) > float(current_vwap)
            if reclaimed:
                arm_idx = idx

    return None


def measure(event: Event, bars: Sequence[Bar]) -> list[Outcome]:
    start = datetime.fromisoformat(event.failure_bar_start)
    event_close = start + timedelta(minutes=5)
    entry = float(event.trigger_price)
    future = [b for b in bars if b.start_utc >= event_close]
    rows: list[Outcome] = []

    for label, minutes in (("15m", 15), ("30m", 30), ("60m", 60)):
        cutoff = event_close + timedelta(minutes=minutes)
        path = [b for b in future if b.start_utc + timedelta(minutes=5) <= cutoff]
        rows.append(_measure_path(event, label, entry, path))
    rows.append(_measure_path(event, "EOD", entry, future))
    return rows


def _measure_path(event: Event, label: str, entry: float, path: Sequence[Bar]) -> Outcome:
    if not path:
        return Outcome(event, label, None, None, None)
    # SHORT: positive is price moving down.
    signed = (entry - float(path[-1].close)) / entry * 10_000.0
    mfe = (entry - min(float(b.low) for b in path)) / entry * 10_000.0
    mae = (max(float(b.high) for b in path) - entry) / entry * 10_000.0
    return Outcome(
        event=event,
        horizon=label,
        signed_close_bps=round(signed, 6),
        mfe_bps=round(mfe, 6),
        mae_bps=round(mae, 6),
    )


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


def summarize(outcomes: Sequence[Outcome]) -> dict[str, Any]:
    r60 = [r for r in outcomes if r.horizon == "60m" and r.signed_close_bps is not None]
    h1 = [r for r in r60 if date.fromisoformat(r.event.session_date) < HALF_SPLIT]
    h2 = [r for r in r60 if date.fromisoformat(r.event.session_date) >= HALF_SPLIT]
    overall, s1, s2 = _stats(r60), _stats(h1), _stats(h2)
    eligible = (
        overall["n"] >= 80
        and s1["n"] >= 30
        and s2["n"] >= 30
        and (s1["mean_signed_close_bps"] or 0) > 0
        and (s2["mean_signed_close_bps"] or 0) > 0
        and (s1["median_signed_close_bps"] or 0) >= 0
        and (s2["median_signed_close_bps"] or 0) >= 0
        and (s1["median_mfe_bps"] or 0) > (s1["median_mae_bps"] or 0)
        and (s2["median_mfe_bps"] or 0) > (s2["median_mae_bps"] or 0)
    )
    return {"overall_60m": overall, "H1_60m": s1, "H2_60m": s2, "advance_to_friction_study": eligible}


def run() -> dict[str, Any]:
    files = session_files(INSTRUMENT)
    if not files:
        raise SystemExit(f"no replay files under data/replay_polygon_5m/{INSTRUMENT}")
    days = sorted(files)
    excluded_roll = roll_excluded_sessions(days)
    events: list[Event] = []
    outcomes: list[Outcome] = []
    skipped = {"incomplete": 0, "roll": 0}

    for day in days:
        bars, _ = load_rth_session(files[day])
        if len(bars) != RTH_BARS:
            skipped["incomplete"] += 1
            continue
        if day in excluded_roll:
            skipped["roll"] += 1
            continue
        event = detect_first_delayed_failure(day, bars)
        if event is None:
            continue
        events.append(event)
        outcomes.extend(measure(event, bars))

    return {
        "trial_id": TRIAL_ID,
        "instrument": INSTRUMENT,
        "definition": "VWAP reclaim then delayed close below cumulative RTH VWAP on bar 2 or 3; immediate failure excluded",
        "events": len(events),
        "skipped": skipped,
        "summary": summarize(outcomes),
        "event_rows": [asdict(e) for e in events],
        "outcomes": [
            {
                "event": asdict(o.event),
                "horizon": o.horizon,
                "signed_close_bps": o.signed_close_bps,
                "mfe_bps": o.mfe_bps,
                "mae_bps": o.mae_bps,
            }
            for o in outcomes
        ],
    }


def to_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"# {report['trial_id']}",
        "",
        f"events: {report['events']} · skipped: {report['skipped']}",
        "",
        "| scope | n | mean 60m bps | median 60m bps | med MFE/MAE |",
        "|---|---:|---:|---:|---:|",
    ]
    for key in ("overall_60m", "H1_60m", "H2_60m"):
        r = s[key]
        lines.append(
            f"| {key} | {r['n']} | {r['mean_signed_close_bps']} | "
            f"{r['median_signed_close_bps']} | {r['median_mfe_bps']}/{r['median_mae_bps']} |"
        )
    lines += [
        "",
        f"Advance to friction study: **{s['advance_to_friction_study']}**",
        "",
        "Raw-signal screen only. Immediate next-bar failures remain excluded.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "logs" / "mnq_vwap_delayed_failed_reclaim"))
    args = p.parse_args(argv)
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
