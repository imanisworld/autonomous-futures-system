"""Synthetic-fixture tests for the Databento 1-minute adapter.

No vendor download. Bars are built in memory and written only under tmp_path.
"""
from __future__ import annotations

import ast
import json
from datetime import date, datetime, timezone

import pytest
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.edge_decomposition_audit import _is_eod_bar

ET = ZoneInfo("America/New_York")
UTC_PLUS = "+00:00"


def _ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


def _minute(day: date, hour: int, minute: int, *, fold: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET, fold=fold)


def _bar(instrument_id: int, moment: datetime, price: float, *, volume: int = 1) -> dict:
    return {
        "instrument_id": instrument_id,
        "ts_event": _ns(moment),
        "open": price,
        "high": price + 0.5,
        "low": price - 0.5,
        "close": price + 0.25,
        "volume": volume,
    }


def _minutes(instrument_id: int, day: date, hour: int, start_minute: int, count: int, price: float):
    return [
        _bar(instrument_id, _minute(day, hour, start_minute + offset), price + offset, volume=offset + 1)
        for offset in range(count)
    ]


def _adapt(tmp_path: Path, definitions, bars, **kwargs):
    from research.databento_322_bar_adapter import adapt_ohlcv_1m

    research = tmp_path / "research"
    audit = tmp_path / "audit"
    report = adapt_ohlcv_1m(
        definitions,
        bars,
        research_15m=research,
        audit_5m=audit,
        **kwargs,
    )
    return research, audit, report


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_resample_uses_bar_start_and_emits_the_exact_1555_bar(tmp_path):
    day = date(2024, 8, 2)
    definitions = [
        {"instrument_id": 7, "raw_symbol": "MNQU4", "asset": "MNQ.FUT", "expiration": "2024-09-20"},
    ]
    bars = _minutes(7, day, 15, 45, 15, 100.0)
    research, audit, report = _adapt(
        tmp_path,
        definitions,
        bars,
        root="MNQ",
        start=day,
        end=day,
    )
    five = _rows(audit / "MNQ" / "MNQ_2024-08-02.jsonl")
    fifteen = _rows(research / "MNQ" / "MNQ_2024-08-02.jsonl")
    assert [row["timestamp"] for row in five] == [
        "2024-08-02T19:45:00+00:00",
        "2024-08-02T19:50:00+00:00",
        "2024-08-02T19:55:00+00:00",
    ]
    assert [row["timestamp"] for row in fifteen] == ["2024-08-02T19:45:00+00:00"]
    eod = five[-1]
    assert eod["open"] == 110.0
    assert eod["high"] == 114.5
    assert eod["low"] == 109.5
    assert eod["close"] == 114.25
    assert eod["volume"] == sum(range(11, 16))
    assert _is_eod_bar(eod["timestamp"], 5) is True
    assert report["roll_days"] == 8
    assert report["output_instrument"] == "MNQ"


def test_est_1555_bar_starts_at_2055_utc(tmp_path):
    day = date(2024, 1, 16)
    definitions = [{"instrument_id": 3, "raw_symbol": "MNQH4", "parent": "MNQ", "expiration": "2024-03-15"}]
    bars = _minutes(3, day, 15, 55, 5, 50.0)
    _research, audit, _report = _adapt(
        tmp_path, definitions, bars, root="MNQ", start=day, end=day
    )
    rows = _rows(audit / "MNQ" / "MNQ_2024-01-16.jsonl")
    eod = next(row for row in rows if row["timestamp"].endswith("T20:55:00+00:00"))
    assert eod["timestamp"] == "2024-01-16T20:55:00+00:00"
    assert _is_eod_bar(eod["timestamp"], 5) is True
    assert UTC_PLUS in eod["timestamp"]


def test_partial_bucket_is_recorded_and_never_price_filled(tmp_path):
    day = date(2024, 8, 2)
    definitions = [{"instrument_id": 7, "raw_symbol": "MNQU4", "asset": "MNQ", "expiration": "2024-09-20"}]
    bars = _minutes(7, day, 15, 50, 5, 10.0)
    bars.extend(
        _bar(7, _minute(day, 15, minute), 80.0)
        for minute in (55, 56, 57, 59)
    )
    _research, audit, report = _adapt(
        tmp_path, definitions, bars, root="MNQ", start=day, end=day
    )
    five = _rows(audit / "MNQ" / "MNQ_2024-08-02.jsonl")
    stamps = [row["timestamp"] for row in five]
    assert "2024-08-02T19:50:00+00:00" in stamps
    partial_bar = next(row for row in five if row["timestamp"] == "2024-08-02T19:55:00+00:00")
    assert partial_bar["open"] == 80.0
    assert partial_bar["high"] == 80.5
    assert partial_bar["low"] == 79.5
    assert partial_bar["close"] == 80.25
    assert partial_bar["volume"] == 4
    missing = report["missing_bars"]
    partial = next(item for item in missing if item["bar_start"] == "2024-08-02T19:55:00+00:00")
    assert partial["timeframe_minutes"] == 5
    assert partial["missing_minutes"] == ["2024-08-02T19:58:00+00:00"]
    assert all(row["close"] != 14.25 or row["timestamp"] != "2024-08-02T19:55:00+00:00" for row in five)
    assert all("filled" not in json.dumps(item) for item in missing)


