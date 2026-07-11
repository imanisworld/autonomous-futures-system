"""
tests/test_stocks_csv_loader.py

stocks_advisory/csv_loader.py tests. Proves the CSV -> Bar/DaySession
conversion: TradingView/BATS-style extra columns are tolerated, missing
required columns raise, the regular-hours filter keeps only 09:30-16:00
ET bars, a day missing its 09:30 open bar is excluded (not silently
evaluated on a truncated session), DaySession construction correctly
aligns QQQ/TQQQ/SQQQ by date and pulls qqq_previous_close/high/low from
the immediately preceding QQQ session in the file -- plus the same
no-network/no-broker/no-clock-access guarantees as the rest of this
lane (this module is the one exception permitted to touch the local
filesystem, since reading the caller-supplied CSV path *is* its job).
"""

from __future__ import annotations

import ast
from pathlib import Path

import stocks_advisory.csv_loader as csv_loader_module
from stocks_advisory.csv_loader import (
    CsvValidationError,
    build_day_sessions,
    load_bars_from_csv,
)

_FORBIDDEN_IMPORT_FRAGMENTS = (
    "robin_stocks",
    "robinhood",
    "execution",
    "webhook",
    "broker",
    "ib_insync",
    "ibapi",
    "tradovate",
    "options_manager",
    "strategy",
    "risk_engine",
    "requests",
    "httpx",
    "urllib",
    "socket",
    "aiohttp",
    "websocket",
)

_ALLOWED_IMPORTS_OUTSIDE_STOCKS_ADVISORY = (
    "__future__",
    "csv",
    "dataclasses",
    "datetime",
    "pathlib",
    "typing",
)

_TRADINGVIEW_HEADER = "time,open,high,low,close,Bar Type 1 Label,Bar Type 2 Label,Bar Type 3 Label,Volume\n"
_PLAIN_HEADER = "timestamp,open,high,low,close,volume\n"


def _tv_row(ts: str, o: float, h: float, l: float, c: float, v: int) -> str:
    return f"{ts},{o},{h},{l},{c},0,1,0,{v}\n"


def _write_csv(path: Path, header: str, rows: list[str]) -> Path:
    path.write_text(header + "".join(rows), encoding="utf-8")
    return path


def _rth_ts(date: str, index: int) -> str:
    minute = 30 + index * 5
    hour = 9 + minute // 60
    return f"{date}T{hour:02d}:{minute % 60:02d}:00-04:00"


def _qqq_two_day_rows() -> list[str]:
    rows = []
    for i in range(8):
        rows.append(_tv_row(_rth_ts("2026-07-07", i), 400 + i, 401 + i, 399 + i, 400.5 + i, 1000))
    for i in range(8):
        rows.append(_tv_row(_rth_ts("2026-07-08", i), 410 + i, 411 + i, 409 + i, 410.5 + i, 1000))
    return rows


def test_loads_tradingview_style_extra_columns(tmp_path):
    path = _write_csv(tmp_path / "BATS_QQQ_5.csv", _TRADINGVIEW_HEADER, _qqq_two_day_rows())
    loaded = load_bars_from_csv(path)
    assert loaded.rows_read == 16
    assert loaded.symbol == "QQQ"
    assert len(loaded.all_bars) == 16


