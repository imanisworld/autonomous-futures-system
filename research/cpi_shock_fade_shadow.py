"""Read-only forward shadow collector for the frozen MNQ CPI shock-fade study.

Research-only. This module cannot submit, prepare, or route orders. It reads
1-minute futures aggregates from the existing Polygon/Massive historical client,
reconstructs the preregistered hypothetical trade, and optionally appends one
immutable JSONL evidence record.

Frozen rule (v1):
  * CPI release days only.
  * Observe MNQ from 08:30 to 08:35 ET.
  * At 08:40 ET, take the opposite direction of that 5-minute shock.
  * Exit at 09:30 ET.
  * No threshold, stop, target, GEX, Signa, Strat, or discretionary filter.

The bar open at 08:40/09:30 is a proxy, not a verified bid/ask fill. The record
makes that limitation explicit and never claims executable evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from sources.polygon_client import PolygonBar, PolygonFuturesClient, front_contract


RULE_VERSION = "cpi_shock_fade_mnq_v1_frozen_2026-09-24"
SCHEMA_VERSION = "cpi_shock_fade_shadow.v1"
SYMBOL = "MNQ"
POINT_VALUE_USD = 2.0
TICK_SIZE = 0.25
TICK_VALUE_USD = POINT_VALUE_USD * TICK_SIZE
ET = ZoneInfo("America/New_York")

# Official BLS 2026 release calendar as verified 2026-09-24.
FORWARD_CPI_DATES = {
    date(2026, 10, 14),
    date(2026, 11, 10),
    date(2026, 12, 10),
}

DEFAULT_OUTPUT = Path("data/research/cpi_shock_fade_shadow.jsonl")


class EvidenceError(ValueError):
    """The requested shadow observation cannot be evaluated honestly."""


def _event_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceError("event_date must be YYYY-MM-DD") from exc


def validate_forward_date(value: str | date) -> date:
    event_date = _event_date(value)
    if event_date not in FORWARD_CPI_DATES:
        raise EvidenceError(
            f"{event_date.isoformat()} is not in the frozen 2026 CPI forward calendar"
        )
    return event_date


def _local_bar_map(event_date: date, bars: Iterable[PolygonBar]) -> dict[time, PolygonBar]:
    result: dict[time, PolygonBar] = {}
    for bar in bars:
        local = bar.ts.astimezone(ET)
        if local.date() != event_date:
            continue
        key = local.time().replace(second=0, microsecond=0, tzinfo=None)
        if key in result:
            raise EvidenceError(f"duplicate 1-minute bar at {key.strftime('%H:%M')} ET")
        result[key] = bar
    return result


def _require(mapping: dict[time, PolygonBar], hhmm: str) -> PolygonBar:
    key = time.fromisoformat(hhmm)
    try:
        return mapping[key]
    except KeyError as exc:
        raise EvidenceError(f"missing required {hhmm} ET 1-minute bar") from exc


def _record_id(event_date: date, contract: str) -> str:
    raw = f"{RULE_VERSION}|{event_date.isoformat()}|{contract}".encode()
    return "cpi_" + hashlib.sha256(raw).hexdigest()[:24]


def evaluate_shadow(
    event_date: str | date,
    contract: str,
    bars: Iterable[PolygonBar],
) -> dict:
    """Evaluate the frozen rule from complete 1-minute bars.

    This function does not validate the BLS calendar so historical fixtures can
    be tested. The forward CLI validates dates before calling it.
    """
    event_date = _event_date(event_date)
    mapping = _local_bar_map(event_date, bars)

    b0830 = _require(mapping, "08:30")
    b0835 = _require(mapping, "08:35")
    b0840 = _require(mapping, "08:40")
    b0930 = _require(mapping, "09:30")

    shock_points = b0835.open - b0830.open
    if shock_points == 0:
        side = "NO_SIGNAL"
        gross_points = 0.0
        gross_dollars = 0.0
        mae_dollars = 0.0
        mfe_dollars = 0.0
    else:
        side = "SHORT" if shock_points > 0 else "LONG"
        signed = -1.0 if side == "SHORT" else 1.0
        gross_points = signed * (b0930.open - b0840.open)
        gross_dollars = gross_points * POINT_VALUE_USD

        path = [
            bar
            for key, bar in mapping.items()
            if time(8, 40) <= key < time(9, 30)
        ]
        if not path:
            raise EvidenceError("missing 08:40-09:30 ET path bars")
        path_low = min(bar.low for bar in path)
        path_high = max(bar.high for bar in path)
        if side == "LONG":
            mae_dollars = max(0.0, b0840.open - path_low) * POINT_VALUE_USD
            mfe_dollars = max(0.0, path_high - b0840.open) * POINT_VALUE_USD
        else:
            mae_dollars = max(0.0, path_high - b0840.open) * POINT_VALUE_USD
            mfe_dollars = max(0.0, b0840.open - path_low) * POINT_VALUE_USD

    stress = {
        str(cost): gross_dollars - float(cost)
        for cost in (5, 10, 20)
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "record_id": _record_id(event_date, contract),
        "event": {
            "kind": "CPI",
            "date": event_date.isoformat(),
            "release_time_et": "08:30",
        },
        "instrument": SYMBOL,
        "contract": contract,
        "signal": {
            "shock_start_open": b0830.open,
            "shock_end_open": b0835.open,
            "shock_points": shock_points,
            "side": side,
        },
        "shadow_trade": {
            "entry_time_et": "08:40",
            "entry_price_proxy": b0840.open,
            "exit_time_et": "09:30",
            "exit_price_proxy": b0930.open,
            "gross_points": gross_points,
            "gross_dollars": gross_dollars,
            "mae_dollars": mae_dollars,
            "mfe_dollars": mfe_dollars,
            "cost_stress_net_dollars": stress,
        },
        "evidence_quality": {
            "bar_source": "polygon_massive_1min_aggregates",
            "fill_proxy": "minute_bar_open",
            "bid_ask_observed": False,
            "tick_trade_observed": False,
            "fill_quality_verified": False,
            "limitation": "historical bid/ask and tick trades are not available on the current data entitlement",
        },
        "safety": {
            "observation_only": True,
            "gate_authoritative": False,
            "execution_authorized": False,
            "order_submission_possible": False,
            "broker_imported": False,
        },
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def append_record(record: dict, path: str | Path = DEFAULT_OUTPUT) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record_id = record["record_id"]
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                existing = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvidenceError(f"existing evidence file is malformed: {path}") from exc
            if existing.get("record_id") == record_id:
                raise EvidenceError(f"duplicate immutable record: {record_id}")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-date", required=True, help="Official CPI date, YYYY-MM-DD")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--write",
        action="store_true",
        help="Append the observation to JSONL. Without this flag the command is dry-run only.",
    )
    args = parser.parse_args(argv)

    event_date = validate_forward_date(args.event_date)
    contract = front_contract(SYMBOL, event_date)
    client = PolygonFuturesClient(min_request_interval=13.0)
    bars = client.fetch_bars(contract, event_date, event_date, timeframe_minutes=1)
    record = evaluate_shadow(event_date, contract, bars)

    print(json.dumps(record, indent=2, sort_keys=True))
    if args.write:
        append_record(record, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
