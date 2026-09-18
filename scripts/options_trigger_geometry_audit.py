"""Audit family-specific Strat geometry against the generic target finder.

Consumes a frozen trigger-time SIP bar snapshot. It performs no provider fetch,
account/broker access, scanner activation, alerting, or execution.

The audit asks a narrow question: when an external Strat rule supplies a
specific magnitude/stop, does the existing generic structural target geometry
match it? Missing family rules stay unresolved rather than being invented.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.causal_bars import MINUTE_5, MINUTE_30, Bar  # noqa: E402
from alert_ranker.coverage_observer import build_symbol_series, structure_levels  # noqa: E402
from alert_ranker.session_calendar import nyse_session_for  # noqa: E402
from alert_ranker.trigger_geometry import geometry_for_trigger  # noqa: E402
from alert_ranker.trigger_time import arm_trigger_setup, resolve_trigger  # noqa: E402
from options_manager.levels import LevelFinderInputs, find_targets  # noqa: E402
from scripts.options_coverage_observer import sessions_through  # noqa: E402

AUDIT_ID = "OPTIONS_STRAT_TRIGGER_GEOMETRY_AUDIT"
AUDIT_VERSION = "geometry-v0.1"

SOURCE_RULES = {
    "reversal_geometry": "https://thestrat.ai/docs/types-of-reversals/",
    "direct_3_2": "https://thestrat.ai/docs/3-2/",
    "continuation_architecture": "https://thestrat.ai/docs/trading-continuation/",
    "2_2_2_run_context": "https://thestrat.ai/docs/2-2-2-continuation/",
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verify_snapshot(
    snapshot_dir: Path,
    *,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    manifest_path = snapshot_dir / "manifest.json"
    payload = manifest_path.read_bytes()
    actual_manifest_sha = _sha256(payload)
    if expected_manifest_sha256 and actual_manifest_sha != expected_manifest_sha256:
        raise ValueError(
            f"manifest sha256 mismatch: expected={expected_manifest_sha256} actual={actual_manifest_sha}"
        )
    manifest = json.loads(payload)
    if manifest.get("snapshot_id") != "OPTIONS_TRIGGER_BAR_SNAPSHOT":
        raise ValueError("unsupported snapshot_id")
    if manifest.get("snapshot_version") != "trigger-bars-v0.1":
        raise ValueError("unsupported snapshot_version")
    if manifest.get("feed") != "sip":
        raise ValueError("snapshot feed must be sip")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("snapshot files missing")
    for entry in files:
        path = snapshot_dir / str(entry["path"])
        data = path.read_bytes()
        if _sha256(data) != entry.get("sha256"):
            raise ValueError(f"snapshot file sha256 mismatch: {path.name}")
        rows = sum(1 for line in data.splitlines() if line.strip())
        if rows != entry.get("rows"):
            raise ValueError(f"snapshot row count mismatch: {path.name}")
    manifest["_verified_manifest_sha256"] = actual_manifest_sha
    return manifest


def _event_key(
    *,
    symbol: str,
    session_date: str,
    bar_start: str,
    direction: str,
    entry: float,
    invalidation: float,
) -> tuple[str, str, str, str, float, float]:
    parsed = datetime.fromisoformat(str(bar_start).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("bar_start must be timezone-aware")
    return (
        str(symbol).upper(),
        str(session_date),
        parsed.astimezone(timezone.utc).isoformat(),
        str(direction),
        round(float(entry), 9),
        round(float(invalidation), 9),
    )


def frozen_212_reversal_keys(
    observer_db: Path,
    *,
    expected_sha256: str,
    symbols: Sequence[str],
    from_date: str,
    to_date: str,
) -> set[tuple[str, str, str, str, float, float]]:
    payload = observer_db.read_bytes()
    actual = _sha256(payload)
    if actual != expected_sha256:
        raise ValueError(
            f"observer db sha256 mismatch: expected={expected_sha256} actual={actual}"
        )
    wanted = {str(symbol).upper() for symbol in symbols}
    conn = sqlite3.connect(f"file:{observer_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT symbol, session_date, bar_start, direction, entry_trigger, invalidation
        FROM coverage_events
        WHERE observer_version = ?
          AND family = ?
          AND session_date BETWEEN ? AND ?
        """,
        ("cov-v0.1", "STRAT_212_REVERSAL", from_date, to_date),
    ).fetchall()
    conn.close()
    return {
        _event_key(
            symbol=row["symbol"],
            session_date=row["session_date"],
            bar_start=row["bar_start"],
            direction=row["direction"],
            entry=row["entry_trigger"],
            invalidation=row["invalidation"],
        )
        for row in rows
        if str(row["symbol"]).upper() in wanted
    }