def test_missing_required_column_raises(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("time,open,high,low,Bar Type 1 Label,Volume\n2026-07-07T09:30:00-04:00,1,2,0,0,100\n")
    try:
        load_bars_from_csv(path)
        assert False, "expected CsvValidationError"
    except CsvValidationError as exc:
        assert "close" in str(exc)


def test_missing_timestamp_column_raises(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("open,high,low,close,volume\n1,2,0,1.5,100\n")
    try:
        load_bars_from_csv(path)
        assert False, "expected CsvValidationError"
    except CsvValidationError as exc:
        assert "timestamp" in str(exc)


def test_regular_hours_filter_excludes_premarket_and_afterhours(tmp_path):
    rows = [
        _tv_row("2026-07-07T09:00:00-04:00", 1, 1, 1, 1, 100),  # premarket
        _tv_row("2026-07-07T09:30:00-04:00", 1, 1, 1, 1, 100),  # RTH open
        _tv_row("2026-07-07T15:55:00-04:00", 1, 1, 1, 1, 100),  # last RTH bar
        _tv_row("2026-07-07T16:00:00-04:00", 1, 1, 1, 1, 100),  # after close
        _tv_row("2026-07-07T18:00:00-04:00", 1, 1, 1, 1, 100),  # afterhours
    ]
    path = _write_csv(tmp_path / "BATS_QQQ_5.csv", _TRADINGVIEW_HEADER, rows)
    loaded = load_bars_from_csv(path)
    assert loaded.rows_read == 5
    assert len(loaded.rth_bars) == 2
    assert loaded.rows_outside_regular_hours == 3


def test_plain_lowercase_header_also_works(tmp_path):
    rows = ["2026-07-07T09:30:00-04:00,1,2,0.5,1.5,100\n"]
    path = _write_csv(tmp_path / "plain.csv", _PLAIN_HEADER, rows)
    loaded = load_bars_from_csv(path)
    assert len(loaded.all_bars) == 1
    assert loaded.all_bars[0].volume == 100


def test_day_missing_930_open_bar_is_excluded(tmp_path):
    qqq_rows = [
        _tv_row("2026-07-06T09:30:00-04:00", 400, 401, 399, 400.5, 1000),
        _tv_row("2026-07-07T09:45:00-04:00", 405, 406, 404, 405.5, 1000),  # missing 09:30 print
    ]
    tqqq_rows = [
        _tv_row("2026-07-06T09:30:00-04:00", 70, 71, 69, 70.5, 1000),
        _tv_row("2026-07-07T09:30:00-04:00", 71, 72, 70, 71.5, 1000),
    ]
    sqqq_rows = [
        _tv_row("2026-07-06T09:30:00-04:00", 40, 41, 39, 40.5, 1000),
        _tv_row("2026-07-07T09:30:00-04:00", 39, 40, 38, 39.5, 1000),
    ]
    qqq = load_bars_from_csv(_write_csv(tmp_path / "QQQ.csv", _TRADINGVIEW_HEADER, qqq_rows))
    tqqq = load_bars_from_csv(_write_csv(tmp_path / "TQQQ.csv", _TRADINGVIEW_HEADER, tqqq_rows))
    sqqq = load_bars_from_csv(_write_csv(tmp_path / "SQQQ.csv", _TRADINGVIEW_HEADER, sqqq_rows))

    sessions, report = build_day_sessions(qqq, tqqq, sqqq)

    assert sessions == []  # 07-06 has no prior session; 07-07 is missing its open bar
    reasons = dict(report.excluded_dates)
    assert "no prior QQQ session" in reasons["2026-07-06"]
    assert "missing regular-hours open bar" in reasons["2026-07-07"]


def test_day_sessions_align_by_date_and_pull_prior_session_stats(tmp_path):
    qqq = load_bars_from_csv(_write_csv(tmp_path / "QQQ.csv", _TRADINGVIEW_HEADER, _qqq_two_day_rows()))
    tqqq_rows = [_tv_row(_rth_ts("2026-07-08", i), 70 + i, 71 + i, 69 + i, 70.5 + i, 500) for i in range(8)]
    sqqq_rows = [_tv_row(_rth_ts("2026-07-08", i), 40 + i, 41 + i, 39 + i, 40.5 + i, 500) for i in range(8)]
    tqqq = load_bars_from_csv(_write_csv(tmp_path / "TQQQ.csv", _TRADINGVIEW_HEADER, tqqq_rows))
    sqqq = load_bars_from_csv(_write_csv(tmp_path / "SQQQ.csv", _TRADINGVIEW_HEADER, sqqq_rows))

    sessions, report = build_day_sessions(qqq, tqqq, sqqq)

    assert report.common_dates == ("2026-07-08",)
    assert len(sessions) == 1
    session = sessions[0]
    assert session.date == "2026-07-08"
    # 2026-07-07 QQQ RTH bars: opens 400..407, highs 401..408, lows 399..406
    assert session.qqq_previous_close == qqq.rth_bars[7].close
    assert session.qqq_previous_high == max(b.high for b in qqq.rth_bars[:8])
    assert session.qqq_previous_low == min(b.low for b in qqq.rth_bars[:8])
    assert len(session.qqq_bars) == 8
    assert len(session.tqqq_bars) == 8
    assert len(session.sqqq_bars) == 8


def test_day_missing_one_symbol_entirely_is_excluded_with_reason(tmp_path):
    qqq = load_bars_from_csv(_write_csv(tmp_path / "QQQ.csv", _TRADINGVIEW_HEADER, _qqq_two_day_rows()))
    tqqq_rows = [_tv_row("2026-07-08T09:30:00-04:00", 70, 71, 69, 70.5, 500)]
    tqqq = load_bars_from_csv(_write_csv(tmp_path / "TQQQ.csv", _TRADINGVIEW_HEADER, tqqq_rows))
    sqqq = load_bars_from_csv(_write_csv(tmp_path / "SQQQ.csv", _TRADINGVIEW_HEADER, []))  # no data at all

    sessions, report = build_day_sessions(qqq, tqqq, sqqq)

    assert sessions == []
    reasons = dict(report.excluded_dates)
    assert "no SQQQ regular-hours data" in reasons["2026-07-08"]


def _imported_modules(module) -> list[str]:
    source = Path(module.__file__).read_text()
    tree = ast.parse(source)
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_module_has_no_forbidden_imports():
    imported = _imported_modules(csv_loader_module)
    for name in imported:
        for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
            assert forbidden not in name, f"csv_loader must not import {name!r}"


def test_module_has_no_cross_boundary_imports_outside_stocks_advisory():
    imported = _imported_modules(csv_loader_module)
    outside = [
        name
        for name in imported
        if not name.startswith("stocks_advisory")
        and name.split(".")[0] not in _ALLOWED_IMPORTS_OUTSIDE_STOCKS_ADVISORY
    ]
    assert not outside, f"csv_loader has an unexpected import: {outside}"


def test_module_has_no_clock_access():
    source = Path(csv_loader_module.__file__).read_text()
    for forbidden in ("datetime.now(", "time.time(", "date.today("):
        assert forbidden not in source, f"csv_loader must not contain {forbidden!r}"


def test_no_futures_or_options_module_imports_csv_loader():
    repo_root = Path(__file__).resolve().parent.parent
    scanned_dirs = [
        repo_root / "options_manager",
        repo_root / "execution",
        repo_root / "webhook",
        repo_root / "strategy",
        repo_root / "risk",
    ]
    offenders = []
    for directory in scanned_dirs:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.py"):
            source = path.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                if any("csv_loader" in name or "stocks_advisory" in name for name in names):
                    offenders.append(str(path))
    assert not offenders, f"csv_loader must not be imported from: {offenders}"
