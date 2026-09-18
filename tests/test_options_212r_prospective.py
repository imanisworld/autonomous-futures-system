from datetime import date, datetime, timezone

from alert_ranker.causal_bars import Bar
from alert_ranker.options_212r_prospective import observe_212_setups
from alert_ranker.session_calendar import nyse_session_for

UTC = timezone.utc


def _bar(start, o, h, l, c):
    return Bar(start=datetime.fromisoformat(start).replace(tzinfo=UTC), open=o, high=h, low=l, close=c, volume=1000, vwap=100.0)


def _history():
    # 13:30 base -> 14:00 parent 2U -> 14:30 inside 1; watch starts 15:00Z.
    return [
        _bar("2026-09-18T13:30:00", 7, 10, 5, 8),
        _bar("2026-09-18T14:00:00", 8, 11, 6, 10),
        _bar("2026-09-18T14:30:00", 9, 10.5, 6.5, 9.5),
    ]


def _session():
    s = nyse_session_for(date(2026, 9, 18))
    assert s is not None
    return s


def test_reversal_trigger_has_source_geometry_and_detectable_time():
    lower = [
        _bar("2026-09-18T15:00:00", 9.4, 10.0, 7.0, 8.0),
        _bar("2026-09-18T15:05:00", 8.0, 8.5, 6.4, 6.8),
    ]
    rows = observe_212_setups(
        ticker="SPY", history_30m=_history(), session_5m=lower,
        session=_session(), decision_ts=datetime(2026, 9, 18, 15, 11, tzinfo=UTC),
    )
    row = [item for item in rows if item.watch_start == "2026-09-18T15:00:00+00:00"][0]
    assert row.status == "TRIGGERED"
    assert row.family == "STRAT_212_REVERSAL"
    assert row.direction == "SHORT"
    assert row.trigger_level == 6.5
    assert row.invalidation_level == 10.5
    assert row.source_target == 6.0
    assert row.source_target_r == 0.125
    assert row.source_target_consumed is False
    assert row.trigger_bar_start == "2026-09-18T15:05:00+00:00"
    assert row.trigger_detectable_at == "2026-09-18T15:10:00+00:00"


def test_no_break_stays_watching_before_window_end_and_expires_after():
    lower = [_bar("2026-09-18T15:00:00", 9, 10, 7, 8)]
    early = observe_212_setups(
        ticker="SPY", history_30m=_history(), session_5m=lower,
        session=_session(), decision_ts=datetime(2026, 9, 18, 15, 6, tzinfo=UTC),
    )
    assert [x for x in early if x.watch_start == "2026-09-18T15:00:00+00:00"][0].status == "WATCHING"
    complete_window = [
        _bar(f"2026-09-18T15:{minute:02d}:00", 9, 10, 7, 8)
        for minute in (0, 5, 10, 15, 20, 25)
    ]
    late = observe_212_setups(
        ticker="SPY", history_30m=_history(), session_5m=complete_window,
        session=_session(), decision_ts=datetime(2026, 9, 18, 15, 31, tzinfo=UTC),
    )
    assert [x for x in late if x.watch_start == "2026-09-18T15:00:00+00:00"][0].status == "EXPIRED"


def test_same_direction_first_break_is_retained_as_continuation_not_reversal():
    lower = [_bar("2026-09-18T15:00:00", 9.5, 10.6, 7.0, 10.55)]
    rows = observe_212_setups(
        ticker="SPY", history_30m=_history(), session_5m=lower,
        session=_session(), decision_ts=datetime(2026, 9, 18, 15, 6, tzinfo=UTC),
    )
    row = [x for x in rows if x.watch_start == "2026-09-18T15:00:00+00:00"][0]
    assert row.family == "STRAT_212_CONTINUATION"
    assert row.source_target is None


def test_both_boundaries_same_five_minute_bar_is_ambiguous():
    lower = [_bar("2026-09-18T15:00:00", 9, 10.6, 6.4, 9)]
    rows = observe_212_setups(
        ticker="SPY", history_30m=_history(), session_5m=lower,
        session=_session(), decision_ts=datetime(2026, 9, 18, 15, 6, tzinfo=UTC),
    )
    row = [x for x in rows if x.watch_start == "2026-09-18T15:00:00+00:00"][0]
    assert row.status == "AMBIGUOUS"
    assert row.direction is None


