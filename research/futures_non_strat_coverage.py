"""Futures non-Strat level coverage (`fns-v0.1`) — research-only, read-only.

Reuses the frozen ``ns-v0.1`` predicates from ``alert_ranker.non_strat_coverage``
on MNQ/MES RTH 5m replay bars. Prereg: docs/prereg-futures-non-strat-coverage-2026-09-21.md.
No strategy, risk, broker or execution import; nothing under strategy/,
execution/ or engine/ imports this module and nothing should.

    python research/futures_non_strat_coverage.py --instrument MNQ --out logs/fns_v01
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.causal_bars import Bar  # noqa: E402
from alert_ranker.non_strat_coverage import (  # noqa: E402
    NonStratEvent,
    measure_outcomes,
    observe_session,
)

OBSERVER_ID = "FUTURES_NON_STRAT_COVERAGE"
OBSERVER_VERSION = "fns-v0.1"
REPLAY_ROOT = ROOT / "data" / "replay_polygon_5m"
EXCHANGE_TZ = ZoneInfo("America/New_York")
RTH_OPEN_ET = time(9, 30)
RTH_CLOSE_ET = time(16, 0)
RTH_BARS = 78
HISTORY_SESSIONS = 4
HALF_SPLIT = date(2025, 9, 1)


# --------------------------------------------------------------------------- #
# bars
# --------------------------------------------------------------------------- #


def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def roll_excluded_sessions(sessions: Sequence[date]) -> set[date]:
    """Mon–Fri of the 3rd-Friday week of Mar/Jun/Sep/Dec, plus the next session."""
    excluded: set[date] = set()
    ordered = sorted(sessions)
    for year in sorted({s.year for s in ordered}):
        for month in (3, 6, 9, 12):
            friday = _third_friday(year, month)
            monday = friday - timedelta(days=4)
            week = {s for s in ordered if monday <= s <= friday}
            excluded |= week
            later = [s for s in ordered if s > friday]
            if week and later:
                excluded.add(later[0])
    return excluded


def load_rth_session(path: Path) -> tuple[list[Bar], dict[str, Any] | None]:
    """RTH bars (09:30–16:00 America/New_York) and the first payload row, from one day file."""
    bars: list[Bar] = []
    first_payload: dict[str, Any] | None = None
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("session") != "new_york":
                continue
            ts = datetime.fromisoformat(row["timestamp"]).astimezone(timezone.utc)
            local = ts.astimezone(EXCHANGE_TZ).time()
            if not (RTH_OPEN_ET <= local < RTH_CLOSE_ET):
                continue
            if first_payload is None:
                first_payload = row
            bars.append(
                Bar(
                    start=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                    # Per-bar volume-weighted price = typical price (hlc3). The
                    # payload's ``vwap`` is ALREADY the cumulative RTH session
                    # VWAP; feeding it here made ``session_vwap`` average the
                    # running VWAP again (a lagged line). The prereg says the
                    # payload field is not used; sum(hlc3*v)/sum(v) over RTH
                    # bars reproduces that cumulative VWAP (verified 2026-09-24).
                    vwap=(float(row["high"]) + float(row["low"]) + float(row["close"])) / 3.0,
                )
            )
    return sorted(bars, key=lambda b: b.start_utc), first_payload


def session_files(instrument: str) -> dict[date, Path]:
    out: dict[date, Path] = {}
    for path in sorted((REPLAY_ROOT / instrument).glob(f"{instrument}_*.jsonl")):
        out[date.fromisoformat(path.stem.split("_", 1)[1])] = path
    return out


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #


def _stamp(event: NonStratEvent) -> NonStratEvent:
    return replace(event, observer_id=OBSERVER_ID, observer_version=OBSERVER_VERSION)


def run_instrument(instrument: str) -> dict[str, Any]:
    files = session_files(instrument)
    if not files:
        raise SystemExit(f"no replay files under {REPLAY_ROOT / instrument}")
    days = sorted(files)
    excluded_roll = roll_excluded_sessions(days)

    loaded: dict[date, tuple[list[Bar], dict[str, Any] | None]] = {}
    skipped: dict[str, int] = {"incomplete": 0, "roll": 0, "no_prior": 0}
    for day in days:
        bars, payload = load_rth_session(files[day])
        if len(bars) != RTH_BARS:
            skipped["incomplete"] += 1
            continue
        loaded[day] = (bars, payload)

    complete_days = sorted(loaded)
    events: list[NonStratEvent] = []
    outcomes = []
    agreement = {"pdh": [0, 0], "pdl": [0, 0], "orb_high": [0, 0], "orb_low": [0, 0]}
    sessions_used: list[date] = []

    for index, day in enumerate(complete_days):
        if day in excluded_roll:
            skipped["roll"] += 1
            continue
        prior_days = complete_days[max(0, index - HISTORY_SESSIONS) : index]
        if not prior_days or prior_days[-1] in excluded_roll:
            skipped["no_prior"] += 1
            continue
        bars, payload = loaded[day]
        prior_bars = loaded[prior_days[-1]][0]
        history = [b for d in prior_days for b in loaded[d][0]]
        day_events = [
            _stamp(e)
            for e in observe_session(
                symbol=instrument,
                session_date=day.isoformat(),
                prior_session_bars=prior_bars,
                session_bars=bars,
                history_bars=history,
            )
        ]
        events.extend(day_events)
        outcomes.extend(measure_outcomes(day_events, {instrument: bars}))
        sessions_used.append(day)

        # Parity of the frozen definitions with the payload's own level fields.
        if payload is not None:
            pdh = max(b.high for b in prior_bars)
            pdl = min(b.low for b in prior_bars)
            orb_high = max(b.high for b in bars[:6])
            orb_low = min(b.low for b in bars[:6])
            for key, mine in (("pdh", pdh), ("pdl", pdl), ("orb_high", orb_high), ("orb_low", orb_low)):
                field = {"pdh": "previous_day_high", "pdl": "previous_day_low"}.get(key, key)
                theirs = payload.get(field)
                if theirs is None:
                    continue
                agreement[key][1] += 1
                if abs(float(theirs) - mine) < 1e-6:
                    agreement[key][0] += 1

    return {
        "observer_id": OBSERVER_ID,
        "observer_version": OBSERVER_VERSION,
        "instrument": instrument,
        "sessions_total": len(days),
        "sessions_used": len(sessions_used),
        "first_session": sessions_used[0].isoformat() if sessions_used else None,
        "last_session": sessions_used[-1].isoformat() if sessions_used else None,
        "skipped": skipped,
        "roll_sessions_in_range": len(excluded_roll & set(days)),
        "payload_level_agreement": {
            k: {"agree": v[0], "n": v[1], "rate": round(v[0] / v[1], 4) if v[1] else None}
            for k, v in agreement.items()
        },
        "families": summarize_families(events, outcomes),
        "events": len(events),
        "episodes": len({e.episode_id for e in events}),
    }


def _median(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _mean(values: Sequence[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def summarize_families(events: Sequence[NonStratEvent], outcomes) -> dict[str, Any]:
    """Episode-level summary: the FIRST event of each episode carries the outcome."""
    first_by_episode: dict[str, NonStratEvent] = {}
    for event in sorted(events, key=lambda e: e.bar_start):
        first_by_episode.setdefault(event.episode_id, event)
    by_key = {(o.episode_id, o.event_bar_start, o.horizon): o for o in outcomes}

    per_family: dict[str, dict[str, Any]] = {}
    for episode_id, event in first_by_episode.items():
        fam = per_family.setdefault(
            event.family,
            {"events": 0, "episodes": 0, "sessions": set(), "h1": [], "h2": [], "ret60": [], "retEOD": [], "mfe60": [], "mae60": [], "shape": []},
        )
        fam["episodes"] += 1
        fam["sessions"].add(event.session_date)
        o60 = by_key.get((episode_id, event.bar_start, "60m"))
        oeod = by_key.get((episode_id, event.bar_start, "EOD"))
        if o60 and o60.close_return_bps is not None:
            fam["ret60"].append(o60.close_return_bps)
            fam["mfe60"].append(o60.mfe_bps)
            fam["mae60"].append(o60.mae_bps)
            fam["shape"].append(1.0 if o60.mfe_bps >= 2.0 * max(o60.mae_bps, 1e-9) else 0.0)
            half = "h1" if date.fromisoformat(event.session_date) < HALF_SPLIT else "h2"
            fam[half].append(o60.close_return_bps)
        if oeod and oeod.close_return_bps is not None:
            fam["retEOD"].append(oeod.close_return_bps)
    for event in events:
        per_family[event.family]["events"] += 1

    out: dict[str, Any] = {}
    for family, row in sorted(per_family.items()):
        out[family] = {
            "events": row["events"],
            "episodes": row["episodes"],
            "sessions_with_episode": len(row["sessions"]),
            "n60": len(row["ret60"]),
            "ret60_median_bps": _median(row["ret60"]),
            "ret60_mean_bps": _mean(row["ret60"]),
            "retEOD_median_bps": _median(row["retEOD"]),
            "mfe60_median_bps": _median(row["mfe60"]),
            "mae60_median_bps": _median(row["mae60"]),
            "share_mfe_ge_2x_mae_60m": _mean(row["shape"]),
            "half1": {"n": len(row["h1"]), "ret60_median_bps": _median(row["h1"])},
            "half2": {"n": len(row["h2"]), "ret60_median_bps": _median(row["h2"])},
        }
    return out


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['observer_id']} {report['observer_version']} — {report['instrument']}",
        "",
        f"sessions: {report['sessions_used']} used of {report['sessions_total']} "
        f"({report['first_session']} → {report['last_session']}); skipped {report['skipped']}; "
        f"roll sessions in range {report['roll_sessions_in_range']}",
        f"events {report['events']} · episodes {report['episodes']}",
        "",
        "payload level agreement (frozen definition == payload field): "
        + ", ".join(f"{k} {v['agree']}/{v['n']}" for k, v in report["payload_level_agreement"].items()),
        "",
        "| family | episodes | sessions | n60 | ret60 med | ret60 mean | EOD med | MFE60 med | MAE60 med | MFE≥2×MAE | H1 n / med | H2 n / med |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for family, r in report["families"].items():
        lines.append(
            f"| {family} | {r['episodes']} | {r['sessions_with_episode']} | {r['n60']} | {r['ret60_median_bps']} | "
            f"{r['ret60_mean_bps']} | {r['retEOD_median_bps']} | {r['mfe60_median_bps']} | {r['mae60_median_bps']} | "
            f"{r['share_mfe_ge_2x_mae_60m']} | {r['half1']['n']} / {r['half1']['ret60_median_bps']} | "
            f"{r['half2']['n']} / {r['half2']['ret60_median_bps']} |"
        )
    lines += ["", "bps from the event bar close; episode-level (first event per episode); no geometry, no PF, no win rate."]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Futures non-Strat level coverage (research-only)")
    parser.add_argument("--instrument", required=True, choices=("MNQ", "MES"))
    parser.add_argument("--out", default=str(ROOT / "logs" / "fns_v01"))
    args = parser.parse_args(argv)
    report = run_instrument(args.instrument)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"fns_v01_{args.instrument}.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    md = to_markdown(report)
    (out / f"fns_v01_{args.instrument}.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
