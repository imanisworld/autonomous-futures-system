"""Synthetic-only QA for the forward 212 floor-outcome machinery."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from alert_ranker.causal_bars import Bar
from alert_ranker.coverage_episodes import Episode, REDUCER_VERSION
from alert_ranker.coverage_observer import OBSERVER_VERSION
from alert_ranker.session_calendar import nyse_session_for
from ops.options_212c_floor_outcome_monitor import (
    EPISODE_SNAPSHOT_FIELDS,
    PATH_RECORD_VERSION,
    PRE_ENTRY_FACTOR_FIELDS,
    TRIAL_ID,
    bind_seal,
    study_readout,
)
from ops.options_212c_floor_outcome_study import (
    ALIGNED,
    AMBIGUOUS,
    DATA_INVALID,
    MISSING,
    NOT_ALIGNED,
    POPULATION_ACTIVATED,
    POPULATION_FLOOR_ELIGIBLE,
    PRIMARY_FACTORS,
    REQUIRED_METRICS,
    STOP,
    TARGET,
    TIMEOUT,
    StudyContractError,
    aggregate_factor_metrics,
    aggregate_metrics,
    build_session_artifact,
    expected_starts,
    factor_labels,
    score_session_record,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/research-experiment-specs/E-2026-10-04-options-212c-floor-outcome-02.json"
# Synthetic session for scorer tests only; the real window never includes it.
DAY = "2026-10-05"


def _episode(**overrides) -> Episode:
    values = dict(
        reducer_version=REDUCER_VERSION,
        symbol="AAPL",
        session_date=DAY,
        family="STRAT_212_CONTINUATION",
        direction="LONG",
        v1_supported=True,
        requested_family=True,
        n_events=1,
        first_bar_start=f"{DAY}T13:30:00+00:00",
        first_bar_close=f"{DAY}T14:00:00+00:00",
        last_bar_start=f"{DAY}T13:30:00+00:00",
        entry_trigger=100.0,
        invalidation=99.0,
        risk=1.0,
        nearest_target_1=101.0,
        nearest_rr_1=1.0,
        nearest_reason="",
        nearest_geometry_ok=True,
        floor_target_1=102.0,
        floor_rr_1=2.0,
        floor_reason="",
        floor_geometry_ok=True,
        floor_rescued=False,
        spy_trend="bullish",
        qqq_trend="bullish",
        hourly_candle_type="two_up",
        daily_candle_type="two_up",
        spy_aligned=True,
        qqq_aligned=True,
        hourly_aligned=True,
        daily_aligned=True,
        alignment_ok=True,
        alignment_failures="",
        first_sight_at=f"{DAY}T14:17:57+00:00",
        first_sight_after_close=False,
        first_sight_price=100.25,
        nearest_remaining_rr=0.75,
        floor_remaining_rr=1.75,
        late_nearest=True,
        late_floor=False,
        would_qualify_v1_rule=False,
        would_qualify_floor_rule=True,
        rejections_v1_rule=["late_at_first_sight"],
        rejections_floor_rule=[],
        rr_quality_flags=[],
        run_id="AAPL|2026-10-05|LONG|run1",
    )
    values.update(overrides)
    return Episode(**values)


def _event(ep: Episode, **overrides) -> dict:
    row = {
        "observer_version": OBSERVER_VERSION,
        "timeframe": "30m",
        "symbol": ep.symbol,
        "session_date": ep.session_date,
        "direction": ep.direction,
        "family": ep.family,
        "bar_start": ep.first_bar_start,
        "bar_close": ep.first_bar_close,
        "v1_supported": ep.v1_supported,
        "requested_family": ep.requested_family,
        "entry_trigger": ep.entry_trigger,
        "invalidation": ep.invalidation,
        "risk": ep.risk,
        "nearest_target_1": ep.nearest_target_1,
        "nearest_target_2": None,
        "nearest_rr_1": ep.nearest_rr_1,
        "nearest_reason": ep.nearest_reason,
        "nearest_geometry_ok": ep.nearest_geometry_ok,
        "floor_target_1": ep.floor_target_1,
        "floor_target_2": 103.0,
        "floor_rr_1": ep.floor_rr_1,
        "floor_reason": ep.floor_reason,
        "floor_geometry_ok": ep.floor_geometry_ok,
        "floor_rescued": ep.floor_rescued,
        "spy_trend": ep.spy_trend,
        "qqq_trend": ep.qqq_trend,
        "hourly_candle_type": ep.hourly_candle_type,
        "daily_candle_type": ep.daily_candle_type,
        "alignment_ok": ep.alignment_ok,
        "alignment_failures": ep.alignment_failures,
        "first_sight_at": ep.first_sight_at,
        "first_sight_after_close": ep.first_sight_after_close,
        "first_sight_price": ep.first_sight_price,
        "nearest_remaining_rr": ep.nearest_remaining_rr,
        "floor_remaining_rr": ep.floor_remaining_rr,
        "late_nearest": ep.late_nearest,
        "late_floor": ep.late_floor,
        "would_qualify_v1_rule": ep.would_qualify_v1_rule,
        "would_qualify_floor_rule": ep.would_qualify_floor_rule,
    }
    row.update(overrides)
    return row


def _bars(
    *,
    first_open=100.25,
    first_high=100.5,
    first_low=100.0,
    first_close=100.3,
    last_close=100.5,
) -> list[Bar]:
    cursor = datetime(2026, 10, 5, 14, 20, tzinfo=timezone.utc)
    close = datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc)
    out: list[Bar] = []
    i = 0
    while cursor + timedelta(minutes=5) <= close:
        if i == 0:
            o, h, l, c = first_open, first_high, first_low, first_close
        else:
            o, h, l, c = 100.3, 100.6, 100.0, 100.4
        if cursor + timedelta(minutes=5) == close:
            c = last_close
            h, l = max(h, c), min(l, c)
        out.append(
            Bar(
                start=cursor,
                open=o,
                high=h,
                low=l,
                close=c,
                volume=1000,
            )
        )
        cursor += timedelta(minutes=5)
        i += 1
    return out


def _source() -> dict:
    return {
        "provider": "synthetic",
        "request_start": f"{DAY}T13:30:00+00:00",
        "request_end": f"{DAY}T20:00:00+00:00",
        "observer_run_id": 123,
        "observer_ran_at": f"{DAY}T20:30:00+00:00",
        "source_sha": "a" * 40,
    }


def _artifact(ep: Episode | None = None, bars: list[Bar] | None = None):
    session = nyse_session_for(datetime.fromisoformat(DAY).date())
    assert session is not None
    ep = ep or _episode()
    return build_session_artifact(
        session,
        [ep],
        [_event(ep)],
        {ep.symbol: bars or _bars()},
        source=_source(),
        captured_at=datetime(2026, 10, 5, 20, 31, tzinfo=timezone.utc),
    )


def _record_with_first_bar(**values) -> dict:
    artifact = _artifact()
    record = json.loads(artifact.body)
    record["episodes"][0]["bars"][0].update(values)
    return record


def test_capture_seals_target2_grid_and_manifest_without_outcome_fields() -> None:
    artifact = _artifact()
    snap = artifact.record["episodes"][0]
    assert snap["floor_target_2"] == 103.0
    assert snap["gate_bucket_floor"] == "WOULD_OTHERWISE_QUALIFY"
    assert snap["bars"][0]["start"] == f"{DAY}T14:20:00+00:00"
    assert snap["bars"][-1]["start"] == f"{DAY}T19:55:00+00:00"
    assert artifact.sha256 == artifact.manifest["sha256"]
    assert artifact.manifest["byte_length"] == len(artifact.body)
    assert artifact.body.endswith(b"\n")
    assert b'"outcome"' not in artifact.body
    assert b'"realized_r"' not in artifact.body


def test_capture_fails_closed_on_episode_drift_and_population_omission() -> None:
    session = nyse_session_for(datetime.fromisoformat(DAY).date())
    assert session is not None
    ep = _episode()
    with pytest.raises(StudyContractError, match="episode_population_drift"):
        build_session_artifact(
            session,
            [ep],
            [_event(ep, floor_target_1=999.0)],
            {ep.symbol: _bars()},
            source=_source(),
            captured_at=datetime(2026, 10, 5, 20, 31, tzinfo=timezone.utc),
        )

    with pytest.raises(StudyContractError, match="episode_population_mismatch"):
        build_session_artifact(
            session,
            [],
            [_event(ep)],
            {ep.symbol: _bars()},
            source=_source(),
            captured_at=datetime(2026, 10, 5, 20, 31, tzinfo=timezone.utc),
        )


    with pytest.raises(StudyContractError, match="event_version_mismatch"):
        build_session_artifact(
            session,
            [ep],
            [_event(ep, observer_version="cov-v9.9")],
            {ep.symbol: _bars()},
            source=_source(),
            captured_at=datetime(2026, 10, 5, 20, 31, tzinfo=timezone.utc),
        )


def test_missing_bar_is_sealed_and_scores_data_invalid_without_refetch() -> None:
    ep = _episode()
    missing = _bars()
    del missing[3]
    artifact = _artifact(ep=ep, bars=missing)
    assert len(artifact.record["episodes"][0]["bars"]) == len(_bars()) - 1
    scored = score_session_record(artifact.record)["rows"][0]
    assert scored["outcome"] == DATA_INVALID
    assert scored["realized_r"] is None
    assert "bar_grid_length" in scored["flags"]


@pytest.mark.parametrize(
    ("first_bar", "expected", "expected_r", "flag"),
    [
        (
            {"open": 98.5, "high": 99.5, "low": 98.0, "close": 99.0},
            STOP,
            -1.75,
            "gap_through_stop",
        ),
        (
            {"open": 102.5, "high": 103.2, "low": 101.5, "close": 102.2},
            TARGET,
            1.75,
            "gap_through_target_1",
        ),
        (
            {"open": 100.25, "high": 102.2, "low": 98.8, "close": 100.5},
            AMBIGUOUS,
            None,
            "gap_or_range_spans_stop_and_target",
        ),
    ],
)
def test_gap_and_same_bar_rules_are_frozen(
    first_bar: dict,
    expected: str,
    expected_r: float | None,
    flag: str,
) -> None:
    scored = score_session_record(_record_with_first_bar(**first_bar))["rows"][0]
    assert scored["outcome"] == expected
    assert scored["realized_r"] == expected_r
    assert flag in scored["flags"]
    assert scored["mae_r"] == 0.0
    assert scored["mfe_r"] == 0.0
    if flag == "gap_through_target_1":
        assert scored["target_2_reached"] is True
        assert scored["target_2_hit_at"] == f"{DAY}T14:20:00+00:00"


def test_touch_timeout_and_data_invalid_paths() -> None:
    target = score_session_record(
        _record_with_first_bar(
            open=100.25, high=102.1, low=100.0, close=101.8
        )
    )["rows"][0]
    assert target["outcome"] == TARGET
    assert target["realized_r"] == 1.75

    stop = score_session_record(
        _record_with_first_bar(
            open=100.25, high=100.5, low=98.9, close=99.2
        )
    )["rows"][0]
    assert stop["outcome"] == STOP
    assert stop["realized_r"] == -1.25

    timeout_artifact = _artifact(bars=_bars(last_close=100.5))
    timeout = score_session_record(timeout_artifact.record)["rows"][0]
    assert timeout["outcome"] == TIMEOUT
    assert timeout["realized_r"] == 0.25
    assert timeout["clock_bucket"] == "10:00 ET"

    bad = json.loads(timeout_artifact.body)
    bad["episodes"][0]["structural_risk"] = 2.0
    invalid = score_session_record(bad)["rows"][0]
    assert invalid["outcome"] == DATA_INVALID
    assert invalid["realized_r"] is None

    bad_identity = json.loads(timeout_artifact.body)
    bad_identity["episodes"][0]["episode_id"] = "tampered"
    with pytest.raises(StudyContractError, match="snapshot_identity_invalid"):
        score_session_record(bad_identity)


def test_session_score_rejects_malformed_nonactivated_population_row() -> None:
    record = json.loads(_artifact().body)
    snap = dict(record["episodes"][0])
    snap["gate_bucket_floor"] = "MARKET_ALIGNMENT_REJECTED"
    snap["episode_id"] = "tampered"
    record["episodes"] = [snap]
    with pytest.raises(StudyContractError, match="snapshot_identity_invalid"):
        score_session_record(record)


def test_aggregate_emits_all_preregistered_metrics() -> None:
    rows = [
        {
            "episode_id": "a",
            "ticker": "AAPL",
            "session_date": DAY,
            "gate_bucket_floor": "WOULD_OTHERWISE_QUALIFY",
            "clock_bucket": "10:00 ET",
            "outcome": TARGET,
            "realized_r": 1.5,
            "mae_r": -0.2,
            "mfe_r": 0.8,
            "flags": [],
        },
        {
            "episode_id": "b",
            "ticker": "MSFT",
            "session_date": DAY,
            "gate_bucket_floor": "WOULD_OTHERWISE_QUALIFY",
            "clock_bucket": "11:00 ET",
            "outcome": STOP,
            "realized_r": -1.0,
            "mae_r": -0.4,
            "mfe_r": 0.3,
            "flags": [],
        },
        {
            "episode_id": "c",
            "ticker": "AAPL",
            "session_date": DAY,
            "gate_bucket_floor": "WOULD_OTHERWISE_QUALIFY",
            "clock_bucket": "12:00 ET",
            "outcome": TIMEOUT,
            "realized_r": 0.25,
            "mae_r": -0.1,
            "mfe_r": 0.4,
            "flags": [],
        },
    ]
    metrics = aggregate_metrics([{"population_size": 5, "rows": rows}])
    assert set(REQUIRED_METRICS).issubset(metrics)
    assert metrics["activation_count"]["count"] == 3
    assert metrics["completed_trades"]["count"] == 3
    assert metrics["wins"]["count"] == 1
    assert metrics["losses"]["count"] == 1
    assert metrics["timeouts"]["count"] == 1
    assert metrics["expectancy"]["value"] == 0.25


def test_machinery_has_no_real_data_io_or_runner_hook() -> None:
    source = (ROOT / "ops/options_212c_floor_outcome_study.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "AlpacaBarProvider",
        "httpx",
        "requests",
        "subprocess",
        "Path(",
        ".open(",
        "write_text(",
        "write_bytes(",
        "EXECUTION_ADAPTERS",
    ):
        assert forbidden not in source


def test_spec_stays_draft_and_metric_contract_matches() -> None:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    assert spec["status"] == "DRAFT"
    assert spec["acceptance_criteria"] is None
    assert spec["rejection_criteria"] is None
    assert tuple(spec["required_metrics"]) == REQUIRED_METRICS
    assert spec["trial_id"] == TRIAL_ID
    assert spec["supersedes"] == "E-2026-10-02-options-212c-floor-outcome-01"


# --------------------------------------------------------------------------- #
# path-v0.2 seal: pre-entry factors, request_end and off-grid refusals
# --------------------------------------------------------------------------- #


def test_seal_carries_raw_pre_entry_factors_and_no_derived_label() -> None:
    artifact = _artifact()
    snap = artifact.record["episodes"][0]
    assert artifact.record["path_record_version"] == PATH_RECORD_VERSION == "options_212c_floor_outcome_path-v0.2"
    assert set(snap) == set(EPISODE_SNAPSHOT_FIELDS)
    assert snap["spy_trend"] == "bullish" and snap["hourly_candle_type"] == "two_up"
    assert snap["floor_remaining_rr"] == 1.75 and snap["late_floor"] is False
    for derived in (b"ALIGNED", b"NOT_ALIGNED", b"MISSING", b"remaining_r_bucket", b"spy_alignment"):
        assert derived not in artifact.body


def test_seal_refuses_factor_drift_against_the_first_event() -> None:
    session = nyse_session_for(datetime.fromisoformat(DAY).date())
    ep = _episode()
    for patch in ({"spy_trend": "bearish"}, {"hourly_candle_type": None}, {"floor_remaining_rr": 1.0}, {"alignment_failures": "spy"}):
        with pytest.raises(StudyContractError, match="episode_population_drift"):
            build_session_artifact(session, [ep], [_event(ep, **patch)], {ep.symbol: _bars()}, source=_source(), captured_at=datetime(2026, 10, 5, 20, 31, tzinfo=timezone.utc))


def test_seal_refuses_request_end_before_close() -> None:
    session = nyse_session_for(datetime.fromisoformat(DAY).date())
    ep = _episode()
    src = _source()
    src["request_end"] = f"{DAY}T19:55:00+00:00"
    with pytest.raises(StudyContractError, match="request_end_before_close"):
        build_session_artifact(session, [ep], [_event(ep)], {ep.symbol: _bars()[:3]}, source=src, captured_at=datetime(2026, 10, 5, 20, 31, tzinfo=timezone.utc))
    # a late request_end is fine; only a short one is a capture failure
    src["request_end"] = f"{DAY}T20:05:00+00:00"
    assert build_session_artifact(session, [ep], [_event(ep)], {ep.symbol: _bars()}, source=src, captured_at=datetime(2026, 10, 5, 20, 31, tzinfo=timezone.utc)).sha256


@pytest.mark.parametrize(
    ("start", "reason"),
    [
        (datetime(2026, 10, 5, 14, 22, tzinfo=timezone.utc), "bar_outside_session_grid"),  # off the 5-minute grid
        (datetime(2026, 10, 5, 13, 25, tzinfo=timezone.utc), "bar_outside_session_grid"),  # before the open
        (datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc), "bar_outside_session_grid"),  # ends after the close
        (datetime(2026, 10, 5, 14, 20, tzinfo=timezone.utc), "duplicate_bar"),
    ],
)
def test_seal_refuses_unexpected_bars_instead_of_dropping_them(start: datetime, reason: str) -> None:
    session = nyse_session_for(datetime.fromisoformat(DAY).date())
    ep = _episode()
    bars = _bars() + [Bar(start=start, open=100.3, high=100.6, low=100.0, close=100.4, volume=1)]
    with pytest.raises(StudyContractError, match=reason):
        build_session_artifact(session, [ep], [_event(ep)], {ep.symbol: bars}, source=_source(), captured_at=datetime(2026, 10, 5, 20, 31, tzinfo=timezone.utc))
    # on-grid bars before first sight are legitimate inputs and are simply not sealed
    early = [Bar(start=datetime(2026, 10, 5, 13, 30, tzinfo=timezone.utc), open=100.3, high=100.6, low=100.0, close=100.4, volume=1)] + _bars()
    artifact = build_session_artifact(session, [ep], [_event(ep)], {ep.symbol: early}, source=_source(), captured_at=datetime(2026, 10, 5, 20, 31, tzinfo=timezone.utc))
    assert artifact.record["episodes"][0]["bars"][0]["start"] == f"{DAY}T14:20:00+00:00"


# --------------------------------------------------------------------------- #
# SHORT direction, early close, seal -> monitor
# --------------------------------------------------------------------------- #


def _short_episode() -> Episode:
    return _episode(
        direction="SHORT", entry_trigger=100.0, invalidation=101.0, risk=1.0,
        floor_target_1=98.0, floor_rr_1=2.0, nearest_target_1=99.0,
        first_sight_price=99.75, floor_remaining_rr=1.75, nearest_remaining_rr=0.75,
        spy_trend="bearish", qqq_trend="bearish", hourly_candle_type="two_down", daily_candle_type="two_down",
        run_id=f"AAPL|{DAY}|SHORT|run1",
    )


def _short_bars(first: tuple[float, float, float, float]) -> list[Bar]:
    out: list[Bar] = []
    cursor = datetime(2026, 10, 5, 14, 20, tzinfo=timezone.utc)
    close = datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc)
    i = 0
    while cursor + timedelta(minutes=5) <= close:
        o, h, l, c = first if i == 0 else (99.7, 100.0, 99.4, 99.6)
        out.append(Bar(start=cursor, open=o, high=h, low=l, close=c, volume=1))
        cursor += timedelta(minutes=5)
        i += 1
    return out


@pytest.mark.parametrize(
    ("first", "expected", "expected_r", "flag"),
    [
        ((101.5, 102.0, 101.2, 101.6), STOP, -1.75, "gap_through_stop"),
        ((97.5, 98.2, 96.8, 97.6), TARGET, 1.75, "gap_through_target_1"),
        ((99.75, 101.1, 97.9, 99.5), AMBIGUOUS, None, "gap_or_range_spans_stop_and_target"),
        ((99.75, 101.0, 99.5, 100.5), STOP, -1.25, None),
        ((99.75, 99.9, 98.0, 98.5), TARGET, 1.75, None),
        ((99.7, 100.0, 99.4, 99.6), TIMEOUT, 0.15, None),
    ],
)
def test_short_direction_rules_mirror_long(first, expected, expected_r, flag) -> None:
    session = nyse_session_for(datetime.fromisoformat(DAY).date())
    ep = _short_episode()
    artifact = build_session_artifact(session, [ep], [_event(ep, floor_target_2=97.0)], {ep.symbol: _short_bars(first)}, source=_source(), captured_at=datetime(2026, 10, 5, 20, 31, tzinfo=timezone.utc))
    row = score_session_record(artifact.record)["rows"][0]
    assert row["direction"] == "SHORT"
    assert row["outcome"] == expected
    assert row["realized_r"] == expected_r
    if flag:
        assert flag in row["flags"]
    if flag == "gap_through_target_1":
        assert row["target_2_reached"] is True
    if expected == TIMEOUT:
        assert row["mae_r"] == -0.25 and row["mfe_r"] == 0.35
    assert row["factors"] == {f: ALIGNED for f in PRIMARY_FACTORS if f != "remaining_r_bucket"} | {"remaining_r_bucket": "ge1p5_lt2"}


def test_early_close_session_grid_ends_at_the_early_close() -> None:
    early = nyse_session_for(datetime(2026, 11, 27).date())  # day after Thanksgiving, 13:00 ET
    assert early is not None and early.is_early_close
    grid = expected_starts(early, "2026-11-27T14:17:57+00:00")
    assert grid[0].isoformat() == "2026-11-27T14:30:00+00:00"
    assert grid[-1] + timedelta(minutes=5) == early.close.astimezone(timezone.utc)
    assert len(grid) == 42
    # a full-session feed for the early-close day seals and times out at the early close
    ep = _episode(session_date="2026-11-27", first_bar_start="2026-11-27T14:30:00+00:00", first_bar_close="2026-11-27T15:00:00+00:00", last_bar_start="2026-11-27T14:30:00+00:00", first_sight_at="2026-11-27T15:17:57+00:00", run_id="AAPL|2026-11-27|LONG|run1")
    bars = []
    cursor = early.open.astimezone(timezone.utc)
    while cursor + timedelta(minutes=5) <= early.close.astimezone(timezone.utc):
        bars.append(Bar(start=cursor, open=100.3, high=100.6, low=100.0, close=100.4, volume=1))
        cursor += timedelta(minutes=5)
    src = _source()
    src.update(request_start="2026-11-27T14:30:00+00:00", request_end="2026-11-27T18:00:00+00:00", observer_ran_at="2026-11-27T18:30:00+00:00")
    artifact = build_session_artifact(early, [ep], [_event(ep)], {ep.symbol: bars}, source=src, captured_at=datetime(2026, 11, 27, 18, 31, tzinfo=timezone.utc))
    sealed = artifact.record["episodes"][0]["bars"]
    assert sealed[0]["start"] == "2026-11-27T15:20:00+00:00" and sealed[-1]["start"] == "2026-11-27T17:55:00+00:00"
    row = score_session_record(artifact.record)["rows"][0]
    assert row["outcome"] == TIMEOUT and row["realized_r"] == 0.15


def test_seal_feeds_monitor_and_monitor_refuses_without_a_registered_start() -> None:
    start = "2026-10-06"  # first session after the ineligible span; synthetic only
    session = nyse_session_for(datetime.fromisoformat(start).date())
    ep = _episode(session_date=start, first_bar_start=f"{start}T13:30:00+00:00", first_bar_close=f"{start}T14:00:00+00:00", last_bar_start=f"{start}T13:30:00+00:00", first_sight_at=f"{start}T14:17:57+00:00", run_id=f"AAPL|{start}|LONG|run1")
    bars = [Bar(start=b.start + timedelta(days=1), open=b.open, high=b.high, low=b.low, close=b.close, volume=1) for b in _bars()]
    src = _source()
    src.update(request_start=f"{start}T13:30:00+00:00", request_end=f"{start}T20:00:00+00:00", observer_ran_at=f"{start}T20:30:00+00:00")
    artifact = build_session_artifact(session, [ep], [_event(ep)], {ep.symbol: bars}, source=src, captured_at=datetime(2026, 10, 6, 20, 31, tzinfo=timezone.utc))
    seal = bind_seal(json.loads(artifact.body))
    assert seal["sha256"] == artifact.sha256 == artifact.manifest["sha256"]
    assert study_readout([seal], eligible_start=start) == {"sessions_elapsed": 1, "stop_condition_met": False, "stop_condition": None, "advance_refused": False}
    # no registered start → refuse; a start inside the ineligible span → refuse
    assert study_readout([seal])["advance_refused"] is True
    assert study_readout([seal], eligible_start="2026-10-05")["advance_refused"] is True
    assert study_readout([seal], eligible_start="2026-10-04")["advance_refused"] is True  # not a session
    tampered = json.loads(artifact.body)
    tampered["episodes"][0]["bars"][0]["close"] = 999.0
    assert study_readout([{"sha256": artifact.sha256, "record": tampered}], eligible_start=start)["sessions_elapsed"] == 0


# --------------------------------------------------------------------------- #
# companion: floor-eligible population, score-time factor labels
# --------------------------------------------------------------------------- #


def test_factor_labels_are_derived_at_score_time_with_explicit_missing() -> None:
    snap = {"direction": "LONG", "spy_trend": "bullish", "qqq_trend": "neutral", "hourly_candle_type": None, "daily_candle_type": "two_up", "floor_remaining_rr": 2.4, "late_floor": False}
    assert factor_labels(snap) == {"spy_alignment": ALIGNED, "qqq_alignment": NOT_ALIGNED, "hourly_alignment": MISSING, "daily_alignment": ALIGNED, "remaining_r_bucket": "ge2"}
    snap.update(direction="SHORT", spy_trend="bearish", floor_remaining_rr=1.2)
    assert factor_labels(snap)["spy_alignment"] == ALIGNED and factor_labels(snap)["daily_alignment"] == NOT_ALIGNED
    assert factor_labels(snap)["remaining_r_bucket"] == "ge1_lt1p5"
    assert factor_labels({**snap, "late_floor": True})["remaining_r_bucket"] == "LATE"
    assert factor_labels({**snap, "floor_remaining_rr": None, "late_floor": None})["remaining_r_bucket"] == MISSING


def test_companion_scores_alignment_rejected_but_not_late_and_aggregates_by_stratum() -> None:
    session = nyse_session_for(datetime.fromisoformat(DAY).date())
    activated = _episode()
    rejected = _episode(symbol="MSFT", spy_trend="bearish", spy_aligned=False, alignment_ok=False, alignment_failures="spy", would_qualify_floor_rule=False, rejections_floor_rule=["market_alignment:spy"], run_id=f"MSFT|{DAY}|LONG|run1")
    rejected_late = _episode(symbol="NVDA", spy_trend="bearish", spy_aligned=False, alignment_ok=False, alignment_failures="spy", late_floor=True, floor_remaining_rr=0.5, would_qualify_floor_rule=False, run_id=f"NVDA|{DAY}|LONG|run1")
    geometry_rejected = _episode(symbol="AMZN", floor_geometry_ok=False, floor_target_1=None, floor_rr_1=None, floor_remaining_rr=None, late_floor=None, would_qualify_floor_rule=False, run_id=f"AMZN|{DAY}|LONG|run1")
    eps = [activated, rejected, rejected_late, geometry_rejected]
    events = [_event(activated), _event(rejected), _event(rejected_late), _event(geometry_rejected, floor_target_2=None)]
    artifact = build_session_artifact(session, eps, events, {e.symbol: _bars(first_high=102.1) for e in eps}, source=_source(), captured_at=datetime(2026, 10, 5, 20, 31, tzinfo=timezone.utc))
    gates = {s["symbol"]: s["gate_bucket_floor"] for s in artifact.record["episodes"]}
    assert gates == {"AAPL": "WOULD_OTHERWISE_QUALIFY", "MSFT": "MARKET_ALIGNMENT_REJECTED", "NVDA": "MARKET_ALIGNMENT_REJECTED", "AMZN": "TARGET_GEOMETRY_REJECTED"}

    primary = score_session_record(artifact.record)
    assert primary["population"] == POPULATION_ACTIVATED
    assert primary["population_size"] == 4
    assert [r["ticker"] for r in primary["rows"]] == ["AAPL"]
    primary_metrics = aggregate_metrics([primary])
    assert primary_metrics["activation_count"]["count"] == 1
    assert primary_metrics["activation_count"]["of"] == 4

    companion = score_session_record(artifact.record, population=POPULATION_FLOOR_ELIGIBLE)
    assert companion["population_size"] == 2
    assert sorted(r["ticker"] for r in companion["rows"]) == ["AAPL", "MSFT"]  # NVDA is late; AMZN has no floor geometry
    by_ticker = {r["ticker"]: r for r in companion["rows"]}
    assert by_ticker["AAPL"]["factors"]["spy_alignment"] == ALIGNED
    assert by_ticker["MSFT"]["factors"]["spy_alignment"] == NOT_ALIGNED
    assert by_ticker["MSFT"]["outcome"] == TARGET
    assert by_ticker["AAPL"]["gate_bucket_floor"] == "WOULD_OTHERWISE_QUALIFY"
    assert by_ticker["MSFT"]["gate_bucket_floor"] == "MARKET_ALIGNMENT_REJECTED"

    table = aggregate_factor_metrics([companion])
    assert table["population"] == POPULATION_FLOOR_ELIGIBLE
    assert table["overall"]["population_size"]["count"] == 2
    assert table["overall"]["setups_evaluated"]["count"] == 2
    assert table["overall"]["activation_count"] == {"count": 1, "rate": 0.5, "of": 2}
    assert table["overall"]["wins"]["count"] == 2
    assert set(table["by_factor"]) == set(PRIMARY_FACTORS)
    assert table["by_factor"]["spy_alignment"][ALIGNED]["count"] == 1
    assert table["by_factor"]["spy_alignment"][NOT_ALIGNED]["count"] == 1
    assert table["by_factor"]["spy_alignment"][MISSING]["count"] == 0
    # cells below the preregistered minimum report counts only
    assert table["by_factor"]["spy_alignment"][ALIGNED]["suppressed"] is True
    assert table["by_factor"]["spy_alignment"][ALIGNED]["metrics"] is None
    assert table["by_factor"]["spy_alignment"][ALIGNED]["activation_count"] == 1
    assert table["by_factor"]["spy_alignment"][NOT_ALIGNED]["activation_count"] == 0
    assert table["by_gate_bucket_floor"]["MARKET_ALIGNMENT_REJECTED"]["count"] == 1
    assert table["by_gate_bucket_floor"]["MARKET_ALIGNMENT_REJECTED"]["activation_count"] == 0
    assert table["by_gate_bucket_floor"]["WOULD_OTHERWISE_QUALIFY"]["activation_count"] == 1
    assert table["by_direction"]["LONG"]["count"] == 2 and table["by_direction"]["SHORT"]["count"] == 0
    assert table["by_direction"]["LONG"]["activation_count"] == 1
    assert set(REQUIRED_METRICS).issubset(table["overall"])
    with pytest.raises(StudyContractError, match="population_invalid"):
        aggregate_factor_metrics([primary])


def test_companion_cell_releases_metrics_at_the_minimum() -> None:
    rows = [
        {"episode_id": str(i), "ticker": "AAPL", "session_date": DAY, "direction": "LONG", "gate_bucket_floor": "WOULD_OTHERWISE_QUALIFY", "clock_bucket": "10:00 ET", "outcome": TARGET if i % 2 else STOP, "realized_r": 1.5 if i % 2 else -1.0, "mae_r": -0.2, "mfe_r": 0.8, "factors": {f: ALIGNED for f in PRIMARY_FACTORS[:-1]} | {"remaining_r_bucket": "ge2"}, "flags": []}
        for i in range(5)
    ]
    table = aggregate_factor_metrics([{"population": POPULATION_FLOOR_ELIGIBLE, "population_size": 5, "rows": rows}])
    cell = table["by_factor"]["remaining_r_bucket"]["ge2"]
    assert cell["completed"] == 5 and cell["suppressed"] is False
    assert cell["activation_count"] == 5
    assert cell["metrics"]["wins"]["count"] == 2
    assert cell["metrics"]["activation_count"]["count"] == 5
    assert table["by_factor"]["remaining_r_bucket"]["ge1_lt1p5"] == {
        "count": 0,
        "completed": 0,
        "activation_count": 0,
        "metrics": None,
        "suppressed": True,
    }


def test_companion_alignment_rejected_cells_have_zero_activations() -> None:
    rejected = [
        {
            "episode_id": str(i),
            "ticker": "MSFT",
            "session_date": DAY,
            "direction": "LONG",
            "gate_bucket_floor": "MARKET_ALIGNMENT_REJECTED",
            "clock_bucket": "10:00 ET",
            "outcome": TARGET if i % 2 else STOP,
            "realized_r": 1.5 if i % 2 else -1.0,
            "mae_r": -0.2,
            "mfe_r": 0.8,
            "factors": {f: NOT_ALIGNED for f in PRIMARY_FACTORS[:-1]} | {"remaining_r_bucket": "ge2"},
            "flags": [],
        }
        for i in range(5)
    ]
    qualify = [
        {
            "episode_id": f"q{i}",
            "ticker": "AAPL",
            "session_date": DAY,
            "direction": "SHORT",
            "gate_bucket_floor": "WOULD_OTHERWISE_QUALIFY",
            "clock_bucket": "10:00 ET",
            "outcome": TARGET,
            "realized_r": 1.0,
            "mae_r": -0.1,
            "mfe_r": 0.5,
            "factors": {f: ALIGNED for f in PRIMARY_FACTORS[:-1]} | {"remaining_r_bucket": "ge2"},
            "flags": [],
        }
        for i in range(5)
    ]
    table = aggregate_factor_metrics(
        [{"population": POPULATION_FLOOR_ELIGIBLE, "population_size": 10, "rows": rejected + qualify}]
    )
    overall = table["overall"]
    assert overall["population_size"]["count"] == 10
    assert overall["setups_evaluated"]["count"] == 10
    assert overall["activation_count"] == {"count": 5, "rate": 0.5, "of": 10}
    assert overall["wins"]["count"] == 7
    assert overall["losses"]["count"] == 3

    rejected_cell = table["by_gate_bucket_floor"]["MARKET_ALIGNMENT_REJECTED"]
    assert rejected_cell["count"] == 5
    assert rejected_cell["activation_count"] == 0
    assert rejected_cell["suppressed"] is False
    assert rejected_cell["metrics"]["activation_count"] == {"count": 0, "rate": 0.0, "of": 5}
    assert rejected_cell["metrics"]["wins"]["count"] == 2
    assert rejected_cell["metrics"]["losses"]["count"] == 3

    qualify_cell = table["by_gate_bucket_floor"]["WOULD_OTHERWISE_QUALIFY"]
    assert qualify_cell["activation_count"] == 5
    assert qualify_cell["metrics"]["activation_count"]["count"] == 5

    not_aligned = table["by_factor"]["spy_alignment"][NOT_ALIGNED]
    assert not_aligned["count"] == 5
    assert not_aligned["activation_count"] == 0
    assert not_aligned["metrics"]["activation_count"]["count"] == 0

    long_cell = table["by_direction"]["LONG"]
    assert long_cell["count"] == 5
    assert long_cell["activation_count"] == 0
    short_cell = table["by_direction"]["SHORT"]
    assert short_cell["count"] == 5
    assert short_cell["activation_count"] == 5


def test_scoring_refuses_a_snapshot_missing_a_v02_factor_field() -> None:
    record = json.loads(_artifact().body)
    del record["episodes"][0]["spy_trend"]
    with pytest.raises(StudyContractError, match="snapshot_field_missing"):
        score_session_record(record)
    assert PRE_ENTRY_FACTOR_FIELDS == ("spy_trend", "qqq_trend", "hourly_candle_type", "daily_candle_type", "alignment_failures", "floor_remaining_rr", "late_floor")
