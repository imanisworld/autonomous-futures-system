"""Resample Databento GLBX.MDP3 1-minute bars into the frozen 3-2-2 harness trees.

The adapter does not download data and does not choose a continuous contract.
Callers pass ohlcv-1m rows plus definition rows already in memory. Front-month
selection is ``sources.polygon_client.front_contract`` with ``DEFAULT_ROLL_DAYS``
(8), the same calendar rule that built the harness corpora. Databento continuous
symbols (``MNQ.c.0`` and any raw symbol that is not a quarterly outright) are
ignored.

Output paths match the harness readers:

- ``{research_15m}/MNQ/MNQ_{utc-date}.jsonl`` for ``detect_candidates``
- ``{audit_5m}/MNQ/MNQ_{utc-date}.jsonl`` for ``load_bars``

Timestamps are bar-start instants in UTC, the same convention as
``scripts/polygon_to_replay.py``. A 5-minute or 15-minute bar is written only
when every constituent 1-minute bar is present. Missing minutes are recorded
and never price-filled. The 17:00-18:00 ET halt is not an expected bucket.

NQ prices are written onto the MNQ path so the unchanged detector and the
unchanged $2/point accounting can read them. The report labels those dollars
MNQ-equivalent. This module does not import or edit point-value code.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sources.polygon_client import DEFAULT_ROLL_DAYS, front_contract

ET = ZoneInfo("America/New_York")
OUTPUT_INSTRUMENT = "MNQ"
DOLLAR_BASIS = "MNQ-equivalent ($2/point)"
NQ_LABEL = "best available evidence, not fully clean"
OUTRIGHT_RE = re.compile(r"^(?:MNQ|NQ|MES|ES|MYM|YM|M2K|RTY)[HMUZ]\d$")


def in_cme_equity_session(moment: datetime) -> bool:
    """CME equity-index electronic session in America/New_York.

    Sunday 18:00 through Friday 17:00, with the daily 17:00-18:00 halt closed.
    """
    local = moment.astimezone(ET)
    weekday = local.weekday()
    minutes = local.hour * 60 + local.minute
    if weekday == 5:
        return False
    if weekday == 6:
        return minutes >= 18 * 60
    if weekday == 4:
        return minutes < 17 * 60
    return minutes < 17 * 60 or minutes >= 18 * 60


def _parent_root(definition: dict) -> str:
    raw = definition.get("asset")
    if raw is None:
        raw = definition.get("parent") or ""
    return str(raw).split(".")[0].strip().upper()


def _utc_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _local_minute(day: date, hour: int, minute: int, fold: int) -> datetime | None:
    candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET, fold=fold)
    back = candidate.astimezone(timezone.utc).astimezone(ET)
    if back.date() != day or back.hour != hour or back.minute != minute or back.fold != fold:
        return None
    return candidate


def _expected_minutes(start: date, end: date) -> list[datetime]:
    minutes: list[datetime] = []
    day = start
    while day <= end:
        for hour in range(24):
            for minute in range(60):
                for fold in (0, 1):
                    local = _local_minute(day, hour, minute, fold)
                    if local is not None and in_cme_equity_session(local):
                        minutes.append(local)
        day += timedelta(days=1)
    return minutes


def _bucket_starts(minutes: list[datetime], width: int) -> list[list[datetime]]:
    present = set(minutes)
    buckets: list[list[datetime]] = []
    for moment in minutes:
        if moment.minute % width != 0 or moment.second != 0:
            continue
        members: list[datetime] = []
        cursor = moment
        complete = True
        for _ in range(width):
            if cursor not in present:
                complete = False
                break
            members.append(cursor)
            cursor = cursor + timedelta(minutes=1)
        if complete:
            buckets.append(members)
    return buckets


def _outrights(definitions: list[dict], root: str) -> dict[str, dict]:
    root = root.strip().upper()
    found: dict[str, dict] = {}
    for definition in definitions:
        symbol = str(definition.get("raw_symbol") or "").replace(" ", "").upper()
        if not OUTRIGHT_RE.fullmatch(symbol):
            continue
        if _parent_root(definition) != root:
            continue
        if symbol in found:
            raise RuntimeError(f"duplicate outright definition for {symbol}")
        found[symbol] = definition
    return found


def _select_bars(bars: list[dict], outrights: dict[str, dict], root: str) -> tuple[dict[str, dict], list[dict]]:
    by_symbol = {symbol: int(defn["instrument_id"]) for symbol, defn in outrights.items()}
    selected: dict[str, dict] = {}
    contracts: dict[str, dict] = {}
    for bar in bars:
        stamp = datetime.fromtimestamp(int(bar["ts_event"]) / 1_000_000_000, tz=timezone.utc)
        local = stamp.astimezone(ET)
        session_date = local.date().isoformat()
        symbol = front_contract(root, local.date(), DEFAULT_ROLL_DAYS)
        instrument_id = by_symbol.get(symbol)
        contracts[session_date] = {
            "date": session_date,
            "raw_symbol": symbol,
            "instrument_id": instrument_id,
        }
        if instrument_id is None or int(bar["instrument_id"]) != instrument_id:
            continue
        key = _utc_iso(stamp)
        if key in selected:
            raise RuntimeError(f"duplicate 1-minute bar at {key} for {symbol}")
        selected[key] = bar
    return selected, [contracts[key] for key in sorted(contracts)]


def _aggregate(members: list[datetime], selected: dict[str, dict]) -> dict | list[str]:
    missing = [_utc_iso(moment) for moment in members if _utc_iso(moment) not in selected]
    if missing:
        return missing
    ordered = [selected[_utc_iso(moment)] for moment in members]
    return {
        "timestamp": _utc_iso(members[0]),
        "open": float(ordered[0]["open"]),
        "high": max(float(row["high"]) for row in ordered),
        "low": min(float(row["low"]) for row in ordered),
        "close": float(ordered[-1]["close"]),
        "volume": int(sum(int(row["volume"]) for row in ordered)),
    }


def _write_tree(rows: list[dict], root: Path) -> None:
    by_day: dict[str, list[dict]] = {}
    for row in rows:
        by_day.setdefault(row["timestamp"][:10], []).append(row)
    for day, day_rows in by_day.items():
        target = root / OUTPUT_INSTRUMENT
        target.mkdir(parents=True, exist_ok=True)
        ordered = sorted(day_rows, key=lambda item: item["timestamp"])
        text = "".join(json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n" for item in ordered)
        (target / f"{OUTPUT_INSTRUMENT}_{day}.jsonl").write_text(text, encoding="utf-8")


def adapt_ohlcv_1m(
    definitions: list[dict],
    bars: list[dict],
    *,
    root: str,
    start: date,
    end: date,
    research_15m: Path,
    audit_5m: Path,
) -> dict:
    """Write the 15-minute research tree and the 5-minute audit tree.

    ``root`` is the parent used for the roll (``MNQ`` or ``NQ``). Files are
    always written under ``MNQ/`` because that is the path the frozen harness
    reads. No missing minute is given an open, high, low, or close.
    """
    if end < start:
        raise RuntimeError(f"end {end} before start {start}")
    symbol_root = root.strip().upper()
    if symbol_root not in {"MNQ", "NQ"}:
        raise RuntimeError(f"adapter root must be MNQ or NQ, got {symbol_root!r}")
    outrights = _outrights(definitions, symbol_root)
    selected, observed = _select_bars(bars, outrights, symbol_root)
    contracts = {
        item["date"]: item
        for item in observed
    }
    day = start
    while day <= end:
        key = day.isoformat()
        if key not in contracts:
            symbol = front_contract(symbol_root, day, DEFAULT_ROLL_DAYS)
            definition = outrights.get(symbol)
            contracts[key] = {
                "date": key,
                "raw_symbol": symbol,
                "instrument_id": None if definition is None else int(definition["instrument_id"]),
            }
        day += timedelta(days=1)

    minutes = _expected_minutes(start, end)
    missing: list[dict] = []
    written: dict[int, list[dict]] = {5: [], 15: []}
    for width in (5, 15):
        for members in _bucket_starts(minutes, width):
            aggregate = _aggregate(members, selected)
            if isinstance(aggregate, list):
                missing.append(
                    {
                        "timeframe_minutes": width,
                        "bar_start": _utc_iso(members[0]),
                        "missing_minutes": aggregate,
                    }
                )
                continue
            written[width].append(aggregate)

    _write_tree(written[15], research_15m)
    _write_tree(written[5], audit_5m)
    label = NQ_LABEL if symbol_root == "NQ" else "MNQ"
    return {
        "price_source": symbol_root,
        "output_instrument": OUTPUT_INSTRUMENT,
        "dollar_basis": DOLLAR_BASIS,
        "label": label,
        "roll_days": DEFAULT_ROLL_DAYS,
        "roll_source": "sources.polygon_client.front_contract",
        "contracts": [contracts[key] for key in sorted(contracts)],
        "missing_bars": sorted(missing, key=lambda item: (item["timeframe_minutes"], item["bar_start"])),
        "bars_5m": len(written[5]),
        "bars_15m": len(written[15]),
    }
