from datetime import date, datetime, timedelta, timezone

from alert_ranker.causal_bars import Bar
from alert_ranker.options_122_prospective import evaluate_capture_gate, observe_122_setups
from alert_ranker.session_calendar import nyse_session_for

UTC = timezone.utc


def _bar(start, o, h, l, c):
    return Bar(start=datetime.fromisoformat(start).replace(tzinfo=UTC), open=o, high=h, low=l, close=c, volume=1000, vwap=100.0)


def _history_122():
    # base -> inside 1 -> directional 2U; the next 30m window watches for SHORT reversal.
    return [
        _bar("2026-09-18T13:30:00", 7, 10, 5, 8),
        _bar("2026-09-18T14:00:00", 8, 9, 6, 8.5),
        _bar("2026-09-18T14:30:00", 8.5, 11, 6.5, 10.5),
    ]


def _session():
    s = nyse_session_for(date(2026, 9, 18))
    assert s is not None
    return s


def _row(lower, decision):
    rows = observe_122_setups(
        ticker="SPY", history_30m=_history_122(), session_5m=lower,
        session=_session(), decision_ts=decision,
    )
    return [x for x in rows if x.watch_start == "2026-09-18T15:00:00+00:00"][0]


def test_122_reversal_is_detected_but_strategy_geometry_stays_unresolved():
    row = _row([
        _bar("2026-09-18T15:00:00", 10.4, 10.8, 7.0, 8.0),
        _bar("2026-09-18T15:05:00", 8.0, 8.5, 6.4, 6.8),
    ], datetime(2026, 9, 18, 15, 11, tzinfo=UTC))
    assert row.status == "TRIGGERED"
    assert row.family == "OTHER:strat_122"
    assert row.direction == "SHORT"
    assert row.trigger_level == 6.5
    assert row.structural_opposite_boundary == 11.0
    assert row.strategy_stop is None
    assert row.strategy_target is None
    assert row.strategy_geometry_status == "UNRESOLVED"
    assert row.trigger_bar_start == "2026-09-18T15:05:00+00:00"


def test_same_direction_first_break_cancels_122_reversal():
    row = _row([
        _bar("2026-09-18T15:00:00", 10.5, 11.1, 7.0, 11.05),
    ], datetime(2026, 9, 18, 15, 6, tzinfo=UTC))
    assert row.status == "CANCELLED"
    assert row.family is None
    assert row.reason_code == "same_direction_break_precludes_122_reversal"


def test_no_break_watching_then_expired():
    early = _row([_bar("2026-09-18T15:00:00", 9, 10, 7, 8)], datetime(2026, 9, 18, 15, 6, tzinfo=UTC))
    assert early.status == "WATCHING"
    late = _row([
        _bar(f"2026-09-18T15:{minute:02d}:00", 9, 10, 7, 8)
        for minute in (0, 5, 10, 15, 20, 25)
    ], datetime(2026, 9, 18, 15, 31, tzinfo=UTC))
    assert late.status == "EXPIRED"


def test_missing_completed_5m_bar_fails_closed():
    row = _row([_bar("2026-09-18T15:05:00", 8, 8.5, 6.4, 6.8)], datetime(2026, 9, 18, 15, 11, tzinfo=UTC))
    assert row.status == "DATA_BLOCKED"
    assert row.reason_code.startswith("missing_5m_bars:")


def test_capture_gate_uses_exact_iex_cross_and_prearm():
    obs = _row([
        _bar("2026-09-18T15:00:00", 10.4, 10.8, 7.0, 8.0),
        _bar("2026-09-18T15:05:00", 8.0, 8.5, 6.4, 6.8),
    ], datetime(2026, 9, 18, 15, 11, tzinfo=UTC))
    crossed = datetime(2026, 9, 18, 15, 6, 30, tzinfo=UTC)
    good = evaluate_capture_gate(
        obs, prearmed_at=datetime(2026, 9, 18, 15, 1, tzinfo=UTC),
        decision_ts=datetime(2026, 9, 18, 15, 7, 0, tzinfo=UTC),
        max_capture_lag_seconds=120, trigger_crossed_at=crossed,
    )
    assert good.eligible is True and good.lag_seconds == 30
    late_arm = evaluate_capture_gate(
        obs, prearmed_at=datetime(2026, 9, 18, 15, 7, tzinfo=UTC),
        decision_ts=datetime(2026, 9, 18, 15, 7, 10, tzinfo=UTC),
        max_capture_lag_seconds=120, trigger_crossed_at=crossed,
    )
    assert late_arm.reason_code == "no_proven_pretrigger_arm"
    late_capture = evaluate_capture_gate(
        obs, prearmed_at=datetime(2026, 9, 18, 15, 1, tzinfo=UTC),
        decision_ts=datetime(2026, 9, 18, 15, 8, 31, tzinfo=UTC),
        max_capture_lag_seconds=120, trigger_crossed_at=crossed,
    )
    assert late_capture.reason_code == "decision_time_capture_late"


def test_capture_gate_rejects_cross_outside_trigger_bucket():
    obs = _row([
        _bar("2026-09-18T15:00:00", 10.4, 10.8, 7.0, 8.0),
        _bar("2026-09-18T15:05:00", 8.0, 8.5, 6.4, 6.8),
    ], datetime(2026, 9, 18, 15, 11, tzinfo=UTC))
    result = evaluate_capture_gate(
        obs, prearmed_at=datetime(2026, 9, 18, 15, 1, tzinfo=UTC),
        decision_ts=datetime(2026, 9, 18, 15, 7, tzinfo=UTC),
        max_capture_lag_seconds=120,
        trigger_crossed_at=datetime(2026, 9, 18, 15, 10, tzinfo=UTC),
    )
    assert result.reason_code == "trigger_cross_outside_proven_bucket"
