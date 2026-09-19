#!/usr/bin/env python3
"""Network-free trigger-context audit over frozen observer + hashed SIP snapshot.

This is the qualification-safe successor to the exploratory provider-refetch
context audit. It never fetches market data. It verifies and consumes the
snapshot/observer inputs through options_trigger_snapshot_comparison, rebuilds
the causal trigger population, then applies trigger_context.py at the start of
the first crossing 5-minute bucket.

The legacy "developing_*" fields are explicitly reported as a completed-30m
derived HTF proxy. They exclude the trigger-containing 30m interval and are not
claimed to be exact live-at-trigger HTF state.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.coverage_observer import build_symbol_series
from alert_ranker.session_calendar import nyse_session_for
from alert_ranker.trigger_context import (
    COMPLETED_ALIGNMENT_BASIS,
    DEVELOPING_PROXY_BASIS,
    TRIGGER_CONTEXT_CUTOFF_BASIS,
    alignment_at_trigger,
)
from scripts.options_coverage_observer import sessions_through
from scripts.options_trigger_snapshot_comparison import (
    PRIMARY_20,
    compare,
    load_old_events,
    load_verified_snapshot,
)

AUDIT_ID = "OPTIONS_TRIGGER_CONTEXT_SNAPSHOT_AUDIT"
AUDIT_VERSION = "trigger-context-snapshot-v0.1"


def _matrix(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in rows:
        old = bool(item["trigger_row"].get("old_alignment_ok"))
        new = bool(item["context"][key])
        counts[f"{old}->{new}"] += 1
    return dict(sorted(counts.items()))


def _failure_counts(
    rows: Sequence[Mapping[str, Any]],
    key: str,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in rows:
        failures = item["context"].get(key) or []
        label = ",".join(str(value) for value in failures) or "PASS"
        counts[label] += 1
    return dict(sorted(counts.items()))


def summarize_context_rows(
    context_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    all_rows = list(context_rows)
    comparable = [
        item for item in all_rows if item["trigger_row"].get("old_family") is not None
    ]
    missing_old = [
        item for item in all_rows if item["trigger_row"].get("old_family") is None
    ]

    old_212 = [
        item
        for item in comparable
        if item["trigger_row"].get("old_family") == "STRAT_212_REVERSAL"
    ]
    additional_212 = [
        item
        for item in missing_old
        if item["trigger_row"].get("trigger_family") == "STRAT_212_REVERSAL"
    ]

    def count_pass(rows: Sequence[Mapping[str, Any]], key: str) -> int:
        return sum(bool(item["context"][key]) for item in rows)

    return {
        "context_basis": {
            "cutoff": TRIGGER_CONTEXT_CUTOFF_BASIS,
            "completed_alignment": COMPLETED_ALIGNMENT_BASIS,
            "completed_30m_htf_proxy": DEVELOPING_PROXY_BASIS,
            "exact_partial_trigger_bar_included": False,
        },
        "all_triggered": {
            "n": len(all_rows),
            "completed_alignment_passes": count_pass(
                all_rows, "completed_alignment_ok"
            ),
            "completed_30m_htf_proxy_passes": count_pass(
                all_rows, "developing_alignment_ok"
            ),
        },
        "comparable_with_frozen_close_event": {
            "n": len(comparable),
            "old_close_alignment_passes": sum(
                bool(item["trigger_row"].get("old_alignment_ok"))
                for item in comparable
            ),
            "completed_alignment_passes": count_pass(
                comparable, "completed_alignment_ok"
            ),
            "completed_30m_htf_proxy_passes": count_pass(
                comparable, "developing_alignment_ok"
            ),
            "old_to_completed_matrix": _matrix(
                comparable, "completed_alignment_ok"
            ),
            "old_to_completed_30m_proxy_matrix": _matrix(
                comparable, "developing_alignment_ok"
            ),
        },
        "missing_frozen_close_event": {
            "n": len(missing_old),
            "completed_alignment_passes": count_pass(
                missing_old, "completed_alignment_ok"
            ),
            "completed_30m_htf_proxy_passes": count_pass(
                missing_old, "developing_alignment_ok"
            ),
        },
        "frozen_212_reversal": {
            "n": len(old_212),
            "old_close_alignment_passes": sum(
                bool(item["trigger_row"].get("old_alignment_ok"))
                for item in old_212
            ),
            "completed_alignment_passes": count_pass(
                old_212, "completed_alignment_ok"
            ),
            "completed_30m_htf_proxy_passes": count_pass(
                old_212, "developing_alignment_ok"
            ),
            "completed_failure_counts": _failure_counts(
                old_212, "completed_failures"
            ),
            "completed_30m_proxy_failure_counts": _failure_counts(
                old_212, "developing_failures"
            ),
        },
        "additional_212_reversal": {
            "n": len(additional_212),
            "completed_alignment_passes": count_pass(
                additional_212, "completed_alignment_ok"
            ),
            "completed_30m_htf_proxy_passes": count_pass(
                additional_212, "developing_alignment_ok"
            ),
            "rows": [
                {
                    "session_date": item["trigger_row"]["session_date"],
                    "symbol": item["trigger_row"]["symbol"],
                    "direction": item["trigger_row"]["trigger_direction"],
                    "trigger_bar_start": item["trigger_row"]["trigger_bar_start"],
                    "completed_failures": item["context"]["completed_failures"],
                    "completed_30m_proxy_failures": item["context"][
                        "developing_failures"
                    ],
                }
                for item in additional_212
            ],
        },
    }


def build_context_rows(
    *,
    snapshot: Mapping[str, Mapping[str, Mapping[str, Sequence[Any]]]],
    trigger_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    series_by_day: dict[str, dict[str, Any]] = {}
    for day_text, frames in snapshot.items():
        day = date.fromisoformat(day_text)
        session = nyse_session_for(day)
        if session is None:
            continue
        sessions = sessions_through(day, 10)
        bars30 = frames.get("30Min")
        if bars30 is None:
            raise ValueError(f"30Min snapshot missing for {day_text}")
        series_by_day[day_text] = {
            symbol: build_symbol_series(symbol, bars, sessions)
            for symbol, bars in bars30.items()
        }

    out: list[dict[str, Any]] = []
    for row in trigger_rows:
        if row.get("trigger_status") != "TRIGGERED":
            continue
        direction = row.get("trigger_direction")
        trigger_start = row.get("trigger_bar_start")
        if direction not in {"LONG", "SHORT"} or not isinstance(trigger_start, str):
            raise ValueError("triggered row missing direction/trigger bucket")
        day_text = str(row["session_date"])
        series = series_by_day.get(day_text)
        if series is None:
            raise ValueError(f"series missing for session {day_text}")
        symbol = str(row["symbol"]).upper()
        if symbol not in series:
            raise ValueError(f"series missing for {day_text} {symbol}")
        session = nyse_session_for(date.fromisoformat(day_text))
        if session is None:
            raise ValueError(f"session missing for {day_text}")
        context = alignment_at_trigger(
            series[symbol],
            session,
            datetime.fromisoformat(trigger_start.replace("Z", "+00:00")),
            direction=direction,
            spy=series.get("SPY"),
            qqq=series.get("QQQ"),
        )
        out.append({
            "trigger_row": dict(row),
            "context": asdict(context),
        })
    return out


def run(args: argparse.Namespace) -> int:
    snapshot_dir = Path(args.snapshot_dir).resolve()
    observer_db = Path(args.observer_db).resolve()
    symbols = tuple(
        dict.fromkeys(
            part.strip().upper()
            for part in (
                args.symbols.split(",") if args.symbols else PRIMARY_20
            )
            if part.strip()
        )
    )

    manifest, snapshot = load_verified_snapshot(snapshot_dir, observer_db)
    old_events = load_old_events(
        observer_db,
        from_date=args.from_date,
        to_date=args.to_date,
        symbols=symbols,
    )
    trigger_rows = compare(
        manifest=manifest,
        snapshot=snapshot,
        old_events=old_events,
        from_date=args.from_date,
        to_date=args.to_date,
        symbols=symbols,
    )
    context_rows = build_context_rows(
        snapshot=snapshot,
        trigger_rows=trigger_rows,
    )
    manifest_bytes = (snapshot_dir / "manifest.json").read_bytes()
    report = {
        "audit_id": AUDIT_ID,
        "audit_version": AUDIT_VERSION,
        "from_date": args.from_date,
        "to_date": args.to_date,
        "symbols": list(symbols),
        "snapshot_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "snapshot_version": manifest.get("snapshot_version"),
        "snapshot_study_epoch": manifest.get("study_epoch"),
        "observer_sha256": manifest["frozen_observer_reference"]["sha256"],
        "summary": summarize_context_rows(context_rows),
        "rows": context_rows if args.include_rows else None,
        "claims_not_made": [
            "exact live-at-trigger partial 30m/hourly/daily state",
            "intrabar trigger timestamp inside the first crossing 5-minute bar",
            "approved market-alignment policy",
            "option profitability",
            "strategy promotion",
        ],
    }
    payload = json.dumps(report, sort_keys=True, indent=2)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n")
    print(payload)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--observer-db", required=True)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--symbols")
    parser.add_argument("--out")
    parser.add_argument("--include-rows", action="store_true")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
