#!/usr/bin/env python3
"""Materialize source-attributed SIP bars for a new trigger-time study epoch.

This does not recreate or mutate the frozen cov-v0.1 corpus. It performs a new
historical Alpaca SIP refetch and freezes the returned bars as canonical JSONL
with per-file hashes and exact query provenance. Downstream trigger-time audits
must consume these bytes rather than re-fetching silently.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.bar_provider import AlpacaBarProvider, CONSOLIDATED_FEED
from alert_ranker.causal_bars import Bar, MINUTE_5, MINUTE_30, Timeframe
from alert_ranker.config import resolve_alpaca_credentials
from alert_ranker.session_calendar import nyse_session_for
from scripts.options_coverage_observer import fetch_all, sessions_through

SNAPSHOT_ID = "OPTIONS_TRIGGER_BAR_SNAPSHOT"
SNAPSHOT_VERSION = "trigger-bars-v0.1"

PRIMARY_20 = (
    "AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ", "AMZN", "GOOGL", "PLTR", "INTC",
    "IWM", "TLT", "JPM", "BAC", "COIN", "XOM", "MRK", "WMT", "NFLX", "GE",
)


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _bar_row(symbol: str, timeframe: Timeframe, bar: Bar) -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe.name,
        "start": bar.start_utc.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "vwap": bar.vwap,
    }


def canonical_bar_payload(
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    *,
    timeframe: Timeframe,
) -> bytes:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_symbol, bars in bars_by_symbol.items():
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            raise ValueError("bar snapshot symbol is empty")
        for bar in bars:
            row = _bar_row(symbol, timeframe, bar)
            key = (symbol, row["start"])
            if key in seen:
                raise ValueError(f"duplicate bar key: {symbol} {row['start']}")
            seen.add(key)
            rows.append(row)
    rows.sort(key=lambda row: (row["symbol"], row["start"]))
    return b"".join(_canonical_json(row) for row in rows)


def payload_summary(payload: bytes) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    starts: list[str] = []
    for line in payload.splitlines():
        if not line:
            continue
        row = json.loads(line)
        counts[str(row["symbol"])] += 1
        starts.append(str(row["start"]))
    return {
        "rows": sum(counts.values()),
        "rows_by_symbol": dict(sorted(counts.items())),
        "first_bar_start": min(starts) if starts else None,
        "last_bar_start": max(starts) if starts else None,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _session_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if nyse_session_for(cursor) is not None:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


async def _fetch_one(
    provider: AlpacaBarProvider,
    *,
    day: date,
    timeframe: Timeframe,
    symbols: Sequence[str],
) -> tuple[bytes, dict[str, Any]]:
    session = nyse_session_for(day)
    if session is None:
        raise ValueError(f"not an exchange session: {day}")

    if timeframe == MINUTE_30:
        history = sessions_through(day, 10)
        if not history:
            raise ValueError(f"no lookback sessions for {day}")
        start = history[0].open - timedelta(hours=1)
        end = session.close
    elif timeframe == MINUTE_5:
        start = session.open
        end = session.close
    else:
        raise ValueError(f"unsupported snapshot timeframe: {timeframe.name}")

    bars, errors = await fetch_all(provider, symbols, timeframe, start, end)
    if errors:
        detail = ",".join(f"{key}:{value}" for key, value in sorted(errors.items()))
        raise RuntimeError(f"provider errors for {day} {timeframe.name}: {detail}")

    missing = sorted(set(symbols) - set(bars))
    if missing:
        raise RuntimeError(
            f"provider returned no {timeframe.name} bars for: {','.join(missing)}"
        )

    payload = canonical_bar_payload(bars, timeframe=timeframe)
    query = {
        "session_date": day.isoformat(),
        "timeframe": timeframe.name,
        "query_start": start.astimezone(timezone.utc).isoformat(),
        "query_end": end.astimezone(timezone.utc).isoformat(),
        "feed": CONSOLIDATED_FEED,
        "symbols": list(symbols),
    }
    return payload, query


async def run(args: argparse.Namespace) -> int:
    start = date.fromisoformat(args.from_date)
    end = date.fromisoformat(args.to_date)
    if end < start:
        raise SystemExit("--to-date must be on/after --from-date")

    symbols = tuple(
        dict.fromkeys(
            item.strip().upper()
            for item in (args.symbols.split(",") if args.symbols else PRIMARY_20)
            if item.strip()
        )
    )
    if not symbols:
        raise SystemExit("snapshot symbol set is empty")

    api_key, secret_key = resolve_alpaca_credentials()
    if not api_key or not secret_key:
        raise SystemExit("Alpaca credentials not configured")

    out_dir = Path(args.out_dir).resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SystemExit(f"output directory must be empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    observer = Path(args.observer_db).resolve() if args.observer_db else None
    if observer is not None and not observer.is_file():
        raise SystemExit(f"observer DB does not exist: {observer}")

    provider = AlpacaBarProvider(
        base_url=os.environ.get(
            "ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"
        ).rstrip("/"),
        api_key=api_key,
        secret_key=secret_key,
        feed=CONSOLIDATED_FEED,
    )

    files: list[dict[str, Any]] = []
    for day in _session_days(start, end):
        for timeframe in (MINUTE_30, MINUTE_5):
            payload, query = await _fetch_one(
                provider,
                day=day,
                timeframe=timeframe,
                symbols=symbols,
            )
            name = f"{day.isoformat()}_{timeframe.name}.jsonl"
            path = out_dir / name
            path.write_bytes(payload)
            files.append({
                "path": name,
                "query": query,
                **payload_summary(payload),
            })

    manifest: dict[str, Any] = {
        "snapshot_id": SNAPSHOT_ID,
        "snapshot_version": SNAPSHOT_VERSION,
        "study_epoch": "NEW_REFETCH_NOT_FROZEN_COV_V0_1",
        "provider": "alpaca",
        "feed": CONSOLIDATED_FEED,
        "base_url": provider.base_url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "symbols": list(symbols),
        "files": files,
        "claims_not_made": [
            "byte identity with the original cov-v0.1 provider responses",
            "historical option-chain completeness",
            "strategy validity",
            "runtime promotion",
        ],
    }
    if observer is not None:
        manifest["frozen_observer_reference"] = {
            "filename": observer.name,
            "bytes": observer.stat().st_size,
            "sha256": _sha256_file(observer),
        }

    manifest_payload = _canonical_json(manifest)
    (out_dir / "manifest.json").write_bytes(manifest_payload)
    print(json.dumps({
        "snapshot_id": SNAPSHOT_ID,
        "snapshot_version": SNAPSHOT_VERSION,
        "out_dir": str(out_dir),
        "files": len(files),
        "bar_rows": sum(item["rows"] for item in files),
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "observer_sha256": (
            manifest.get("frozen_observer_reference", {}).get("sha256")
        ),
    }, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--symbols", help="comma-separated symbols; defaults to frozen primary 20")
    parser.add_argument("--observer-db", help="optional frozen observer DB to bind by SHA-256")
    parser.add_argument("--out-dir", required=True)
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
