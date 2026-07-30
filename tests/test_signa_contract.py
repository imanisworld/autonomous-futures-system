"""Signa API v1 contract tests, pinned against REAL captured responses.

Fixtures in tests/fixtures/signa_api_v1/ were captured live on 2026-07-29 from
GET https://app.getsigna.ai/api/v1/signal (api_version v1, engine_version v3.1).

These tests exist to stop the parser drifting back to a shape the API never
returned, and to hold the line that **Signa is observational metadata only**.
No assertion here may ever become a gate.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sources.signa_client import (
    SUPPORTED_TIMEFRAMES,
    TIMEFRAME_PARAM,
    SignaClient,
    parse_signa_reading,
)

FIXTURES = Path(__file__).parent / "fixtures" / "signa_api_v1"


def _load(tf: str) -> dict:
    return json.loads((FIXTURES / f"signa_spy_{tf}.json").read_text())


def _reading(tf: str, now: datetime | None = None):
    return parse_signa_reading(
        symbol="SPY", payload=_load(tf), requested_timeframe=tf, now=now
    )


# --- request contract ------------------------------------------------------


def test_timeframe_param_is_tf_not_timeframe():
    """The single most consequential defect: `timeframe` is silently ignored
    by the server, which falls back to 1d."""
    assert TIMEFRAME_PARAM == "tf"


def test_supported_timeframes_documented():
    for tf in ("1d", "4h", "1h"):
        assert tf in SUPPORTED_TIMEFRAMES


# --- three surfaces, preserved separately ----------------------------------


def test_all_three_surfaces_are_present_and_distinct():
    reading = _reading("1d")
    assert set(reading.surfaces) == {"engine", "signa", "data"}
    assert all(reading.surfaces[name].present for name in ("engine", "signa", "data"))


def test_conflicting_grades_are_both_preserved_with_no_winner():
    """The real 1d response carries engine=B and signa=C. Neither is
    authoritative; both must survive."""
    reading = _reading("1d")
    assert reading.surfaces["engine"].grade == "B"
    assert reading.surfaces["signa"].grade == "C"
    assert reading.grades == {"engine": "B", "signa": "C"}


def test_grade_conflict_is_recorded_not_resolved_and_not_blocking():
    reading = _reading("1d")
    assert reading.signa_grade_conflict is True
    # Recorded as an observation...
    assert reading.to_observation()["signa_grade_conflict"] is True
    # ...and the reading is still perfectly usable. Conflict is not an error.
    assert reading.ok is True
    assert reading.error is None


def test_conflicting_directions_are_all_preserved():
    """Real response: engine BULLISH, signa HOLD, data WAIT."""
    reading = _reading("1d")
    assert reading.surfaces["engine"].direction == "UP"
    assert reading.surfaces["signa"].direction == "NEUTRAL"   # HOLD
    assert reading.surfaces["data"].direction == "WAIT"
    assert reading.surfaces["data"].direction_raw == "WAIT"
    assert reading.surfaces_disagree is True


def test_engine_is_timeframe_invariant_and_marked_as_such():
    """engine is a nightly consensus. Labelling it 4H or 1H would be false."""
    d1, h4, h1 = _reading("1d"), _reading("4h"), _reading("1h")
    grades = {r.surfaces["engine"].grade for r in (d1, h4, h1)}
    scores = {r.surfaces["engine"].score for r in (d1, h4, h1)}
    directions = {r.surfaces["engine"].direction for r in (d1, h4, h1)}
    assert grades == {"B"}
    assert scores == {81.0}
    assert directions == {"UP"}
    for r in (d1, h4, h1):
        assert r.surfaces["engine"].timeframe_meaningful is False
        assert r.surfaces["data"].timeframe_meaningful is True


def test_echoed_timeframe_is_recorded_per_fixture():
    for tf in ("1d", "4h", "1h"):
        assert _reading(tf).echoed_timeframe == tf
        assert _reading(tf).timeframe_mismatch is False


def test_timeframe_mismatch_is_detected():
    """A server that substitutes a timeframe must not pass unnoticed."""
    reading = parse_signa_reading(
        symbol="SPY", payload=_load("1d"), requested_timeframe="4h"
    )
    assert reading.echoed_timeframe == "1d"
    assert reading.timeframe_mismatch is True


# --- distinct numeric fields -----------------------------------------------


def test_score_confidence_and_conviction_are_never_merged():
    """Four different vendor measurements. The old parser collapsed them."""
    reading = _reading("1d")
    engine = reading.surfaces["engine"]
    signa = reading.surfaces["signa"]
    data = reading.surfaces["data"]

    assert engine.score == 81.0          # engine.score
    assert engine.confidence == 31.0     # engine.confidence — NOT the score
    assert signa.conviction == 48.0      # signa.conviction — NOT a score
    assert data.confidence == 35.0       # data.confidence — its own thing

    assert signa.score is None           # conviction must not leak into score
    assert signa.confidence is None
    assert engine.conviction is None


def test_zero_score_is_preserved_not_treated_as_missing():
    """The old `a or b` chaining silently replaced a legitimate 0."""
    payload = _load("1d")
    payload["engine"]["score"] = 0
    reading = parse_signa_reading(symbol="SPY", payload=payload)
    assert reading.surfaces["engine"].score == 0.0


def test_a_plus_grade_is_preserved_verbatim():
    payload = _load("1d")
    payload["engine"]["grade"] = "A+"
    reading = parse_signa_reading(symbol="SPY", payload=payload)
    surface = reading.surfaces["engine"]
    assert surface.grade == "A+"          # NOT truncated to "A"
    assert surface.grade_letter == "A"    # convenience only
    assert surface.is_plus is True


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("BULLISH", "UP"), ("LONG", "UP"), ("UP", "UP"),
        ("BEARISH", "DOWN"), ("SHORT", "DOWN"),
        ("WAIT", "WAIT"),
        ("NEUTRAL", "NEUTRAL"), ("HOLD", "NEUTRAL"),
        ("SOMETHING_NEW", "UNKNOWN"),
    ],
)
def test_direction_normalization_including_wait_and_unknown(raw, expected):
    """WAIT stays distinct from NEUTRAL; unrecognized becomes UNKNOWN rather
    than passing through raw where a string compare could match it."""
    payload = _load("1d")
    payload["data"]["direction"] = raw
    reading = parse_signa_reading(symbol="SPY", payload=payload)
    assert reading.surfaces["data"].direction == expected


# --- provenance / staleness (recorded, never enforced) ---------------------


def test_signal_timestamp_and_age_are_recorded():
    now = datetime(2026, 7, 30, 1, 25, 26, tzinfo=timezone.utc)
    reading = _reading("1d", now=now)
    assert reading.signal_timestamp == "2026-07-30T00:25:26.257+00:00"
    assert reading.age_seconds == pytest.approx(3600, abs=1)


def test_signal_timestamp_is_identical_across_timeframes():
    """It timestamps the nightly engine, not the live data block — so it must
    not be read as freshness for `data`."""
    stamps = {_reading(tf).signal_timestamp for tf in ("1d", "4h", "1h")}
    assert len(stamps) == 1


def test_unknown_age_is_not_resolved_in_either_direction():
    payload = _load("1d")
    payload.pop("signal_timestamp")
    reading = parse_signa_reading(symbol="SPY", payload=payload)
    assert reading.age_seconds is None
    assert reading.is_stale(60) is None   # unknown, NOT "fresh" and NOT "stale"


def test_staleness_is_an_observation_not_an_error():
    now = datetime(2027, 1, 1, tzinfo=timezone.utc)
    reading = _reading("1d", now=now)
    assert reading.is_stale(3600) is True
    # Still ok, still no error: staleness never invalidates the reading.
    assert reading.ok is True
    assert reading.error is None


# --- fields the old parser dropped entirely --------------------------------


def test_previously_unparsed_fields_are_captured():
    reading = _reading("1d")
    data = reading.surfaces["data"]
    signa = reading.surfaces["signa"]

    assert data.tier == "NEUTRAL"
    assert data.stage == 3
    assert data.stage_description == "Topping / Distribution"
    assert data.overall_score == 27.5
    assert data.entry == 729.75
    assert data.stop == 741.9699
    assert data.target == 705.3102
    assert data.rr == 2.0
    assert data.patterns == ("Double Top",)
    assert len(data.triggers) == 2
    assert data.triggers[0].name == "MACD Bearish"
    assert data.triggers[0].weight == 0.2

    assert signa.flow_score == 64.0
    assert signa.volume_grade == "D"
    assert signa.regime_class == "bull"
    assert signa.risk_rating == "MODERATE"
    assert signa.alpha_event is False

    assert reading.options_flow["options_flow_sentiment"] == "BEARISH"
    assert "faber-taa" in reading.confidence_pillars
    assert reading.engine_version == "v3.1"


def test_cross_surface_conflict_is_passed_through_untouched():
    """The API ships an explicit conflict field. Semantics unproven, so it is
    preserved raw rather than interpreted."""
    assert _reading("1d").cross_surface_conflict is None
    payload = _load("1d")
    payload["crossSurfaceConflict"] = {"kind": "grade", "detail": "engine!=signa"}
    reading = parse_signa_reading(symbol="SPY", payload=payload)
    assert reading.cross_surface_conflict == {"kind": "grade", "detail": "engine!=signa"}


def test_weekly_direction_field_does_not_exist_in_the_real_response():
    """strategy/signa_gate.py gates on weekly_direction, which the API never
    returns — that gate is dead code. Pinned so the absence stays visible."""
    for tf in ("1d", "4h", "1h"):
        payload = _load(tf)
        assert "weekly_direction" not in payload.get("data", {})
        assert "weeklyDirection" not in payload.get("signa", {})


# --- failure modes are neutral, never exceptions ---------------------------


def test_missing_api_key_returns_neutral_reading():
    reading = SignaClient(api_key="").fetch_reading("SPY")
    assert reading.ok is False
    assert reading.error == "missing_api_key"
    assert reading.surfaces == {}


def test_empty_payload_parses_without_raising():
    reading = parse_signa_reading(symbol="SPY", payload={})
    assert reading.grades == {}
    assert reading.signa_grade_conflict is False
    assert reading.surfaces_disagree is False
