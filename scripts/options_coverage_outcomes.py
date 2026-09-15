"""Episode outcome study: causal forward movement for deduped 30m coverage episodes.

    python scripts/options_coverage_outcomes.py --from 2026-09-09 --to 2026-09-15 --out logs/coverage_outcomes

READ-ONLY RESEARCH.  Reads the coverage observer sqlite, reduces it with the
#579 episode reducer, fetches regular-session 5Min bars for the sessions and
symbols that have episodes (5m is used only to resolve the forward path --
never to discover or redefine a setup), measures both entry views against both
stored target geometries, and writes:

    <out>/outcomes_<from>_<to>.json      machine-readable summary + episodes
    <out>/outcomes_<from>_<to>.csv       episode-level outcome table
    <out>/outcomes_<from>_<to>.md        short report

Identity OPTIONS_COVERAGE_OUTCOMES / out-v0.1.  Never writes to the observer
or V1 databases.  Needs Alpaca data credentials for the 5Min fetch.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.bar_provider import CONSOLIDATED_FEED, AlpacaBarProvider  # noqa: E402
from alert_ranker.causal_bars import MINUTE_5  # noqa: E402
from alert_ranker.config import resolve_alpaca_credentials  # noqa: E402
from alert_ranker.coverage_episodes import Episode, reduce_events  # noqa: E402
from alert_ranker.coverage_observer import OBSERVER_VERSION  # noqa: E402
from alert_ranker.coverage_outcomes import (  # noqa: E402
    GEOMETRIES,
    OUTCOME_ID,
    OUTCOME_VERSION,
    EpisodeOutcome,
    measure_episode,
    summarize_outcomes,
)
from alert_ranker.session_calendar import nyse_session_for  # noqa: E402
from scripts.options_coverage_observer import fetch_all  # noqa: E402

DEFAULT_SQLITE = ROOT / "logs" / "options_coverage_observer.sqlite"
DEFAULT_OUT = ROOT / "logs" / "coverage_outcomes"


def load_events(path: Path, date_from: str, date_to: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT row_json FROM coverage_events WHERE observer_version=? AND session_date BETWEEN ? AND ? ORDER BY symbol, bar_start",
            (OBSERVER_VERSION, date_from, date_to),
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(r[0]) for r in rows]


async def measure_all(episodes: Sequence[Episode], provider: AlpacaBarProvider, pause_seconds: float) -> tuple[list[EpisodeOutcome], dict[str, str]]:
    by_session: dict[str, list[Episode]] = defaultdict(list)
    for ep in episodes:
        by_session[ep.session_date].append(ep)
    outcomes: list[EpisodeOutcome] = []
    errors: dict[str, str] = {}
    for session_date in sorted(by_session):
        session = nyse_session_for(date.fromisoformat(session_date))
        if session is None:
            continue
        symbols = sorted({ep.symbol for ep in by_session[session_date]})
        bars, errs = await fetch_all(provider, symbols, MINUTE_5, session.open, session.close)
        for symbol, reason in errs.items():
            errors[f"{session_date}:{symbol}"] = reason
        for ep in by_session[session_date]:
            outcomes.append(measure_episode(ep, session.open, session.close, bars.get(ep.symbol, [])))
        await asyncio.sleep(pause_seconds)
    return outcomes, errors


CSV_COLUMNS = [
    "symbol", "session_date", "family", "direction", "first_bar_start", "n_events", "v1_supported",
    "entry_trigger", "invalidation", "structural_risk", "first_sight_at", "first_sight_after_close", "first_sight_price",
    "blind_window_extension_r", "alignment_ok", "alignment_failures", "gate_bucket_nearest", "gate_bucket_floor",
    "clean", "rr_quality_flags", "quality_flags",
]
VIEW_COLUMNS = [
    "entry_at", "target_1", "target_1_r", "target_2", "target_2_r", "outcome", "first_decisive_event", "mfe_r", "mae_r", "close_r",
    "max_favorable_r_before_invalidation", "max_adverse_r_before_target", "hit_0_5r_at", "hit_1_0r_at", "hit_1_5r_at", "hit_2_0r_at",
    "target_1_hit_at", "target_2_hit_at", "invalidation_hit_at", "minutes_to_1r", "minutes_to_target_1", "minutes_to_invalidation", "flags",
]


def write_csv(path: Path, outcomes: Sequence[EpisodeOutcome]) -> None:
    header = list(CSV_COLUMNS)
    for view in ("mechanical", "first_sight"):
        for geometry in GEOMETRIES:
            header.extend(f"{view}:{geometry}:{c}" for c in VIEW_COLUMNS)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for o in outcomes:
            row = o.to_row()
            line: list[Any] = []
            for c in CSV_COLUMNS:
                value = row[c]
                line.append(",".join(value) if isinstance(value, list) else value)
            for view in ("mechanical", "first_sight"):
                for geometry in GEOMETRIES:
                    pv = o.view(view, geometry)
                    for c in VIEW_COLUMNS:
                        value = getattr(pv, c) if pv else None
                        line.append(",".join(value) if isinstance(value, list) else value)
            writer.writerow(line)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _family_table(summary: dict[str, Any], population: str) -> list[str]:
    lines = [
        f"| family | ep | clean | flagged | view:geom | scored | TARGET_FIRST | INVAL_FIRST | UNRES | AMBIG | ≥1R | ≥2R | ≥1R % | tgt-first % | med MFE R | med MAE R |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for family, block in sorted(summary["by_family"].items(), key=lambda kv: -kv[1]["episodes"]):
        first = True
        for view in ("mechanical", "first_sight"):
            for geometry in GEOMETRIES:
                s = block[population][f"{view}:{geometry}"]
                lead = f"| {family} | {block['episodes']} | {block['clean_episodes']} | {block['quality_flagged_episodes']} |" if first else "|  |  |  |  |"
                first = False
                lines.append(
                    f"{lead} {view}:{geometry} | {s['scored']} | {s['TARGET_FIRST']} | {s['INVALIDATION_FIRST']} | {s['UNRESOLVED_AT_CLOSE']} | {s['AMBIGUOUS']} | {s['ge_1r']} | {s['ge_2r']} | {_fmt(s['ge_1r_rate_pct'])} | {_fmt(s['target_first_rate_pct'])} | {_fmt(s['mfe_r']['median'])} | {_fmt(s['mae_r']['median'])} |"
                )
    return lines


def _timing_table(summary: dict[str, Any], population: str) -> list[str]:
    lines = [
        "| family | ep | blind-window R p25 / median / p75 | % lose ≥0.25R pre-sight | % gain ≥0.25R pre-sight | mech ≥1R eps | % still ≥1R at sight | mech ≥1R % → sight ≥1R % (nearest) | mech tgt-first % → sight tgt-first % (nearest) | same (floor) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for family, block in sorted(summary["by_family"].items(), key=lambda kv: -kv[1]["episodes"]):
        t = block[population]["timing"]
        tl = block[population]["timing_loss"]
        q = t["blind_window_extension_r"]
        lines.append(
            f"| {family} | {block['episodes']} | {_fmt(q['p25'])} / {_fmt(q['median'])} / {_fmt(q['p75'])} | {_fmt(t['pct_losing_ge_0_25r_before_first_sight'])} | {_fmt(t['pct_gaining_ge_0_25r_before_first_sight'])} | {t['mechanical_ge1r_episodes']} | {_fmt(t['pct_mechanical_ge1r_still_ge1r_at_first_sight'])} | {_fmt(tl['nearest']['mechanical_ge1r_rate_pct'])} → {_fmt(tl['nearest']['first_sight_ge1r_rate_pct'])} | {_fmt(tl['nearest']['mechanical_target_first_rate_pct'])} → {_fmt(tl['nearest']['first_sight_target_first_rate_pct'])} | {_fmt(tl['floor']['mechanical_target_first_rate_pct'])} → {_fmt(tl['floor']['first_sight_target_first_rate_pct'])} |"
        )
    return lines


def _group_table(summary: dict[str, Any], key: str, population: str, label: str) -> list[str]:
    lines = [
        f"| {label} | ep | clean | mech ≥1R % (near) | sight ≥1R % (near) | mech tgt-first % (near) | sight tgt-first % (near) | mech tgt-first % (floor) | sight tgt-first % (floor) | med MFE R mech | med MFE R sight |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, block in summary[key].items():
        p = block[population]
        lines.append(
            f"| {name} | {block['episodes']} | {block['clean_episodes']} | {_fmt(p['mechanical:nearest']['ge_1r_rate_pct'])} | {_fmt(p['first_sight:nearest']['ge_1r_rate_pct'])} | {_fmt(p['mechanical:nearest']['target_first_rate_pct'])} | {_fmt(p['first_sight:nearest']['target_first_rate_pct'])} | {_fmt(p['mechanical:floor']['target_first_rate_pct'])} | {_fmt(p['first_sight:floor']['target_first_rate_pct'])} | {_fmt(p['mechanical:nearest']['mfe_r']['median'])} | {_fmt(p['first_sight:nearest']['mfe_r']['median'])} |"
        )
    return lines


def write_markdown(path: Path, summary: dict[str, Any], date_from: str, date_to: str, errors: dict[str, str]) -> None:
    total = summary["total"]
    lines = [
        f"# {OUTCOME_ID} {OUTCOME_VERSION} — {date_from} → {date_to}",
        "",
        "READ-ONLY research over the coverage observer (cov-v0.1) + episode reducer (ep-v0.1). Same-session horizon; 5m bars used only to resolve the forward path; no intrabar ordering assumed; R normalised so favourable is positive; structural risk is the R denominator for both views.",
        "",
        f"Episodes {total['episodes']} · clean {total['clean_episodes']} · flagged {total['quality_flagged_episodes']} · quality flags {json.dumps(summary['quality_flag_counts'], sort_keys=True)}",
        f"Provider errors during 5m fetch: {len(errors)}" + (f" — {json.dumps(errors, sort_keys=True)[:400]}" if errors else ""),
        "",
        "## By family — CLEAN episodes",
        *_family_table(summary, "clean"),
        "",
        "## By family — ALL episodes (flagged included)",
        *_family_table(summary, "all"),
        "",
        "## Timing loss (blind window) — CLEAN episodes",
        *_timing_table(summary, "clean"),
        "",
        "## By session (clean)",
        *_group_table(summary, "by_session", "clean", "session"),
        "",
        "## By direction (clean)",
        *_group_table(summary, "by_direction", "clean", "direction"),
        "",
        "## By market alignment (clean)",
        *_group_table(summary, "by_alignment", "clean", "alignment"),
        "",
        "## By gate-stage bucket, nearest geometry (clean)",
        *_group_table(summary, "by_gate_bucket_nearest", "clean", "bucket"),
        "",
        "## By gate-stage bucket, floor geometry (clean)",
        *_group_table(summary, "by_gate_bucket_floor", "clean", "bucket"),
        "",
        "## By symbol (clean) — top 25 by episodes",
    ]
    top = dict(sorted(summary["by_symbol"].items(), key=lambda kv: -kv[1]["episodes"])[:25])
    lines.extend(_group_table({"by_symbol": top}, "by_symbol", "clean", "symbol"))
    path.write_text("\n".join(lines) + "\n")


async def run(args: argparse.Namespace) -> int:
    sqlite_path = Path(args.sqlite or os.environ.get("OPTIONS_COVERAGE_SQLITE_PATH") or DEFAULT_SQLITE)
    events = load_events(sqlite_path, args.date_from, args.date_to)
    episodes = reduce_events(events)
    if not episodes:
        print("no episodes in range", file=sys.stderr)
        return 2

    api_key, secret_key = resolve_alpaca_credentials()
    if not api_key or not secret_key:
        print("Alpaca credentials not configured", file=sys.stderr)
        return 2
    provider = AlpacaBarProvider(
        base_url=os.environ.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/"),
        api_key=api_key,
        secret_key=secret_key,
        feed=CONSOLIDATED_FEED,
    )
    outcomes, errors = await measure_all(episodes, provider, args.pause_seconds)
    summary = summarize_outcomes(outcomes)
    summary["date_from"], summary["date_to"] = args.date_from, args.date_to
    summary["raw_events"], summary["episodes"] = len(events), len(episodes)
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    summary["provider_errors"] = errors

    out = Path(args.out or DEFAULT_OUT)
    out.mkdir(parents=True, exist_ok=True)
    stem = out / f"outcomes_{args.date_from}_{args.date_to}"
    stem.with_suffix(".json").write_text(json.dumps({"summary": summary, "episodes": [o.to_row() for o in outcomes]}, indent=1, sort_keys=True))
    write_csv(stem.with_suffix(".csv"), outcomes)
    write_markdown(stem.with_suffix(".md"), summary, args.date_from, args.date_to, errors)
    print(f"{OUTCOME_ID} {OUTCOME_VERSION}: {len(events)} events → {len(episodes)} episodes → {len(outcomes)} outcomes; clean {summary['total']['clean_episodes']}; errors {len(errors)}")
    print(f"wrote {stem}.json / .csv / .md")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only coverage episode outcome study")
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--sqlite")
    parser.add_argument("--out")
    parser.add_argument("--pause-seconds", type=float, default=3.0, help="pause between sessions to respect the provider rate limit")
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
