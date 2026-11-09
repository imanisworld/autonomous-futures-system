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
``scripts/polygon_to_replay.py``. A 5-minute or 15-minute bar is built from
the 1-minute bars that exist in that bucket, the same way the frozen
15-minute-to-60-minute resampler keeps a candle when some sub-bars are
absent. A bucket with zero 1-minute bars is not written. Missing minutes are
recorded and never given a synthesized price. The 17:00-18:00 ET halt is not
an expected bucket.

A weekday that is missing a 15-minute bar in 07:00-11:00 ET, or a 5-minute
bar in 10:00-16:00 ET, is ``DATA_GAP``: counted, reported, and not scored.
Bars that the CME equity-index calendar does not list for that day (a full
closure, or the session after a 13:00 ET early close) are
``expected_holiday_closure`` and are not data gaps. Front-month selection
still uses the civil ET date and ``roll_days=8``.

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
NQ_RESEARCH_LABEL = "MNQ-equivalent research, not executable historical MNQ"
OUTRIGHT_RE = re.compile(r"^(?:MNQ|NQ|MES|ES|MYM|YM|M2K|RTY)[HMUZ]\d$")
EARLY_CLOSE_ET = (13, 0)
DATA_GAP = "DATA_GAP"


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    return cursor - timedelta(days=(cursor.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    year_mod = year % 19
    century, century_mod = divmod(year, 100)
    century_quad, century_quad_mod = divmod(century, 4)
    leap_correction, leap_mod = divmod(century + 8, 25)
    paschal = (year_mod * 19 + century - century_quad - leap_correction + 15) % 30
    century_leap, century_leap_mod = divmod(century_mod, 4)
    weekday_shift = (32 + 2 * century_quad_mod + 2 * century_leap - paschal - century_leap_mod) % 7
    correction = (year_mod + 11 * paschal + 22 * weekday_shift) // 451
    month, day = divmod(paschal + weekday_shift - 7 * correction + 114, 31)
    return date(year, month, day + 1)


def _previous_weekday(day: date) -> date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def cme_equity_index_session(day: date) -> str:
    """CME equity-index session kind for one civil ET date.

    ``closed`` and ``early_close`` (13:00 ET) follow the equity-index futures
    holiday schedule. 2025-01-20 is an early close: the last RTH bar is 12:55.
    This does not change ``front_contract`` roll dates.
    """
    year = day.year
    closed = {
        _observed_fixed_holiday(year, 1, 1),
        _easter_sunday(year) - timedelta(days=2),
        _nth_weekday(year, 11, 3, 4),
        _observed_fixed_holiday(year, 7, 4),
        _observed_fixed_holiday(year, 12, 25),
    }
    if day in closed:
        return "closed"
    early = {
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _last_weekday(year, 5, 0),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4) + timedelta(days=1),
        _previous_weekday(_observed_fixed_holiday(year, 12, 25)),
        _previous_weekday(_observed_fixed_holiday(year, 7, 4)),
    }
    if year >= 2022:
        early.add(_observed_fixed_holiday(year, 6, 19))
    early -= closed
    if day in early:
        return "early_close"
    return "regular"


def _session_close_minutes(day: date) -> int | None:
    kind = cme_equity_index_session(day)
    if kind == "closed":
        return None
    if kind == "early_close":
        return EARLY_CLOSE_ET[0] * 60 + EARLY_CLOSE_ET[1]
    return 16 * 60


def _required_starts(day: date) -> tuple[list[datetime], list[datetime]]:
    """15-minute starts in [07:00, 11:00) and 5-minute starts in [10:00, close)."""
    if day.weekday() >= 5:
        return [], []
    close = _session_close_minutes(day)
    if close is None:
        return [], []
    starts_15: list[datetime] = []
    minute = 7 * 60
    while minute < 11 * 60 and minute < close:
        starts_15.append(datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=ET))
        minute += 15
    starts_5: list[datetime] = []
    minute = 10 * 60
    while minute < close:
        starts_5.append(datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=ET))
        minute += 5
    return starts_15, starts_5


def _et_starts(rows: list[dict]) -> set[tuple[date, int, int]]:
    found: set[tuple[date, int, int]] = set()
    for row in rows:
        local = datetime.fromisoformat(row["timestamp"]).astimezone(ET)
        found.add((local.date(), local.hour, local.minute))
    return found


