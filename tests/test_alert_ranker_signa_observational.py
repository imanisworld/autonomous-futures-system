from __future__ import annotations

import inspect
from datetime import datetime
from zoneinfo import ZoneInfo

import alert_ranker.scanner as scanner_module
from alert_ranker.discord import build_discord_payload
from alert_ranker.scorer import ScoreResult, score_setup
from sources.signa_client import parse_signa_signal


NOW = datetime(2026, 9, 1, 10, 0, tzinfo=ZoneInfo("America/New_York"))


def _payload(**overrides):
    data = {
        "ticker": "AAPL",
        "pattern": "2-1-2",
        "direction": "LONG",
        "price": 101.0,
        "vwap": 100.0,
        "ema20": 99.0,
        "volume_ratio": 1.3,
        "iv_rank": 25.0,
    }
    data.update(overrides)
    return data


def test_signa_alignment_opposition_and_missing_do_not_change_actionable_score():
    missing = score_setup(_payload(), now=NOW)
    aligned = score_setup(
        _payload(signa_grade="A", signa_score=95, signa_daily_direction="UP"),
        now=NOW,
    )
    opposed = score_setup(
        _payload(signa_grade="A", signa_score=95, signa_daily_direction="DOWN"),
        now=NOW,
    )
    weak = score_setup(
        _payload(signa_grade="F", signa_score=5, signa_daily_direction="DOWN"),
        now=NOW,
    )

    scores = {missing.score, aligned.score, opposed.score, weak.score}
    assert len(scores) == 1
    assert missing.components["signa"] == 0
    assert aligned.components["signa"] == 0
    assert opposed.components["signa"] == 0
    assert weak.components["signa"] == 0


def test_signa_remains_available_in_raw_telemetry_without_score_authority():
    result = score_setup(
        _payload(signa_grade="A", signa_score=88, signa_daily_direction="UP"),
        now=NOW,
    )

    assert result.raw["signa_grade"] == "A"
    assert result.raw["signa_score"] == 88
    assert result.raw["signa_daily_direction"] == "UP"
    assert result.components["signa"] == 0


def _discord_result(*, setup_status: str | None) -> ScoreResult:
    raw = {
        "price": 101.0,
        "ny_open": True,
        "signa_symbol": "AAPL",
        "signa_grade": "A",
        "signa_score": 90,
        "signa_daily_direction": "UP",
    }
    if setup_status is not None:
        raw["setup_status"] = setup_status
    return ScoreResult(
        ticker="AAPL",
        direction="LONG",
        score=9,
        pattern="2-1-2",
        components={"strat_pattern": 3, "vwap": 2, "trend": 2, "volume": 2, "signa": 0},
        raw=raw,
    )


def test_generic_discord_cannot_claim_confirmed_without_triggered_setup():
    payload = build_discord_payload(_discord_result(setup_status=None))
    embed = payload["embeds"][0]
    text = f"{embed['title']} {embed['description']}"
    assert "SETUP WATCHING" in embed["title"]
    assert "CONFIRMED" not in text
    assert "All gates passed" not in text
    assert "not TRIGGERED" in embed["description"]


def test_triggered_setup_may_use_triggered_wording():
    payload = build_discord_payload(_discord_result(setup_status="TRIGGERED"))
    embed = payload["embeds"][0]
    assert "TRIGGERED" in embed["title"]
    assert "Mechanical setup status: TRIGGERED" in embed["description"]


def test_signa_display_is_explicitly_observational():
    payload = build_discord_payload(_discord_result(setup_status=None))
    fields = {field["name"]: field["value"] for field in payload["embeds"][0]["fields"]}
    assert fields["Signa Context"].startswith("Observational")
    assert "score +" not in fields["Signa Context"]


def test_signa_provenance_keeps_raw_surfaces_and_timestamps():
    raw_payload = {
        "ok": True,
        "engine": {
            "grade": "A",
            "score": 82,
            "direction": "WAIT",
            "runAt": "2026-09-01T13:00:00Z",
        },
        "data": {
            "direction": "UP",
            "technicals_as_of": "2026-09-01T13:29:00Z",
        },
        "signa": {"action": "BUY"},
        "stale": False,
        "cached": True,
    }
    signal = parse_signa_signal(
        "AAPL",
        raw_payload,
        requested_timeframe="1d",
        retrieved_at="2026-09-01T13:30:00Z",
    )

    assert signal.grade == "A"
    assert signal.score == 82
    assert signal.daily_direction == "UP"
    assert signal.requested_timeframe == "1d"
    assert signal.retrieved_at == "2026-09-01T13:30:00Z"
    assert signal.engine_run_at == "2026-09-01T13:00:00Z"
    assert signal.technicals_as_of == "2026-09-01T13:29:00Z"
    assert signal.stale is False
    assert signal.cached is True
    assert signal.raw_engine_grade == "A"
    assert signal.raw_engine_score == 82
    assert signal.raw_engine_direction == "WAIT"
    assert signal.raw_data_direction == "UP"
    assert signal.raw is raw_payload
    assert signal.provenance_fields()["signa_raw_engine_direction"] == "WAIT"


def test_scanner_has_no_second_direct_signa_discord_authority():
    source = inspect.getsource(scanner_module)
    assert "_maybe_send_candidate_alert" not in source
    assert "_legacy_callout_allowed" not in source
    assert "build_candidate_embed" not in source
    assert "httpx.post" not in source
    assert "Signa pivots as proxy" not in source
