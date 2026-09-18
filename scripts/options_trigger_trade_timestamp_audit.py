#!/usr/bin/env python3
"""Freeze exact SIP trade timestamps for the frozen 212R trigger population.

Inputs are the already-frozen trigger-bar snapshot and cov-v0.1 observer DB.
For each exact frozen 2-1-2 reversal row, this audit replays the proven 5-minute
first-cross bucket, fetches historical Alpaca SIP trades only for that bucket,
and finds the first price-forming trade through the frozen trigger.

A trade is eligible only when all of its condition codes are documented by
Alpaca as updating minute-bar price fields for its tape. Unknown or non-price-forming
conditions fail closed. The eligible trades must reproduce the frozen
5-minute bar OHLC before a crossing timestamp is accepted.

No option data, broker/account endpoint, scanner activation, order route, or
runtime policy is used here.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import sys
from typing import Any, Mapping, Sequence

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.config import resolve_alpaca_credentials  # noqa: E402
from alert_ranker.causal_bars import Bar, MINUTE_5  # noqa: E402
from scripts.options_trigger_geometry_audit import (  # noqa: E402
    annotate_frozen_212_membership,
    audit_session,
    frozen_212_reversal_keys,
    load_snapshot_file,
    verify_snapshot,
)

AUDIT_ID = "OPTIONS_212R_TRIGGER_TRADE_TIMESTAMP_AUDIT"
AUDIT_VERSION = "trigger-trades-v0.1"
SOURCE_RULE_URL = "https://docs.alpaca.markets/us/docs/market-data-faq"
CONDITION_RULE_ID = "alpaca-stock-minute-price-v1"
TRADES_PATH_TEMPLATE = "/v2/stocks/{symbol}/trades"
CONSOLIDATED_FEED = "sip"

# Alpaca's documented minute-bar price fields update table. The key is tape; values
# are condition codes that DO update minute price fields. Missing/unknown codes fail
# closed. For CTA tapes A/B, an empty condition list is Regular Sale and valid.
_MINUTE_PRICE_GREEN: dict[str, frozenset[str]] = {
    "A": frozenset({" ", "E", "F", "K", "L", "O", "T", "X", "5", "6"}),
    "B": frozenset({" ", "E", "F", "K", "L", "O", "T", "X", "5", "6"}),
    "C": frozenset({"@", "A", "B", "D", "F", "K", "L", "O", "T", "X", "Y", "5", "6"}),
    "O": frozenset({"@", "T"}),
}
_MINUTE_PRICE_RED: dict[str, frozenset[str]] = {
    "A": frozenset({"B", "C", "H", "I", "M", "N", "P", "Q", "R", "U", "V", "Z", "4", "7", "9"}),
    "B": frozenset({"B", "C", "H", "I", "M", "N", "P", "Q", "R", "U", "V", "Z", "4", "7", "9"}),
    "C": frozenset({"C", "G", "H", "I", "M", "N", "P", "Q", "R", "U", "V", "W", "Z", "4", "7", "9"}),
    "O": frozenset({"C", "I", "N", "P", "R", "U", "W"}),
}

_TS_RE = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d{1,9}))?Z$"
)


class TriggerTradeAuditError(RuntimeError):
    pass


@dataclass(frozen=True, kw_only=True)
class CanonicalTrade:
    symbol: str
    timestamp: str
    timestamp_ns: int
    price: float
    size: int
    exchange: str
    conditions: tuple[str, ...]
    tape: str
    trade_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "timestamp_ns": self.timestamp_ns,
            "price": self.price,
            "size": self.size,
            "exchange": self.exchange,
            "conditions": list(self.conditions),
            "tape": self.tape,
            "trade_id": self.trade_id,
        }


def canonical_json_line(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def timestamp_ns(value: object) -> int:
    text = str(value or "")
    match = _TS_RE.fullmatch(text)
    if match is None:
        raise TriggerTradeAuditError(f"invalid SIP timestamp: {text!r}")
    base = datetime.fromisoformat(match.group("base") + "+00:00")
    frac = (match.group("frac") or "").ljust(9, "0")
    return int(base.timestamp()) * 1_000_000_000 + int(frac or "0")


def minute_price_eligible(*, tape: object, conditions: Sequence[object] | None) -> bool:
    tape_code = str(tape or "").strip().upper()
    if tape_code not in _MINUTE_PRICE_GREEN:
        raise TriggerTradeAuditError(f"unknown SIP tape: {tape_code!r}")
    normalized = tuple(str(item) for item in (conditions or ()))
    if not normalized:
        if tape_code in {"A", "B"}:
            return True
        raise TriggerTradeAuditError(
            f"undocumented empty condition set for tape {tape_code}"
        )

    green = _MINUTE_PRICE_GREEN[tape_code]
    red = _MINUTE_PRICE_RED[tape_code]
    for condition in normalized:
        if condition not in green and condition not in red:
            raise TriggerTradeAuditError(
                f"unknown trade condition {condition!r} for tape {tape_code}"
            )
    if any(condition in red for condition in normalized):
        return False
    return True


def parse_trade(symbol: str, payload: Mapping[str, Any]) -> CanonicalTrade:
    try:
        ts = str(payload["t"])
        price = float(payload["p"])
        size = int(payload["s"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TriggerTradeAuditError(f"malformed trade: {exc}") from exc
    if price <= 0 or size <= 0:
        raise TriggerTradeAuditError("trade price/size must be positive")
    conditions_raw = payload.get("c") or []
    if not isinstance(conditions_raw, list):
        raise TriggerTradeAuditError("trade conditions must be a list")
    return CanonicalTrade(
        symbol=symbol.upper(),
        timestamp=ts,
        timestamp_ns=timestamp_ns(ts),
        price=price,
        size=size,
        exchange=str(payload.get("x") or ""),
        conditions=tuple(sorted(str(item) for item in conditions_raw)),
        tape=str(payload.get("z") or "").upper(),
        trade_id=str(payload.get("i") or ""),
    )


def canonical_trade_payload(trades: Sequence[CanonicalTrade]) -> bytes:
    ordered = sorted(trades, key=lambda item: (item.timestamp_ns, item.trade_id))
    seen: set[tuple[int, str, str]] = set()
    rows: list[bytes] = []
    for trade in ordered:
        key = (trade.timestamp_ns, trade.trade_id, trade.symbol)
        if key in seen:
            continue
        seen.add(key)
        rows.append(canonical_json_line(trade.as_dict()))
    return b"".join(rows)


def datetime_ns(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    point = value.astimezone(timezone.utc)
    return int(point.timestamp()) * 1_000_000_000 + point.microsecond * 1_000


def trades_in_window(
    trades: Sequence[CanonicalTrade],
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[CanonicalTrade]:
    start_ns = datetime_ns(window_start)
    end_ns = datetime_ns(window_end)
    if end_ns <= start_ns:
        raise ValueError("window_end must be after window_start")
    return [
        trade
        for trade in sorted(trades, key=lambda item: (item.timestamp_ns, item.trade_id))
        if start_ns <= trade.timestamp_ns < end_ns
    ]


def first_crossing_trade(
    *,
    trades: Sequence[CanonicalTrade],
    direction: str,
    trigger_level: float,
    window_start: datetime,
    window_end: datetime,
) -> tuple[CanonicalTrade | None, list[CanonicalTrade]]:
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    eligible: list[CanonicalTrade] = []
    for trade in trades_in_window(
        trades,
        window_start=window_start,
        window_end=window_end,
    ):
        if not minute_price_eligible(tape=trade.tape, conditions=trade.conditions):
            continue
        eligible.append(trade)
    for trade in eligible:
        crossed = trade.price > trigger_level if direction == "LONG" else trade.price < trigger_level
        if crossed:
            return trade, eligible
    return None, eligible


def eligible_trade_ohlc(trades: Sequence[CanonicalTrade]) -> dict[str, float]:
    """Reconstruct minute-bar OHLC from price-forming trades only."""
    if not trades:
        raise TriggerTradeAuditError("no minute-price-eligible trades")
    return {
        "open": float(trades[0].price),
        "high": max(float(item.price) for item in trades),
        "low": min(float(item.price) for item in trades),
        "close": float(trades[-1].price),
    }


def _bar_lookup(snapshot_dir: Path, day: str) -> dict[tuple[str, str], Bar]:
    bars = load_snapshot_file(snapshot_dir / f"{day}_5Min.jsonl")
    return {
        (symbol, bar.start_utc.isoformat()): bar
        for symbol, values in bars.items()
        for bar in values
    }


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass
class AlpacaTradeProvider:
    base_url: str
    api_key: str
    secret_key: str
    page_limit: int = 10000
    max_pages: int = 20
    timeout: float = 20.0

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
        }

    async def fetch_trades(
        self,
        *,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[CanonicalTrade]:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise TriggerTradeAuditError("invalid trade query window")

        params: dict[str, Any] = {
            "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "feed": CONSOLIDATED_FEED,
            "limit": self.page_limit,
            "sort": "asc",
        }
        collected: list[CanonicalTrade] = []
        page_params = dict(params)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for _ in range(self.max_pages):
                response = await client.get(
                    self.base_url.rstrip("/")
                    + TRADES_PATH_TEMPLATE.format(symbol=symbol.upper()),
                    params=page_params,
                    headers=self.headers,
                )
                if response.status_code != 200:
                    raise TriggerTradeAuditError(
                        f"trade provider HTTP {response.status_code}: {response.text[:200]}"
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise TriggerTradeAuditError("trade provider returned invalid JSON") from exc
                trades = payload.get("trades") or []
                if not isinstance(trades, list):
                    raise TriggerTradeAuditError("trade provider payload is malformed")
                collected.extend(parse_trade(symbol, row) for row in trades)
                token = payload.get("next_page_token")
                if not token:
                    break
                page_params = dict(params)
                page_params["page_token"] = token
            else:
                raise TriggerTradeAuditError("trade pagination truncated")

        return trades_in_window(
            collected,
            window_start=start,
            window_end=end,
        )


def _trade_file_name(symbol: str, start: datetime) -> str:
    stamp = start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"raw/{start.date().isoformat()}_{symbol.upper()}_{stamp}.jsonl"


def _event_id(row: Mapping[str, Any]) -> str:
    return "|".join(
        (
            str(row["session_date"]),
            str(row["symbol"]),
            str(row["watch_bar_start"]),
            str(row["direction"]),
            f"{float(row['entry']):.9f}",
            f"{float(row['generic_stop']):.9f}",
        )
    )


async def run(args: argparse.Namespace) -> int:
    snapshot_dir = Path(args.snapshot_dir).resolve()
    observer_db = Path(args.frozen_observer_db).resolve()
    out_dir = Path(args.out_dir).resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SystemExit(f"output directory must be empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)

    manifest = verify_snapshot(
        snapshot_dir,
        expected_manifest_sha256=args.expected_manifest_sha256,
    )
    symbols = tuple(str(value).upper() for value in manifest["symbols"])
    frozen_keys = frozen_212_reversal_keys(
        observer_db,
        expected_sha256=args.expected_observer_sha256,
        symbols=symbols,
        from_date=str(manifest["from_date"]),
        to_date=str(manifest["to_date"]),
    )

    rows: list[dict[str, Any]] = []
    start_day = date.fromisoformat(str(manifest["from_date"]))
    end_day = date.fromisoformat(str(manifest["to_date"]))
    cursor = start_day
    while cursor <= end_day:
        rows.extend(audit_session(snapshot_dir, cursor, symbols))
        cursor += timedelta(days=1)
    annotate_frozen_212_membership(rows, frozen_keys)
    events = [
        row
        for row in rows
        if row.get("family") == "STRAT_212_REVERSAL"
        and row.get("frozen_212_reversal_match") is True
    ]
    if len(events) != len(frozen_keys):
        raise TriggerTradeAuditError(
            f"frozen population mismatch: expected={len(frozen_keys)} matched={len(events)}"
        )

    api_key, secret_key = resolve_alpaca_credentials()
    if not api_key or not secret_key:
        raise SystemExit("Alpaca credentials not configured")
    provider = AlpacaTradeProvider(
        base_url=os.environ.get(
            "ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"
        ),
        api_key=api_key,
        secret_key=secret_key,
    )

    evidence_rows: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    fetched_windows: dict[tuple[str, str], tuple[list[CanonicalTrade], str]] = {}
    bar_maps: dict[str, dict[tuple[str, str], Bar]] = {}

    for row in sorted(events, key=lambda item: (item["session_date"], item["symbol"], item["watch_bar_start"])):
        trigger_bar_start = datetime.fromisoformat(str(row["trigger_bar_start"]))
        if trigger_bar_start.tzinfo is None:
            raise TriggerTradeAuditError("trigger bar start is naive")
        trigger_bar_start = trigger_bar_start.astimezone(timezone.utc)
        trigger_bar_end = trigger_bar_start + MINUTE_5.delta
        symbol = str(row["symbol"]).upper()
        key = (symbol, trigger_bar_start.isoformat())

        if key not in fetched_windows:
            trades = await provider.fetch_trades(
                symbol=symbol,
                start=trigger_bar_start,
                end=trigger_bar_end,
            )
            payload = canonical_trade_payload(trades)
            relative = _trade_file_name(symbol, trigger_bar_start)
            target = out_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            fetched_windows[key] = (trades, relative)
            files.append(
                {
                    "path": relative,
                    "symbol": symbol,
                    "window_start": trigger_bar_start.isoformat(),
                    "window_end_exclusive": trigger_bar_end.isoformat(),
                    "rows": len(payload.splitlines()),
                    "sha256": _sha256(payload),
                    "query": {
                        "path": TRADES_PATH_TEMPLATE.format(symbol=symbol),
                        "feed": CONSOLIDATED_FEED,
                        "start": trigger_bar_start.isoformat(),
                        "end": trigger_bar_end.isoformat(),
                        "sort": "asc",
                    },
                }
            )

        trades, relative = fetched_windows[key]
        crossing, eligible = first_crossing_trade(
            trades=trades,
            direction=str(row["direction"]),
            trigger_level=float(row["entry"]),
            window_start=trigger_bar_start,
            window_end=trigger_bar_end,
        )
        if not eligible:
            raise TriggerTradeAuditError(
                f"no minute-price-eligible trades: {symbol} {trigger_bar_start.isoformat()}"
            )

        day = str(row["session_date"])
        if day not in bar_maps:
            bar_maps[day] = _bar_lookup(snapshot_dir, day)
        frozen_bar = bar_maps[day].get((symbol, trigger_bar_start.isoformat()))
        if frozen_bar is None:
            raise TriggerTradeAuditError(
                f"frozen 5m trigger bar missing: {symbol} {trigger_bar_start.isoformat()}"
            )

        reconstructed_ohlc = eligible_trade_ohlc(eligible)
        frozen_ohlc = {
            "open": float(frozen_bar.open),
            "high": float(frozen_bar.high),
            "low": float(frozen_bar.low),
            "close": float(frozen_bar.close),
        }
        ohlc_match = all(
            abs(reconstructed_ohlc[field] - frozen_ohlc[field]) <= 1e-9
            for field in ("open", "high", "low", "close")
        )
        if not ohlc_match:
            raise TriggerTradeAuditError(
                "eligible trades do not reproduce frozen 5m OHLC: "
                f"{symbol} {trigger_bar_start.isoformat()} "
                f"trades={reconstructed_ohlc} bar={frozen_ohlc}"
            )
        if crossing is None:
            raise TriggerTradeAuditError(
                f"no eligible crossing trade despite frozen trigger: {symbol} {trigger_bar_start.isoformat()}"
            )

        evidence_rows.append(
            {
                "event_id": _event_id(row),
                "session_date": day,
                "symbol": symbol,
                "direction": row["direction"],
                "watch_bar_start": row["watch_bar_start"],
                "trigger_bucket_start": trigger_bar_start.isoformat(),
                "trigger_bucket_end_exclusive": trigger_bar_end.isoformat(),
                "trigger_level": row["entry"],
                "invalidation_level": row["generic_stop"],
                "raw_trade_file": relative,
                "raw_trade_rows": len(trades),
                "eligible_trade_rows": len(eligible),
                "eligible_ohlc": reconstructed_ohlc,
                "frozen_bar_ohlc": frozen_ohlc,
                "bar_ohlc_match": True,
                "first_cross_trade": crossing.as_dict(),
                "first_cross_offset_seconds": round(
                    (crossing.timestamp_ns - datetime_ns(trigger_bar_start))
                    / 1_000_000_000,
                    9,
                ),
            }
        )

    evidence_payload = b"".join(canonical_json_line(row) for row in evidence_rows)
    (out_dir / "events.jsonl").write_bytes(evidence_payload)

    offsets = [float(row["first_cross_offset_seconds"]) for row in evidence_rows]
    exact_level_n = sum(
        abs(float(row["first_cross_trade"]["price"]) - float(row["trigger_level"])) <= 1e-9
        for row in evidence_rows
    )
    condition_counts: Counter[str] = Counter()
    tape_counts: Counter[str] = Counter()
    for row in evidence_rows:
        trade = row["first_cross_trade"]
        tape_counts[str(trade["tape"])] += 1
        for condition in trade["conditions"]:
            condition_counts[str(condition)] += 1

    result_manifest = {
        "audit_id": AUDIT_ID,
        "audit_version": AUDIT_VERSION,
        "provider": "alpaca",
        "feed": CONSOLIDATED_FEED,
        "source_rule_url": SOURCE_RULE_URL,
        "source_snapshot_manifest_sha256": args.expected_manifest_sha256,
        "source_observer_sha256": args.expected_observer_sha256,
        "expected_frozen_rows": len(frozen_keys),
        "resolved_rows": len(evidence_rows),
        "all_frozen_rows_resolved": len(evidence_rows) == len(frozen_keys),
        "event_file": {
            "path": "events.jsonl",
            "rows": len(evidence_rows),
            "sha256": _sha256(evidence_payload),
        },
        "raw_trade_files": sorted(files, key=lambda item: item["path"]),
        "summary": {
            "unique_trade_windows": len(files),
            "raw_trade_rows": sum(int(item["rows"]) for item in files),
            "bar_ohlc_match_rows": sum(
                bool(row["bar_ohlc_match"]) for row in evidence_rows
            ),
            "first_cross_offset_seconds_min": min(offsets) if offsets else None,
            "first_cross_offset_seconds_median": (
                round(float(statistics.median(offsets)), 9) if offsets else None
            ),
            "first_cross_offset_seconds_max": max(offsets) if offsets else None,
            "first_cross_exact_level_n": exact_level_n,
            "first_cross_through_level_n": len(evidence_rows) - exact_level_n,
            "first_cross_tape_counts": dict(sorted(tape_counts.items())),
            "first_cross_condition_counts": dict(sorted(condition_counts.items())),
        },
        "claims_not_made": [
            "option contract selection",
            "historical option delta/open-interest provenance",
            "option fill or profitability",
            "strategy promotion",
            "runtime activation",
        ],
    }
    manifest_payload = canonical_json_line(result_manifest)
    (out_dir / "manifest.json").write_bytes(manifest_payload)

    print(
        json.dumps(
            {
                "audit_id": AUDIT_ID,
                "resolved_rows": len(evidence_rows),
                "unique_trade_windows": len(files),
                "events_sha256": result_manifest["event_file"]["sha256"],
                "manifest_sha256": _sha256(manifest_payload),
                "offset_min": result_manifest["summary"]["first_cross_offset_seconds_min"],
                "offset_max": result_manifest["summary"]["first_cross_offset_seconds_max"],
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--frozen-observer-db", required=True)
    parser.add_argument("--expected-observer-sha256", required=True)
    parser.add_argument("--out-dir", required=True)
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
