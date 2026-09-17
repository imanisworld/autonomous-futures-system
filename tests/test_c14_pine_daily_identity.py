"""C14 — replay daily-session identity must match Pine/CME trade-date identity.

Proven external evidence (TradingView Pine diagnostic exports, MES1! and MNQ1!
15m, 2026-09-17 — see tests/fixtures/c14_pine_daily_identity/README.md):

    boundary (ET)              MES        MNQ
    Sun 2026-09-06 18:00       reset      reset      new daily identity
    Mon 2026-09-07 18:00       NO reset   NO reset   Labor Day reopen continues 09-08
    Tue 2026-09-08 18:00       reset      reset
    Sun 2026-09-13 18:00       reset      reset      ordinary Sunday control

The old replay rule ("every 18:00 ET == new day") was therefore wrong. The fix
is a single exchange trade-date helper, ``cme_trading_day``, consumed by
``detect_day_boundaries`` (VWAP reset, HOD/LOD, PDH/PDL/PDC) and
``resample_daily``. These tests pin the replay against the real Pine rows.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.csv_to_replay import (
    CALENDAR_CME_EQUITY_INDEX,
    CME_EQUITY_INDEX_INSTRUMENTS,
    cme_equity_index_non_trade_dates,
    cme_trading_day,
    compute_vwap,
    detect_day_boundaries,
    trading_day_calendar,
    vwap_day_range,
)
from scripts.polygon_to_replay import derive_candles, resample_daily

_ET = ZoneInfo("America/New_York")
FIXTURES = Path(__file__).parent / "fixtures" / "c14_pine_daily_identity"
INSTRUMENTS = ("MES", "MNQ")


def _load(instrument: str) -> dict[str, list[dict]]:
    """Fixture rows grouped by window, each row carrying the replay bar + Pine fields."""
    windows: dict[str, list[dict]] = {}
    with (FIXTURES / f"{instrument}1_15m.csv").open(newline="") as f:
        for r in csv.DictReader(f):
            et = datetime.fromisoformat(r["time"])
            windows.setdefault(r["window"], []).append({
                "et": et,
                "ts": int(et.timestamp()),
                "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
                "volume": int(float(r["volume"])),
                "pine_daily_time": int(r["daily_time"]),
                "pine_tradingday": datetime.fromtimestamp(
                    int(r["time_tradingday"]) / 1000, tz=timezone.utc).date(),
                "pine_vwap": float(r["native_vwap"]),
                "pine_hod": float(r["hod"]), "pine_lod": float(r["lod"]),
            })
    return windows


def _bars(rows: list[dict]) -> list[dict]:
    return [{k: r[k] for k in ("ts", "open", "high", "low", "close", "volume")} for r in rows]


def _pine_boundaries(rows: list[dict]) -> list[int]:
    return [0] + [i for i in range(1, len(rows))
                  if rows[i]["pine_daily_time"] != rows[i - 1]["pine_daily_time"]]


def _index_at(rows: list[dict], et: datetime) -> int:
    for i, r in enumerate(rows):
        if r["et"] == et:
            return i
    raise AssertionError(f"fixture has no bar at {et}")


def _replay_vwap_series(bars: list[dict], instrument: str) -> list[float]:
    """The converters' VWAP accumulator, keyed off vwap_day_range exactly as convert()/
    derive_candles() do — but from the first bar, so every day is fully accumulated."""
    boundaries = detect_day_boundaries(bars, instrument)
    ranges = [(s, boundaries[i + 1] if i + 1 < len(boundaries) else len(bars))
              for i, s in enumerate(boundaries)]
    out, acc, prev = [], [], None
    for i, b in enumerate(bars):
        rng = vwap_day_range(ranges, i)
        if rng != prev:
            acc, prev = [], rng
        acc.append(b)
        out.append(compute_vwap(acc))
    return out


@pytest.fixture(scope="module", params=INSTRUMENTS)
def inst_windows(request):
    return request.param, _load(request.param)


# ---------------------------------------------------------------------------
# The four required boundaries, explicitly, for BOTH instruments.
# ---------------------------------------------------------------------------
class TestRequiredBoundaries:
    REQUIRED = (
        ("labor_day", datetime(2026, 9, 6, 18, 0, tzinfo=_ET), True),
        ("labor_day", datetime(2026, 9, 7, 18, 0, tzinfo=_ET), False),
        ("labor_day", datetime(2026, 9, 8, 18, 0, tzinfo=_ET), True),
        ("sunday_control", datetime(2026, 9, 13, 18, 0, tzinfo=_ET), True),
    )

    @pytest.mark.parametrize("window,et,expect_reset", REQUIRED,
                             ids=["sep06_reset", "sep07_NO_reset", "sep08_reset", "sep13_reset"])
    def test_replay_boundary_matches_pine(self, inst_windows, window, et, expect_reset):
        inst, windows = inst_windows
        rows = _load_window(windows, window)
        i = _index_at(rows, et)
        assert i > 0
        # The fixture itself says what Pine did at this bar — never inferred.
        pine_reset = rows[i]["pine_daily_time"] != rows[i - 1]["pine_daily_time"]
        assert pine_reset is expect_reset
        replay_reset = i in detect_day_boundaries(_bars(rows), inst)
        assert replay_reset is expect_reset

    def test_sep07_1800_is_same_trade_date_as_sep06_1800(self, inst_windows):
        inst, windows = inst_windows
        rows = _load_window(windows, "labor_day")
        sun = rows[_index_at(rows, datetime(2026, 9, 6, 18, 0, tzinfo=_ET))]
        mon = rows[_index_at(rows, datetime(2026, 9, 7, 18, 0, tzinfo=_ET))]
        tue = rows[_index_at(rows, datetime(2026, 9, 8, 18, 0, tzinfo=_ET))]
        assert cme_trading_day(sun["ts"], inst) == cme_trading_day(mon["ts"], inst) == date(2026, 9, 8)
        assert cme_trading_day(tue["ts"], inst) == date(2026, 9, 9)
        assert sun["pine_tradingday"] == mon["pine_tradingday"] == date(2026, 9, 8)
        assert tue["pine_tradingday"] == date(2026, 9, 9)


def _load_window(windows: dict[str, list[dict]], name: str) -> list[dict]:
    if name not in windows:
        pytest.skip(f"window {name} not in this instrument's export")
    return windows[name]


# ---------------------------------------------------------------------------
# Whole-window identity: every bar and every boundary, all windows.
# ---------------------------------------------------------------------------
class TestDailyIdentityAllWindows:
    def test_trading_day_equals_pine_time_tradingday_on_every_bar(self, inst_windows):
        inst, windows = inst_windows
        for rows in windows.values():
            for r in rows:
                assert cme_trading_day(r["ts"], inst) == r["pine_tradingday"], r["et"]

    def test_detect_day_boundaries_equals_pine_daily_bar_changes(self, inst_windows):
        inst, windows = inst_windows
        for name, rows in windows.items():
            assert detect_day_boundaries(_bars(rows), inst) == _pine_boundaries(rows), name

    def test_holiday_reopen_is_the_only_1800_without_reset(self, inst_windows):
        """Every 18:00 ET bar in the fixtures is a boundary EXCEPT the holiday-week
        reopens Pine did not roll on (Labor Day Mon 18:00, and the Sunday after a
        Friday holiday). Ordinary weekday and Sunday 18:00 bars all still reset."""
        inst, windows = inst_windows
        no_reset = {
            "labor_day": {datetime(2026, 9, 7, 18, 0, tzinfo=_ET)},
            "independence_day": {datetime(2026, 7, 5, 18, 0, tzinfo=_ET)},
            "juneteenth": {datetime(2026, 6, 21, 18, 0, tzinfo=_ET)},
            "sunday_control": set(),
        }
        for name, rows in windows.items():
            boundaries = set(detect_day_boundaries(_bars(rows), inst))
            for i, r in enumerate(rows):
                if (r["et"].hour, r["et"].minute) == (18, 0):
                    expect = r["et"] not in no_reset[name]
                    assert (i in boundaries) is expect, (name, r["et"])
                elif i:
                    assert i not in boundaries, (name, r["et"])  # never resets mid-day

    def test_resample_daily_uses_the_same_identity(self, inst_windows):
        inst, windows = inst_windows
        for name, rows in windows.items():
            daily = resample_daily(_bars(rows), inst)
            expect = sorted({cme_trading_day(r["ts"], inst) for r in rows})
            assert [cme_trading_day(d["ts"], inst) for d in daily] == expect, name
            assert len(daily) == len(_pine_boundaries(rows)), name


# ---------------------------------------------------------------------------
# VWAP and HOD/LOD consume that identity — and match Pine's own values.
# ---------------------------------------------------------------------------
class TestVwapAndHodLod:
    def test_vwap_does_not_reset_on_sep07_1800(self, inst_windows):
        inst, windows = inst_windows
        rows = _load_window(windows, "labor_day")
        bars = _bars(rows)
        vw = _replay_vwap_series(bars, inst)
        i = _index_at(rows, datetime(2026, 9, 7, 18, 0, tzinfo=_ET))
        hlc3 = round((bars[i]["high"] + bars[i]["low"] + bars[i]["close"]) / 3, 2)
        # A reset would make the accumulator equal this bar's typical price.
        assert vw[i] != hlc3
        assert abs(vw[i] - rows[i]["pine_vwap"]) <= 0.01
        # It is the continuation of the accumulation that began Sun 18:00.
        start = _index_at(rows, datetime(2026, 9, 6, 18, 0, tzinfo=_ET))
        assert vw[i] == compute_vwap(bars[start:i + 1])

    @pytest.mark.parametrize("window,et", [
        ("labor_day", datetime(2026, 9, 6, 18, 0, tzinfo=_ET)),
        ("labor_day", datetime(2026, 9, 8, 18, 0, tzinfo=_ET)),
        ("sunday_control", datetime(2026, 9, 13, 18, 0, tzinfo=_ET)),
    ], ids=["sep06", "sep08", "sep13"])
    def test_vwap_and_hod_lod_reset_on_the_real_boundary_bars(self, inst_windows, window, et):
        inst, windows = inst_windows
        rows = _load_window(windows, window)
        bars = _bars(rows)
        i = _index_at(rows, et)
        vw = _replay_vwap_series(bars, inst)
        hlc3 = round((bars[i]["high"] + bars[i]["low"] + bars[i]["close"]) / 3, 2)
        assert vw[i] == hlc3                      # replay accumulator restarted here
        assert abs(rows[i]["pine_vwap"] - hlc3) <= 0.01   # and so did Pine's ta.vwap
        assert rows[i]["pine_hod"] == bars[i]["high"]     # Pine HOD/LOD restarted here
        assert rows[i]["pine_lod"] == bars[i]["low"]

    def test_no_hod_lod_reset_on_sep07_1800(self, inst_windows):
        inst, windows = inst_windows
        rows = _load_window(windows, "labor_day")
        i = _index_at(rows, datetime(2026, 9, 7, 18, 0, tzinfo=_ET))
        start = _index_at(rows, datetime(2026, 9, 6, 18, 0, tzinfo=_ET))
        day_high = max(r["high"] for r in rows[start:i + 1])
        day_low = min(r["low"] for r in rows[start:i + 1])
        # Pine carried Sunday's/Monday-day's extremes through the reopen ...
        assert rows[i]["pine_hod"] == day_high and rows[i]["pine_lod"] == day_low
        assert not (rows[i]["pine_hod"] == rows[i]["high"] and rows[i]["pine_lod"] == rows[i]["low"])
        # ... and so does the real converter.
        by_et = {datetime.fromisoformat(c["timestamp"]).astimezone(_ET): c
                 for c in derive_candles(_bars(rows), inst, 15)}
        c = by_et[rows[i]["et"]]
        assert (c["hod"], c["lod"]) == (day_high, day_low)

    def test_derive_candles_hod_lod_and_vwap_match_pine_all_windows(self, inst_windows):
        """Real polygon_to_replay pipeline vs Pine on every emitted candle.

        VWAP is compared from the second replay day onward: derive_candles only
        starts its accumulator once candles are emitted (first NY ORB), so the
        dataset's very first day is partial by construction — pre-existing and
        unrelated to C14 (real corpus builds fetch warm-up days for this)."""
        inst, windows = inst_windows
        for name, rows in windows.items():
            bars = _bars(rows)
            boundaries = detect_day_boundaries(bars, inst)
            second_day_ts = bars[boundaries[1]]["ts"]
            by_ts = {int(datetime.fromisoformat(c["timestamp"]).timestamp()): c
                     for c in derive_candles(bars, inst, 15)}
            assert by_ts, name
            compared = 0
            for r in rows:
                c = by_ts.get(r["ts"])
                if c is None:
                    continue
                assert (c["hod"], c["lod"]) == (r["pine_hod"], r["pine_lod"]), (name, r["et"])
                if r["ts"] >= second_day_ts:
                    assert abs(c["vwap"] - r["pine_vwap"]) <= 0.01, (name, r["et"], c["vwap"], r["pine_vwap"])
                    compared += 1
            assert compared > 100, name


# ---------------------------------------------------------------------------
# The helper itself: deterministic, rule-generated, ordinary days untouched.
# ---------------------------------------------------------------------------
class TestCmeTradingDayHelper:
    def test_2026_non_trade_dates_are_rule_generated(self):
        assert cme_equity_index_non_trade_dates(2026) == frozenset({
            date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 5, 25),
            date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26),
            date(2026, 12, 25),
        })

    def test_good_friday_is_not_a_non_trade_date(self):
        # CME runs an abbreviated (or no) Good Friday session on Friday's own trade
        # date; it must not push the Thursday-evening session onto Monday.
        assert date(2026, 4, 3) not in cme_equity_index_non_trade_dates(2026)
        assert cme_trading_day(datetime(2026, 4, 2, 18, 0, tzinfo=_ET), "MES") == date(2026, 4, 3)

    def test_saturday_new_year_not_observed_on_friday(self):
        assert date(2027, 12, 31) not in cme_equity_index_non_trade_dates(2027)
        assert date(2028, 1, 1) not in cme_equity_index_non_trade_dates(2028)

    def test_ordinary_weekday_and_sunday_reopen_unchanged(self):
        # Tue 2026-06-09 17:59 → 06-09; 18:00 → 06-10 (old mechanical rule, still true).
        assert cme_trading_day(datetime(2026, 6, 9, 17, 59, tzinfo=_ET), "MES") == date(2026, 6, 9)
        assert cme_trading_day(datetime(2026, 6, 9, 18, 0, tzinfo=_ET), "MES") == date(2026, 6, 10)
        # Sun 2026-06-14 18:00 → Mon 06-15; Fri 06-12 16:59 → 06-12.
        assert cme_trading_day(datetime(2026, 6, 14, 18, 0, tzinfo=_ET), "MES") == date(2026, 6, 15)
        assert cme_trading_day(datetime(2026, 6, 12, 16, 59, tzinfo=_ET), "MES") == date(2026, 6, 12)

    def test_holiday_eve_reopen_maps_to_next_trade_date(self):
        # Thanksgiving 2026-11-26: Wed 18:00 → Fri 11-27; Thu 18:00 reopen → still Fri.
        assert cme_trading_day(datetime(2026, 11, 25, 18, 0, tzinfo=_ET), "MES") == date(2026, 11, 27)
        assert cme_trading_day(datetime(2026, 11, 26, 18, 0, tzinfo=_ET), "MES") == date(2026, 11, 27)
        # Christmas 2026-12-25 (Fri): Thu 12-24 12:00 → 12-24; Sun 12-27 18:00 → Mon 12-28.
        assert cme_trading_day(datetime(2026, 12, 24, 12, 0, tzinfo=_ET), "MES") == date(2026, 12, 24)
        assert cme_trading_day(datetime(2026, 12, 27, 18, 0, tzinfo=_ET), "MES") == date(2026, 12, 28)

    def test_accepts_unix_int_and_datetime(self):
        dt = datetime(2026, 9, 7, 18, 0, tzinfo=_ET)
        assert cme_trading_day(int(dt.timestamp()), "MNQ") == cme_trading_day(dt, "MNQ") == date(2026, 9, 8)

    def test_dst_boundary_does_not_shift_reset(self):
        # Sun 2026-11-01 (DST ends 02:00 ET): both the (bar-less) Sunday pre-open and the
        # 18:00 ET reopen key to Monday; the Friday-before close keys to Friday.
        assert cme_trading_day(datetime(2026, 11, 1, 17, 59, tzinfo=_ET), "MES") == date(2026, 11, 2)
        assert cme_trading_day(datetime(2026, 11, 1, 18, 0, tzinfo=_ET), "MES") == date(2026, 11, 2)
        assert cme_trading_day(datetime(2026, 10, 30, 16, 59, tzinfo=_ET), "MES") == date(2026, 10, 30)


# ---------------------------------------------------------------------------
# Calendar selection is explicit per product: only the proven equity-index
# instruments get the holiday calendar; everything else keeps the mechanical key.
# ---------------------------------------------------------------------------
class TestCalendarIsProductAware:
    LABOR_SUN = datetime(2026, 9, 6, 18, 0, tzinfo=_ET)
    LABOR_MON = datetime(2026, 9, 7, 18, 0, tzinfo=_ET)

    def test_proven_equity_index_products_use_the_calendar(self):
        assert CME_EQUITY_INDEX_INSTRUMENTS == frozenset({"MES", "MNQ", "M2K"})
        for inst in ("MES", "MNQ", "M2K", "mes"):
            assert trading_day_calendar(inst) == CALENDAR_CME_EQUITY_INDEX
            assert cme_trading_day(self.LABOR_SUN, inst) == cme_trading_day(self.LABOR_MON, inst) == date(2026, 9, 8)

    @pytest.mark.parametrize("inst", ["MGC", "MCL", "MBT", "MYM", "ES", "NQ", "", "UNKNOWN"])
    def test_unsupported_products_do_not_inherit_the_equity_calendar(self, inst):
        assert trading_day_calendar(inst) is None
        # Mechanical key, exactly as before C14: Sun 18:00 → 09-07, Mon 18:00 → 09-08.
        assert cme_trading_day(self.LABOR_SUN, inst) == date(2026, 9, 7)
        assert cme_trading_day(self.LABOR_MON, inst) == date(2026, 9, 8)
        # Juneteenth eve reopen stays on the (holiday) civil date, not the next trade date.
        assert cme_trading_day(datetime(2026, 6, 18, 18, 0, tzinfo=_ET), inst) == date(2026, 6, 19)

    def test_unsupported_product_boundaries_are_every_1800_on_the_labor_day_fixture(self):
        rows = _load("MES")["labor_day"]  # bars only; the instrument label decides the key
        bars = _bars(rows)
        mech = set(detect_day_boundaries(bars, "MCL"))
        equity = set(detect_day_boundaries(bars, "MES"))
        every_1800 = {0} | {i for i, r in enumerate(rows) if (r["et"].hour, r["et"].minute) == (18, 0)}
        assert mech == every_1800
        assert equity == every_1800 - {_index_at(rows, self.LABOR_MON)}
        assert len(resample_daily(bars, "MCL")) == len(mech)
        assert len(resample_daily(bars, "MES")) == len(equity)

    def test_instrument_argument_is_required(self):
        with pytest.raises(TypeError):
            cme_trading_day(self.LABOR_SUN)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            detect_day_boundaries([])  # type: ignore[call-arg]
