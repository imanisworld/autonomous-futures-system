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
from ops.options_212c_floor_outcome_study import (
    AMBIGUOUS,
    DATA_INVALID,
    REQUIRED_METRICS,
    STOP,
    TARGET,
    TIMEOUT,
    StudyContractError,
    aggregate_metrics,
    build_session_artifact,
    score_session_record,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/research-experiment-specs/E-2026-10-02-options-212c-floor-outcome-01.json"
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
        hourly_candle_type="2U",
        daily_candle_type="2U",
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
        "entry_trigger": ep.entry_trigger,
        "invalidation": ep.invalidation,
        "risk": ep.risk,
        "floor_target_1": ep.floor_target_1,
        "floor_target_2": 103.0,
        "first_sight_at": ep.first_sight_at,
        "first_sight_after_close": ep.first_sight_after_close,
        "first_sight_price": ep.first_sight_price,
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


def test_capture_fails_closed_on_first_event_drift_and_missing_bar() -> None:
    session = nyse_session_for(datetime.fromisoformat(DAY).date())
    assert session is not None
    ep = _episode()
    with pytest.raises(StudyContractError, match="episode_event_mismatch"):
        build_session_artifact(
            session,
            [ep],
            [_event(ep, floor_target_1=999.0)],
            {ep.symbol: _bars()},
            source=_source(),
            captured_at=datetime(2026, 10, 5, 20, 31, tzinfo=timezone.utc),
        )

    missing = _bars()
    del missing[3]
    with pytest.raises(StudyContractError, match="missing_bar"):
        build_session_artifact(
            session,
            [ep],
            [_event(ep)],
            {ep.symbol: missing},
            source=_source(),
            captured_at=datetime(2026, 10, 5, 20, 31, tzinfo=timezone.utc),
        )


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


def test_aggregate_emits_all_preregistered_metrics() -> None:
    rows = [
        {
            "episode_id": "a",
            "ticker": "AAPL",
            "session_date": DAY,
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


def test_spec_stays_draft_and_metric_contract_matches() -> None:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    assert spec["status"] == "DRAFT"
    assert spec["acceptance_criteria"] is None
    assert spec["rejection_criteria"] is None
    assert tuple(spec["required_metrics"]) == REQUIRED_METRICS
