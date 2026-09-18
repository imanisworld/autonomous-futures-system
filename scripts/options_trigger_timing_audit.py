"""Read-only comparison of close-classified vs trigger-time Strat evidence.

Fetches historical consolidated 30m/5m bars, reconstructs the existing cov-v0.1
observer result, then independently arms the prior completed bar and resolves
the first lower-timeframe boundary break. Nothing is written unless --out is
supplied. No options chain, account, broker, alert, or execution path is used.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.bar_provider import AlpacaBarProvider, CONSOLIDATED_FEED  # noqa: E402
from alert_ranker.causal_bars import MINUTE_5, MINUTE_30  # noqa: E402
from alert_ranker.config import resolve_alpaca_credentials  # noqa: E402
from alert_ranker.coverage_observer import build_symbol_series, observe_symbol  # noqa: E402
from alert_ranker.session_calendar import nyse_session_for  # noqa: E402
from alert_ranker.trigger_context import alignment_at_trigger  # noqa: E402
from alert_ranker.trigger_time import arm_trigger_setup, resolve_trigger  # noqa: E402
from scripts.options_coverage_observer import fetch_all, sessions_through  # noqa: E402


PRIMARY_20 = (
    "AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ", "AMZN", "GOOGL", "PLTR", "INTC",
    "IWM", "TLT", "JPM", "BAC", "COIN", "XOM", "MRK", "WMT", "NFLX", "GE",
)
INDEX_SYMBOLS = ("SPY", "QQQ")
LOOKBACK_DAYS = 10


def session_days(start: date, end: date) -> list[date]:
    rows: list[date] = []
    cursor = start
    while cursor <= end:
        if nyse_session_for(cursor) is not None:
            rows.append(cursor)
        cursor += timedelta(days=1)
    return rows


def _median(values: Sequence[float]) -> float | None:
    return round(float(statistics.median(values)), 2) if values else None


def _bar_manifest_hash(dataset: dict[str, list[Any]]) -> str:
    rows: list[dict[str, Any]] = []
    for symbol in sorted(dataset):
        for bar in sorted(dataset[symbol], key=lambda item: item.start_utc):
            rows.append(
                {
                    "symbol": symbol,
                    "start": bar.start_utc.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "vwap": bar.vwap,
                }
            )
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    triggered = [row for row in rows if row["trigger_status"] == "TRIGGERED"]
    ambiguous = [row for row in rows if row["trigger_status"] == "AMBIGUOUS"]
    cancelled = [row for row in rows if row["trigger_status"] == "CANCELLED"]
    no_trigger = [row for row in rows if row["trigger_status"] == "NO_TRIGGER"]
    comparable = [row for row in triggered if row["old_family"] is not None]
    changed = [row for row in comparable if row["old_family"] != row["trigger_family"]]
    direction_changed = [row for row in comparable if row["old_direction"] != row["trigger_direction"]]
    missing_old = [row for row in triggered if row["old_family"] is None]
    later_outside = [row for row in triggered if row["final_scenario"] == "outside_bar"]

    latencies = [
        float(row["old_first_sight_latency_minutes"])
        for row in triggered
        if row["old_first_sight_latency_minutes"] is not None
    ]

    context_rows = [row for row in triggered if row["trigger_context"] is not None]
    completed_pass = sum(bool(row["trigger_context"]["completed_alignment_ok"]) for row in context_rows)
    developing_pass = sum(bool(row["trigger_context"]["developing_alignment_ok"]) for row in context_rows)
    old_pass = sum(bool(row["old_alignment_ok"]) for row in comparable)

    transitions = Counter(
        f'{row["old_family"] or "NONE"} -> {row["trigger_family"] or "NONE"}'
        for row in triggered
    )
    trigger_families = Counter(row["trigger_family"] for row in triggered if row["trigger_family"])
    old_families = Counter(row["old_family"] for row in rows if row["old_family"])

    return {
        "armed_watch_bars": len(rows),
        "triggered": len(triggered),
        "ambiguous_first_break": len(ambiguous),
        "cancelled": len(cancelled),
        "no_trigger": len(no_trigger),
        "triggered_with_old_directional_event": len(comparable),
        "triggered_missing_from_old_directional_events": len(missing_old),
        "family_changed_among_comparable": len(changed),
        "direction_changed_among_comparable": len(direction_changed),
        "triggered_bar_later_became_outside": len(later_outside),
        "old_first_sight_latency_minutes": {
            "n": len(latencies),
            "median": _median(latencies),
            "min": round(min(latencies), 2) if latencies else None,
            "max": round(max(latencies), 2) if latencies else None,
        },
        "alignment": {
            "trigger_context_rows": len(context_rows),
            "old_close_alignment_passes_among_comparable": old_pass,
            "completed_only_trigger_time_passes": completed_pass,
            "developing_htf_trigger_time_passes": developing_pass,
        },
        "trigger_family_counts": dict(sorted(trigger_families.items())),
        "old_family_counts_on_armed_watch_bars": dict(sorted(old_families.items())),
        "top_transitions": transitions.most_common(20),
    }


async def audit_day(
    provider: AlpacaBarProvider,
    day: date,
    universe: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session = nyse_session_for(day)
    if session is None:
        return [], {}
    sessions = sessions_through(day, LOOKBACK_DAYS)
    wanted = list(dict.fromkeys([*universe, *INDEX_SYMBOLS]))
    start = sessions[0].open - timedelta(hours=1)
    bars30, errors30 = await fetch_all(provider, wanted, MINUTE_30, start, session.close)
    bars5, errors5 = await fetch_all(provider, wanted, MINUTE_5, session.open, session.close)
    errors = {**errors30, **{key: f"5Min:{value}" for key, value in errors5.items()}}
    if errors:
        bad = ",".join(f"{key}:{value}" for key, value in sorted(errors.items()))
        raise RuntimeError(f"provider errors for {day}: {bad}")

    series = {symbol: build_symbol_series(symbol, bars30.get(symbol, []), sessions) for symbol in wanted}
    spy = series.get("SPY")
    qqq = series.get("QQQ")
    rows: list[dict[str, Any]] = []

    for symbol in universe:
        item = series[symbol]
        if not item.observable:
            continue

        old_events = observe_symbol(
            item,
            session,
            spy=spy,
            qqq=qqq,
            fine_bars=bars5.get(symbol),
            fine_timeframe=MINUTE_5,
        )
        old_by_start = {event.bar_start: event for event in old_events}

        for index, current in enumerate(item.series):
            if current.start_utc.date() != session.open.astimezone(timezone.utc).date():
                continue
            if index < 2:
                continue
            armed = arm_trigger_setup(item.series[:index])
            if armed is None:
                continue

            current_end = current.start_utc + MINUTE_30.delta
            result = resolve_trigger(
                armed,
                bars5.get(symbol, ()),
                lower_timeframe=MINUTE_5,
                watch_start=current.start_utc,
                watch_until=current_end,
            )
            old = old_by_start.get(current.start_utc.isoformat())
            context = None
            if result.status == "TRIGGERED" and result.trigger_bar_start is not None and result.direction:
                context = alignment_at_trigger(
                    item,
                    session,
                    result.trigger_bar_start,
                    direction=result.direction,
                    spy=spy,
                    qqq=qqq,
                )

            latency = None
            if old is not None and result.trigger_bar_start is not None:
                old_sight = datetime.fromisoformat(old.first_sight_at)
                latency = round((old_sight - result.trigger_bar_start).total_seconds() / 60.0, 2)

            rows.append({
                "session_date": day.isoformat(),
                "symbol": symbol,
                "watch_bar_start": current.start_utc.isoformat(),
                "armed_pattern": armed.pattern,
                "boundary_high": armed.boundary_high,
                "boundary_low": armed.boundary_low,
                "trigger_status": result.status,
                "trigger_family": result.family,
                "trigger_subtype": result.subtype,
                "trigger_direction": result.direction,
                "trigger_bar_start": result.trigger_bar_start.isoformat() if result.trigger_bar_start else None,
                "trigger_level": result.trigger_level,
                "invalidation_level": result.invalidation_level,
                "final_scenario": result.final_scenario,
                "opposite_side_broken_later": result.opposite_side_broken_later,
                "old_family": old.family if old else None,
                "old_direction": old.direction if old else None,
                "old_alignment_ok": old.alignment_ok if old else None,
                "old_first_sight_at": old.first_sight_at if old else None,
                "old_first_sight_latency_minutes": latency,
                "trigger_context": asdict(context) if context else None,
            })
    provenance = {
        "session_date": day.isoformat(),
        "feed": CONSOLIDATED_FEED,
        "symbols": sorted(wanted),
        "bars30_sha256": _bar_manifest_hash(bars30),
        "bars5_sha256": _bar_manifest_hash(bars5),
        "bars30_count": sum(len(items) for items in bars30.values()),
        "bars5_count": sum(len(items) for items in bars5.values()),
    }
    return rows, provenance


async def run(args: argparse.Namespace) -> int:
    universe = tuple(
        symbol.strip().upper()
        for symbol in (args.symbols.split(",") if args.symbols else PRIMARY_20)
        if symbol.strip()
    )
    api_key, secret_key = resolve_alpaca_credentials()
    if not api_key or not secret_key:
        raise SystemExit("Alpaca credentials not configured")
    provider = AlpacaBarProvider(
        base_url=os.environ.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/"),
        api_key=api_key,
        secret_key=secret_key,
        feed=CONSOLIDATED_FEED,
    )

    all_rows: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for day in session_days(date.fromisoformat(args.from_date), date.fromisoformat(args.to_date)):
        day_rows, day_provenance = await audit_day(provider, day, universe)
        all_rows.extend(day_rows)
        if day_provenance:
            provenance.append(day_provenance)

    report = {
        "audit_id": "OPTIONS_STRAT_TRIGGER_TIMING_AUDIT",
        "audit_version": "trigger-audit-v0.1",
        "from_date": args.from_date,
        "to_date": args.to_date,
        "universe": list(universe),
        "row_count": len(all_rows),
        "provenance": provenance,
        "summary": summarize(all_rows),
        "rows": all_rows if args.include_rows else None,
        "claims_not_made": [
            "option profitability",
            "strategy promotion",
            "runtime real-time observability",
            "contract selection validity at trigger time",
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
    parser = argparse.ArgumentParser(description="Compare delayed close classification with causal trigger-time Strat evidence")
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--symbols", help="comma-separated symbols; default frozen primary 20")
    parser.add_argument("--out", help="optional JSON output path")
    parser.add_argument("--include-rows", action="store_true")
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
