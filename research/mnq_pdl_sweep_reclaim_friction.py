"""MNQ true PDL sweep→reclaim friction viability — research only.

Prereg:
docs/prereg-mnq-pdl-sweep-reclaim-friction-2026-09-24.md

Uses the existing causal PDL_REJECTION_LONG event definition. Measures only
next-5m-open -> 60m-close economics at frozen 2pt/3pt all-in friction.
No strategy, risk, broker, execution or runtime path is imported.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.non_strat_coverage import observe_session
from research.futures_non_strat_coverage import (
    HALF_SPLIT,
    HISTORY_SESSIONS,
    RTH_BARS,
    load_rth_session,
    roll_excluded_sessions,
    session_files,
)

TRIAL_ID = "T-2026-09-24-prereg-mnq-pdl-sweep-reclaim-friction-2026-09-24-01"
INSTRUMENT = "MNQ"
FAMILY = "PDL_REJECTION_LONG"
FRICTION_POINTS = (2.0, 3.0)
MNQ_DOLLARS_PER_POINT = 2.0


@dataclass(frozen=True)
class ScoredEpisode:
    session_date: str
    event_bar_start: str
    entry_bar_start: str
    entry_open: float
    exit_close: float
    gross_points: float
    gross_dollars: float
    net_2pt_points: float
    net_3pt_points: float


def score_path(*, entry_open: float, exit_close: float) -> dict[str, float]:
    gross = float(exit_close) - float(entry_open)
    return {
        "gross_points": gross,
        "gross_dollars": gross * MNQ_DOLLARS_PER_POINT,
        "net_2pt_points": gross - 2.0,
        "net_3pt_points": gross - 3.0,
    }


def _stats(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 6),
        "median": round(statistics.median(values), 6),
        "win_rate": round(sum(v > 0 for v in values) / len(values), 6),
    }


def summarize(rows: Sequence[ScoredEpisode]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, pred in (
        ("overall", lambda r: True),
        ("H1", lambda r: date.fromisoformat(r.session_date) < HALF_SPLIT),
        ("H2", lambda r: date.fromisoformat(r.session_date) >= HALF_SPLIT),
    ):
        subset = [r for r in rows if pred(r)]
        out[label] = {
            "gross_points": _stats([r.gross_points for r in subset]),
            "net_2pt_points": _stats([r.net_2pt_points for r in subset]),
            "net_3pt_points": _stats([r.net_3pt_points for r in subset]),
        }
    h1 = out["H1"]["net_3pt_points"]
    h2 = out["H2"]["net_3pt_points"]
    out["advance_to_bracket_study"] = (
        h1["n"] >= 50
        and h2["n"] >= 50
        and (h1["mean"] or 0) > 0
        and (h2["mean"] or 0) > 0
        and (h1["median"] or 0) > 0
        and (h2["median"] or 0) > 0
    )
    return out


def run() -> dict[str, Any]:
    files = session_files(INSTRUMENT)
    if not files:
        raise SystemExit(f"no replay files under data/replay_polygon_5m/{INSTRUMENT}")

    days = sorted(files)
    excluded_roll = roll_excluded_sessions(days)
    loaded = {}
    skipped = {"incomplete": 0, "roll": 0, "no_prior": 0, "late_event": 0}

    for day in days:
        bars, _ = load_rth_session(files[day])
        if len(bars) != RTH_BARS:
            skipped["incomplete"] += 1
            continue
        loaded[day] = bars

    complete = sorted(loaded)
    scored: list[ScoredEpisode] = []
    source_episodes = 0

    for idx, day in enumerate(complete):
        if day in excluded_roll:
            skipped["roll"] += 1
            continue
        prior_days = complete[max(0, idx - HISTORY_SESSIONS):idx]
        if not prior_days or prior_days[-1] in excluded_roll:
            skipped["no_prior"] += 1
            continue

        bars = loaded[day]
        prior_bars = loaded[prior_days[-1]]
        history = [b for d in prior_days for b in loaded[d]]
        events = observe_session(
            symbol=INSTRUMENT,
            session_date=day.isoformat(),
            prior_session_bars=prior_bars,
            session_bars=bars,
            history_bars=history,
        )
        first_by_episode = {}
        for e in sorted((e for e in events if e.family == FAMILY), key=lambda e: e.bar_start):
            first_by_episode.setdefault(e.episode_id, e)

        by_start = {b.start_utc.isoformat(): i for i, b in enumerate(bars)}
        for e in first_by_episode.values():
            source_episodes += 1
            event_idx = by_start.get(e.bar_start)
            if event_idx is None:
                raise RuntimeError(f"event bar missing from session: {e.bar_start}")
            entry_idx = event_idx + 1
            exit_idx = entry_idx + 11  # 12 completed 5m bars = 60 elapsed minutes.
            if exit_idx >= len(bars):
                skipped["late_event"] += 1
                continue
            entry = bars[entry_idx]
            exit_bar = bars[exit_idx]
            econ = score_path(entry_open=entry.open, exit_close=exit_bar.close)
            scored.append(
                ScoredEpisode(
                    session_date=day.isoformat(),
                    event_bar_start=e.bar_start,
                    entry_bar_start=entry.start_utc.isoformat(),
                    entry_open=float(entry.open),
                    exit_close=float(exit_bar.close),
                    **econ,
                )
            )

    return {
        "trial_id": TRIAL_ID,
        "instrument": INSTRUMENT,
        "family": FAMILY,
        "entry": "next_5m_open",
        "exit": "60m_close",
        "friction_points": FRICTION_POINTS,
        "source_episodes": source_episodes,
        "resolved": len(scored),
        "skipped": skipped,
        "summary": summarize(scored),
        "episodes": [asdict(r) for r in scored],
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['trial_id']}",
        "",
        f"source episodes: {report['source_episodes']} · resolved: {report['resolved']} · skipped: {report['skipped']}",
        "",
        "| half | n | gross mean/med | net 2pt mean/med | net 3pt mean/med |",
        "|---|---:|---|---|---|",
    ]
    for half in ("overall", "H1", "H2"):
        s = report["summary"][half]
        lines.append(
            f"| {half} | {s['gross_points']['n']} | "
            f"{s['gross_points']['mean']}/{s['gross_points']['median']} | "
            f"{s['net_2pt_points']['mean']}/{s['net_2pt_points']['median']} | "
            f"{s['net_3pt_points']['mean']}/{s['net_3pt_points']['median']} |"
        )
    lines += [
        "",
        f"Advance to bracket study: **{report['summary']['advance_to_bracket_study']}**",
        "",
        "This is a friction-viability screen, not a tradeable strategy result.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "logs" / "mnq_pdl_sweep_reclaim_friction"))
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