def test_missing_completed_five_minute_bar_blocks_instead_of_skipping_over_gap():
    lower = [_bar("2026-09-18T15:05:00", 8, 8.5, 6.4, 6.8)]  # 15:00 bar absent.
    rows = observe_212_setups(
        ticker="SPY", history_30m=_history(), session_5m=lower,
        session=_session(), decision_ts=datetime(2026, 9, 18, 15, 11, tzinfo=UTC),
    )
    row = [x for x in rows if x.watch_start == "2026-09-18T15:00:00+00:00"][0]
    assert row.status == "DATA_BLOCKED"
    assert row.reason_code.startswith("missing_5m_bars:")


def test_capture_gate_requires_true_prearm_and_timely_detection():
    from alert_ranker.options_212r_prospective import evaluate_capture_gate

    lower = [
        _bar("2026-09-18T15:00:00", 9.4, 10.0, 7.0, 8.0),
        _bar("2026-09-18T15:05:00", 8.0, 8.5, 6.4, 6.8),
    ]
    obs = [x for x in observe_212_setups(
        ticker="SPY", history_30m=_history(), session_5m=lower,
        session=_session(), decision_ts=datetime(2026, 9, 18, 15, 11, tzinfo=UTC),
    ) if x.watch_start == "2026-09-18T15:00:00+00:00"][0]

    good = evaluate_capture_gate(
        obs, prearmed_at=datetime(2026, 9, 18, 15, 4, tzinfo=UTC),
        decision_ts=datetime(2026, 9, 18, 15, 10, 45, tzinfo=UTC),
        max_capture_lag_seconds=60,
    )
    assert good.eligible is True
    assert good.lag_seconds == 45

    late_arm = evaluate_capture_gate(
        obs, prearmed_at=datetime(2026, 9, 18, 15, 6, tzinfo=UTC),
        decision_ts=datetime(2026, 9, 18, 15, 10, 45, tzinfo=UTC),
        max_capture_lag_seconds=60,
    )
    assert late_arm.reason_code == "no_proven_pretrigger_arm"

    late_capture = evaluate_capture_gate(
        obs, prearmed_at=datetime(2026, 9, 18, 15, 4, tzinfo=UTC),
        decision_ts=datetime(2026, 9, 18, 15, 12, tzinfo=UTC),
        max_capture_lag_seconds=60,
    )
    assert late_capture.reason_code == "decision_time_capture_late"
    assert late_capture.eligible is False


def test_opening_watch_reanchors_from_immediately_prior_session():
    # Prior session closes with 2U -> inside 1. The next logical 30m watch bar
    # is today's 09:30 ET open despite the overnight clock gap.
    history = [
        _bar("2026-09-17T18:30:00", 7, 10, 5, 8),
        _bar("2026-09-17T19:00:00", 8, 11, 6, 10),
        _bar("2026-09-17T19:30:00", 9, 10.5, 6.5, 9.5),
    ]
    lower = [_bar("2026-09-18T13:30:00", 9.4, 10.0, 6.4, 6.8)]
    rows = observe_212_setups(
        ticker="SPY", history_30m=history, session_5m=lower,
        session=_session(), decision_ts=datetime(2026, 9, 18, 13, 36, tzinfo=UTC),
    )
    opening = [x for x in rows if x.watch_start == "2026-09-18T13:30:00+00:00"]
    assert len(opening) == 1
    assert opening[0].status == "TRIGGERED"
    assert opening[0].family == "STRAT_212_REVERSAL"
    assert opening[0].direction == "SHORT"


def test_opening_watch_refuses_stale_nonprevious_session_precursor():
    history = [
        _bar("2026-09-16T18:30:00", 7, 10, 5, 8),
        _bar("2026-09-16T19:00:00", 8, 11, 6, 10),
        _bar("2026-09-16T19:30:00", 9, 10.5, 6.5, 9.5),
    ]
    lower = [_bar("2026-09-18T13:30:00", 9.4, 10.0, 6.4, 6.8)]
    rows = observe_212_setups(
        ticker="SPY", history_30m=history, session_5m=lower,
        session=_session(), decision_ts=datetime(2026, 9, 18, 13, 36, tzinfo=UTC),
    )
    assert not [x for x in rows if x.watch_start == "2026-09-18T13:30:00+00:00"]
