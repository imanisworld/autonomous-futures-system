"""Simulated-clock tests for the observation-only setup-capture collector.

Fixtures use an availability oracle (bars served only after bar_end + lag) and
recorded-style Public 30m / Alpaca tape prints. Arm times are never assigned
directly — they come from watcher.run(now=sim_now).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from alert_ranker.app import _latest_clock_unsynced, _setup_capture_telemetry, create_app
from alert_ranker.causal_bars import MINUTE_30, Bar
from alert_ranker.config import ScannerConfig
from alert_ranker.market_data import PUBLIC_ALLOWED_PREFIXES, PUBLIC_MARKETDATA_PREFIX
from alert_ranker.paper_v1 import episode_bucket, setup_episode_key
from alert_ranker.public_chart_bars import parse_complete_grid_bars
from alert_ranker.session_calendar import nyse_session_for
from alert_ranker.setup_capture import (
    CAPTURE_VERSION,
    DEFAULT_JOURNAL,
    OPTION_ROOT_SYMBOLS,
    STATUS_DATA_BLOCKED,
    STATUS_EXPIRED,
    STATUS_GAP_THROUGH_OPEN,
    STATUS_INVALIDATED,
    STATUS_MISSED_LATE,
    STATUS_TRIGGERED,
    STATUS_WATCHING,
    WATCHER_UNIVERSE,
    ArmedStructure,
    CaptureRecord,
    IndexMinuteBar,
    TapePrint,
    catch_count,
    consume_risk_budget,
    format_level,
    link_scanner_first_sight,
    quantize_level,
    quote_instrument_type,
    rebuild_session_hours,
    reject_vendor_hour_bars,
    structure_key,
    submit_broker_order,
    watcher_universe,
)
from alert_ranker.setup_capture_engine import (
    BarAvailabilityOracle,
    SetupCaptureEngine,
    alpaca_spx_forbidden,
    assert_no_forbidden_imports,
    module_has_no_execution_imports,
)
from alert_ranker.setup_capture_store import JournalLocked, SetupCaptureJournal

# Direct loopback peer for TestClient: /setup-capture is gated (not in
# PUBLIC_PATHS). Blank access_token allows only unproxied loopback.
LOCAL_PEER = ("127.0.0.1", 50000)
NY = ZoneInfo("America/New_York")
OCT5_HIGH = 770.0768
OCT5_LOW = 769.17
ROOT = Path(__file__).resolve().parents[1]


def et(year: int, month: int, day: int, hour: int, minute: int, second: int = 0, micro: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, micro, tzinfo=NY)


def _bar(start: datetime, high: float, low: float, open_: float | None = None, close: float | None = None) -> Bar:
    mid = (high + low) / 2
    return Bar(
        start=start.astimezone(timezone.utc),
        open=open_ if open_ is not None else mid,
        high=high,
        low=low,
        close=close if close is not None else mid,
        volume=1_000,
    )


def _print(
    ts: datetime,
    price: float,
    *,
    feed: str = "iex",
    trade_id: str = "t1",
    conditions: tuple[str, ...] = (),
    tape: str = "A",
) -> TapePrint:
    aware = ts.astimezone(timezone.utc)
    ns = int(aware.timestamp()) * 1_000_000_000 + aware.microsecond * 1_000
    iso = aware.strftime("%Y-%m-%dT%H:%M:%S")
    if aware.microsecond:
        iso += f".{aware.microsecond:06d}000"
    iso += "Z"
    return TapePrint(
        timestamp=aware,
        timestamp_ns=ns,
        price=price,
        trade_id=trade_id,
        feed=feed,
        conditions=conditions,
        tape=tape,
    )


def oct2_30m() -> list[Bar]:
    """Friday Oct 2 2026 RTH 30m bars that rebuild the Oct 5 H1 2U-2U stub."""
    d = date_oct2 = et(2026, 10, 2, 9, 30)
    starts = []
    cursor = et(2026, 10, 2, 9, 30)
    while cursor < et(2026, 10, 2, 16, 0):
        starts.append(cursor)
        cursor += timedelta(minutes=30)
    # Defaults: quiet inside range, then the last three hours match the audit.
    bars = []
    for start in starts:
        if start == et(2026, 10, 2, 13, 30):
            bars.append(_bar(start, 769.47, 768.50))
        elif start == et(2026, 10, 2, 14, 0):
            bars.append(_bar(start, 769.40, 768.42))
        elif start == et(2026, 10, 2, 14, 30):
            bars.append(_bar(start, 769.60, 768.80))
        elif start == et(2026, 10, 2, 15, 0):
            bars.append(_bar(start, 769.55, 768.75))
        elif start == et(2026, 10, 2, 15, 30):
            bars.append(_bar(start, OCT5_HIGH, OCT5_LOW))
        else:
            # earlier directional 2s so daily/30m may or may not arm; keep inside
            bars.append(_bar(start, 768.20, 767.80))
    return bars


def oct2_session_closes() -> dict:
    session = nyse_session_for(et(2026, 10, 2, 12, 0).date())
    assert session is not None
    return {session.open: session.close}


def make_engine(
    tmp_path: Path,
    oracle: BarAvailabilityOracle,
    *,
    iex: list[TapePrint] | None = None,
    sip: list[TapePrint] | None = None,
    index: list[IndexMinuteBar] | None = None,
    clock_offset_s: float = 0.0,
    fetch_delay: timedelta = timedelta(0),
    chain_hook=None,
    equity_feed_available: bool = True,
    wall_clock=None,
    spy_only: bool = True,
):
    journal = SetupCaptureJournal(tmp_path / "options_setup_capture.jsonl")

    def bars(symbol: str, now: datetime):
        if spy_only and symbol != "SPY":
            return []
        return oracle.serve(now)

    def iex_fn(symbol: str, start: datetime, end: datetime):
        alpaca_spx_forbidden(symbol)
        rows = iex or []
        return [row for row in rows if start <= row.ts_utc < end]

    def sip_fn(symbol: str, start: datetime, end: datetime):
        alpaca_spx_forbidden(symbol)
        rows = sip or []
        return [row for row in rows if start <= row.ts_utc < end]

    def index_fn(symbol: str, start: datetime, end: datetime):
        rows = index or []
        return [row for row in rows if start <= row.start.astimezone(timezone.utc) < end]

    return SetupCaptureEngine(
        journal=journal,
        bar_oracle=bars,
        iex_prints=iex_fn if equity_feed_available else None,
        sip_prints=sip_fn if equity_feed_available else None,
        index_minutes=index_fn,
        clock_offset_s=clock_offset_s,
        fetch_delay=fetch_delay,
        wall_clock=wall_clock,
        chain_hook=chain_hook,
    )


def minute_loop(engine: SetupCaptureEngine, start: datetime, end: datetime) -> None:
    t = start
    while t <= end:
        engine.run(now=t)
        t += timedelta(minutes=1)


def watching_rows(engine: SetupCaptureEngine) -> list[CaptureRecord]:
    return [row for row in engine.journal.list_all(limit=500) if row.timeframe == "1H"]


def test_a1_watching_before_monday_open_from_friday_oracle(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), publication_lag=timedelta(0), session_closes=oct2_session_closes())
    engine = make_engine(tmp_path, oracle)
    friday = et(2026, 10, 2, 16, 16)
    engine.run(now=friday)
    rows = [row for row in watching_rows(engine) if abs(row.boundary_high - OCT5_HIGH) < 1e-6]
    assert len(rows) == 1
    row = rows[0]
    assert row.status == STATUS_WATCHING
    assert row.timeframe == "1H"
    assert abs(row.boundary_high - OCT5_HIGH) < 1e-6
    assert abs(row.boundary_low - OCT5_LOW) < 1e-6
    persisted = datetime.fromisoformat(row.persisted_at)
    assert persisted == friday.astimezone(timezone.utc)
    assert persisted < et(2026, 10, 5, 9, 30).astimezone(timezone.utc)
    assert "1H" in row.structure_key
    assert datetime.fromisoformat(row.structure_close) == et(2026, 10, 2, 16, 0).astimezone(timezone.utc)


def test_c1a_removing_bar_yields_no_watching(tmp_path):
    bars = oct2_30m()
    stub = [bar for bar in bars if bar.start_utc == et(2026, 10, 2, 15, 30).astimezone(timezone.utc)][0]
    oracle = BarAvailabilityOracle(bars, session_closes=oct2_session_closes())
    oracle.remove(stub)
    engine = make_engine(tmp_path, oracle)
    engine.run(now=et(2026, 10, 2, 16, 16))
    oct5_keys = [
        row.structure_key
        for row in watching_rows(engine)
        if abs(row.boundary_high - OCT5_HIGH) < 1e-6
    ]
    assert oct5_keys == []


def test_c2a_partial_stub_does_not_arm(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(tmp_path, oracle)
    engine.run(now=et(2026, 10, 2, 15, 46))
    assert not any(abs(row.boundary_high - OCT5_HIGH) < 1e-6 for row in watching_rows(engine))
    engine.run(now=et(2026, 10, 2, 15, 59, 30))
    assert not any(abs(row.boundary_high - OCT5_HIGH) < 1e-6 for row in watching_rows(engine))
    engine.run(now=et(2026, 10, 2, 16, 0, 1))
    oct5 = [row for row in watching_rows(engine) if abs(row.boundary_high - OCT5_HIGH) < 1e-6]
    assert len(oct5) == 1
    assert abs(oct5[0].boundary_low - OCT5_LOW) < 1e-6


def test_a5_cold_start_101645_is_missed_late(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    cross = _print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="iex")
    sip = _print(et(2026, 10, 5, 9, 30, 20), 770.09, feed="sip", trade_id="s1")
    engine = make_engine(tmp_path, oracle, iex=[cross], sip=[sip])
    engine.run(now=et(2026, 10, 5, 10, 16, 45))
    rows = watching_rows(engine)
    assert len(rows) == 1
    assert rows[0].status == STATUS_MISSED_LATE
    persisted = datetime.fromisoformat(rows[0].persisted_at)
    assert persisted >= et(2026, 10, 5, 10, 16, 45).astimezone(timezone.utc)
    knowable = datetime.fromisoformat(rows[0].knowable_at)
    assert knowable == et(2026, 10, 2, 16, 0).astimezone(timezone.utc)
    assert catch_count(rows) == 0


def test_a3_triggered_on_minute_poll_with_trade_fields(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(
        tmp_path,
        oracle,
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, trade_id="iex-20")],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip", trade_id="sip-20")],
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    provisional = watching_rows(engine)[0]
    assert provisional.status == STATUS_TRIGGERED
    assert provisional.setup_type == "H1_222_CONTINUATION"
    assert provisional.direction == "LONG"
    assert provisional.trigger_trade_id == "iex-20"
    assert provisional.trigger_feed == "iex"
    assert provisional.detected_at is not None
    assert provisional.true_lag_seconds is not None
    assert provisional.true_lag_seconds <= 120
    # Provisional IEX rows are not catches until SIP confirms.
    assert provisional.prospective_catch is False
    assert provisional.sip_crossed_at is None
    assert catch_count([provisional]) == 0
    engine.run(now=et(2026, 10, 5, 10, 46, 0))
    row = watching_rows(engine)[0]
    assert row.sip_crossed_at is not None
    assert row.prospective_catch is True
    assert catch_count([row]) == 1


def test_c1d_watcher_cadence_not_scanner(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(
        tmp_path,
        oracle,
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10)],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip")],
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    now = et(2026, 10, 5, 9, 30, 0)
    triggered_at = None
    while now <= et(2026, 10, 5, 9, 32, 0):
        engine.run(now=now)
        row = watching_rows(engine)[0]
        if row.status == STATUS_TRIGGERED and triggered_at is None:
            triggered_at = now
        now += timedelta(seconds=60)
    assert triggered_at == et(2026, 10, 5, 9, 31, 0)


def test_c1f_iex_none_sip_cross_is_missed_late(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(
        tmp_path,
        oracle,
        iex=[],
        sip=[_print(et(2026, 10, 5, 9, 30, 40), 770.09, feed="sip", trade_id="sip-only")],
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 10, 47, 0))
    row = watching_rows(engine)[0]
    assert row.status == STATUS_MISSED_LATE
    assert row.status_reason == "iex_no_cross_sip_cross"
    assert catch_count([row]) == 0


def test_c1f_iex_none_sip_none_expires(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(tmp_path, oracle, iex=[], sip=[])
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 10, 47, 0))
    row = watching_rows(engine)[0]
    assert row.status == STATUS_EXPIRED
    assert row.status != STATUS_MISSED_LATE


def test_c1i_lag_measured_against_sip(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(
        tmp_path,
        oracle,
        iex=[_print(et(2026, 10, 5, 9, 33, 10), 770.12)],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.09, feed="sip")],
        fetch_delay=timedelta(0),
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 34, 0))
    provisional = watching_rows(engine)[0]
    assert provisional.status == STATUS_TRIGGERED
    assert provisional.trigger_feed == "iex"
    # SIP reconcile only after watch_until + 16m (10:46 ET).
    engine.run(now=et(2026, 10, 5, 10, 46, 0))
    row = watching_rows(engine)[0]
    assert row.status == STATUS_TRIGGERED
    assert row.sip_crossed_at is not None
    assert row.capture_late is True
    assert row.true_lag_seconds is not None and row.true_lag_seconds >= 220
    assert catch_count([row]) == 0


def test_a6_low_break_invalidates_long(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(
        tmp_path,
        oracle,
        iex=[_print(et(2026, 10, 5, 9, 30, 5), 769.10, trade_id="low")],
        sip=[_print(et(2026, 10, 5, 9, 30, 5), 769.10, feed="sip")],
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    row = watching_rows(engine)[0]
    assert row.direction == "SHORT"
    assert row.setup_type == "H1_222_REVERSAL"
    engine2 = make_engine(
        tmp_path,
        oracle,
        iex=[
            _print(et(2026, 10, 5, 9, 30, 5), 769.10, trade_id="low"),
            _print(et(2026, 10, 5, 9, 45, 0), 770.20, trade_id="high-later"),
        ],
    )
    engine2.run(now=et(2026, 10, 5, 9, 46, 0))
    row2 = watching_rows(engine2)[0]
    assert row2.status != STATUS_TRIGGERED or row2.direction != "LONG"


def test_a7_restart_after_downtime_is_triggered_late(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(tmp_path, oracle, iex=[], sip=[])
    engine.run(now=et(2026, 10, 2, 16, 16))
    assert watching_rows(engine)[0].status == STATUS_WATCHING
    restarted = make_engine(
        tmp_path,
        oracle,
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10)],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip")],
    )
    restarted.run(now=et(2026, 10, 5, 9, 40, 0))
    row = watching_rows(restarted)[0]
    assert row.status == STATUS_TRIGGERED
    assert row.capture_late is True
    assert datetime.fromisoformat(row.trigger_crossed_at) == et(2026, 10, 5, 9, 30, 20).astimezone(timezone.utc)


def test_c4a_kill9_reloads_first_seen_from_journal(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(tmp_path, oracle)
    engine.run(now=et(2026, 10, 2, 16, 16))
    first = watching_rows(engine)[0].first_seen_at
    revived = make_engine(tmp_path, oracle)
    revived.run(now=et(2026, 10, 5, 9, 0, 0))
    assert watching_rows(revived)[0].first_seen_at == first
    assert watching_rows(revived)[0].status == STATUS_WATCHING


def test_a8_ah_and_premarket_ignored(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(
        tmp_path,
        oracle,
        iex=[
            _print(et(2026, 10, 2, 16, 6, 0), 770.16, conditions=("T",)),
            _print(et(2026, 10, 5, 9, 29, 59, 999000), 770.10, conditions=("T",)),
            _print(et(2026, 10, 5, 9, 30, 0), 769.69),
        ],
        sip=[],
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 29, 30))
    assert watching_rows(engine)[0].status == STATUS_WATCHING
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    assert watching_rows(engine)[0].status == STATUS_WATCHING


def test_c5d_opening_print_o_triggers_q_does_not(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine_q = make_engine(
        tmp_path,
        oracle,
        iex=[_print(et(2026, 10, 5, 9, 30, 0, 200000), 770.20, conditions=("Q",))],
    )
    engine_q.run(now=et(2026, 10, 2, 16, 16))
    engine_q.run(now=et(2026, 10, 5, 9, 31, 0))
    assert watching_rows(engine_q)[0].status == STATUS_WATCHING
    engine_o = make_engine(
        tmp_path / "o",
        BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes()),
        iex=[_print(et(2026, 10, 5, 9, 30, 0, 200000), 770.20, conditions=("O",), trade_id="open")],
        sip=[_print(et(2026, 10, 5, 9, 30, 0, 200000), 770.20, feed="sip", conditions=("O",))],
    )
    engine_o.run(now=et(2026, 10, 2, 16, 16))
    engine_o.run(now=et(2026, 10, 5, 9, 31, 0))
    row = watching_rows(engine_o)[0]
    assert row.status in {STATUS_TRIGGERED, STATUS_GAP_THROUGH_OPEN}
    assert row.trigger_trade_id == "open"


def test_gap_through_open_is_not_a_catch(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(
        tmp_path,
        oracle,
        iex=[_print(et(2026, 10, 5, 9, 30, 0), 770.40, trade_id="gap")],
        sip=[_print(et(2026, 10, 5, 9, 30, 0), 770.40, feed="sip")],
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    row = watching_rows(engine)[0]
    assert row.status == STATUS_GAP_THROUGH_OPEN
    assert row.first_print_price == 770.40
    assert catch_count([row]) == 0


def test_c7a_30m_and_1h_are_distinct_keys(tmp_path):
    # Build a 30m 2U-2U as well: last three 30m bars directional.
    bars = oct2_30m()
    oracle = BarAvailabilityOracle(bars, session_closes=oct2_session_closes())
    engine = make_engine(tmp_path, oracle)
    engine.run(now=et(2026, 10, 2, 16, 16))
    keys = {row.structure_key for row in engine.journal.list_all(limit=50)}
    tfs = {row.timeframe for row in engine.journal.list_all(limit=50) if row.status == STATUS_WATCHING}
    assert "1H" in tfs
    assert len({key for key in keys if "|1H|" in key}) == 1


def test_c7b_1h_window_covers_second_half_not_next_hour(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    late = _print(et(2026, 10, 5, 10, 15, 0), 770.20)
    engine = make_engine(tmp_path, oracle, iex=[late], sip=[_print(et(2026, 10, 5, 10, 15, 0), 770.20, feed="sip")])
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 10, 16, 0))
    assert watching_rows(engine)[0].status == STATUS_TRIGGERED
    other = make_engine(
        tmp_path / "next",
        BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes()),
        iex=[_print(et(2026, 10, 5, 10, 35, 0), 770.20)],
        sip=[_print(et(2026, 10, 5, 10, 35, 0), 770.20, feed="sip")],
    )
    other.run(now=et(2026, 10, 2, 16, 16))
    other.run(now=et(2026, 10, 5, 10, 36, 0))
    row = watching_rows(other)[0]
    assert row.status != STATUS_TRIGGERED


def test_c7c_vendor_hour_bars_rejected():
    vendor = [_bar(et(2026, 10, 2, 10, 0), 770, 769)]
    with pytest.raises(ValueError, match="vendor_hour_bars_unused"):
        reject_vendor_hour_bars(vendor)


def test_dst_friday_oct30_to_monday_nov2(tmp_path):
    session = nyse_session_for(et(2026, 10, 30, 12, 0).date())
    assert session is not None
    bars = []
    cursor = et(2026, 10, 30, 9, 30)
    while cursor < et(2026, 10, 30, 16, 0):
        high, low = (110.0, 100.0)
        if cursor == et(2026, 10, 30, 13, 30):
            high, low = 101.0, 99.0
        elif cursor == et(2026, 10, 30, 14, 0):
            high, low = 101.5, 99.2
        elif cursor == et(2026, 10, 30, 14, 30):
            high, low = 102.0, 100.0
        elif cursor == et(2026, 10, 30, 15, 0):
            high, low = 102.2, 100.1
        elif cursor == et(2026, 10, 30, 15, 30):
            high, low = 103.0, 101.0
        bars.append(_bar(cursor, high, low))
        cursor += timedelta(minutes=30)
    oracle = BarAvailabilityOracle(bars, session_closes={session.open: session.close})
    engine = make_engine(tmp_path, oracle)
    engine.run(now=et(2026, 10, 30, 16, 16))
    row = watching_rows(engine)[0]
    watch_start = datetime.fromisoformat(row.watch_start)
    assert watch_start == et(2026, 11, 2, 9, 30).astimezone(timezone.utc)


def test_early_close_stub_watch_candle():
    session = nyse_session_for(et(2026, 11, 27, 10, 0).date())
    assert session is not None and session.is_early_close
    bars = []
    cursor = session.open.astimezone(NY)
    while cursor.astimezone(timezone.utc) < session.close:
        bars.append(_bar(cursor, 50 + cursor.hour, 40 + cursor.hour))
        cursor += timedelta(minutes=30)
    hours = rebuild_session_hours(bars, sessions=[session], cutoff=session.close + timedelta(seconds=1))
    stub_starts = [bar.start_utc.astimezone(NY) for bar in hours]
    assert any(start.hour == 12 and start.minute == 30 for start in stub_starts)


def test_c3c_scanner_duplicates_link_one_structure_key():
    close = et(2026, 10, 2, 16, 0)
    armed = ArmedStructure(
        ticker="SPY",
        timeframe="1H",
        pattern="222:2U:2U",
        two_back_type="2U",
        previous_type="2U",
        boundary_high=OCT5_HIGH,
        boundary_low=OCT5_LOW,
        structure_close=close,
        knowable_at=close,
        watch_start=et(2026, 10, 5, 9, 30),
        watch_until=et(2026, 10, 5, 10, 30),
        setup_bar_start=et(2026, 10, 2, 15, 30),
    )
    k9925 = link_scanner_first_sight(
        ticker="SPY",
        timeframe="1H",
        structure_close=close,
        pattern="222:2U:2U",
        trigger=OCT5_HIGH,
        invalidation=OCT5_LOW,
        known=[armed],
    )
    k9933 = link_scanner_first_sight(
        ticker="SPY",
        timeframe="1H",
        structure_close=close,
        pattern="222:2U:2U",
        trigger=OCT5_HIGH + 0.01,
        invalidation=OCT5_LOW,
        known=[armed],
    )
    assert k9925 == k9933 == armed.structure_key
    scan_a = setup_episode_key(
        ticker="SPY",
        lane="COUNTERFACTUAL",
        timeframe="1H",
        setup_type="H1_222_CONTINUATION",
        direction="LONG",
        trigger=OCT5_HIGH,
        moment=et(2026, 10, 5, 10, 16, 45),
    )
    scan_b = setup_episode_key(
        ticker="SPY",
        lane="COUNTERFACTUAL",
        timeframe="1H",
        setup_type="H1_222_CONTINUATION",
        direction="LONG",
        trigger=OCT5_HIGH,
        moment=et(2026, 10, 5, 10, 31, 45),
    )
    assert scan_a != scan_b
    assert episode_bucket("1H", et(2026, 10, 5, 10, 16, 45)) != episode_bucket(
        "1H", et(2026, 10, 5, 10, 31, 45)
    )


def test_c3d_native_30m_and_5m_aggregate_same_fingerprint():
    a = quantize_level(770.0768000001)
    b = quantize_level(770.0768)
    assert format(a, ".4f") == format(b, ".4f")
    k1 = structure_key(
        ticker="SPY",
        timeframe="1H",
        structure_close=et(2026, 10, 2, 16, 0),
        pattern="222:2U:2U",
    )
    k2 = structure_key(
        ticker="SPY",
        timeframe="1H",
        structure_close=et(2026, 10, 2, 16, 0),
        pattern="222:2U:2U",
        trigger=770.0768,
        invalidation=769.17,
    )
    assert k1 == k2 == "SPY|1H|2026-10-02T20:00:00Z|222:2U:2U"
    armed = ArmedStructure(
        ticker="SPY",
        timeframe="1H",
        pattern="222:2U:2U",
        two_back_type="2U",
        previous_type="2U",
        boundary_high=a,
        boundary_low=769.17,
        structure_close=et(2026, 10, 2, 16, 0),
        knowable_at=et(2026, 10, 2, 16, 0),
        watch_start=et(2026, 10, 5, 9, 30),
        watch_until=et(2026, 10, 5, 10, 30),
        setup_bar_start=et(2026, 10, 2, 15, 30),
    )
    assert format_level(b) in armed.fingerprint()


def test_c3b_second_process_exits_locked(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(tmp_path, oracle)
    engine.journal.acquire()
    try:
        other = make_engine(tmp_path, oracle)
        result = other.run(now=et(2026, 10, 2, 16, 16))
        assert result["status"] == "LOCKED"
    finally:
        engine.journal.release()


def test_c4c_torn_trailing_line_is_repaired(tmp_path):
    path = tmp_path / "options_setup_capture.jsonl"
    key = "SPY|1H|2026-10-02T20:00:00Z|222:2U:2U"
    path.write_text(
        '{"record_type":"WATCHING","structure_key":"%s","status":"WATCHING","ticker":"SPY","timeframe":"1H","pattern":"222:2U:2U","boundary_high":770.0768,"boundary_low":769.17,"persisted_at":"2026-10-02T20:16:00+00:00","capture_version":"capture-v0.2"}\n{not json'
        % key
    )
    store = SetupCaptureJournal(path)
    state = store.load_state()
    assert state["repaired_torn_line"] is True
    assert key in state["current"]
    # Second load must not crash-loop (B4).
    state2 = store.load_state()
    assert key in state2["current"]
    assert '"JOURNAL_REPAIR"' in path.read_text()


def test_c4d_version_bump_keeps_friday_watching(tmp_path):
    path = tmp_path / "options_setup_capture.jsonl"
    close = "2026-10-02T20:00:00+00:00"
    key = "SPY|1H|2026-10-02T20:00:00Z|222:2U:2U|770.0768|769.1700"
    row = {
        "record_type": "WATCHING",
        "structure_key": key,
        "capture_version": "capture-v0.1",
        "status": STATUS_WATCHING,
        "ticker": "SPY",
        "timeframe": "1H",
        "pattern": "222:2U:2U",
        "boundary_high": OCT5_HIGH,
        "boundary_low": OCT5_LOW,
        "structure_close": close,
        "persisted_at": "2026-10-02T20:16:00+00:00",
        "first_seen_at": "2026-10-02T20:16:00+00:00",
        "watch_start": "2026-10-05T13:30:00+00:00",
        "watch_until": "2026-10-05T14:30:00+00:00",
        "knowable_at": close,
    }
    path.write_text(json.dumps(row) + "\n")
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(tmp_path, oracle, iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10)], sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip")])
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    row = engine.journal.get(key)
    assert row is not None
    assert row.status in {STATUS_WATCHING, STATUS_TRIGGERED, STATUS_MISSED_LATE}
    assert datetime.fromisoformat(row.first_seen_at) <= datetime.fromisoformat("2026-10-02T20:16:00+00:00")


def test_c4b_unit_journal_is_absolute_shared_logs():
    unit = (ROOT / "ops/systemd/options-setup-capture.service").read_text()
    assert "--journal /root/afs-shared/logs/options_setup_capture.jsonl" in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/root/afs-shared/logs" in unit
    assert DEFAULT_JOURNAL.startswith("/root/afs-shared/logs/")


def _block_capture_journal_stat(monkeypatch) -> None:
    real_stat = os.stat

    def blocked_stat(path, *args, **kwargs):
        target = os.fspath(path)
        if target == DEFAULT_JOURNAL or str(target).endswith("options_setup_capture.jsonl"):
            raise PermissionError(13, "Permission denied", target)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", blocked_stat)


def test_telemetry_fail_soft_when_journal_unreadable(monkeypatch):
    _block_capture_journal_stat(monkeypatch)
    cfg = SimpleNamespace(
        setup_capture_journal=DEFAULT_JOURNAL,
        setup_capture_enabled=True,
    )
    payload = _setup_capture_telemetry(cfg)
    assert payload["reason"] == "journal_unreadable"
    assert payload["watching_count"] == 0
    assert payload["execution_authority"] is False
    assert payload["scanner_embedded"] is False


def test_health_survives_unreadable_default_capture_journal(tmp_path, monkeypatch):
    _block_capture_journal_stat(monkeypatch)
    cfg = ScannerConfig(
        market_data_provider="tastytrade",
        tastytrade_username="user",
        tastytrade_password="pass",
        tastytrade_base_url="https://api.tastyworks.com",
        public_api_key_configured=False,
        public_base_url="https://api.public.com",
        alpaca_api_key_configured=False,
        alpaca_secret_key_configured=False,
        alpaca_paper=True,
        alpaca_data_base_url="https://data.alpaca.markets",
        port=8010,
        discord_webhook_url="",
        watchlist=["AAPL"],
        interval_minutes=5,
        sqlite_path=tmp_path / "options_scanner.sqlite",
    )
    app = create_app(cfg)
    with TestClient(app, client=LOCAL_PEER) as client:
        health = client.get("/health")
        assert health.status_code == 200
        body = health.json()
        assert body["status"] == "healthy"
        assert body["access_gate"] == "direct_loopback_only"
        assert body["setup_capture"]["reason"] == "journal_unreadable"
        assert body["setup_capture"]["watching_count"] == 0
        # /health must not leak absolute capture-journal filesystem paths.
        assert "journal" not in body["setup_capture"]
        assert "/root/afs-shared" not in health.text
        assert "options_setup_capture.jsonl" not in health.text
        assert DEFAULT_JOURNAL not in health.text
        public = client.get("/public/status")
        assert public.status_code == 200
        assert public.json()["counts"]["setup_capture_watching"] == 0
        assert "/root/afs-shared" not in public.text
        assert "options_setup_capture.jsonl" not in public.text
        capture = client.get("/setup-capture")
        assert capture.status_code == 200
        assert capture.json()["reason"] == "journal_unreadable"
        # Operator /setup-capture may still name the journal path.
        assert capture.json().get("journal") == DEFAULT_JOURNAL


def test_health_setup_capture_omits_internal_journal_path(tmp_path):
    """Regression: public /health must not expose /root/afs-shared journal paths."""
    cfg = ScannerConfig(
        market_data_provider="tastytrade",
        tastytrade_username="user",
        tastytrade_password="pass",
        tastytrade_base_url="https://api.tastyworks.com",
        public_api_key_configured=False,
        public_base_url="https://api.public.com",
        alpaca_api_key_configured=False,
        alpaca_secret_key_configured=False,
        alpaca_paper=True,
        alpaca_data_base_url="https://data.alpaca.markets",
        port=8010,
        discord_webhook_url="",
        watchlist=["AAPL"],
        interval_minutes=5,
        sqlite_path=tmp_path / "options_scanner.sqlite",
        setup_capture_journal=str(tmp_path / "options_setup_capture.jsonl"),
        setup_capture_enabled=True,
    )
    (tmp_path / "options_setup_capture.jsonl").write_text("")
    app = create_app(cfg)
    with TestClient(app, client=LOCAL_PEER) as client:
        health = client.get("/health")
        assert health.status_code == 200
        sc = health.json()["setup_capture"]
        assert "journal" not in sc
        assert sc.get("enabled") is True
        assert sc.get("observation_only") is True
        assert "/root/" not in health.text
        assert "/afs-shared" not in health.text
        assert "options_setup_capture.jsonl" not in health.text
        # Even the configured tmp journal path must not appear on /health.
        assert str(tmp_path / "options_setup_capture.jsonl") not in health.text
        private = client.get("/setup-capture").json()
        assert private.get("journal") == str(tmp_path / "options_setup_capture.jsonl")


def test_c1g_unknown_condition_writes_source_blocked(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(
        tmp_path,
        oracle,
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, conditions=("@",), tape="A")],
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    text = (tmp_path / "options_setup_capture.jsonl").read_text()
    assert "SOURCE_BLOCKED" in text or "unknown_trade_condition" in text or "DATA_BLOCKED" in text


def test_c6a_triggered_fsynced_before_chain_hook(tmp_path):
    seen: list[str] = []
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())

    def chain():
        seen.append("chain")
        text = (tmp_path / "options_setup_capture.jsonl").read_text()
        assert "RESOLUTION" in text
        seen.append("journal_before_chain")

    engine = make_engine(
        tmp_path,
        oracle,
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10)],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip")],
        chain_hook=chain,
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    assert seen[:2] == ["chain", "journal_before_chain"]


def test_a4_import_graph_excludes_signa_gex_paper_chain():
    for name in (
        "setup_capture.py",
        "setup_capture_store.py",
        "setup_capture_engine.py",
        "setup_capture_runtime.py",
        "alpaca_trades.py",
    ):
        path = ROOT / "alert_ranker" / name
        source = path.read_text()
        assert module_has_no_execution_imports(source)
        assert_no_forbidden_imports(path)
    collect = (ROOT / "scripts" / "options_setup_capture_collect.py").read_text()
    assert "options_122_iex_provisional_audit" not in collect
    assert "from alert_ranker.alpaca_trades import" in collect


def test_c8a_watchlist_unchanged_and_universe_is_spy_qqq_spx():
    cfg = ScannerConfig(
        market_data_provider="public",
        tastytrade_username="",
        tastytrade_password="",
        tastytrade_base_url="https://api.tastyworks.com",
        public_api_key_configured=True,
        public_base_url="https://api.public.com",
        alpaca_api_key_configured=False,
        alpaca_secret_key_configured=False,
        alpaca_paper=True,
        alpaca_data_base_url="https://data.alpaca.markets",
        port=8010,
        discord_webhook_url="",
        watchlist=["AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ"],
        interval_minutes=5,
        sqlite_path=Path("logs/options_scanner.sqlite"),
        public_account_id="ACC",
    )
    assert "SPX" not in cfg.watchlist
    assert watcher_universe() == ("SPY", "QQQ", "SPX")
    assert "SPXW" not in watcher_universe()


def test_c8f_spx_index_minute_bar_resolution(tmp_path):
    session = nyse_session_for(et(2026, 10, 2, 12, 0).date())
    bars = oct2_30m()
    oracle = BarAvailabilityOracle(bars, session_closes=oct2_session_closes())

    def bars_fn(symbol: str, now: datetime):
        if symbol != "SPX":
            return []
        return oracle.serve(now)

    minutes = [
        IndexMinuteBar(
            start=et(2026, 10, 5, 9, 30).astimezone(timezone.utc),
            open=769.5,
            high=770.20,
            low=769.4,
            close=770.1,
        )
    ]
    journal = SetupCaptureJournal(tmp_path / "options_setup_capture.jsonl")
    engine = SetupCaptureEngine(
        journal=journal,
        bar_oracle=bars_fn,
        iex_prints=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("spx_alpaca_unsupported")),
        index_minutes=lambda symbol, start, end: minutes,
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    rows = [row for row in journal.list_all(limit=20) if row.ticker == "SPX"]
    assert rows
    terminal = rows[0]
    if terminal.status == STATUS_TRIGGERED:
        assert terminal.trigger_source == "public_index_1m_bar"
        assert terminal.trigger_resolution == "BAR"
        assert terminal.trigger_trade_id is None


def test_alpaca_spx_raises_not_empty():
    with pytest.raises(RuntimeError, match="spx_alpaca_unsupported"):
        alpaca_spx_forbidden("SPX")


def test_c8d_watcher_modules_do_not_bypass_read_only_guard():
    for name in ("setup_capture.py", "setup_capture_store.py"):
        source = (ROOT / "alert_ranker" / name).read_text()
        assert "_ensure_client" not in source
        assert "_ensure_token" not in source
    engine_src = (ROOT / "alert_ranker" / "setup_capture_engine.py").read_text()
    assert "pub._ensure_client" not in engine_src
    assert "pub._ensure_token" not in engine_src


def test_c5b_premarket_block_is_ignored():
    session = nyse_session_for(et(2026, 10, 2, 12, 0).date())
    payload = {
        "preMarket": {
            "bars": [
                {
                    "timestamp": et(2026, 10, 2, 16, 6).astimezone(timezone.utc).isoformat(),
                    "open": 770.16,
                    "high": 770.16,
                    "low": 770.16,
                    "close": 770.16,
                    "volume": 1,
                }
            ]
        },
        "regularMarket": {"bars": []},
        "afterMarket": {"bars": []},
    }
    parsed = parse_complete_grid_bars(payload, timeframe=MINUTE_30, decision_ts=et(2026, 10, 2, 16, 16))
    assert parsed == ()


def test_c2b_no_bar_starts_after_decision():
    session = nyse_session_for(et(2026, 10, 2, 12, 0).date())
    payload = {
        "regularMarket": {
            "bars": [
                {
                    "timestamp": et(2026, 10, 2, 15, 30).astimezone(timezone.utc).isoformat(),
                    "open": 769.5,
                    "high": OCT5_HIGH,
                    "low": OCT5_LOW,
                    "close": 769.8,
                    "volume": 1,
                },
                {
                    "timestamp": et(2026, 10, 2, 16, 0).astimezone(timezone.utc).isoformat(),
                    "open": 770,
                    "high": 771,
                    "low": 769,
                    "close": 770,
                    "volume": 1,
                },
            ]
        }
    }
    decision = et(2026, 10, 2, 16, 0, 1)
    bars = parse_complete_grid_bars(payload, timeframe=MINUTE_30, decision_ts=decision)
    for bar in bars:
        assert bar.start_utc + MINUTE_30.delta <= decision.astimezone(timezone.utc)


def test_execution_and_risk_guards():
    with pytest.raises(RuntimeError, match="setup_capture_has_no_execution_authority"):
        submit_broker_order()
    assert consume_risk_budget() is False
    assert quote_instrument_type("SPX") == "INDEX"
    with pytest.raises(ValueError):
        quote_instrument_type("SPXW")
    assert PUBLIC_ALLOWED_PREFIXES == (PUBLIC_MARKETDATA_PREFIX,)


def test_scanner_is_not_wrapped_with_capture():
    source = (ROOT / "alert_ranker" / "scanner.py").read_text()
    assert "build_setup_capture_scanner" not in source
    app = (ROOT / "alert_ranker" / "app.py").read_text()
    assert "options-setup-capture-arm" not in app
    assert "options-setup-capture-quotes" not in app


def test_webhook_alert_allowlist_gap_is_documented():
    """Pre-existing hole: POST /webhook/alert accepts any ticker. Out of scope."""
    source = (ROOT / "alert_ranker" / "app.py").read_text()
    handler = source.split("@app.post(\"/webhook/alert\")", 1)[1].split("@app.post", 1)[0]
    assert "scan_ticker" in handler
    assert "in cfg.watchlist" not in handler
    engine_src = (ROOT / "alert_ranker" / "setup_capture_engine.py").read_text()
    assert "webhook/alert" not in engine_src


def test_c1e_detected_at_includes_fetch_delay(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(
        tmp_path,
        oracle,
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10)],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip")],
        fetch_delay=timedelta(seconds=25),
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    row = watching_rows(engine)[0]
    detected = datetime.fromisoformat(row.detected_at)
    assert detected >= et(2026, 10, 5, 9, 31, 25).astimezone(timezone.utc)


def test_clock_unsynced_is_data_blocked(tmp_path):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(tmp_path, oracle, clock_offset_s=31)
    result = engine.run(now=et(2026, 10, 2, 16, 16))
    assert result["reason"] == "clock_unsynced"
    text = (tmp_path / "options_setup_capture.jsonl").read_text()
    assert "clock_unsynced" in text


def test_clock_unsynced_status_clears_after_healthy_run(tmp_path):
    """/setup-capture clock_unsynced must reflect the latest _clock record only."""
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    journal = SetupCaptureJournal(tmp_path / "options_setup_capture.jsonl")
    bad = SetupCaptureEngine(
        journal=journal,
        bar_oracle=lambda sym, now: oracle.serve(now) if sym == "SPY" else [],
        iex_prints=lambda *a: [],
        sip_prints=lambda *a: [],
        clock_offset_s=31,
    )
    assert bad.run(now=et(2026, 10, 2, 16, 16))["reason"] == "clock_unsynced"
    assert _latest_clock_unsynced(journal.peek_state()) is True
    good = SetupCaptureEngine(
        journal=journal,
        bar_oracle=lambda sym, now: oracle.serve(now) if sym == "SPY" else [],
        iex_prints=lambda *a: [],
        sip_prints=lambda *a: [],
        clock_offset_s=0.0,
    )
    for _ in range(3):
        good.run(now=et(2026, 10, 2, 16, 16))
    assert _latest_clock_unsynced(journal.peek_state()) is False


def test_arming_race_first_minute_after_ready(tmp_path):
    """3/55 crosses in the first minute after ready — arm at close, poll +60s."""
    session = nyse_session_for(et(2026, 10, 5, 12, 0).date())
    assert session is not None
    bars = []
    cursor = et(2026, 10, 5, 9, 30)
    # Build prior completed 1H so 10:30 close arms a 10:30-11:30 watch.
    # Use Oct 2 history plus Monday morning 30m.
    bars.extend(oct2_30m())
    bars.append(_bar(et(2026, 10, 5, 9, 30), 770.5, 769.8))
    bars.append(_bar(et(2026, 10, 5, 10, 0), 770.8, 769.9))
    oracle = BarAvailabilityOracle(
        bars,
        session_closes={**oct2_session_closes(), session.open: session.close},
    )
    engine = make_engine(
        tmp_path,
        oracle,
        iex=[_print(et(2026, 10, 5, 10, 30, 20), 771.0)],
        sip=[_print(et(2026, 10, 5, 10, 30, 20), 771.0, feed="sip")],
    )
    engine.run(now=et(2026, 10, 5, 10, 30, 0))
    monday = [row for row in engine.journal.list_all(limit=20) if row.timeframe == "1H" and row.status == STATUS_WATCHING]
    assert monday, "must arm at the close, not after the first-minute cross"
    engine.run(now=et(2026, 10, 5, 10, 31, 0))


def test_oct5_honest_replay_watching_then_iex_or_sip(tmp_path):
    """Do not manufacture a trade. WATCHING Friday; SIP-only path is MISSED_LATE."""
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(
        tmp_path,
        oracle,
        iex=[],
        sip=[_print(et(2026, 10, 5, 9, 30, 40), 770.09, feed="sip")],
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    assert watching_rows(engine)[0].status == STATUS_WATCHING
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    assert watching_rows(engine)[0].status == STATUS_WATCHING
    engine.run(now=et(2026, 10, 5, 10, 47, 0))
    row = watching_rows(engine)[0]
    assert row.status == STATUS_MISSED_LATE
    assert row.status_reason == "iex_no_cross_sip_cross"
    k = link_scanner_first_sight(
        ticker="SPY",
        timeframe="1H",
        structure_close=et(2026, 10, 2, 16, 0),
        pattern=row.pattern,
        trigger=OCT5_HIGH,
        invalidation=OCT5_LOW,
        known=[],
    )
    assert k == row.structure_key


def test_b1_oct5_sip_only_at_real_60s_cadence_is_missed_late(tmp_path):
    """B1: no clock jump — SIP reconcile after watch_until+16m, not EXPIRED at 10:30."""
    eng = make_engine(
        tmp_path,
        BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes()),
        iex=[],
        sip=[_print(et(2026, 10, 5, 9, 30, 40), 770.09, feed="sip")],
    )
    eng.run(now=et(2026, 10, 2, 16, 16))
    minute_loop(eng, et(2026, 10, 5, 9, 30), et(2026, 10, 5, 10, 50))
    row = watching_rows(eng)[0]
    assert row.status == STATUS_MISSED_LATE
    assert row.status_reason == "iex_no_cross_sip_cross"


def test_b2_delayed_sip_plan_provisional_triggered_then_reconcile(tmp_path):
    """B2: IEX catch writes TRIGGERED immediately; SIP queried only at reconcile."""
    o = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    j = SetupCaptureJournal(tmp_path / "j.jsonl")
    state = {"now": None}
    cross_iex = _print(et(2026, 10, 5, 9, 30, 20), 770.10)
    cross_sip = _print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip")

    def bars(sym, now):
        return o.serve(now) if sym == "SPY" else []

    def iex(sym, s, e):
        return [p for p in [cross_iex] if s <= p.ts_utc < e]

    def sip(sym, s, e):
        if state["now"] is not None and e > state["now"] - timedelta(minutes=15):
            raise RuntimeError(
                "sip trade provider HTTP 403: subscription does not permit querying recent SIP data"
            )
        return [p for p in [cross_sip] if s <= p.ts_utc < e]

    eng = SetupCaptureEngine(journal=j, bar_oracle=bars, iex_prints=iex, sip_prints=sip)
    state["now"] = et(2026, 10, 2, 16, 16)
    eng.run(now=state["now"])
    first = None
    t = et(2026, 10, 5, 9, 31)
    while t <= et(2026, 10, 5, 10, 50):
        state["now"] = t
        eng.run(now=t)
        r = [x for x in j.list_all() if x.timeframe == "1H"][0]
        if r.status != STATUS_WATCHING and first is None:
            first = (t, r.status, r.capture_late, r.trigger_feed)
        t += timedelta(minutes=1)
    assert first is not None
    assert first[0] == et(2026, 10, 5, 9, 31)
    assert first[1] == STATUS_TRIGGERED
    assert first[2] is False
    assert first[3] == "iex"
    errs = sum(1 for line in (tmp_path / "j.jsonl").read_text().splitlines() if '"COLLECTOR_ERROR"' in line)
    assert errs == 0
    final = [x for x in j.list_all() if x.timeframe == "1H"][0]
    assert final.sip_crossed_at is not None


def test_b3_transient_iex_429_recovers_to_triggered(tmp_path):
    o = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    j = SetupCaptureJournal(tmp_path / "j.jsonl")

    def bars(sym, now):
        return o.serve(now) if sym == "SPY" else []

    def iex(sym, s, e):
        if e <= et(2026, 10, 5, 9, 31, 0).astimezone(timezone.utc) + timedelta(seconds=1):
            raise RuntimeError("iex trade provider HTTP 429")
        return [p for p in [_print(et(2026, 10, 5, 9, 31, 20), 770.10)] if s <= p.ts_utc < e]

    def sip(sym, s, e):
        return [p for p in [_print(et(2026, 10, 5, 9, 31, 20), 770.10, feed="sip")] if s <= p.ts_utc < e]

    eng = SetupCaptureEngine(journal=j, bar_oracle=bars, iex_prints=iex, sip_prints=sip)
    eng.run(now=et(2026, 10, 2, 16, 16))
    eng.run(now=et(2026, 10, 5, 9, 31, 0))
    mid = [r for r in j.list_all() if r.timeframe == "1H"][0]
    assert mid.status == STATUS_WATCHING
    eng.run(now=et(2026, 10, 5, 9, 32, 0))
    row = [r for r in j.list_all() if r.timeframe == "1H"][0]
    assert row.status == STATUS_TRIGGERED
    assert row.timeframe == "1H"


def test_b4_torn_line_second_load_is_clean(tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_text(
        '{"record_type":"WATCHING","structure_key":"k","status":"WATCHING","capture_version":"capture-v0.2"}\n{"record_type":"RESOL'
    )
    j = SetupCaptureJournal(p)
    j.load_state()
    state = j.load_state()
    assert "k" in state["current"]
    assert state["current"]["k"].status == STATUS_WATCHING


def test_b5_status_read_leaves_journal_byte_identical(tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_text(
        '{"record_type":"WATCHING","structure_key":"k","status":"WATCHING","capture_version":"capture-v0.2"}\n{"partial'
    )
    before = p.read_bytes()
    counts = SetupCaptureJournal(p, create=False).read_counts()
    after = p.read_bytes()
    assert after == before
    assert counts["watching"] == 1


def test_b6_detected_at_after_iex_fetch_only(tmp_path):
    """Clock advances only inside the IEX fetch; detected_at must be post-fetch."""
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    cross = _print(et(2026, 10, 5, 9, 30, 20), 770.10)
    clock = {"t": et(2026, 10, 5, 9, 31, 0)}

    def wall():
        return clock["t"]

    def iex(sym, start, end):
        # 90 s of wall time only during the fetch (E10-style).
        clock["t"] = clock["t"] + timedelta(seconds=90)
        return [p for p in [cross] if start <= p.ts_utc < end]

    def sip(sym, start, end):
        return [
            p
            for p in [_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip")]
            if start <= p.ts_utc < end
        ]

    eng = SetupCaptureEngine(
        journal=SetupCaptureJournal(tmp_path / "j.jsonl"),
        bar_oracle=lambda sym, now: oracle.serve(now) if sym == "SPY" else [],
        iex_prints=iex,
        sip_prints=sip,
        wall_clock=wall,
    )
    eng.run(now=et(2026, 10, 2, 16, 16))
    clock["t"] = et(2026, 10, 5, 9, 31, 0)
    summary = eng.run(now=et(2026, 10, 5, 9, 31, 0))
    row = [r for r in eng.journal.list_all() if r.timeframe == "1H"][0]
    detected = datetime.fromisoformat(row.detected_at)
    # Two structures × 90 s fetch = 09:34:00; lag vs 09:30:20 = 220 s.
    # These exact bounds fail on the pre-fix 1b8dc74 code (130 s / 09:32:30).
    assert detected == et(2026, 10, 5, 9, 34, 0).astimezone(timezone.utc)
    assert row.true_lag_seconds == 220.0
    assert summary["cycle_seconds"] is not None and summary["cycle_seconds"] >= 180
    assert summary["cycle_over_cadence"] is True


def test_b7_clock_offset_failure_blocks(monkeypatch):
    import scripts.options_setup_capture_collect as collect

    def boom(*_a, **_k):
        raise FileNotFoundError("timedatectl")

    monkeypatch.setattr(collect.subprocess, "run", boom)
    assert collect._clock_offset_seconds() > 30


_CHRONY_OK = """\
Reference ID    : A9FEA97B ()
Stratum         : 3
System time     : 0.012345678 seconds fast of NTP time
Last offset     : +0.000001234 seconds
Leap status     : Normal
"""

_CHRONY_NEVER_SYNCED = """\
Reference ID    : 00000000 ()
Stratum         : 0
System time     : 0.000000000 seconds slow of NTP time
Last offset     : +0.000000000 seconds
Leap status     : Not synchronised
"""

_CHRONY_STALE_SYSTEM_TIME = """\
Reference ID    : A9FEA97B ()
Stratum         : 3
System time     : 45.200000000 seconds fast of NTP time
Last offset     : +0.000012000 seconds
Leap status     : Normal
"""


def test_b7_chronyc_tracking_uses_system_time_when_leap_normal(monkeypatch):
    import scripts.options_setup_capture_collect as collect

    def fake_run(cmd, **_k):
        if cmd[:1] == ["chronyc"]:
            return SimpleNamespace(returncode=0, stdout=_CHRONY_OK)
        raise FileNotFoundError("timedatectl")

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    assert abs(collect._clock_offset_seconds() - 0.012345678) < 1e-9


def test_b7a_unsynced_kernel_not_overridden_by_chrony(monkeypatch):
    """(a) NTPSynchronized=no must fail closed even if chrony looks idle-zero."""
    import scripts.options_setup_capture_collect as collect

    def fake_run(cmd, **_k):
        if cmd[:3] == ["timedatectl", "show", "-p"]:
            return SimpleNamespace(returncode=0, stdout="no\n")
        if cmd[:1] == ["chronyc"]:
            return SimpleNamespace(returncode=0, stdout=_CHRONY_NEVER_SYNCED)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    assert collect._clock_offset_seconds() > 30


def test_b7b_no_timedatectl_unsynced_chrony_fails_closed(monkeypatch):
    """(b) missing timedatectl + chrony Never synced → fail closed."""
    import scripts.options_setup_capture_collect as collect

    def fake_run(cmd, **_k):
        if cmd[:1] == ["chronyc"]:
            return SimpleNamespace(returncode=0, stdout=_CHRONY_NEVER_SYNCED)
        raise FileNotFoundError("timedatectl")

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    assert collect._clock_offset_seconds() > 30


def test_b7c_chrony_system_time_not_last_offset(monkeypatch):
    """(c) System time 45 s off must fail even when Last offset is tiny."""
    import scripts.options_setup_capture_collect as collect

    def fake_run(cmd, **_k):
        if cmd[:1] == ["chronyc"]:
            return SimpleNamespace(returncode=0, stdout=_CHRONY_STALE_SYSTEM_TIME)
        raise FileNotFoundError("timedatectl")

    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    offset = collect._clock_offset_seconds()
    assert abs(offset) > 30
    assert abs(offset - 45.2) < 1e-6


def test_b8_missing_alpaca_creds_fail_closed(tmp_path):
    eng = make_engine(
        tmp_path,
        BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes()),
        equity_feed_available=False,
    )
    eng.run(now=et(2026, 10, 2, 16, 16))
    minute_loop(eng, et(2026, 10, 5, 9, 30), et(2026, 10, 5, 10, 50))
    row = watching_rows(eng)[0]
    assert row.status == STATUS_DATA_BLOCKED
    assert "alpaca_credentials_missing" in row.status_reason


def test_b8_spx_delayed_index_keeps_watching_until_print(tmp_path):
    o = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    j = SetupCaptureJournal(tmp_path / "j.jsonl")

    def bars(sym, now):
        return o.serve(now) if sym == "SPX" else []

    cross = IndexMinuteBar(
        start=et(2026, 10, 5, 10, 20).astimezone(timezone.utc),
        open=770.0,
        high=770.5,
        low=769.9,
        close=770.3,
    )

    def idx(sym, s, e):
        return [b for b in [cross] if b.window_end + timedelta(minutes=15) <= e and s <= b.start < e]

    eng = SetupCaptureEngine(journal=j, bar_oracle=bars, index_minutes=idx)
    eng.run(now=et(2026, 10, 2, 16, 16))
    minute_loop(eng, et(2026, 10, 5, 9, 30), et(2026, 10, 5, 10, 50))
    row = [x for x in j.list_all() if x.ticker == "SPX" and x.timeframe == "1H"][0]
    assert row.status == STATUS_TRIGGERED
    assert row.data_delayed is True or row.capture_late is True


def test_b8_spx_delayed_no_cross_terminals_data_blocked(tmp_path):
    """E7b: delayed feed with every bar present but no cross → DATA_BLOCKED."""
    o = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    j = SetupCaptureJournal(tmp_path / "j.jsonl")

    def bars(sym, now):
        return o.serve(now) if sym == "SPX" else []

    def idx(sym, s, e):
        # Publish every RTH minute bar 15 minutes late, never crossing levels.
        out = []
        t = et(2026, 10, 5, 9, 30)
        while t < et(2026, 10, 5, 16, 0):
            start = t.astimezone(timezone.utc)
            published = start + timedelta(minutes=16)
            if published <= e and s <= start < e:
                out.append(
                    IndexMinuteBar(
                        start=start,
                        open=769.5,
                        high=769.8,
                        low=769.4,
                        close=769.6,
                    )
                )
            t += timedelta(minutes=1)
        return out

    eng = SetupCaptureEngine(journal=j, bar_oracle=bars, index_minutes=idx)
    eng.run(now=et(2026, 10, 2, 16, 16))
    minute_loop(eng, et(2026, 10, 5, 9, 30), et(2026, 10, 5, 10, 50))
    row = [x for x in j.list_all() if x.ticker == "SPX" and x.timeframe == "1H"][0]
    assert row.status == STATUS_DATA_BLOCKED
    assert row.data_delayed is True
    assert "spx_index_delayed" in row.status_reason


def test_b8_iex_unknown_condition_terminals_and_dedupes_source_blocked(tmp_path, monkeypatch):
    """E12: unknown-condition DATA_BLOCKED → one SOURCE_BLOCKED then terminal."""
    import alert_ranker.setup_capture_engine as eng_mod

    o = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    j = SetupCaptureJournal(tmp_path / "j.jsonl")

    def bars(sym, now):
        return o.serve(now) if sym == "SPY" else []

    def iex(sym, s, e):
        return [_print(et(2026, 10, 5, 9, 30, 20), 770.10)]

    monkeypatch.setattr(
        eng_mod,
        "first_boundary_from_prints",
        lambda *_a, **_k: {
            "status": STATUS_DATA_BLOCKED,
            "reason_code": "unknown_trade_condition",
        },
    )
    eng = SetupCaptureEngine(journal=j, bar_oracle=bars, iex_prints=iex, sip_prints=lambda *a: [])
    eng.run(now=et(2026, 10, 2, 16, 16))
    minute_loop(eng, et(2026, 10, 5, 9, 30), et(2026, 10, 5, 10, 50))
    row = [x for x in j.list_all() if x.timeframe == "1H"][0]
    assert row.status == STATUS_DATA_BLOCKED
    assert row.data_delayed is True
    text = (tmp_path / "j.jsonl").read_text()
    blocked_rows = text.count('"SOURCE_BLOCKED"')
    # Two structures (30m + 1H) → at most one SOURCE_BLOCKED each (de-duped).
    assert blocked_rows <= 4
    assert blocked_rows >= 1


def test_iex_no_cross_sip_error_retries_before_data_blocked(tmp_path):
    """Transient SIP error at reconcile_at must use the 30-minute retry budget."""
    o = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    j = SetupCaptureJournal(tmp_path / "j.jsonl")
    sip_calls = {"n": 0}

    def bars(sym, now):
        return o.serve(now) if sym == "SPY" else []

    def iex(sym, s, e):
        return []

    def sip(sym, s, e):
        sip_calls["n"] += 1
        raise RuntimeError("sip trade provider HTTP 429")

    eng = SetupCaptureEngine(journal=j, bar_oracle=bars, iex_prints=iex, sip_prints=sip)
    eng.run(now=et(2026, 10, 2, 16, 16))
    eng.run(now=et(2026, 10, 5, 10, 46, 0))
    mid = [x for x in j.list_all() if x.timeframe == "1H"][0]
    assert mid.status == STATUS_WATCHING
    assert sip_calls["n"] >= 1
    # Deadline = watch_until+16m+30m = 11:16 ET.
    eng.run(now=et(2026, 10, 5, 11, 16, 0))
    final = [x for x in j.list_all() if x.timeframe == "1H"][0]
    assert final.status == STATUS_DATA_BLOCKED
    assert "sip_reconcile_failed" in final.status_reason


def test_b13_provisional_catch_excluded_and_sip_retries_capped(tmp_path):
    """Unconfirmed IEX TRIGGERED is not a catch; SIP failures terminalize."""
    o = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    j = SetupCaptureJournal(tmp_path / "j.jsonl")
    sip_calls = {"n": 0}

    def bars(sym, now):
        return o.serve(now) if sym == "SPY" else []

    def iex(sym, s, e):
        return [p for p in [_print(et(2026, 10, 5, 9, 30, 20), 770.10)] if s <= p.ts_utc < e]

    def sip(sym, s, e):
        sip_calls["n"] += 1
        raise RuntimeError("sip trade provider HTTP 403: delayed plan")

    eng = SetupCaptureEngine(journal=j, bar_oracle=bars, iex_prints=iex, sip_prints=sip)
    eng.run(now=et(2026, 10, 2, 16, 16))
    eng.run(now=et(2026, 10, 5, 9, 31, 0))
    provisional = [x for x in j.list_all() if x.timeframe == "1H"][0]
    assert provisional.status == STATUS_TRIGGERED
    assert provisional.sip_crossed_at is None
    assert provisional.prospective_catch is False
    assert catch_count(j.list_all()) == 0
    # Through SIP deadline (watch_until+16m+30m = 11:16) and beyond.
    minute_loop(eng, et(2026, 10, 5, 10, 46), et(2026, 10, 5, 11, 20))
    final = [x for x in j.list_all() if x.timeframe == "1H"][0]
    assert final.status == STATUS_DATA_BLOCKED
    assert "sip_reconcile_failed" in final.status_reason
    assert catch_count(j.list_all()) == 0
    text = (tmp_path / "j.jsonl").read_text()
    # De-duped SOURCE_BLOCKED — far below one-per-minute spam (660).
    assert text.count('"SOURCE_BLOCKED"') < 20
    assert sip_calls["n"] < 100


def test_b9_level_revision_one_key_and_source_drift(tmp_path):
    bars = oct2_30m()
    first = _bar(et(2026, 10, 2, 15, 30), OCT5_HIGH, 769.18)
    second = _bar(et(2026, 10, 2, 15, 30), OCT5_HIGH, OCT5_LOW)
    j = SetupCaptureJournal(tmp_path / "j.jsonl")
    served = {"v": first}

    def bars_fn(sym, now):
        if sym != "SPY":
            return []
        out = [b for b in bars if b.start_utc != first.start_utc]
        out.append(served["v"])
        out.sort(key=lambda b: b.start_utc)
        return [b for b in out if b.start_utc + timedelta(minutes=30) <= now]

    eng = SetupCaptureEngine(
        journal=j, bar_oracle=bars_fn, iex_prints=lambda *a: [], sip_prints=lambda *a: []
    )
    eng.run(now=et(2026, 10, 2, 16, 0, 5))
    served["v"] = second
    eng.run(now=et(2026, 10, 2, 16, 1, 5))
    keys = sorted(
        {
            r.structure_key
            for r in j.list_all()
            if r.timeframe == "1H" and r.status == STATUS_WATCHING
        }
    )
    assert len(keys) == 1
    assert "SOURCE_DRIFT" in (tmp_path / "j.jsonl").read_text()
    watching = [r for r in j.list_all() if r.structure_key == keys[0]][0]
    assert abs(watching.boundary_low - OCT5_LOW) < 1e-6


def test_b10_five_minute_rows_rejected_as_thirty_minute():
    base = et(2026, 10, 2, 15, 30).astimezone(timezone.utc)
    rows = [
        {
            "timestamp": (base + timedelta(minutes=5 * i)).isoformat(),
            "open": 1,
            "high": 2 + i,
            "low": 1,
            "close": 1,
            "volume": 1,
        }
        for i in range(6)
    ]
    with pytest.raises(ValueError, match="unexpected_bar_spacing"):
        parse_complete_grid_bars(
            {"regularMarket": {"bars": rows}},
            timeframe=MINUTE_30,
            decision_ts=et(2026, 10, 2, 16, 1),
        )


def test_b12_collector_transitive_import_graph_excludes_forbidden():
    import subprocess
    import sys

    # Clean interpreter: the pytest process already imported paper_v1 via other tests.
    script = r"""
import importlib, sys
importlib.import_module("scripts.options_setup_capture_collect")
forbidden = {
    "alert_ranker.paper_v1",
    "alert_ranker.trigger_time",
    "alert_ranker.discord",
    "alert_ranker.scanner_legacy",
    "sources.signa_client",
    "sources.gex_client",
    "scripts.options_122_iex_provisional_audit",
}
loaded = set(sys.modules)
bad = sorted(forbidden & loaded)
assert not bad, bad
assert "alert_ranker.alpaca_trades" in loaded
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