def test_absent_interior_bucket_is_recorded_not_filled(tmp_path):
    day = date(2024, 8, 2)
    definitions = [{"instrument_id": 7, "raw_symbol": "MNQU4", "asset": "MNQ", "expiration": "2024-09-20"}]
    bars = _minutes(7, day, 10, 0, 5, 1.0) + _minutes(7, day, 10, 10, 5, 2.0)
    _research, audit, report = _adapt(
        tmp_path, definitions, bars, root="MNQ", start=day, end=day
    )
    stamps = [row["timestamp"] for row in _rows(audit / "MNQ" / "MNQ_2024-08-02.jsonl")]
    assert "2024-08-02T14:00:00+00:00" in stamps
    assert "2024-08-02T14:10:00+00:00" in stamps
    assert "2024-08-02T14:05:00+00:00" not in stamps
    hole = next(item for item in report["missing_bars"] if item["bar_start"] == "2024-08-02T14:05:00+00:00")
    assert len(hole["missing_minutes"]) == 5


def test_session_boundary_does_not_cross_the_1700_halt(tmp_path):
    day = date(2024, 8, 1)  # Thursday
    definitions = [{"instrument_id": 7, "raw_symbol": "MNQU4", "asset": "MNQ", "expiration": "2024-09-20"}]
    bars = _minutes(7, day, 16, 55, 5, 30.0) + _minutes(7, day, 18, 0, 5, 90.0)
    _research, audit, report = _adapt(
        tmp_path, definitions, bars, root="MNQ", start=day, end=day
    )
    stamps = [row["timestamp"] for row in _rows(audit / "MNQ" / "MNQ_2024-08-01.jsonl")]
    assert "2024-08-01T20:55:00+00:00" in stamps
    assert "2024-08-01T22:00:00+00:00" in stamps
    assert "2024-08-01T21:00:00+00:00" not in stamps
    halt_starts = [
        item["bar_start"]
        for item in report["missing_bars"]
        if item["bar_start"].startswith("2024-08-01T21:")
    ]
    assert halt_starts == []


def test_roll_follows_front_contract_and_ignores_the_continuous_symbol(tmp_path):
    definitions = [
        {"instrument_id": 1, "raw_symbol": "MNQM4", "asset": "MNQ", "expiration": "2024-06-21"},
        {"instrument_id": 2, "raw_symbol": "MNQU4", "asset": "MNQ", "expiration": "2024-09-20"},
        {"instrument_id": 99, "raw_symbol": "MNQ.c.0", "asset": "MNQ"},
    ]
    before = date(2024, 6, 12)
    after = date(2024, 6, 13)
    bars = (
        _minutes(1, before, 10, 0, 5, 11.0)
        + _minutes(2, before, 10, 0, 5, 22.0)
        + _minutes(99, before, 10, 0, 5, 99.0)
        + _minutes(1, after, 10, 0, 5, 33.0)
        + _minutes(2, after, 10, 0, 5, 44.0)
        + _minutes(99, after, 10, 0, 5, 99.0)
    )
    _research, audit, report = _adapt(
        tmp_path, definitions, bars, root="MNQ", start=before, end=after
    )
    june_12 = _rows(audit / "MNQ" / "MNQ_2024-06-12.jsonl")
    june_13 = _rows(audit / "MNQ" / "MNQ_2024-06-13.jsonl")
    chosen_12 = next(row for row in june_12 if row["timestamp"] == "2024-06-12T14:00:00+00:00")
    chosen_13 = next(row for row in june_13 if row["timestamp"] == "2024-06-13T14:00:00+00:00")
    assert chosen_12["open"] == 11.0
    assert chosen_13["open"] == 44.0
    assert all(row["open"] not in {22.0, 99.0, 33.0} for row in june_12)
    assert all(row["open"] != 99.0 for row in june_13)
    by_day = {item["date"]: item["raw_symbol"] for item in report["contracts"]}
    assert by_day["2024-06-12"] == "MNQM4"
    assert by_day["2024-06-13"] == "MNQU4"


