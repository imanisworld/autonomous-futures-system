#!/usr/bin/env python3
"""Offline trigger-time comparison against frozen observer + hashed SIP refetch.

This script performs no provider/network calls. It verifies every snapshot file
against its manifest, verifies the frozen observer SQLite SHA-256 bound into
that manifest, then compares the frozen cov-v0.1 close-classified event with
the first causal 5-minute boundary-break result from trigger_time.py.

The SIP bars are a NEW refetch study epoch, not original frozen provider bytes.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.causal_bars import Bar, MINUTE_5, MINUTE_30
from alert_ranker.coverage_observer import OBSERVER_VERSION, build_symbol_series
from alert_ranker.session_calendar import nyse_session_for
from alert_ranker.trigger_time import arm_trigger_setup, resolve_trigger
from scripts.options_coverage_observer import sessions_through

AUDIT_ID = "OPTIONS_TRIGGER_SNAPSHOT_COMPARISON"
AUDIT_VERSION = "trigger-compare-v0.1"

PRIMARY_20 = (
    "AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ", "AMZN", "GOOGL", "PLTR", "INTC",
    "IWM", "TLT", "JPM", "BAC", "COIN", "XOM", "MRK", "WMT", "NFLX", "GE",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_bar(row: Mapping[str, Any], expected_timeframe: str) -> tuple[str, Bar]:
    timeframe = str(row.get("timeframe") or "")
    if timeframe != expected_timeframe:
        raise ValueError(
            f"snapshot timeframe mismatch: expected {expected_timeframe}, got {timeframe}"
        )
    symbol = str(row.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("snapshot bar symbol missing")
    raw_start = row.get("start")
    if not isinstance(raw_start, str):
        raise ValueError("snapshot bar start missing")
    start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("snapshot bar start must be timezone-aware")
    return symbol, Bar(
        start=start,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        vwap=(float(row["vwap"]) if row.get("vwap") is not None else None),
    )


def load_snapshot_file(
    path: Path,
    *,
    expected_sha256: str,
    expected_timeframe: str,
) -> dict[str, list[Bar]]:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError(f"snapshot file hash mismatch: {path.name}")
    out: dict[str, list[Bar]] = {}
    seen: set[tuple[str, str]] = set()
    for line in payload.splitlines():
        if not line:
            continue
        row = json.loads(line)
        symbol, bar = _parse_bar(row, expected_timeframe)
        key = (symbol, bar.start_utc.isoformat())
        if key in seen:
            raise ValueError(f"duplicate snapshot bar key: {key}")
        seen.add(key)
        out.setdefault(symbol, []).append(bar)
    for bars in out.values():
        bars.sort(key=lambda item: item.start_utc)
    return out


def load_verified_snapshot(
    snapshot_dir: Path,
    observer_db: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, dict[str, list[Bar]]]]]:
    manifest_path = snapshot_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("snapshot_id") != "OPTIONS_TRIGGER_BAR_SNAPSHOT":
        raise ValueError("unexpected snapshot identity")
    if manifest.get("study_epoch") != "NEW_REFETCH_NOT_FROZEN_COV_V0_1":
        raise ValueError("snapshot epoch is not explicit new-refetch evidence")

    observer_ref = manifest.get("frozen_observer_reference") or {}
    expected_observer_sha = str(observer_ref.get("sha256") or "")
    if not expected_observer_sha:
        raise ValueError("snapshot manifest does not bind frozen observer DB")
    if _sha256_file(observer_db) != expected_observer_sha:
        raise ValueError("frozen observer DB hash mismatch")

    loaded: dict[str, dict[str, dict[str, list[Bar]]]] = {}
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("snapshot manifest files missing")
    for item in files:
        if not isinstance(item, Mapping):
            raise ValueError("snapshot manifest file row invalid")
        query = item.get("query") or {}
        session_date = str(query.get("session_date") or "")
        timeframe = str(query.get("timeframe") or "")
        name = str(item.get("path") or "")
        expected_sha = str(item.get("sha256") or "")
        if timeframe not in {MINUTE_30.name, MINUTE_5.name}:
            raise ValueError(f"unexpected snapshot timeframe: {timeframe}")
        bars = load_snapshot_file(
            snapshot_dir / name,
            expected_sha256=expected_sha,
            expected_timeframe=timeframe,
        )
        actual_counts = {
            symbol: len(rows) for symbol, rows in sorted(bars.items())
        }
        actual_rows = sum(actual_counts.values())
        if actual_rows != int(item.get("rows", -1)):
            raise ValueError(f"snapshot row-count mismatch: {name}")
        if actual_counts != dict(item.get("rows_by_symbol") or {}):
            raise ValueError(f"snapshot symbol-count mismatch: {name}")
        slot = loaded.setdefault(session_date, {})
        if timeframe in slot:
            raise ValueError(f"duplicate snapshot query: {session_date} {timeframe}")
        slot[timeframe] = bars

    return manifest, loaded


def load_old_events(
    observer_db: Path,
    *,
    from_date: str,
    to_date: str,
    symbols: Sequence[str],
) -> dict[tuple[str, str], dict[str, Any]]:
    conn = sqlite3.connect(f"file:{observer_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in symbols)
        rows = conn.execute(
            f"""
            SELECT row_json
            FROM coverage_events
            WHERE observer_version=?
              AND session_date BETWEEN ? AND ?
              AND symbol IN ({placeholders})
            ORDER BY symbol, bar_start
            """,
            (OBSERVER_VERSION, from_date, to_date, *symbols),
        ).fetchall()
    finally:
        conn.close()

    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        event = json.loads(row["row_json"])
        key = (str(event["symbol"]).upper(), str(event["bar_start"]))
        if key in out:
            raise ValueError(f"duplicate frozen observer event: {key}")
        out[key] = event
    return out


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def compare(
    *,
    manifest: Mapping[str, Any],
    snapshot: Mapping[str, Mapping[str, Mapping[str, Sequence[Bar]]]],
    old_events: Mapping[tuple[str, str], Mapping[str, Any]],
    from_date: str,
    to_date: str,
    symbols: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start_day = date.fromisoformat(from_date)
    end_day = date.fromisoformat(to_date)
    day = start_day
    while day <= end_day:
        session = nyse_session_for(day)
        if session is None:
            day = day + timedelta(days=1)
            continue
        day_key = day.isoformat()
        day_snapshot = snapshot.get(day_key)
        if not day_snapshot:
            raise ValueError(f"snapshot missing session: {day_key}")
        bars30 = day_snapshot.get(MINUTE_30.name)
        bars5 = day_snapshot.get(MINUTE_5.name)
        if bars30 is None or bars5 is None:
            raise ValueError(f"snapshot missing timeframe for session: {day_key}")

        sessions = sessions_through(day, 10)
        for symbol in symbols:
            series = build_symbol_series(symbol, bars30.get(symbol, ()), sessions)
            if not series.observable:
                raise ValueError(f"refetch series not observable: {day_key} {symbol}")
            current_session = tuple(series.by_session.get(day, ()))
            index_by_start = {
                bar.start_utc: index for index, bar in enumerate(series.series)
            }
            if symbol not in bars5:
                raise ValueError(f"refetch fine bars missing: {day_key} {symbol}")
            fine = tuple(bars5[symbol])

            for current in current_session:
                index = index_by_start.get(current.start_utc)
                if index is None or index < 2:
                    continue
                armed = arm_trigger_setup(series.series[:index])
                if armed is None:
                    continue
                # The series is regular-session only. For the first bar of a
                # new session, the logical "next 30m bar" follows an overnight
                # gap rather than starting 30 minutes after the prior session's
                # final bar. Preserve the precursor/pattern/boundaries while
                # anchoring the watch window to the actual current RTH bar.
                watch_armed = replace(
                    armed,
                    armed_at=current.start_utc,
                    watch_until=current.start_utc + MINUTE_30.delta,
                )
                result = resolve_trigger(
                    watch_armed,
                    fine,
                    lower_timeframe=MINUTE_5,
                )
                old = old_events.get((symbol, current.start_utc.isoformat()))
                latency = None
                if (
                    old is not None
                    and result.trigger_bar_start is not None
                    and old.get("first_sight_at")
                ):
                    sight = datetime.fromisoformat(
                        str(old["first_sight_at"]).replace("Z", "+00:00")
                    )
                    latency = (
                        sight.astimezone(timezone.utc) - result.trigger_bar_start
                    ).total_seconds() / 60.0

                trigger_offset = None
                if result.trigger_bar_start is not None:
                    trigger_offset = (
                        result.trigger_bar_start - current.start_utc
                    ).total_seconds() / 60.0

                rows.append({
                    "session_date": day_key,
                    "symbol": symbol,
                    "watch_bar_start": current.start_utc.isoformat(),
                    "armed_pattern": armed.pattern,
                    "precursor_completed_at": armed.armed_at.isoformat(),
                    "trigger_status": result.status,
                    "trigger_family": result.family,
                    "trigger_subtype": result.subtype,
                    "trigger_direction": result.direction,
                    "trigger_bar_start": (
                        result.trigger_bar_start.isoformat()
                        if result.trigger_bar_start is not None else None
                    ),
                    "trigger_offset_minutes": _round(trigger_offset),
                    "trigger_level": result.trigger_level,
                    "invalidation_level": result.invalidation_level,
                    "final_scenario": result.final_scenario,
                    "opposite_side_broken_later": result.opposite_side_broken_later,
                    "trigger_reason_code": result.reason_code,
                    "old_family": old.get("family") if old else None,
                    "old_direction": old.get("direction") if old else None,
                    "old_alignment_ok": old.get("alignment_ok") if old else None,
                    "old_first_sight_at": old.get("first_sight_at") if old else None,
                    "old_first_sight_latency_minutes": _round(latency),
                    "old_entry_trigger": old.get("entry_trigger") if old else None,
                    "old_invalidation": old.get("invalidation") if old else None,
                })
        day = day + timedelta(days=1)
    return rows




def _same_float(left: Any, right: Any) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False

def _stats(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {
        "n": len(values),
        "min": _round(min(values)),
        "median": _round(float(statistics.median(values))),
        "max": _round(max(values)),
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(row["trigger_status"]) for row in rows)
    triggered = [row for row in rows if row["trigger_status"] == "TRIGGERED"]
    comparable = [row for row in triggered if row.get("old_family") is not None]
    missing_old = [row for row in triggered if row.get("old_family") is None]
    missing_old_212 = [
        row for row in missing_old
        if row.get("trigger_family") == "STRAT_212_REVERSAL"
    ]
    changed_family = [
        row for row in comparable
        if row.get("trigger_family") != row.get("old_family")
    ]
    changed_direction = [
        row for row in comparable
        if row.get("trigger_direction") != row.get("old_direction")
    ]

    old_212 = [row for row in rows if row.get("old_family") == "STRAT_212_REVERSAL"]
    old_212_statuses = Counter(str(row["trigger_status"]) for row in old_212)
    old_212_recovered = [
        row for row in old_212
        if row.get("trigger_status") == "TRIGGERED"
        and row.get("trigger_family") == "STRAT_212_REVERSAL"
        and row.get("trigger_direction") == row.get("old_direction")
    ]
    old_212_changed = [
        row for row in old_212
        if row.get("trigger_status") == "TRIGGERED"
        and (
            row.get("trigger_family") != "STRAT_212_REVERSAL"
            or row.get("trigger_direction") != row.get("old_direction")
        )
    ]
    old_212_level_matches = [
        row for row in old_212_recovered
        if _same_float(row.get("trigger_level"), row.get("old_entry_trigger"))
        and _same_float(row.get("invalidation_level"), row.get("old_invalidation"))
    ]
    old_212_offsets = [
        float(row["trigger_offset_minutes"])
        for row in old_212_recovered
        if row.get("trigger_offset_minutes") is not None
    ]
    old_212_latencies = [
        float(row["old_first_sight_latency_minutes"])
        for row in old_212_recovered
        if row.get("old_first_sight_latency_minutes") is not None
    ]

    latencies = [
        float(row["old_first_sight_latency_minutes"])
        for row in triggered
        if row.get("old_first_sight_latency_minutes") is not None
    ]
    offsets = [
        float(row["trigger_offset_minutes"])
        for row in triggered
        if row.get("trigger_offset_minutes") is not None
    ]

    return {
        "armed_watch_bars": len(rows),
        "status_counts": dict(sorted(statuses.items())),
        "triggered": len(triggered),
        "triggered_missing_from_frozen_directional_events": len(missing_old),
        "missing_frozen_event_trigger_family_counts": dict(sorted(Counter(
            str(row["trigger_family"]) for row in missing_old if row.get("trigger_family")
        ).items())),
        "missing_frozen_event_final_scenario_counts": dict(sorted(Counter(
            str(row["final_scenario"]) for row in missing_old
        ).items())),
        "family_changed_among_comparable": len(changed_family),
        "family_change_transitions": dict(sorted(Counter(
            f'{row["old_family"]} -> {row["trigger_family"]}'
            for row in changed_family
        ).items())),
        "direction_changed_among_comparable": len(changed_direction),
        "trigger_family_counts": dict(sorted(Counter(
            str(row["trigger_family"]) for row in triggered if row.get("trigger_family")
        ).items())),
        "trigger_offset_minutes": _stats(offsets),
        "old_first_sight_latency_from_trigger_bar_start_minutes": _stats(latencies),
        "triggered_bar_later_became_outside": sum(
            row.get("final_scenario") == "outside_bar" for row in triggered
        ),
        "frozen_212_reversal": {
            "old_rows": len(old_212),
            "trigger_time_total_reversal_rows": sum(
                row.get("trigger_status") == "TRIGGERED"
                and row.get("trigger_family") == "STRAT_212_REVERSAL"
                for row in rows
            ),
            "additional_trigger_time_reversals_absent_frozen_close_events": len(
                missing_old_212
            ),
            "additional_reversals_that_later_became_outside": sum(
                row.get("final_scenario") == "outside_bar"
                for row in missing_old_212
            ),
            "trigger_status_counts": dict(sorted(old_212_statuses.items())),
            "recovered_same_family_and_direction": len(old_212_recovered),
            "exact_trigger_and_invalidation_level_matches": len(old_212_level_matches),
            "triggered_changed_family_or_direction": len(old_212_changed),
            "trigger_offset_minutes": _stats(old_212_offsets),
            "trigger_offset_distribution": dict(sorted(Counter(old_212_offsets).items())),
            "old_first_sight_latency_from_trigger_bar_start_minutes": _stats(
                old_212_latencies
            ),
            "ambiguous": sum(row.get("trigger_status") == "AMBIGUOUS" for row in old_212),
            "later_became_outside": sum(
                row.get("final_scenario") == "outside_bar" for row in old_212
            ),
            "changed_examples": [
                {
                    "session_date": row["session_date"],
                    "symbol": row["symbol"],
                    "watch_bar_start": row["watch_bar_start"],
                    "old_direction": row.get("old_direction"),
                    "trigger_family": row.get("trigger_family"),
                    "trigger_direction": row.get("trigger_direction"),
                    "trigger_reason_code": row.get("trigger_reason_code"),
                }
                for row in old_212_changed[:20]
            ],
        },
    }


def run(args: argparse.Namespace) -> int:
    snapshot_dir = Path(args.snapshot_dir).resolve()
    observer_db = Path(args.observer_db).resolve()
    symbols = tuple(
        dict.fromkeys(
            part.strip().upper()
            for part in (args.symbols.split(",") if args.symbols else PRIMARY_20)
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
    rows = compare(
        manifest=manifest,
        snapshot=snapshot,
        old_events=old_events,
        from_date=args.from_date,
        to_date=args.to_date,
        symbols=symbols,
    )
    observer_sha = _sha256_file(observer_db)
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
        "observer_sha256": observer_sha,
        "frozen_observer_version": OBSERVER_VERSION,
        "frozen_old_event_rows": len(old_events),
        "summary": summarize(rows),
        "rows": rows if args.include_rows else None,
        "claims_not_made": [
            "byte identity with the original cov-v0.1 provider responses",
            "trigger-time market-alignment parity",
            "exact intrabar trigger timestamp inside the first 5-minute crossing bar",
            "option-contract selection at trigger time",
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
    parser.add_argument("--symbols", help="comma-separated symbols; defaults to primary 20")
    parser.add_argument("--out", help="optional JSON report path")
    parser.add_argument("--include-rows", action="store_true")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
