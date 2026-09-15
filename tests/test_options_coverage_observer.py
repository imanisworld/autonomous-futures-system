"""Read-only 30m coverage observer: definitions, gates, identity, funnel."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from alert_ranker.causal_bars import MINUTE_5, MINUTE_30, Bar
from alert_ranker.coverage_observer import (
    OBSERVED_TIMEFRAME,
    OBSERVER_ID,
    OBSERVER_VERSION,
    REQUESTED_FAMILIES,
    V1_SUPPORTED_FAMILIES,
    CoverageEvent,
    build_symbol_series,
    classify_window,
    first_sight,
    funnel,
    observe_symbol,
    structure_levels,
)
from alert_ranker.session_calendar import nyse_session_for

UTC = timezone.utc


def _bar(start: datetime, high: float, low: float, *, open_=None, close=None, volume: float = 1_000.0) -> Bar:
    mid = (high + low) / 2
    return Bar(start=start, open=mid if open_ is None else open_, high=high, low=low, close=mid if close is None else close, volume=volume, vwap=mid)


def _window(*hl: tuple[float, float]) -> list[Bar]:
    t0 = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)
    return [_bar(t0 + timedelta(minutes=30 * i), h, l) for i, (h, l) in enumerate(hl)]


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #


def test_identity_is_distinct_from_v1():
    assert OBSERVER_ID == "OPTIONS_COVERAGE_OBSERVER"
    assert OBSERVER_VERSION.startswith("cov-v")
    assert OBSERVED_TIMEFRAME == "30m"
    assert V1_SUPPORTED_FAMILIES == frozenset({"STRAT_212_CONTINUATION"})
    assert set(V1_SUPPORTED_FAMILIES) < set(REQUESTED_FAMILIES)


def test_observer_has_no_trade_side_effects():
    """The module must not import anything that can alert, price, or block."""
    import alert_ranker.coverage_observer as module

    source = open(module.__file__).read()
    for forbidden in ("discord", "fetch_expirations", "block_episode", "risk_budget", "shadow_journal", "_apply_paper_v1_contract", "scanner_legacy", "ScanStorage"):
        assert forbidden not in source, forbidden


# --------------------------------------------------------------------------- #
# family classification (borrowed definitions)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "hl, family, direction, trigger, invalidation",
    [
        # 2U, 1, 2U -> continuation (V1 supported)
        (((10, 0), (11, 1), (10.5, 1.5), (12, 2)), "STRAT_212_CONTINUATION", "LONG", 10.5, 1.5),
        # 2U, 1, 2D -> reversal
        (((10, 0), (11, 1), (10.5, 1.5), (10, 0.5)), "STRAT_212_REVERSAL", "SHORT", 1.5, 10.5),
        # 2U, 2U, 2U -> 2-2-2 continuation
        (((10, 0), (11, 1), (12, 2), (13, 3)), "STRAT_222_CONTINUATION", "LONG", 12, 2),
        # 2U, 2U, 2D -> 2-2-2 reversal
        (((10, 0), (11, 1), (12, 2), (11, 1)), "STRAT_222_REVERSAL", "SHORT", 2, 12),
        # 3, 1, 2U -> 3-1-2
        (((10, 5), (12, 3), (11, 4), (13, 5)), "STRAT_312", "LONG", 11, 4),
        # 3, 2U, 2U -> 3-2-2 continuation
        (((10, 5), (12, 3), (13, 4), (14, 5)), "STRAT_322_CONTINUATION", "LONG", 13, 4),
        # 3, 2U, 2D -> 3-2-2 reversal
        (((10, 5), (12, 3), (13, 4), (12, 2)), "STRAT_322_REVERSAL", "SHORT", 4, 13),
        # 1, 2U, 2D -> not a requested family, kept as OTHER
        (((10, 0), (9, 1), (10, 2), (9, 0)), "OTHER:strat_122", "SHORT", 2, 10),
    ],
)
def test_classify_window_families(hl, family, direction, trigger, invalidation):
    bars = _window(*hl)
    result = classify_window(*bars)
    assert result is not None
    assert result["family"] == family
    assert result["direction"] == direction
    assert result["entry_trigger"] == trigger
    assert result["invalidation"] == invalidation


def test_classify_window_ignores_non_directional_current_bar():
    inside = _window((10, 0), (11, 1), (12, 2), (11.5, 2.5))
    outside = _window((10, 0), (11, 1), (12, 2), (13, 1))
    assert classify_window(*inside) is None
    assert classify_window(*outside) is None


# --------------------------------------------------------------------------- #
# target pool mirrors BarContextBuilder._causal_structure_levels
# --------------------------------------------------------------------------- #


def test_structure_levels_exclude_trigger_and_breakout_bars():
    day1 = datetime(2026, 9, 14, 13, 30, tzinfo=UTC)
    day2 = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)
    series = [
        _bar(day1, 100, 90),
        _bar(day1 + timedelta(minutes=30), 105, 95),  # day-1 high 105 / low 90
        _bar(day2, 103, 97),  # directional opener -> its own high/low retained
        _bar(day2 + timedelta(minutes=30), 102, 98),  # inside (trigger bar) -> excluded
        _bar(day2 + timedelta(minutes=60), 104, 99),  # breakout -> excluded
    ]
    resistance, support = structure_levels(series, 4)
    assert resistance == (103, 105)
    assert support == (97, 90)


# --------------------------------------------------------------------------- #
# first sight
# --------------------------------------------------------------------------- #


def test_first_sight_matches_scanner_grid():
    close = datetime(2026, 9, 15, 16, 0, tzinfo=UTC)
    assert first_sight(close) == datetime(2026, 9, 15, 16, 17, 57, tzinfo=UTC)
    close = datetime(2026, 9, 15, 18, 30, tzinfo=UTC)
    assert first_sight(close) == datetime(2026, 9, 15, 18, 47, 57, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# series construction fails closed
# --------------------------------------------------------------------------- #


def _sessions(*days: date):
    return [s for s in (nyse_session_for(d) for d in days) if s is not None]


def _full_session(day: date, base: float, step: float = 0.0) -> list[Bar]:
    session = nyse_session_for(day)
    assert session is not None
    bars = []
    cursor = session.open
    i = 0
    while cursor + MINUTE_30.delta <= session.close:
        bars.append(_bar(cursor, base + i * step + 1, base + i * step - 1))
        cursor += MINUTE_30.delta
        i += 1
    return bars


def test_build_symbol_series_requires_whole_sessions():
    days = (date(2026, 9, 14), date(2026, 9, 15))
    good = _full_session(days[0], 100) + _full_session(days[1], 100)
    series = build_symbol_series("AAPL", good, _sessions(*days))
    assert series.observable
    assert len(series.series) == 26

    with_gap = [b for b in good if b.start != good[3].start]
    broken = build_symbol_series("AAPL", with_gap, _sessions(*days))
    assert not broken.observable
    assert broken.reason.startswith("incomplete_sessions:2026-09-14")

    nothing = build_symbol_series("VIX", [], _sessions(*days))
    assert not nothing.observable
    assert nothing.reason.startswith("missing_sessions")


# --------------------------------------------------------------------------- #
# end-to-end on synthetic bars: gates and funnel
# --------------------------------------------------------------------------- #


def _synthetic_day():
    """Two sessions; the second contains one 2-1-2 continuation short at 15:30Z.

    Prior-day structure is placed so the nearest support is < 1R away and a
    farther support is > 1R away, reproducing the geometry the replay found.
    """
    d0, d1, d2 = date(2026, 9, 11), date(2026, 9, 14), date(2026, 9, 15)
    s0, s1, s2 = nyse_session_for(d0), nyse_session_for(d1), nyse_session_for(d2)
    # The >=1R floor needs TWO qualifying levels (find_targets still wants a
    # target_2), so two prior sessions each contribute one far low.
    older = _full_session(d0, 100)
    older = [_bar(b.start, b.high, 95.0 if i == 5 else b.low) for i, b in enumerate(older)]  # day-0 low 95
    prior = _full_session(d1, 100)  # day-1 high 101 / low 99
    # Day 2: flat bars, then 2D opener, inside, 2D breakout at 15:30Z.
    bars = []
    cursor = s2.open
    plan = [
        (100.5, 99.5),  # 13:30 inside of prior day's last bar? irrelevant history
        (100.5, 99.5),  # 14:00
        (100.5, 99.5),  # 14:30
        (100.2, 98.0),  # 15:00 two_down opener (low 98)
        (99.5, 98.4),   # 15:30 inside -> trigger low 98.4 / stop 99.5 (risk 1.1)
        (99.0, 97.9),   # 16:00 two_down breakout (current)
    ]
    for high, low in plan:
        bars.append(_bar(cursor, high, low, close=(high + low) / 2))
        cursor += MINUTE_30.delta
    # nearest support = opener low 98.0 (0.4 away = 0.36R); prior-day low 99 is
    # above entry so not a short target; add a far prior-day low via day 1.
    prior = [_bar(b.start, b.high, 96.0 if i == 5 else b.low) for i, b in enumerate(prior)]  # day-1 low 96 -> 2.18R
    return [s0, s1, s2], older + prior + bars, bars


def test_observe_symbol_gates_and_identity():
    sessions, bars, day_bars = _synthetic_day()
    series = build_symbol_series("XYZ", bars, sessions)
    assert series.observable
    session = sessions[-1]
    # 5Min bars: price sits at 98.3 at first sight (breakout bar closes 16:30Z -> 16:47:57Z)
    fine = [_bar(session.open + timedelta(minutes=5 * i), 98.4, 98.2, close=98.3) for i in range(60)]
    events = observe_symbol(series, session, spy=None, qqq=None, fine_bars=fine, fine_timeframe=MINUTE_5)
    shorts = [e for e in events if e.family == "STRAT_212_CONTINUATION"]
    assert len(shorts) == 1
    e = shorts[0]
    assert (e.observer_id, e.observer_version, e.timeframe) == (OBSERVER_ID, OBSERVER_VERSION, "30m")
    assert e.v1_supported and e.requested_family
    assert e.direction == "SHORT"
    assert e.entry_trigger == 98.4 and e.invalidation == 99.5
    assert e.nearest_target_1 == 98.0 and e.nearest_rr_1 == pytest.approx(0.4 / 1.1)
    assert not e.nearest_geometry_ok
    assert e.floor_target_1 == 96.0 and e.floor_geometry_ok and e.floor_rescued
    # No index context -> alignment must fail on spy/qqq, never silently pass.
    assert not e.alignment_ok
    assert "spy" in e.alignment_failures and "qqq" in e.alignment_failures
    assert e.first_sight_at.endswith("16:47:57+00:00")
    assert not e.first_sight_after_close
    assert e.first_sight_price == 98.3 and e.first_sight_price_source == "5Min_close"
    assert e.late_nearest is True  # 0.3/1.2 remaining
    assert e.floor_target_2 == 95.0
    assert e.late_floor is False  # 2.3/1.2 remaining
    assert e.would_qualify_v1_rule is False and e.would_qualify_floor_rule is False

    report = funnel(events)
    v1 = report["v1_rule"]
    assert v1["all_structural_events"] == len(events)
    assert v1["v1_supported"] == 1 and v1["target_geometry_failure"] == 1
    assert v1["would_otherwise_qualify"] == 0
    fl = report["floor_rule"]
    assert fl["target_geometry_failure"] == 0 and fl["market_alignment_failure"] == 1
    fam = report["by_family"]["STRAT_212_CONTINUATION"]
    assert fam["v1_supported"] and fam["count"] == 1


def test_last_bar_of_session_is_never_seen_by_v1():
    sessions, bars, _ = _synthetic_day()
    session = sessions[-1]
    # Extend day 2 to the close with a 2-1-2 continuation on the final bar.
    cursor = bars[-1].start + MINUTE_30.delta
    extra = []
    while cursor + MINUTE_30.delta <= session.close:
        extra.append(_bar(cursor, 99.0, 97.9))
        cursor += MINUTE_30.delta
    # make the final three bars 2D, 1, 2D
    extra[-3] = _bar(extra[-3].start, 98.8, 97.0)
    extra[-2] = _bar(extra[-2].start, 98.5, 97.3)
    extra[-1] = _bar(extra[-1].start, 98.0, 96.5)
    series = build_symbol_series("XYZ", bars + extra, sessions)
    events = observe_symbol(series, session, spy=None, qqq=None)
    last = [e for e in events if e.bar_start == extra[-1].start_utc.isoformat()]
    assert len(last) == 1
    assert last[0].first_sight_after_close is True
    assert last[0].would_qualify_v1_rule is False
    report = funnel([e for e in events if e.v1_supported])
    assert report["v1_rule"]["first_sight_after_close"] + report["v1_rule"]["target_geometry_failure"] + report["v1_rule"]["market_alignment_failure"] >= 1


def test_funnel_as_if_supported_walks_unsupported_families():
    rows = [
        CoverageEvent(
            observer_id=OBSERVER_ID, observer_version=OBSERVER_VERSION, symbol="A", timeframe="30m", session_date="2026-09-15",
            bar_start="s", bar_close="c", family="STRAT_212_REVERSAL", sequence="strat_212_reversal", requested_family=True, v1_supported=False,
            direction="LONG", two_back_type="two_down", previous_type="inside_bar", current_type="two_up", entry_trigger=10, invalidation=9, risk=1,
            nearest_target_1=12, nearest_target_2=13, nearest_rr_1=2.0, nearest_reason="valid_targets", nearest_geometry_ok=True,
            floor_target_1=12, floor_target_2=13, floor_rr_1=2.0, floor_reason="valid_targets", floor_geometry_ok=True, floor_rescued=False,
            spy_trend="bullish", qqq_trend="bullish", hourly_candle_type="two_up", daily_candle_type="two_up", alignment_ok=True, alignment_failures="",
            first_sight_at="t", first_sight_after_close=False, first_sight_price=10.2, first_sight_price_source="5Min_close",
            nearest_remaining_rr=1.5, floor_remaining_rr=1.5, late_nearest=False, late_floor=False,
            would_qualify_v1_rule=True, would_qualify_floor_rule=True,
        )
    ]
    report = funnel(rows)
    assert report["v1_rule"]["unsupported_setup_family"] == 1
    assert report["v1_rule"]["would_otherwise_qualify"] == 0
    fam = report["by_family"]["STRAT_212_REVERSAL"]
    assert fam["requested"] and not fam["v1_supported"]
    assert fam["as_if_supported_v1_rule"]["would_otherwise_qualify"] == 1