def classify_written_bars(rows_15m: list[dict], rows_5m: list[dict], start: date, end: date) -> dict:
    """Mark weekdays whose required 15-minute or 5-minute bars are absent.

    A missing bar means no 1-minute print fell in that bucket. Holiday closures
    and the session after a 13:00 ET early close are reported separately and
    are not ``DATA_GAP``.
    """
    present_15 = _et_starts(rows_15m)
    present_5 = _et_starts(rows_5m)
    gaps: list[dict] = []
    closures: list[dict] = []
    day = start
    while day <= end:
        kind = cme_equity_index_session(day) if day.weekday() < 5 else "weekend"
        if kind in {"closed", "early_close"}:
            closures.append({"date": day.isoformat(), "kind": kind})
        required_15, required_5 = _required_starts(day)
        missing_15 = [
            moment.strftime("%H:%M")
            for moment in required_15
            if (day, moment.hour, moment.minute) not in present_15
        ]
        missing_5 = [
            moment.strftime("%H:%M")
            for moment in required_5
            if (day, moment.hour, moment.minute) not in present_5
        ]
        if missing_15 or missing_5:
            gaps.append(
                {
                    "date": day.isoformat(),
                    "status": DATA_GAP,
                    "missing_15m": missing_15,
                    "missing_5m": missing_5,
                    "scored": False,
                }
            )
        day += timedelta(days=1)
    return {
        "data_gaps": gaps,
        "data_gap_count": len(gaps),
        "expected_holiday_closures": closures,
    }


