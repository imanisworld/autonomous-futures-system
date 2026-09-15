"""Coverage episode reducer: structural contiguity rule, first-event semantics, R:R flags."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alert_ranker.coverage_episodes import (
    IMPLAUSIBLE_REMAINING_RR,
    REDUCER_VERSION,
    direction_runs,
    reduce_events,
    summarize,
)

T0 = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)


def _event(offset_bars: int, family="STRAT_222_CONTINUATION", direction="SHORT", symbol="BKNG", session="2026-09-15", **over):
    start = T0 + timedelta(minutes=30 * offset_bars)
    base = dict(
        observer_id="OPTIONS_COVERAGE_OBSERVER", observer_version="cov-v0.1", symbol=symbol, timeframe="30m", session_date=session,
        bar_start=start.isoformat(), bar_close=(start + timedelta(minutes=30)).isoformat(), family=family, sequence="x",
        requested_family=True, v1_supported=family == "STRAT_212_CONTINUATION", direction=direction,
        two_back_type="two_down", previous_type="two_down", current_type="two_down",
        entry_trigger=175.0 - offset_bars, invalidation=176.0 - offset_bars, risk=1.0,
        nearest_target_1=171.0, nearest_target_2=170.0, nearest_rr_1=4.0 - offset_bars * 0.5, nearest_reason="valid_targets", nearest_geometry_ok=True,
        floor_target_1=171.0, floor_target_2=170.0, floor_rr_1=4.0, floor_reason="valid_targets", floor_geometry_ok=True, floor_rescued=False,
        spy_trend="bearish", qqq_trend="bearish", hourly_candle_type="two_down", daily_candle_type="two_down", alignment_ok=True, alignment_failures="",
        first_sight_at=(start + timedelta(minutes=47, seconds=57)).isoformat(), first_sight_after_close=False, first_sight_price=174.5 - offset_bars,
        first_sight_price_source="5Min_close", nearest_remaining_rr=2.3, floor_remaining_rr=2.3, late_nearest=False, late_floor=False,
        would_qualify_v1_rule=True, would_qualify_floor_rule=True, resistance_levels=[], support_levels=[],
    )
    base.update(over)
    return base


def test_contiguous_same_family_direction_collapse_to_one_episode():
    events = [_event(0), _event(1), _event(2)]
    episodes = reduce_events(events)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep.reducer_version == REDUCER_VERSION
    assert ep.n_events == 3
    # first-opportunity semantics: the first bar's trigger/geometry define the episode
    assert ep.entry_trigger == 175.0 and ep.invalidation == 176.0
    assert ep.nearest_rr_1 == 4.0
    assert ep.first_bar_start == events[0]["bar_start"]
    assert ep.last_bar_start == events[2]["bar_start"]


def test_gap_family_change_or_direction_change_starts_new_episode():
    gap = reduce_events([_event(0), _event(2)])
    assert len(gap) == 2
    family = reduce_events([_event(0), _event(1, family="STRAT_222_REVERSAL", direction="LONG")])
    assert len(family) == 2
    direction = reduce_events([_event(0), _event(1, direction="LONG")])
    assert len(direction) == 2
    session = reduce_events([_event(0), _event(1, session="2026-09-16")])
    assert len(session) == 2


def test_direction_runs_span_families_but_not_direction_changes():
    # 3-2-2C rolling into 2-2-2C is one move; the LONG after it is another.
    events = [
        _event(0, family="STRAT_322_CONTINUATION"),
        _event(1, family="STRAT_222_CONTINUATION"),
        _event(2, family="STRAT_222_CONTINUATION"),
        _event(3, family="STRAT_222_REVERSAL", direction="LONG"),
    ]
    episodes = reduce_events(events)
    assert len(episodes) == 3
    assert direction_runs(episodes) == 2


def test_rejections_recorded_in_gate_order_and_not_collapsed():
    ev = _event(0, family="STRAT_212_REVERSAL", nearest_geometry_ok=False, nearest_reason="valid_targets", nearest_rr_1=0.4,
                alignment_ok=False, alignment_failures="spy,hourly", late_nearest=True, late_floor=False)
    ep = reduce_events([ev])[0]
    assert ep.rejections_v1_rule == [
        "unsupported_setup_family",
        "target_geometry:valid_targets",
        "market_alignment:spy,hourly",
        "late_at_first_sight",
    ]
    assert ep.rejections_floor_rule == ["unsupported_setup_family", "market_alignment:spy,hourly"]
    assert (ep.spy_aligned, ep.qqq_aligned, ep.hourly_aligned, ep.daily_aligned) == (False, True, False, True)


def test_rr_quality_flags_are_reported_not_clipped():
    # price one cent from the stop -> tiny denominator; remaining R absurd
    ev = _event(0, first_sight_price=175.99, nearest_remaining_rr=498.0, floor_remaining_rr=498.0)
    ep = reduce_events([ev])[0]
    assert "first_sight_denominator_small" in ep.rr_quality_flags
    assert "remaining_rr_implausible:nearest_remaining_rr" in ep.rr_quality_flags
    assert ep.nearest_remaining_rr == 498.0  # value preserved
    tiny = _event(0, entry_trigger=185.0, invalidation=185.1, risk=0.1)
    assert "structural_risk_tiny" in reduce_events([tiny])[0].rr_quality_flags
    nan = _event(0, nearest_remaining_rr=float("nan"))
    assert "remaining_rr_implausible:nearest_remaining_rr" in reduce_events([nan])[0].rr_quality_flags
    clean = reduce_events([_event(0)])[0]
    assert clean.rr_quality_flags == [] and not clean.rr_quality_flagged
    assert IMPLAUSIBLE_REMAINING_RR == 20.0


def test_summary_keeps_raw_and_episode_counts():
    events = [_event(0), _event(1), _event(2), _event(5, family="STRAT_312")]
    episodes = reduce_events(events)
    summary = summarize(episodes)
    fam = summary["by_family"]["STRAT_222_CONTINUATION"]
    assert fam["raw_events"] == 3 and fam["episodes"] == 1 and fam["refire_ratio"] == 3.0
    assert summary["by_family"]["STRAT_312"]["raw_events"] == 1
    assert summary["total"]["raw_events"] == 4 and summary["total"]["episodes"] == 2
    assert summary["by_session"]["2026-09-15"]["episodes"] == 2
    assert summary["by_symbol"]["BKNG"]["episodes_would_otherwise_qualify_v1_rule"] == 2