def test_nq_parent_bars_are_written_on_the_mnq_harness_path(tmp_path):
    day = date(2014, 6, 11)
    definitions = [
        {"instrument_id": 5, "raw_symbol": "NQM4", "parent": "NQ.FUT", "expiration": "2014-06-20"},
        {"instrument_id": 6, "raw_symbol": "MNQM4", "asset": "MNQ", "expiration": "2014-06-20"},
    ]
    bars = _minutes(5, day, 15, 55, 5, 70.0) + _minutes(6, day, 15, 55, 5, 8.0)
    _research, audit, report = _adapt(
        tmp_path, definitions, bars, root="NQ", start=day, end=day
    )
    rows = _rows(audit / "MNQ" / "MNQ_2014-06-11.jsonl")
    eod = next(row for row in rows if row["timestamp"] == "2014-06-11T19:55:00+00:00")
    assert eod["open"] == 70.0
    assert report["price_source"] == "NQ"
    assert report["output_instrument"] == "MNQ"
    assert report["dollar_basis"] == "MNQ-equivalent ($2/point)"
    assert report["label"] == "best available evidence, not fully clean"
    assert report["result_label"] == "MNQ-equivalent research, not executable historical MNQ"
    assert all(row["open"] != 8.0 for row in rows)


def _required_rows(day: date) -> tuple[list[dict], list[dict]]:
    from research.databento_322_bar_adapter import _required_starts

    def row(moment: datetime) -> dict:
        return {
            "timestamp": moment.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1,
        }

    starts_15, starts_5 = _required_starts(day)
    return [row(moment) for moment in starts_15], [row(moment) for moment in starts_5]


def test_missing_15m_bar_in_the_morning_window_is_a_data_gap():
    from research.databento_322_bar_adapter import classify_written_bars

    day = date(2024, 8, 2)
    rows_15, rows_5 = _required_rows(day)
    rows_15 = [row for row in rows_15 if not row["timestamp"].endswith("T12:00:00+00:00")]
    report = classify_written_bars(rows_15, rows_5, day, day)
    assert report["data_gap_count"] == 1
    gap = report["data_gaps"][0]
    assert gap["status"] == "DATA_GAP"
    assert gap["scored"] is False
    assert "08:00" in gap["missing_15m"]
    assert gap["missing_5m"] == []


def test_missing_5m_bar_in_the_session_window_is_a_data_gap():
    from research.databento_322_bar_adapter import classify_written_bars

    day = date(2024, 8, 2)
    rows_15, rows_5 = _required_rows(day)
    rows_5 = [row for row in rows_5 if not row["timestamp"].endswith("T18:00:00+00:00")]
    report = classify_written_bars(rows_15, rows_5, day, day)
    assert report["data_gap_count"] == 1
    gap = report["data_gaps"][0]
    assert gap["scored"] is False
    assert "14:00" in gap["missing_5m"]
    assert gap["missing_15m"] == []


def test_early_close_does_not_treat_the_1555_bar_as_a_data_gap():
    from research.databento_322_bar_adapter import cme_equity_index_session, classify_written_bars

    day = date(2025, 1, 20)
    assert cme_equity_index_session(day) == "early_close"
    rows_15, rows_5 = _required_rows(day)
    report = classify_written_bars(rows_15, rows_5, day, day)
    assert report["data_gap_count"] == 0
    assert report["expected_holiday_closures"] == [{"date": "2025-01-20", "kind": "early_close"}]
    assert all(row["timestamp"] < "2025-01-20T18:00:00+00:00" for row in rows_5)


def test_full_holiday_closure_is_not_a_data_gap():
    from research.databento_322_bar_adapter import cme_equity_index_session, classify_written_bars

    day = date(2024, 12, 25)
    assert cme_equity_index_session(day) == "closed"
    report = classify_written_bars([], [], day, day)
    assert report["data_gap_count"] == 0
    assert report["expected_holiday_closures"] == [{"date": "2024-12-25", "kind": "closed"}]


def test_definition_without_a_contract_month_is_refused(tmp_path):
    day = date(2024, 8, 2)
    definitions = [{"instrument_id": 7, "raw_symbol": "MNQU4", "asset": "MNQ"}]
    with pytest.raises(RuntimeError, match="no expiration or maturity"):
        _adapt(tmp_path, definitions, _minutes(7, day, 10, 0, 5, 1.0), root="MNQ", start=day, end=day)


