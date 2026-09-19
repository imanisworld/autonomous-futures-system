#!/usr/bin/env python3
"""Audit a miss-allowed IEX provisional 212R observer against delayed SIP.

Research/evidence only. The population is every structurally ARMED 2-1-2 watch
window in the frozen primary-20 trigger snapshot. IEX is evaluated first and
without SIP knowledge; delayed SIP then reconciles each provisional reversal.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
from typing import Any, Mapping, Sequence

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.causal_bars import MINUTE_5, MINUTE_30
from alert_ranker.config import resolve_alpaca_credentials
from alert_ranker.session_calendar import nyse_session_for
from alert_ranker.trigger_time import arm_trigger_setup, resolve_trigger
from scripts.options_coverage_observer import build_symbol_series, sessions_through
from scripts.options_trigger_geometry_audit import load_snapshot_file, verify_snapshot
from scripts.options_trigger_trade_timestamp_audit import (
    CanonicalTrade,
    TriggerTradeAuditError,
    first_crossing_trade,
    parse_trade,
    trades_in_window,
)

AUDIT_ID = "OPTIONS_212R_IEX_PROVISIONAL_RECONCILIATION"
AUDIT_VERSION = "iex-provisional-v0.1"
EXPECTED_ARMS = 183
TRADES_PATH_TEMPLATE = "/v2/stocks/{symbol}/trades"
LATENCY_BUCKETS = (1, 5, 15, 30, 60, 90, 120)


@dataclass(frozen=True)
class Arm:
    session_date: str
    symbol: str
    watch_start: datetime
    watch_end: datetime
    boundary_high: float
    boundary_low: float
    reference_direction: str

    @property
    def arm_id(self) -> str:
        return "|".join(
            (
                self.session_date,
                self.symbol,
                self.watch_start.isoformat(),
                f"{self.boundary_high:.9f}",
                f"{self.boundary_low:.9f}",
                self.reference_direction,
            )
        )


@dataclass
class HistoricalTradeProvider:
    base_url: str
    api_key: str
    secret_key: str
    feed: str
    page_limit: int = 10000
    max_pages: int = 50
    timeout: float = 30.0

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
        if self.feed not in {"iex", "sip"}:
            raise ValueError("feed must be iex or sip")
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise TriggerTradeAuditError("invalid trade query window")

        params: dict[str, Any] = {
            "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "feed": self.feed,
            "limit": self.page_limit,
            "sort": "asc",
        }
        collected: list[CanonicalTrade] = []
        page_params = dict(params)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for page in range(self.max_pages):
                response = None
                for attempt, delay in enumerate((0.0, 1.0, 2.0, 5.0, 10.0)):
                    if delay:
                        await asyncio.sleep(delay)
                    response = await client.get(
                        self.base_url.rstrip("/")
                        + TRADES_PATH_TEMPLATE.format(symbol=symbol.upper()),
                        params=page_params,
                        headers=self.headers,
                    )
                    if response.status_code != 429:
                        break
                assert response is not None
                if response.status_code != 200:
                    raise TriggerTradeAuditError(
                        f"{self.feed} trade provider HTTP {response.status_code}: "
                        f"{response.text[:200]}"
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise TriggerTradeAuditError(
                        f"{self.feed} trade provider returned invalid JSON"
                    ) from exc
                trades = payload.get("trades") or []
                if not isinstance(trades, list):
                    raise TriggerTradeAuditError(
                        f"{self.feed} trade provider payload is malformed"
                    )
                collected.extend(parse_trade(symbol, row) for row in trades)
                token = payload.get("next_page_token")
                if not token:
                    break
                page_params = dict(params)
                page_params["page_token"] = token
            else:
                raise TriggerTradeAuditError(
                    f"{self.feed} trade pagination truncated after {self.max_pages} pages"
                )

        return trades_in_window(
            collected,
            window_start=start,
            window_end=end,
        )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(x) for x in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return ordered[lo]
    weight = rank - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _first_boundary(
    trades: Sequence[CanonicalTrade],
    *,
    arm: Arm,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    high_cross, eligible_high = first_crossing_trade(
        trades=trades,
        direction="LONG",
        trigger_level=arm.boundary_high,
        window_start=window_start,
        window_end=window_end,
    )
    low_cross, eligible_low = first_crossing_trade(
        trades=trades,
        direction="SHORT",
        trigger_level=arm.boundary_low,
        window_start=window_start,
        window_end=window_end,
    )
    if len(eligible_high) != len(eligible_low):
        raise TriggerTradeAuditError("eligibility pass mismatch")
    candidates = [
        ("HIGH", "LONG", high_cross),
        ("LOW", "SHORT", low_cross),
    ]
    candidates = [item for item in candidates if item[2] is not None]
    if not candidates:
        return {
            "status": "NO_BREAK",
            "raw_trade_rows": len(trades),
            "eligible_trade_rows": len(eligible_high),
        }
    candidates.sort(key=lambda item: (item[2].timestamp_ns, item[0]))
    if len(candidates) > 1 and candidates[0][2].timestamp_ns == candidates[1][2].timestamp_ns:
        return {
            "status": "DATA_BLOCKED",
            "reason_code": "simultaneous_boundary_break",
            "raw_trade_rows": len(trades),
            "eligible_trade_rows": len(eligible_high),
        }
    side, direction, trade = candidates[0]
    expected_reversal = (
        (arm.reference_direction == "two_up" and direction == "SHORT")
        or (arm.reference_direction == "two_down" and direction == "LONG")
    )
    return {
        "status": "PROVEN",
        "break_side": side,
        "direction": direction,
        "family_side": "REVERSAL" if expected_reversal else "CONTINUATION",
        "timestamp": trade.timestamp,
        "timestamp_ns": trade.timestamp_ns,
        "price": trade.price,
        "trade_id": trade.trade_id,
        "raw_trade_rows": len(trades),
        "eligible_trade_rows": len(eligible_high),
    }


def _extract_arms(snapshot_dir: Path, manifest: Mapping[str, Any]) -> tuple[list[Arm], dict[tuple[str, str], Sequence[Any]]]:
    symbols = tuple(str(value).upper() for value in manifest["symbols"])
    start_day = date.fromisoformat(str(manifest["from_date"]))
    end_day = date.fromisoformat(str(manifest["to_date"]))
    arms: list[Arm] = []
    fine_by_day_symbol: dict[tuple[str, str], Sequence[Any]] = {}

    cursor = start_day
    while cursor <= end_day:
        session = nyse_session_for(cursor)
        if session is None:
            cursor += timedelta(days=1)
            continue
        bars30 = load_snapshot_file(snapshot_dir / f"{cursor.isoformat()}_30Min.jsonl")
        bars5 = load_snapshot_file(snapshot_dir / f"{cursor.isoformat()}_5Min.jsonl")
        sessions = sessions_through(cursor, 10)

        for symbol in symbols:
            fine_by_day_symbol[(cursor.isoformat(), symbol)] = tuple(bars5.get(symbol, ()))
            series = build_symbol_series(symbol, bars30.get(symbol, ()), sessions)
            if not series.observable:
                continue
            for index, current in enumerate(series.series):
                if not (session.open <= current.start_utc < session.close) or index < 2:
                    continue
                armed = arm_trigger_setup(series.series[:index])
                if armed is None or armed.pattern != "212":
                    continue
                if armed.reference_direction not in {"two_up", "two_down"}:
                    raise RuntimeError("212 arm missing reference direction")
                arms.append(
                    Arm(
                        session_date=cursor.isoformat(),
                        symbol=symbol,
                        watch_start=current.start_utc,
                        watch_end=current.start_utc + MINUTE_30.delta,
                        boundary_high=float(armed.boundary_high),
                        boundary_low=float(armed.boundary_low),
                        reference_direction=str(armed.reference_direction),
                    )
                )
        cursor += timedelta(days=1)

    arms.sort(key=lambda item: (item.session_date, item.symbol, item.watch_start, item.boundary_high, item.boundary_low))
    ids = [item.arm_id for item in arms]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate 212 arm identity")
    if len(arms) != EXPECTED_ARMS:
        raise RuntimeError(f"frozen 212 arm count mismatch: expected={EXPECTED_ARMS} actual={len(arms)}")
    return arms, fine_by_day_symbol


async def _sip_authoritative(
    provider: HistoricalTradeProvider,
    *,
    arm: Arm,
    bars5: Sequence[Any],
) -> dict[str, Any]:
    # Use frozen consolidated-SIP 5m bars only to locate the first candidate
    # bucket. Exact ordering/timestamp then comes from SIP trades in that bucket.
    from alert_ranker.trigger_time import ArmedStratTrigger

    armed = ArmedStratTrigger(
        pattern="212",
        armed_at=arm.watch_start,
        watch_until=arm.watch_end,
        boundary_high=arm.boundary_high,
        boundary_low=arm.boundary_low,
        reference_direction=arm.reference_direction,
        source_timeframe=MINUTE_30.name,
    )
    coarse = resolve_trigger(
        armed,
        bars5,
        lower_timeframe=MINUTE_5,
        watch_start=arm.watch_start,
        watch_until=arm.watch_end,
    )
    if coarse.status == "NO_TRIGGER":
        return {
            "status": "NO_BREAK",
            "coarse_status": coarse.status,
            "coarse_reason_code": coarse.reason_code,
        }
    if coarse.trigger_bar_start is None:
        return {
            "status": "DATA_BLOCKED",
            "reason_code": "sip_candidate_bucket_missing",
            "coarse_status": coarse.status,
        }
    bucket_start = coarse.trigger_bar_start
    bucket_end = bucket_start + MINUTE_5.delta
    try:
        trades = await provider.fetch_trades(
            symbol=arm.symbol,
            start=bucket_start,
            end=bucket_end,
        )
        exact = _first_boundary(
            trades,
            arm=arm,
            window_start=bucket_start,
            window_end=bucket_end,
        )
    except Exception as exc:
        return {
            "status": "DATA_BLOCKED",
            "reason_code": f"sip_query_or_semantics:{type(exc).__name__}",
            "detail": str(exc)[:240],
            "coarse_status": coarse.status,
        }
    exact["coarse_status"] = coarse.status
    exact["bucket_start"] = bucket_start.isoformat()
    if exact["status"] == "NO_BREAK":
        exact["status"] = "DATA_BLOCKED"
        exact["reason_code"] = "sip_bar_trade_cross_mismatch"
        return exact
    if (
        coarse.status == "TRIGGERED"
        and exact.get("direction") is not None
        and coarse.direction is not None
        and exact["direction"] != coarse.direction
    ):
        return {
            **exact,
            "status": "DATA_BLOCKED",
            "reason_code": "sip_exact_vs_5m_direction_mismatch",
            "coarse_direction": coarse.direction,
        }
    return exact


async def _iex_provisional(
    provider: HistoricalTradeProvider,
    *,
    arm: Arm,
) -> dict[str, Any]:
    try:
        trades = await provider.fetch_trades(
            symbol=arm.symbol,
            start=arm.watch_start,
            end=arm.watch_end,
        )
        return _first_boundary(
            trades,
            arm=arm,
            window_start=arm.watch_start,
            window_end=arm.watch_end,
        )
    except Exception as exc:
        return {
            "status": "DATA_BLOCKED",
            "reason_code": f"iex_query_or_semantics:{type(exc).__name__}",
            "detail": str(exc)[:240],
        }


def reconcile_pair(*, arm: Arm, sip: Mapping[str, Any], iex: Mapping[str, Any]) -> dict[str, Any]:
    if iex.get("status") == "DATA_BLOCKED":
        return {
            "provisional_emitted": False,
            "reconciliation": "DATA_BLOCKED",
            "reason_code": "iex_source_blocked",
            "latency_seconds": None,
        }
    if sip.get("status") == "DATA_BLOCKED":
        provisional = iex.get("status") == "PROVEN" and iex.get("family_side") == "REVERSAL"
        return {
            "provisional_emitted": bool(provisional),
            "reconciliation": "DATA_BLOCKED",
            "reason_code": "sip_source_blocked",
            "latency_seconds": None,
        }

    provisional = iex.get("status") == "PROVEN" and iex.get("family_side") == "REVERSAL"
    if not provisional:
        if iex.get("status") == "NO_BREAK":
            reason = "iex_no_break"
        elif iex.get("family_side") == "CONTINUATION":
            reason = "iex_continuation_first"
        else:
            reason = "iex_no_provisional_reversal"
        return {
            "provisional_emitted": False,
            "reconciliation": "MISS_NO_PROVISIONAL",
            "reason_code": reason,
            "latency_seconds": None,
        }

    if sip.get("status") == "NO_BREAK":
        return {
            "provisional_emitted": True,
            "reconciliation": "REJECTED_SIP_NO_BREAK",
            "reason_code": None,
            "latency_seconds": None,
        }

    if sip.get("family_side") != "REVERSAL":
        return {
            "provisional_emitted": True,
            "reconciliation": "REJECTED_SIP_CONTINUATION_FIRST",
            "reason_code": None,
            "latency_seconds": None,
        }

    if iex.get("direction") != sip.get("direction"):
        return {
            "provisional_emitted": True,
            "reconciliation": "REJECTED_DIRECTION_MISMATCH",
            "reason_code": None,
            "latency_seconds": None,
        }

    latency = (int(iex["timestamp_ns"]) - int(sip["timestamp_ns"])) / 1_000_000_000.0
    if latency < 0:
        return {
            "provisional_emitted": True,
            "reconciliation": "SOURCE_INCONSISTENT_NEGATIVE_LATENCY",
            "reason_code": None,
            "latency_seconds": latency,
        }
    return {
        "provisional_emitted": True,
        "reconciliation": "CONFIRMED_SAME_REVERSAL",
        "reason_code": None,
        "latency_seconds": latency,
    }


def _summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sip_classes = Counter()
    iex_classes = Counter()
    reconciliation = Counter()
    confirmed_latency: list[float] = []
    confirmed_by_direction = Counter()
    missed_by_direction = Counter()
    confirmed_by_session = Counter()
    missed_by_session = Counter()

    sip_reversal_n = 0
    iex_provisional_n = 0
    for row in rows:
        sip = row["sip"]
        iex = row["iex"]
        rec = row["policy"]

        if sip.get("status") == "PROVEN":
            sip_classes[str(sip.get("family_side"))] += 1
            if sip.get("family_side") == "REVERSAL":
                sip_reversal_n += 1
        else:
            sip_classes[str(sip.get("status"))] += 1

        if iex.get("status") == "PROVEN":
            iex_classes[str(iex.get("family_side"))] += 1
        else:
            iex_classes[str(iex.get("status"))] += 1

        reconciliation[str(rec["reconciliation"])] += 1
        if rec.get("provisional_emitted"):
            iex_provisional_n += 1

        if rec["reconciliation"] == "CONFIRMED_SAME_REVERSAL":
            latency = float(rec["latency_seconds"])
            confirmed_latency.append(latency)
            confirmed_by_direction[str(iex["direction"])] += 1
            confirmed_by_session[str(row["session_date"])] += 1
        elif (
            sip.get("status") == "PROVEN"
            and sip.get("family_side") == "REVERSAL"
            and rec["reconciliation"] != "CONFIRMED_SAME_REVERSAL"
        ):
            missed_by_direction[str(sip.get("direction"))] += 1
            missed_by_session[str(row["session_date"])] += 1

    confirmed_n = reconciliation["CONFIRMED_SAME_REVERSAL"]
    blocked_n = reconciliation["DATA_BLOCKED"]
    negative_n = reconciliation["SOURCE_INCONSISTENT_NEGATIVE_LATENCY"]
    false_provisional_n = (
        reconciliation["REJECTED_SIP_CONTINUATION_FIRST"]
        + reconciliation["REJECTED_SIP_NO_BREAK"]
        + reconciliation["REJECTED_DIRECTION_MISMATCH"]
        + negative_n
    )
    if blocked_n:
        verdict = "DATA_BLOCKED"
    elif negative_n:
        verdict = "PROVISIONAL_SOURCE_NOT_RELIABLE_ENOUGH_FOR_RESEARCH"
    else:
        verdict = "MISS_ALLOWED_RESEARCH_OBSERVER_FEASIBLE"

    latency_summary = {
        "n": len(confirmed_latency),
        "min": min(confirmed_latency) if confirmed_latency else None,
        "median": statistics.median(confirmed_latency) if confirmed_latency else None,
        "p90": _percentile(confirmed_latency, 0.90),
        "p95": _percentile(confirmed_latency, 0.95),
        "max": max(confirmed_latency) if confirmed_latency else None,
        "negative_n": sum(value < 0 for value in confirmed_latency),
        "buckets": {
            f"lte_{seconds}s": sum(value <= seconds for value in confirmed_latency)
            for seconds in LATENCY_BUCKETS
        },
    }

    return {
        "arms": len(rows),
        "sip_class_counts": dict(sorted(sip_classes.items())),
        "iex_class_counts": dict(sorted(iex_classes.items())),
        "reconciliation_counts": dict(sorted(reconciliation.items())),
        "sip_reversal_n": sip_reversal_n,
        "iex_provisional_reversal_n": iex_provisional_n,
        "confirmed_reversal_n": confirmed_n,
        "false_provisional_n": false_provisional_n,
        "provisional_confirmation_rate": (
            confirmed_n / iex_provisional_n if iex_provisional_n else None
        ),
        "sip_reversal_recall": (
            confirmed_n / sip_reversal_n if sip_reversal_n else None
        ),
        "confirmed_by_direction": dict(sorted(confirmed_by_direction.items())),
        "missed_sip_reversal_by_direction": dict(sorted(missed_by_direction.items())),
        "confirmed_by_session": dict(sorted(confirmed_by_session.items())),
        "missed_sip_reversal_by_session": dict(sorted(missed_by_session.items())),
        "confirmed_latency_seconds": latency_summary,
        "study_verdict": verdict,
        "exact_sip_substitute": False,
        "claims_not_made": [
            "IEX is SIP-equivalent",
            "212R option expectancy",
            "212R strategy promotion",
            "DEMO eligibility",
            "deployment or scheduling authorization",
        ],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    snapshot_dir = Path(args.snapshot_dir).resolve()
    manifest = verify_snapshot(
        snapshot_dir,
        expected_manifest_sha256=args.expected_manifest_sha256,
    )
    arms, fine = _extract_arms(snapshot_dir, manifest)

    if args.env_file:
        load_dotenv(args.env_file, override=True)
    key, secret = resolve_alpaca_credentials()
    if not key or not secret:
        raise SystemExit("Alpaca credentials missing")
    base_url = os.environ.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").strip()

    iex_provider = HistoricalTradeProvider(base_url, key, secret, "iex")
    sip_provider = HistoricalTradeProvider(base_url, key, secret, "sip")

    out_rows: list[dict[str, Any]] = []
    total = len(arms)
    for index, arm in enumerate(arms, start=1):
        bars5 = fine[(arm.session_date, arm.symbol)]
        sip = await _sip_authoritative(sip_provider, arm=arm, bars5=bars5)
        iex = await _iex_provisional(iex_provider, arm=arm)
        policy = reconcile_pair(arm=arm, sip=sip, iex=iex)
        out_rows.append(
            {
                "arm_id": arm.arm_id,
                "session_date": arm.session_date,
                "symbol": arm.symbol,
                "watch_start": arm.watch_start.isoformat(),
                "watch_end": arm.watch_end.isoformat(),
                "boundary_high": arm.boundary_high,
                "boundary_low": arm.boundary_low,
                "reference_direction": arm.reference_direction,
                "sip": sip,
                "iex": iex,
                "policy": policy,
            }
        )
        if index % 10 == 0 or index == total:
            print(f"processed {index}/{total}", flush=True)

    result = {
        "audit_id": AUDIT_ID,
        "audit_version": AUDIT_VERSION,
        "preregistration": "docs/options-212r-iex-provisional-preregistration-2026-09-18.md",
        "snapshot_manifest_sha256": args.expected_manifest_sha256,
        "snapshot_id": manifest.get("snapshot_id"),
        "from_date": manifest.get("from_date"),
        "to_date": manifest.get("to_date"),
        "symbols": manifest.get("symbols"),
        "policy": {
            "provisional_source": "alpaca_iex",
            "authoritative_reconciliation_source": "alpaca_sip_delayed",
            "provisional_emission": "first IEX boundary break must be 212 reversal",
            "misses_are_backfilled": False,
            "reconciliation_rewrites_decision_timestamp": False,
            "production_capture_lag_selected": False,
            "timer_cadence_selected": False,
        },
        "summary": _summarize(out_rows),
        "rows": out_rows,
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--env-file")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    result = asyncio.run(run(args))
    payload = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(payload)
    print(f"wrote {out} sha256={hashlib.sha256(payload.encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
