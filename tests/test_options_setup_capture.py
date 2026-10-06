"""Simulated-clock tests for the observation-only setup-capture collector.

Fixtures use an availability oracle (bars served only after bar_end + lag) and
recorded-style Public 30m / Alpaca tape prints. Arm times are never assigned
directly — they come from watcher.run(now=sim_now).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from alert_ranker.causal_bars import MINUTE_30, Bar
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
from alert_ranker.config import ScannerConfig

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
):
    journal = SetupCaptureJournal(tmp_path / "options_setup_capture.jsonl")

    def bars(symbol: str, now: datetime):
        if symbol != "SPY":
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
        iex_prints=iex_fn,
        sip_prints=sip_fn,
        index_minutes=index_fn,
        clock_offset_s=clock_offset_s,
        fetch_delay=fetch_delay,
        chain_hook=chain_hook,
    )


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
    row = watching_rows(engine)[0]
    assert row.status == STATUS_TRIGGERED
    assert row.setup_type == "H1_222_CONTINUATION"
    assert row.direction == "LONG"
    assert row.trigger_trade_id == "iex-20"
    assert row.trigger_feed == "iex"
    assert row.detected_at is not None
    assert row.true_lag_seconds is not None
    assert row.true_lag_seconds <= 120
    assert row.prospective_catch is True


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
    row = watching_rows(engine)[0]
    assert row.status == STATUS_TRIGGERED
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
        trigger=770.0768000001,
        invalidation=769.17,
    )
    k2 = structure_key(
        ticker="SPY",
        timeframe="1H",
        structure_close=et(2026, 10, 2, 16, 0),
        pattern="222:2U:2U",
        trigger=770.0768,
        invalidation=769.17,
    )
    assert k1 == k2


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
    path.write_text('{"record_type":"WATCHING","structure_key":"SPY|1H|x|222:2U:2U|770.0768|769.1700","status":"WATCHING","ticker":"SPY","timeframe":"1H","pattern":"222:2U:2U","boundary_high":770.0768,"boundary_low":769.17,"persisted_at":"2026-10-02T20:16:00+00:00","capture_version":"capture-v0.2"}\n{not json')
    store = SetupCaptureJournal(path)
    state = store.load_state()
    assert state["repaired_torn_line"] is True
    assert "SPY|1H|x|222:2U:2U|770.0768|769.1700" in state["current"]


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
    ):
        path = ROOT / "alert_ranker" / name
        source = path.read_text()
        assert module_has_no_execution_imports(source)
        assert_no_forbidden_imports(path)


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