def data_gap_dates_from_trees(research_15m: Path, audit_5m: Path, start: date, end: date) -> dict:
    """Read harness trees and return the same DATA_GAP report as the adapter."""
    rows_15: list[dict] = []
    rows_5: list[dict] = []
    for folder, sink in ((research_15m, rows_15), (audit_5m, rows_5)):
        tree = folder / OUTPUT_INSTRUMENT
        if not tree.is_dir():
            continue
        for path in sorted(tree.glob(f"{OUTPUT_INSTRUMENT}_*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    sink.append(json.loads(line))
    return classify_written_bars(rows_15, rows_5, start, end)


def _closure_kind(moment: datetime) -> str | None:
    local = moment.astimezone(ET)
    kind = cme_equity_index_session(local.date())
    if kind == "closed":
        return "closed"
    if kind == "early_close":
        close = EARLY_CLOSE_ET[0] * 60 + EARLY_CLOSE_ET[1]
        if local.hour * 60 + local.minute >= close:
            return "early_close"
    return None


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


_QUARTER_MONTHS = (3, 6, 9, 12)
_MONTH_CODE = {3: "H", 6: "M", 9: "U", 12: "Z"}


def _third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def _expiration_date(definition: dict) -> date | None:
    for key in ("expiration", "maturity"):
        raw = definition.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, date):
            return raw
        if isinstance(raw, str):
            return date.fromisoformat(raw[:10])
        if isinstance(raw, int):
            if raw > 10**15:
                seconds = raw / 1_000_000_000
            elif raw > 10**12:
                seconds = raw / 1_000
            else:
                seconds = float(raw)
            return datetime.fromtimestamp(seconds, tz=timezone.utc).date()
    return None


def _front_symbol_and_expiry(root: str, on_date: date) -> tuple[str, date]:
    """Front raw symbol plus the third-Friday expiry that symbol stands for.

    ``front_contract`` returns a single year digit, so NQM0 is both June 2010
    and June 2020. The expiry date is what distinguishes them.
    """
    symbol = front_contract(root, on_date, DEFAULT_ROLL_DAYS)
    year, month = on_date.year, on_date.month
    symbol_root = root.strip().upper()
    for _ in range(8):
        quarter_month = next((item for item in _QUARTER_MONTHS if item >= month), None)
        if quarter_month is None:
            year, month = year + 1, 1
            continue
        expiry = _third_friday(year, quarter_month)
        if on_date < expiry - timedelta(days=DEFAULT_ROLL_DAYS):
            expected = f"{symbol_root}{_MONTH_CODE[quarter_month]}{year % 10}"
            if expected != symbol:
                raise RuntimeError(
                    f"front expiry {expiry.isoformat()} produced {expected}, "
                    f"front_contract returned {symbol}"
                )
            return symbol, expiry
        month = quarter_month + 1
        if month > 12:
            year, month = year + 1, 1
    raise RuntimeError(f"could not resolve front expiry for {root} on {on_date}")


def _index_outrights(definitions: list[dict], root: str) -> dict[str, list[dict]]:
    """Index outrights by raw symbol. Identity is instrument_id plus expiration.

    A repeated raw symbol is a decade collision when the expirations differ.
    The same raw symbol and the same expiration is a real ambiguity and refuses.
    """
    root = root.strip().upper()
    grouped: dict[str, list[dict]] = {}
    for definition in definitions:
        symbol = str(definition.get("raw_symbol") or "").replace(" ", "").upper()
        if not OUTRIGHT_RE.fullmatch(symbol):
            continue
        if _parent_root(definition) != root:
            continue
        grouped.setdefault(symbol, []).append(
            {
                "instrument_id": int(definition["instrument_id"]),
                "expiration": _expiration_date(definition),
                "raw_symbol": symbol,
            }
        )
    for symbol, items in grouped.items():
        if len(items) == 1:
            continue
        expirations = [item["expiration"] for item in items]
        if any(item is None for item in expirations) or len(set(expirations)) != len(expirations):
            raise RuntimeError(
                f"ambiguous outright {symbol}: duplicate raw symbol without a distinct expiration"
            )
    return grouped


def _instrument_for_front(indexed: dict[str, list[dict]], symbol: str, expiry: date) -> int | None:
    items = indexed.get(symbol, [])
    if not items:
        return None
    if len(items) == 1:
        only = items[0]
        if only["expiration"] is not None and only["expiration"] != expiry:
            return None
        return only["instrument_id"]
    matched = [item for item in items if item["expiration"] == expiry]
    if len(matched) == 1:
        return matched[0]["instrument_id"]
    if len(matched) > 1:
        raise RuntimeError(
            f"ambiguous outright {symbol} on {expiry.isoformat()}: "
            + ", ".join(str(item["instrument_id"]) for item in matched)
        )
    return None


def _select_bars(
    bars: list[dict], indexed: dict[str, list[dict]], root: str
) -> tuple[dict[str, dict], list[dict]]:
    selected: dict[str, dict] = {}
    contracts: dict[str, dict] = {}
    for bar in bars:
        stamp = datetime.fromtimestamp(int(bar["ts_event"]) / 1_000_000_000, tz=timezone.utc)
        local = stamp.astimezone(ET)
        session_date = local.date().isoformat()
        symbol, expiry = _front_symbol_and_expiry(root, local.date())
        instrument_id = _instrument_for_front(indexed, symbol, expiry)
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


def _aggregate(members: list[datetime], selected: dict[str, dict]) -> tuple[dict | None, list[str]]:
    """Build one bar from the minutes that exist. Never synthesize a missing minute."""
    missing = [_utc_iso(moment) for moment in members if _utc_iso(moment) not in selected]
    present = [moment for moment in members if _utc_iso(moment) in selected]
    if not present:
        return None, missing
    ordered = [selected[_utc_iso(moment)] for moment in present]
    return {
        "timestamp": _utc_iso(members[0]),
        "open": float(ordered[0]["open"]),
        "high": max(float(row["high"]) for row in ordered),
        "low": min(float(row["low"]) for row in ordered),
        "close": float(ordered[-1]["close"]),
        "volume": int(sum(int(row["volume"]) for row in ordered)),
    }, missing


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
    reads. A missing minute is recorded and is not given a price. A bucket
    with at least one minute is still written.
    """
    if end < start:
        raise RuntimeError(f"end {end} before start {start}")
    symbol_root = root.strip().upper()
    if symbol_root not in {"MNQ", "NQ"}:
        raise RuntimeError(f"adapter root must be MNQ or NQ, got {symbol_root!r}")
    indexed = _index_outrights(definitions, symbol_root)
    selected, observed = _select_bars(bars, indexed, symbol_root)
    contracts = {
        item["date"]: item
        for item in observed
    }
    day = start
    while day <= end:
        key = day.isoformat()
        if key not in contracts:
            symbol, expiry = _front_symbol_and_expiry(symbol_root, day)
            contracts[key] = {
                "date": key,
                "raw_symbol": symbol,
                "instrument_id": _instrument_for_front(indexed, symbol, expiry),
            }
        day += timedelta(days=1)

    minutes = _expected_minutes(start, end)
    missing: list[dict] = []
    written: dict[int, list[dict]] = {5: [], 15: []}
    for width in (5, 15):
        for members in _bucket_starts(minutes, width):
            aggregate, missing_minutes = _aggregate(members, selected)
            closure = _closure_kind(members[0])
            if missing_minutes:
                missing.append(
                    {
                        "timeframe_minutes": width,
                        "bar_start": _utc_iso(members[0]),
                        "missing_minutes": missing_minutes,
                        "expected_holiday_closure": closure is not None,
                        "closure_kind": closure,
                    }
                )
            if aggregate is not None:
                written[width].append(aggregate)
    gap_report = classify_written_bars(written[15], written[5], start, end)

    _write_tree(written[15], research_15m)
    _write_tree(written[5], audit_5m)
    label = NQ_LABEL if symbol_root == "NQ" else "MNQ"
    report = {
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
        "data_gaps": gap_report["data_gaps"],
        "data_gap_count": gap_report["data_gap_count"],
        "expected_holiday_closures": gap_report["expected_holiday_closures"],
        "roll_date_basis": "civil ET date of the bar start; roll_days=8; not the CME session date",
    }
    if symbol_root == "NQ":
        report["result_label"] = NQ_RESEARCH_LABEL
    return report