def annotate_frozen_212_membership(
    rows: Sequence[dict[str, Any]],
    frozen_keys: set[tuple[str, str, str, str, float, float]],
) -> None:
    for row in rows:
        row["frozen_212_reversal_match"] = False
        if row.get("family") != "STRAT_212_REVERSAL":
            continue
        key = _event_key(
            symbol=row["symbol"],
            session_date=row["session_date"],
            bar_start=row["watch_bar_start"],
            direction=row["direction"],
            entry=row["entry"],
            invalidation=row["generic_stop"],
        )
        row["frozen_212_reversal_match"] = key in frozen_keys


def _bar_from_row(row: Mapping[str, Any]) -> Bar:
    if row.get("timeframe") not in {"30Min", "5Min"}:
        raise ValueError("unsupported bar timeframe")
    return Bar(
        start=datetime.fromisoformat(str(row["start"])),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        vwap=None if row.get("vwap") is None else float(row["vwap"]),
    )


def load_snapshot_file(path: Path) -> dict[str, list[Bar]]:
    out: dict[str, list[Bar]] = defaultdict(list)
    for raw in path.read_bytes().splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError(f"missing symbol in {path.name}")
        out[symbol].append(_bar_from_row(row))
    return {symbol: sorted(bars, key=lambda bar: bar.start_utc) for symbol, bars in out.items()}


def _target_result(
    *,
    direction: str,
    entry: float,
    invalidation: float,
    resistance: Sequence[float],
    support: Sequence[float],
    floor: float | None,
):
    kwargs: dict[str, Any] = {}
    if floor is not None:
        kwargs["min_target_rr"] = floor
    return find_targets(
        LevelFinderInputs(
            direction="CALL" if direction == "LONG" else "PUT",
            entry=entry,
            underlying_invalidation=invalidation,
            resistance_levels=resistance,
            support_levels=support,
            **kwargs,
        )
    )


def _relation(entry: float, canonical: float | None, generic: float | None) -> str:
    if canonical is None:
        return "CANONICAL_UNRESOLVED"
    if generic is None:
        return "GENERIC_INVALID"
    if math.isclose(canonical, generic, rel_tol=0.0, abs_tol=1e-9):
        return "MATCH"
    canonical_distance = abs(canonical - entry)
    generic_distance = abs(generic - entry)
    return "BEFORE_CANONICAL" if generic_distance < canonical_distance else "BEYOND_CANONICAL"


def _median(values: Sequence[float]) -> float | None:
    return round(float(statistics.median(values)), 4) if values else None


def _walk_target_stop(
    *,
    direction: str,
    entry: float,
    target: float | None,
    stop: float | None,
    trigger_bar_start: datetime | None,
    session_close: datetime,
    bars: Sequence[Bar],
) -> str | None:
    if target is None or stop is None or trigger_bar_start is None:
        return None
    if math.isclose(target, entry, rel_tol=0.0, abs_tol=1e-9):
        return "TARGET_CONSUMED_AT_ENTRY"
    entry_side_valid = target > entry if direction == "LONG" else target < entry
    if not entry_side_valid:
        return "INVALID_GEOMETRY"
    ordered = [
        bar
        for bar in bars
        if trigger_bar_start <= bar.start_utc < session_close
    ]
    for bar in ordered:
        target_hit = bar.high >= target if direction == "LONG" else bar.low <= target
        stop_hit = bar.low <= stop if direction == "LONG" else bar.high >= stop
        if target_hit and stop_hit:
            return "AMBIGUOUS"
        if target_hit:
            return "TARGET_FIRST"
        if stop_hit:
            return "STOP_FIRST"
    return "UNRESOLVED_AT_CLOSE"


def _family_key(family: str, subtype: str | None) -> str:
    if family == "STRAT_312":
        return f"{family}:{subtype or 'UNSPECIFIED'}"
    return family


