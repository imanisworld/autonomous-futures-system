from datetime import date, datetime, timedelta, timezone

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

    exact_bucket_arm = evaluate_capture_gate(
        obs, prearmed_at=datetime(2026, 9, 18, 15, 5, tzinfo=UTC),
        decision_ts=datetime(2026, 9, 18, 15, 10, 45, tzinfo=UTC),
        max_capture_lag_seconds=60,
    )
    assert exact_bucket_arm.eligible is False
    assert exact_bucket_arm.reason_code == "no_proven_pretrigger_arm"

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


def test_212_setup_can_reanchor_from_previous_session_into_open():
    monday = nyse_session_for(date(2026, 9, 21))
    assert monday is not None
    friday = nyse_session_for(date(2026, 9, 18))
    assert friday is not None
    history = [
        Bar(start=datetime(2026, 9, 18, 18, 30, tzinfo=UTC), open=7, high=10, low=5, close=8, volume=1000, vwap=8),
        Bar(start=datetime(2026, 9, 18, 19, 0, tzinfo=UTC), open=8, high=11, low=6, close=10, volume=1000, vwap=9),
        Bar(start=datetime(2026, 9, 18, 19, 30, tzinfo=UTC), open=9, high=10.5, low=6.5, close=9.5, volume=1000, vwap=9),
    ]
    lower = [
        Bar(start=monday.open, open=9, high=10, low=6.4, close=6.8, volume=1000, vwap=8),
    ]
    rows = observe_212_setups(
        ticker="SPY", history_30m=history, session_5m=lower,
        session=monday, decision_ts=monday.open + timedelta(minutes=6),
    )
    row = [x for x in rows if x.watch_start == monday.open.isoformat()][0]
    assert row.status == "TRIGGERED"
    assert row.family == "STRAT_212_REVERSAL"
    assert row.direction == "SHORT"


def test_capture_gate_exact_cross_timestamp_overrides_bar_close_lag():
    from alert_ranker.options_212r_prospective import evaluate_capture_gate

    lower = [
        _bar("2026-09-18T15:00:00", 9.4, 10.0, 7.0, 8.0),
        _bar("2026-09-18T15:05:00", 8.0, 8.5, 6.4, 6.8),
    ]
    obs = [x for x in observe_212_setups(
        ticker="SPY", history_30m=_history(), session_5m=lower,
        session=_session(), decision_ts=datetime(2026, 9, 18, 15, 11, tzinfo=UTC),
    ) if x.watch_start == "2026-09-18T15:00:00+00:00"][0]

    result = evaluate_capture_gate(
        obs,
        prearmed_at=datetime(2026, 9, 18, 15, 4, tzinfo=UTC),
        decision_ts=datetime(2026, 9, 18, 15, 10, 45, tzinfo=UTC),
        max_capture_lag_seconds=60,
        trigger_crossed_at=datetime(2026, 9, 18, 15, 6, 30, tzinfo=UTC),
    )
    assert result.eligible is False
    assert result.reason_code == "decision_time_capture_late"
    assert result.lag_seconds == 255


def test_capture_gate_rejects_exact_cross_outside_proven_five_minute_bucket():
    from alert_ranker.options_212r_prospective import evaluate_capture_gate

    lower = [
        _bar("2026-09-18T15:00:00", 9.4, 10.0, 7.0, 8.0),
        _bar("2026-09-18T15:05:00", 8.0, 8.5, 6.4, 6.8),
    ]
    obs = [x for x in observe_212_setups(
        ticker="SPY", history_30m=_history(), session_5m=lower,
        session=_session(), decision_ts=datetime(2026, 9, 18, 15, 11, tzinfo=UTC),
    ) if x.watch_start == "2026-09-18T15:00:00+00:00"][0]

    result = evaluate_capture_gate(
        obs,
        prearmed_at=datetime(2026, 9, 18, 15, 4, tzinfo=UTC),
        decision_ts=datetime(2026, 9, 18, 15, 10, 45, tzinfo=UTC),
        max_capture_lag_seconds=60,
        trigger_crossed_at=datetime(2026, 9, 18, 15, 10, 0, tzinfo=UTC),
    )
    assert result.eligible is False
    assert result.reason_code == "trigger_cross_outside_proven_bucket"


def test_exact_cross_allows_arm_after_bucket_start_when_arm_precedes_true_cross():
    from alert_ranker.options_212r_prospective import evaluate_capture_gate

    lower = [
        _bar("2026-09-18T15:00:00", 9.4, 10.0, 7.0, 8.0),
        _bar("2026-09-18T15:05:00", 8.0, 8.5, 6.4, 6.8),
    ]
    obs = [x for x in observe_212_setups(
        ticker="SPY", history_30m=_history(), session_5m=lower,
        session=_session(), decision_ts=datetime(2026, 9, 18, 15, 11, tzinfo=UTC),
    ) if x.watch_start == "2026-09-18T15:00:00+00:00"][0]

    result = evaluate_capture_gate(
        obs,
        prearmed_at=datetime(2026, 9, 18, 15, 5, 5, tzinfo=UTC),
        decision_ts=datetime(2026, 9, 18, 15, 6, 45, tzinfo=UTC),
        max_capture_lag_seconds=60,
        trigger_crossed_at=datetime(2026, 9, 18, 15, 6, 30, tzinfo=UTC),
    )
    assert result.eligible is True
    assert result.lag_seconds == 15

    hindsight = evaluate_capture_gate(
        obs,
        prearmed_at=datetime(2026, 9, 18, 15, 6, 30, tzinfo=UTC),
        decision_ts=datetime(2026, 9, 18, 15, 6, 45, tzinfo=UTC),
        max_capture_lag_seconds=60,
        trigger_crossed_at=datetime(2026, 9, 18, 15, 6, 30, tzinfo=UTC),
    )
    assert hindsight.eligible is False
    assert hindsight.reason_code == "no_proven_pretrigger_arm"