def test_decade_symbol_collision_resolves_by_expiration_and_fails_closed(tmp_path):
    """NQM0 is both the 2010 and 2020 June contracts. The raw symbol is not enough."""
    definitions = [
        {
            "instrument_id": 6641,
            "raw_symbol": "NQM0",
            "asset": "NQ",
            "maturity_year": 2010,
            "maturity_month": 6,
        },
        {"instrument_id": 16908, "raw_symbol": "NQM0", "asset": "NQ", "expiration": "2020-06-19"},
    ]
    day_2010 = date(2010, 6, 7)
    day_2020 = date(2020, 6, 8)
    bars_2010 = _minutes(6641, day_2010, 10, 0, 5, 10.0) + _minutes(16908, day_2010, 10, 0, 5, 20.0)
    bars_2020 = _minutes(6641, day_2020, 10, 0, 5, 30.0) + _minutes(16908, day_2020, 10, 0, 5, 40.0)
    _research, audit, report_2010 = _adapt(
        tmp_path, definitions, bars_2010, root="NQ", start=day_2010, end=day_2010
    )
    chosen_2010 = next(
        row
        for row in _rows(audit / "MNQ" / "MNQ_2010-06-07.jsonl")
        if row["timestamp"] == "2010-06-07T14:00:00+00:00"
    )
    assert chosen_2010["open"] == 10.0
    assert {item["date"]: item["instrument_id"] for item in report_2010["contracts"]}["2010-06-07"] == 6641
    _research, audit, report_2020 = _adapt(
        tmp_path, definitions, bars_2020, root="NQ", start=day_2020, end=day_2020
    )
    chosen_2020 = next(
        row
        for row in _rows(audit / "MNQ" / "MNQ_2020-06-08.jsonl")
        if row["timestamp"] == "2020-06-08T14:00:00+00:00"
    )
    assert chosen_2020["open"] == 40.0
    assert {item["date"]: item["instrument_id"] for item in report_2020["contracts"]}["2020-06-08"] == 16908
    from research.databento_322_bar_adapter import _index_outrights

    with pytest.raises(RuntimeError, match="ambiguous outright NQM0"):
        _index_outrights(
            [
                {"instrument_id": 1, "raw_symbol": "NQM0", "asset": "NQ", "expiration": "2010-06-18"},
                {"instrument_id": 2, "raw_symbol": "NQM0", "asset": "NQ", "expiration": "2010-06-18"},
            ],
            "NQ",
        )


def test_juneteenth_shifted_expiration_still_follows_the_harness_roll(tmp_path):
    """MNQM6 expires on Thursday 2026-06-18. The harness still rolls on 2026-03-12."""
    definitions = [
        {"instrument_id": 41, "raw_symbol": "MNQH6", "asset": "MNQ", "expiration": "2026-03-20"},
        {"instrument_id": 42, "raw_symbol": "MNQM6", "asset": "MNQ", "expiration": "2026-06-18"},
    ]
    before = date(2026, 3, 11)
    roll_day = date(2026, 3, 12)
    bars = (
        _minutes(41, before, 10, 0, 5, 11.0)
        + _minutes(42, before, 10, 0, 5, 22.0)
        + _minutes(41, roll_day, 10, 0, 5, 33.0)
        + _minutes(42, roll_day, 10, 0, 5, 44.0)
    )
    _research, audit, report = _adapt(
        tmp_path, definitions, bars, root="MNQ", start=before, end=roll_day
    )
    march_11 = next(
        row
        for row in _rows(audit / "MNQ" / "MNQ_2026-03-11.jsonl")
        if row["timestamp"] == "2026-03-11T14:00:00+00:00"
    )
    march_12 = next(
        row
        for row in _rows(audit / "MNQ" / "MNQ_2026-03-12.jsonl")
        if row["timestamp"] == "2026-03-12T14:00:00+00:00"
    )
    assert march_11["open"] == 11.0
    assert march_12["open"] == 44.0
    by_day = {item["date"]: item["instrument_id"] for item in report["contracts"]}
    assert by_day["2026-03-11"] == 41
    assert by_day["2026-03-12"] == 42

    with pytest.raises(RuntimeError, match="missing outright MNQM6"):
        _adapt(
            tmp_path,
            [definitions[0]],
            _minutes(41, roll_day, 10, 0, 5, 11.0),
            root="MNQ",
            start=roll_day,
            end=roll_day,
        )
    with pytest.raises(RuntimeError, match="ambiguous outright MNQM6"):
        _adapt(
            tmp_path,
            definitions
            + [{"instrument_id": 43, "raw_symbol": "MNQM6", "asset": "MNQ", "expiration": "2026-06-19"}],
            bars,
            root="MNQ",
            start=roll_day,
            end=roll_day,
        )


def test_adapter_source_does_not_open_a_network_client():
    path = Path(__file__).resolve().parents[1] / "research/databento_322_bar_adapter.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"httpx", "requests", "urllib", "databento", "socket"})
    for banned in ("urlopen", "httpx", "POLYGON_API_KEY", "DATABENTO"):
        assert banned not in source
