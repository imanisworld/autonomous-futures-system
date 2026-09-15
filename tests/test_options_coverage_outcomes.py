"""Coverage outcome study: causal walk semantics, views, buckets, no intrabar ordering."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alert_ranker.causal_bars import Bar
from alert_ranker.coverage_episodes import Episode, REDUCER_VERSION
from alert_ranker.coverage_outcomes import (
    OUTCOME_ID,
    OUTCOME_VERSION,
    gate_bucket,
    measure_episode,
    summarize_outcomes,
)
from alert_ranker.session_calendar import nyse_session_for

UTC = timezone.utc
SESSION = nyse_session_for(datetime(2026, 9, 15).date())
OPEN = SESSION.open.astimezone(UTC)  # 13:30Z


def _b(minutes: int, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(start=OPEN + timedelta(minutes=minutes), open=o, high=h, low=l, close=c, volume=1.0, vwap=(h + l) / 2)


def _episode(**over) -> Episode:
    # breakout 30m bar 15:00-15:30Z (minutes 90..120 after open); LONG, trigger 101, stop 100 (risk 1)
    base = dict(
        reducer_version=REDUCER_VERSION, symbol="XYZ", session_date="2026-09-15", family="STRAT_222_CONTINUATION", direction="LONG",
        v1_supported=False, requested_family=True, n_events=1,
        first_bar_start=(OPEN + timedelta(minutes=90)).isoformat(), first_bar_close=(OPEN + timedelta(minutes=120)).isoformat(),
        last_bar_start=(OPEN + timedelta(minutes=90)).isoformat(),
        entry_trigger=101.0, invalidation=100.0, risk=1.0,
        nearest_target_1=102.0, nearest_rr_1=1.0, nearest_reason="valid_targets", nearest_geometry_ok=True,
        floor_target_1=103.0, floor_rr_1=2.0, floor_reason="valid_targets", floor_geometry_ok=True, floor_rescued=False,
        spy_trend="bullish", qqq_trend="bullish", hourly_candle_type="two_up", daily_candle_type="two_up",
        spy_aligned=True, qqq_aligned=True, hourly_aligned=True, daily_aligned=True, alignment_ok=True, alignment_failures="",
        first_sight_at=(OPEN + timedelta(minutes=137, seconds=57)).isoformat(),  # 15:47:57Z
        first_sight_after_close=False, first_sight_price=101.4,
        nearest_remaining_rr=0.43, floor_remaining_rr=1.14, late_nearest=True, late_floor=False,
        would_qualify_v1_rule=False, would_qualify_floor_rule=False,
    )
    base.update(over)
    return Episode(**base)


def _flat(minutes_from: int, minutes_to: int, price: float) -> list[Bar]:
    return [_b(m, price, price + 0.05, price - 0.05, price) for m in range(minutes_from, minutes_to, 5)]


def test_mechanical_view_starts_at_trigger_cross_and_first_sight_after_tick():
    bars = _flat(0, 90, 100.5)
    bars += [_b(90, 100.6, 100.9, 100.5, 100.8), _b(95, 100.8, 101.2, 100.7, 101.1)]  # cross at 15:05Z
    bars += _flat(100, 140, 101.3)
    bars += [_b(140, 101.4, 102.1, 101.3, 102.0)]  # target 102 at 15:50Z (after first sight 15:47:57)
    bars += _flat(145, 390, 102.0)
    o = measure_episode(_episode(), SESSION.open, SESSION.close, bars)
    assert (o.outcome_id, o.outcome_version) == (OUTCOME_ID, OUTCOME_VERSION)
    mech = o.view("mechanical", "nearest")
    assert mech.entry_at == (OPEN + timedelta(minutes=95)).isoformat()
    assert mech.outcome == "TARGET_FIRST" and mech.target_1_hit_at == (OPEN + timedelta(minutes=140)).isoformat()
    assert mech.hit_1_0r_at == (OPEN + timedelta(minutes=140)).isoformat()
    assert mech.minutes_to_target_1 == 50.0  # 15:05 entry bar -> 15:55 close of target bar
    sight = o.view("first_sight", "nearest")
    assert sight.entry_reference == 101.4
    assert sight.outcome == "TARGET_FIRST"
    assert sight.mfe_r == round((102.1 - 101.4) / 1.0, 4)
    # blind window: 101.4 - 101.0 = +0.4R of move consumed before V1 could see it
    assert o.blind_window_extension_r == 0.4
    assert o.clean
    assert o.gate_bucket_nearest == "UNSUPPORTED_FAMILY"


def test_same_5m_bar_target_and_stop_is_ambiguous_not_scored():
    bars = _flat(0, 90, 100.5) + [_b(90, 100.6, 101.2, 100.5, 101.0)]  # cross bar, clean
    bars += [_b(95, 101.0, 102.2, 99.9, 100.0)]  # touches 102 target AND 100 stop
    bars += _flat(100, 390, 100.0)
    o = measure_episode(_episode(), SESSION.open, SESSION.close, bars)
    mech = o.view("mechanical", "nearest")
    assert mech.outcome == "AMBIGUOUS"
    assert "same_5m_bar_target_stop" in mech.flags
    assert mech.ambiguous_bar["bar_start"] == (OPEN + timedelta(minutes=95)).isoformat()
    assert mech.ambiguous_bar["high"] == 102.2 and mech.ambiguous_bar["low"] == 99.9
    # the floor geometry (target 103) is NOT touched in that bar -> it resolves as a loss
    assert o.view("mechanical", "floor").outcome == "INVALIDATION_FIRST"


def test_trigger_and_stop_in_same_cross_bar_is_trigger_path_ambiguous():
    bars = _flat(0, 90, 100.5) + [_b(90, 100.5, 101.3, 99.8, 100.2)] + _flat(95, 390, 100.2)
    o = measure_episode(_episode(), SESSION.open, SESSION.close, bars)
    mech = o.view("mechanical", "nearest")
    assert mech.outcome == "AMBIGUOUS" and "trigger_path_ambiguous" in mech.flags


def test_thresholds_not_counted_in_invalidation_bar_and_unresolved_at_close():
    bars = _flat(0, 90, 100.5) + [_b(90, 100.6, 101.2, 100.5, 101.0)]
    bars += _flat(95, 200, 101.5)  # +0.5R reached
    bars += [_b(200, 101.5, 102.5, 99.9, 100.1)]  # +1.5R AND stop in the same bar -> stop wins, +1R not counted
    bars += _flat(205, 390, 100.1)
    o = measure_episode(_episode(nearest_target_1=104.0, floor_target_1=104.0), SESSION.open, SESSION.close, bars)
    mech = o.view("mechanical", "nearest")
    assert mech.outcome == "INVALIDATION_FIRST"
    assert mech.hit_0_5r_at is not None and mech.hit_1_0r_at is None
    assert mech.max_favorable_r_before_invalidation == 0.55  # 101.55 high of the flat bars
    # unresolved: never touches target 104 nor stop
    calm = _flat(0, 90, 100.5) + [_b(90, 100.6, 101.2, 100.5, 101.0)] + _flat(95, 390, 101.5)
    o2 = measure_episode(_episode(nearest_target_1=104.0, floor_target_1=104.0), SESSION.open, SESSION.close, calm)
    assert o2.view("mechanical", "nearest").outcome == "UNRESOLVED_AT_CLOSE"
    assert o2.view("mechanical", "nearest").first_decisive_event == "close"


def test_first_sight_after_close_and_missing_bars_are_flagged_not_dropped():
    bars = _flat(0, 90, 100.5) + [_b(90, 100.6, 101.2, 100.5, 101.0)] + _flat(95, 390, 101.5)
    o = measure_episode(_episode(first_sight_after_close=True, first_sight_price=None), SESSION.open, SESSION.close, bars)
    assert o.view("first_sight", "nearest").outcome == "FIRST_SIGHT_AFTER_CLOSE"
    assert "first_sight_after_close" in o.quality_flags and not o.clean
    assert o.blind_window_extension_r is None
    gappy = [b for b in bars if b.start != OPEN + timedelta(minutes=200)]
    o2 = measure_episode(_episode(), SESSION.open, SESSION.close, gappy)
    assert "missing_forward_bars" in o2.quality_flags
    o3 = measure_episode(_episode(), SESSION.open, SESSION.close, [])
    assert o3.view("mechanical", "nearest").outcome == "DATA_INVALID"
    assert "missing_forward_bars" in o3.quality_flags


def test_short_direction_normalises_r_positive_when_favourable():
    ep = _episode(direction="SHORT", entry_trigger=100.0, invalidation=101.0, nearest_target_1=99.0, floor_target_1=98.0, first_sight_price=99.6)
    bars = _flat(0, 90, 100.5) + [_b(90, 100.4, 100.6, 99.9, 100.0)]  # cross below 100
    bars += [_b(95, 100.0, 100.1, 98.9, 99.0)] + _flat(100, 390, 99.0)
    o = measure_episode(ep, SESSION.open, SESSION.close, bars)
    mech = o.view("mechanical", "nearest")
    assert mech.outcome == "TARGET_FIRST" and mech.mfe_r > 0 and mech.mae_r <= 0
    assert o.blind_window_extension_r == 0.4  # 100.0 -> 99.6 favourable for a short


def test_gate_bucket_precedence_and_summary_shape():
    assert gate_bucket(_episode(), "nearest") == "UNSUPPORTED_FAMILY"
    v1 = _episode(v1_supported=True, nearest_geometry_ok=False)
    assert gate_bucket(v1, "nearest") == "TARGET_GEOMETRY_REJECTED"
    assert gate_bucket(_episode(v1_supported=True, alignment_ok=False), "floor") == "MARKET_ALIGNMENT_REJECTED"
    assert gate_bucket(_episode(v1_supported=True), "nearest") == "LATE_AT_FIRST_SIGHT"
    assert gate_bucket(_episode(v1_supported=True), "floor") == "WOULD_OTHERWISE_QUALIFY"
    bars = _flat(0, 90, 100.5) + [_b(90, 100.6, 101.2, 100.5, 101.0)] + _flat(95, 140, 101.3) + [_b(140, 101.4, 102.1, 101.3, 102.0)] + _flat(145, 390, 102.0)
    summary = summarize_outcomes([measure_episode(_episode(), SESSION.open, SESSION.close, bars)])
    fam = summary["by_family"]["STRAT_222_CONTINUATION"]
    assert fam["episodes"] == 1 and fam["clean_episodes"] == 1
    assert fam["clean"]["mechanical:nearest"]["TARGET_FIRST"] == 1
    assert fam["clean"]["timing"]["pct_gaining_ge_0_25r_before_first_sight"] == 100.0
    assert summary["by_gate_bucket_nearest"]["UNSUPPORTED_FAMILY"]["episodes"] == 1
