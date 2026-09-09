from datetime import date, datetime
from zoneinfo import ZoneInfo

from research.daily_strat_failure_mode import (
    Bar,
    DailyBar,
    _first_directional_trigger,
    _first_outside_choice,
    baseline_bracket,
    trading_day_for_bar,
)
from strategy.strat_classifier import TWO_UP, classify_sequence


ET = ZoneInfo("America/New_York")


def _daily(high=110.0, low=100.0):
    return DailyBar(
        trading_day=date(2026, 9, 8),
        open=104.0,
        high=high,
        low=low,
        close=106.0,
        n_sub_bars=276,
        complete=True,
    )


def _bar(hour, minute, *, o, h, l, c):
    return Bar(
        ts=datetime(2026, 9, 8, hour, minute, tzinfo=ET),
        open=o,
        high=h,
        low=l,
        close=c,
    )


def test_222_is_a_22_continuation_slice_not_a_new_canonical_identity():
    assert classify_sequence(TWO_UP, TWO_UP, TWO_UP) == "strat_22_continuation"


def test_baseline_bracket_matches_shadow_prior_bar_break_contract():
    previous = _daily(high=110.0, low=100.0)
    assert baseline_bracket(previous, "LONG", 0.25) == (110.25, 99.75, 131.25)
    assert baseline_bracket(previous, "SHORT", 0.25) == (99.75, 110.25, 78.75)


def test_directional_trigger_is_causal_and_uses_first_boundary_cross():
    previous = _daily(high=110.0, low=100.0)
    bars = [
        _bar(9, 30, o=105.0, h=109.0, l=101.0, c=104.0),
        # Short reversal becomes actionable here.
        _bar(10, 0, o=103.0, h=104.0, l=99.5, c=100.0),
        # The Daily candle can later become outside; that future fact must not
        # be needed to create the already-triggered short.
        _bar(14, 0, o=108.0, h=111.0, l=107.0, c=110.0),
    ]
    trigger = _first_directional_trigger(bars, previous, "SHORT", 0.25)
    assert trigger == (bars[1].ts, "touch")


def test_outside_continuation_fails_closed_when_first_5m_bar_breaks_both_sides():
    previous = _daily(high=110.0, low=100.0)
    bars = [_bar(9, 30, o=105.0, h=111.0, l=99.0, c=106.0)]
    assert _first_outside_choice(bars, previous, 0.25) is None


def test_gap_trigger_is_labeled_gap_not_touch():
    previous = _daily(high=110.0, low=100.0)
    bars = [_bar(9, 30, o=111.0, h=112.0, l=110.5, c=111.5)]
    assert _first_directional_trigger(bars, previous, "LONG", 0.25) == (bars[0].ts, "gap")


def test_globex_session_maps_6pm_to_next_trading_day_and_excludes_maintenance():
    sunday_reopen = datetime(2026, 9, 6, 18, 0, tzinfo=ET)
    maintenance = datetime(2026, 9, 8, 17, 30, tzinfo=ET)
    assert trading_day_for_bar(sunday_reopen) == date(2026, 9, 7)
    assert trading_day_for_bar(maintenance) is None