def audit_session(
    snapshot_dir: Path,
    day: date,
    symbols: Sequence[str],
) -> list[dict[str, Any]]:
    session = nyse_session_for(day)
    if session is None:
        return []
    bars30 = load_snapshot_file(snapshot_dir / f"{day.isoformat()}_30Min.jsonl")
    bars5 = load_snapshot_file(snapshot_dir / f"{day.isoformat()}_5Min.jsonl")
    sessions = sessions_through(day, 10)
    series = {
        symbol: build_symbol_series(symbol, bars30.get(symbol, ()), sessions)
        for symbol in symbols
    }
    rows: list[dict[str, Any]] = []

    for symbol in symbols:
        item = series[symbol]
        if not item.observable:
            continue
        for index, current in enumerate(item.series):
            if not (session.open <= current.start_utc < session.close):
                continue
            if index < 2:
                continue

            armed = arm_trigger_setup(item.series[:index])
            if armed is None:
                continue
            result = resolve_trigger(
                armed,
                bars5.get(symbol, ()),
                lower_timeframe=MINUTE_5,
                watch_start=current.start_utc,
                watch_until=current.start_utc + MINUTE_30.delta,
            )
            if result.status != "TRIGGERED" or not result.family or not result.direction:
                continue

            parent = item.series[index - 2]
            geometry = geometry_for_trigger(
                armed=armed,
                result=result,
                parent_bar=parent,
            )
            entry = float(result.trigger_level)
            generic_stop = float(result.invalidation_level)
            resistance, support = structure_levels(item.series, index)
            nearest = _target_result(
                direction=result.direction,
                entry=entry,
                invalidation=generic_stop,
                resistance=resistance,
                support=support,
                floor=None,
            )
            floor = _target_result(
                direction=result.direction,
                entry=entry,
                invalidation=generic_stop,
                resistance=resistance,
                support=support,
                floor=1.0,
            )

            nearest_t1 = nearest.target_1 if nearest.status == "VALID" else None
            floor_t1 = floor.target_1 if floor.status == "VALID" else None
            canonical_target = geometry.target
            canonical_target_valid = None
            canonical_target_consumed_at_entry = False
            if canonical_target is not None:
                canonical_target_consumed_at_entry = math.isclose(
                    canonical_target,
                    entry,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
                if not canonical_target_consumed_at_entry:
                    canonical_target_valid = (
                        canonical_target > entry
                        if result.direction == "LONG"
                        else canonical_target < entry
                    )

            canonical_stop_parity = None
            if geometry.stop is not None:
                canonical_stop_parity = math.isclose(
                    geometry.stop,
                    generic_stop,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )

            generic_risk = abs(entry - generic_stop)
            canonical_r = None
            if (
                canonical_target is not None
                and geometry.stop is not None
                and abs(entry - geometry.stop) > 0
            ):
                canonical_r = abs(canonical_target - entry) / abs(entry - geometry.stop)

            walk_bars = bars5.get(symbol, ())
            canonical_outcome = _walk_target_stop(
                direction=result.direction,
                entry=entry,
                target=canonical_target,
                stop=geometry.stop,
                trigger_bar_start=result.trigger_bar_start,
                session_close=session.close,
                bars=walk_bars,
            )
            nearest_outcome = _walk_target_stop(
                direction=result.direction,
                entry=entry,
                target=nearest_t1,
                stop=geometry.stop,
                trigger_bar_start=result.trigger_bar_start,
                session_close=session.close,
                bars=walk_bars,
            )
            floor_outcome = _walk_target_stop(
                direction=result.direction,
                entry=entry,
                target=floor_t1,
                stop=geometry.stop,
                trigger_bar_start=result.trigger_bar_start,
                session_close=session.close,
                bars=walk_bars,
            )
            rows.append(
                {
                    "session_date": day.isoformat(),
                    "symbol": symbol,
                    "watch_bar_start": current.start_utc.isoformat(),
                    "trigger_bar_start": (
                        result.trigger_bar_start.isoformat()
                        if result.trigger_bar_start
                        else None
                    ),
                    "family": result.family,
                    "subtype": result.subtype,
                    "family_key": _family_key(result.family, result.subtype),
                    "direction": result.direction,
                    "armed_pattern": armed.pattern,
                    "entry": entry,
                    "generic_stop": generic_stop,
                    "generic_risk": generic_risk,
                    "generic_risk_pct_of_entry": (
                        generic_risk / abs(entry) * 100.0 if entry else None
                    ),
                    "canonical_geometry_status": geometry.status,
                    "canonical_target": canonical_target,
                    "canonical_target_source": geometry.target_source,
                    "canonical_target_valid": canonical_target_valid,
                    "canonical_target_consumed_at_entry": canonical_target_consumed_at_entry,
                    "canonical_r": canonical_r,
                    "canonical_outcome": canonical_outcome,
                    "canonical_stop": geometry.stop,
                    "canonical_stop_source": geometry.stop_source,
                    "canonical_stop_matches_generic": canonical_stop_parity,
                    "nearest_status": nearest.status,
                    "nearest_reason": nearest.reason_code,
                    "nearest_target_1": nearest_t1,
                    "nearest_relation": _relation(entry, canonical_target, nearest_t1),
                    "nearest_outcome": nearest_outcome,
                    "floor_status": floor.status,
                    "floor_reason": floor.reason_code,
                    "floor_target_1": floor_t1,
                    "floor_relation": _relation(entry, canonical_target, floor_t1),
                    "floor_outcome": floor_outcome,
                    "later_became_outside": result.final_scenario == "outside_bar",
                }
            )
    return rows


def summarize_frozen_212(rows: Sequence[dict[str, Any]], expected_count: int) -> dict[str, Any]:
    subset = [
        row
        for row in rows
        if row.get("family") == "STRAT_212_REVERSAL"
        and row.get("frozen_212_reversal_match") is True
    ]
    values = [
        float(row["canonical_r"])
        for row in subset
        if row.get("canonical_r") is not None
    ]
    return {
        "expected_frozen_rows": expected_count,
        "matched_rows": len(subset),
        "all_expected_rows_matched": len(subset) == expected_count,
        "target_consumed_at_entry_n": sum(
            bool(row.get("canonical_target_consumed_at_entry")) for row in subset
        ),
        "wrong_side_target_n": sum(
            row.get("canonical_target_valid") is False for row in subset
        ),
        "canonical_stop_matches_generic_n": sum(
            row.get("canonical_stop_matches_generic") is True for row in subset
        ),
        "canonical_r_n": len(values),
        "canonical_r_median": _median(values),
        "canonical_r_below_1_n": sum(value < 1.0 for value in values),
        "nearest_relation": dict(Counter(row["nearest_relation"] for row in subset)),
        "floor_relation": dict(Counter(row["floor_relation"] for row in subset)),
        "canonical_outcomes": dict(
            Counter(row["canonical_outcome"] for row in subset if row["canonical_outcome"] is not None)
        ),
        "nearest_outcomes": dict(
            Counter(row["nearest_outcome"] for row in subset if row["nearest_outcome"] is not None)
        ),
        "floor_outcomes": dict(
            Counter(row["floor_outcome"] for row in subset if row["floor_outcome"] is not None)
        ),
    }


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_family: dict[str, Any] = {}
    for key in sorted({row["family_key"] for row in rows}):
        subset = [row for row in rows if row["family_key"] == key]
        target_defined = [row for row in subset if row["canonical_target"] is not None]
        stop_defined = [row for row in subset if row["canonical_stop"] is not None]
        far_side_risk = [
            float(row["generic_risk_pct_of_entry"])
            for row in subset
            if row["canonical_geometry_status"] == "NO_OWN_MAGNITUDE"
            and row["generic_risk_pct_of_entry"] is not None
        ]
        canonical_r = [
            float(row["canonical_r"])
            for row in subset
            if row["canonical_r"] is not None
        ]
        by_family[key] = {
            "n": len(subset),
            "geometry_status": dict(Counter(row["canonical_geometry_status"] for row in subset)),
            "canonical_target_defined_n": len(target_defined),
            "canonical_target_invalid_side_n": sum(
                row["canonical_target_valid"] is False for row in target_defined
            ),
            "canonical_target_consumed_at_entry_n": sum(
                bool(row["canonical_target_consumed_at_entry"]) for row in target_defined
            ),
            "nearest_relation": dict(Counter(row["nearest_relation"] for row in subset)),
            "floor_relation": dict(Counter(row["floor_relation"] for row in subset)),
            "canonical_stop_defined_n": len(stop_defined),
            "canonical_stop_matches_generic_n": sum(
                row["canonical_stop_matches_generic"] is True for row in stop_defined
            ),
            "canonical_r_n": len(canonical_r),
            "canonical_r_median": _median(canonical_r),
            "canonical_r_below_1_n": sum(value < 1.0 for value in canonical_r),
            "canonical_r_zero_n": sum(math.isclose(value, 0.0, abs_tol=1e-12) for value in canonical_r),
            "canonical_outcomes": dict(
                Counter(row["canonical_outcome"] for row in subset if row["canonical_outcome"] is not None)
            ),
            "nearest_outcomes_with_canonical_stop": dict(
                Counter(row["nearest_outcome"] for row in subset if row["nearest_outcome"] is not None)
            ),
            "floor_outcomes_with_canonical_stop": dict(
                Counter(row["floor_outcome"] for row in subset if row["floor_outcome"] is not None)
            ),
            "generic_far_side_stop_risk_pct_median": _median(far_side_risk),
            "later_became_outside_n": sum(bool(row["later_became_outside"]) for row in subset),
        }

    source_defined = [
        row
        for row in rows
        if row["canonical_geometry_status"] in {"DEFINED", "TARGET_ONLY"}
        and row["canonical_target"] is not None
    ]
    nearest = Counter(row["nearest_relation"] for row in source_defined)
    floor = Counter(row["floor_relation"] for row in source_defined)
    return {
        "triggered_rows": len(rows),
        "source_defined_target_rows": len(source_defined),
        "nearest_target_relation_on_source_defined": dict(nearest),
        "floor_target_relation_on_source_defined": dict(floor),
        "families": by_family,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--out")
    parser.add_argument("--include-rows", action="store_true")
    parser.add_argument("--frozen-observer-db")
    parser.add_argument("--expected-observer-sha256")
    args = parser.parse_args(argv)

    snapshot_dir = Path(args.snapshot_dir).resolve()
    manifest = verify_snapshot(
        snapshot_dir,
        expected_manifest_sha256=args.expected_manifest_sha256,
    )
    symbols = tuple(str(value) for value in manifest["symbols"])
    rows: list[dict[str, Any]] = []
    start = date.fromisoformat(str(manifest["from_date"]))
    end = date.fromisoformat(str(manifest["to_date"]))
    cursor = start
    while cursor <= end:
        if nyse_session_for(cursor) is not None:
            rows.extend(audit_session(snapshot_dir, cursor, symbols))
        cursor = cursor.fromordinal(cursor.toordinal() + 1)

    frozen_212_summary = None
    observer_sha256 = None
    if args.frozen_observer_db or args.expected_observer_sha256:
        if not args.frozen_observer_db or not args.expected_observer_sha256:
            raise ValueError(
                "--frozen-observer-db and --expected-observer-sha256 must be supplied together"
            )
        observer_path = Path(args.frozen_observer_db).resolve()
        frozen_keys = frozen_212_reversal_keys(
            observer_path,
            expected_sha256=args.expected_observer_sha256,
            symbols=symbols,
            from_date=manifest["from_date"],
            to_date=manifest["to_date"],
        )
        annotate_frozen_212_membership(rows, frozen_keys)
        frozen_212_summary = summarize_frozen_212(rows, len(frozen_keys))
        observer_sha256 = args.expected_observer_sha256

    report = {
        "audit_id": AUDIT_ID,
        "audit_version": AUDIT_VERSION,
        "snapshot_manifest_sha256": manifest["_verified_manifest_sha256"],
        "snapshot_id": manifest["snapshot_id"],
        "snapshot_version": manifest["snapshot_version"],
        "from_date": manifest["from_date"],
        "to_date": manifest["to_date"],
        "symbols": list(symbols),
        "source_rules": SOURCE_RULES,
        "frozen_observer_sha256": observer_sha256,
        "frozen_212_reversal": frozen_212_summary,
        "summary": summarize(rows),
        "rows": rows if args.include_rows else None,
        "claims_not_made": [
            "strategy profitability",
            "option profitability",
            "family promotion",
            "unresolved stop equivalence",
            "real-time trigger observability",
        ],
    }
    payload = json.dumps(report, sort_keys=True, indent=2)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
