"""Read-only episode report over the coverage observer sqlite.

    python scripts/options_coverage_episodes.py --from 2026-09-09 --to 2026-09-15
    python scripts/options_coverage_episodes.py --from 2026-09-09 --to 2026-09-15 --json out.json

Opens ``logs/options_coverage_observer.sqlite`` (or ``--sqlite`` /
``OPTIONS_COVERAGE_SQLITE_PATH``) read-only, reduces raw 30m bar-events into
episodes with :mod:`alert_ranker.coverage_episodes`, and prints the
family / symbol / session tables plus the A-D evidence answers.  Writes
nothing unless ``--json`` is given, and never touches V1 or the V1 database.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.coverage_episodes import COLUMNS, answer_questions, reduce_events, summarize  # noqa: E402
from alert_ranker.coverage_observer import OBSERVER_VERSION  # noqa: E402

DEFAULT_SQLITE = ROOT / "logs" / "options_coverage_observer.sqlite"

SHORT = {
    "raw_events": "raw",
    "episodes": "ep",
    "direction_runs": "runs",
    "episodes_with_valid_targets": "tgt",
    "episodes_with_floor_targets": "floor",
    "episodes_market_aligned": "align",
    "episodes_ge1R_at_first_sight_nearest": "ge1R_n",
    "episodes_ge1R_at_first_sight_floor": "ge1R_f",
    "episodes_would_otherwise_qualify_v1_rule": "qual_v1",
    "episodes_would_otherwise_qualify_floor_rule": "qual_fl",
    "after_close_episodes": "close",
    "rr_quality_flagged": "rrflag",
}


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


def _table(title: str, rows: dict[str, dict[str, Any]], label: str, extra: Sequence[str] = ()) -> None:
    cols = list(COLUMNS)
    print(f"\n[{title}]")
    header = f"{label:30s} " + " ".join(f"{SHORT[c]:>7s}" for c in cols) + "".join(f" {e:>7s}" for e in extra)
    print(header)
    for key, info in rows.items():
        line = f"{key:30s} " + " ".join(f"{info[c]:7d}" for c in cols)
        for e in extra:
            value = info.get(e, "")
            line += f" {value:>7}" if not isinstance(value, float) else f" {value:7.2f}"
        print(line)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only coverage episode reducer report")
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--sqlite")
    parser.add_argument("--json", help="Write the full summary + episodes to this JSON file")
    args = parser.parse_args(argv)

    path = Path(args.sqlite or os.environ.get("OPTIONS_COVERAGE_SQLITE_PATH") or DEFAULT_SQLITE)
    events = load_events(path, args.date_from, args.date_to)
    episodes = reduce_events(events)
    summary = summarize(episodes)
    raw_family = Counter(e["family"] for e in events)
    answers = answer_questions(summary, dict(raw_family))

    print(f"coverage episodes {summary['reducer_version']} over observer {OBSERVER_VERSION}  {args.date_from}..{args.date_to}")
    print(f"raw bar-events {len(events)}  →  episodes {len(episodes)}  →  direction runs {summary['total']['direction_runs']}")
    print(f"rr quality flags: {summary['rr_quality_flag_counts']}")

    fam_rows = {}
    for family, info in sorted(summary["by_family"].items(), key=lambda kv: -kv[1]["episodes"]):
        tag = "V1" if info["v1_supported"] else ("REQ" if info["requested"] else "other")
        fam_rows[f"{family} [{tag}]"] = info
    _table("by family", fam_rows, "family", extra=("refire_ratio",))
    _table("by session", summary["by_session"], "session")
    _table("by symbol", summary["by_symbol"], "symbol")

    print("\n[A] common after dedupe (episodes):", answers["A_common_after_dedupe"])
    print("[B] refire ratio raw/episodes (family, ratio, raw, episodes):", answers["B_inflated_by_refire"])
    print("[C] clean episodes per family:")
    for family, info in answers["C_clean_episodes"].items():
        print(f"    {family:30s} {info}")
    print("[D] rank by raw:     ", answers["D_rank_order_raw"])
    print("[D] rank by episodes:", answers["D_rank_order_episodes"])

    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary, "answers": answers, "episodes": [e.to_row() for e in episodes]}, indent=1, sort_keys=True))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
