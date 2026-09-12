from __future__ import annotations

from alert_ranker.discord import build_discord_payload
from alert_ranker.scorer import ScoreResult
from alert_ranker.signa_v2_display import render_signa_v2


def test_render_signa_v2_full_observation() -> None:
    text = render_signa_v2(
        {
            "signa_v2_ok": True,
            "signa_v2_symbol": "AAPL",
            "signa_v2_timeframe": "1d",
            "signa_v2_grade": "A",
            "signa_v2_score": 91,
            "signa_v2_confidence": 88,
            "signa_v2_direction": "LONG",
            "signa_v2_reward_to_risk": 2.3,
        }
    )
    assert text == (
        "V2 observational · AAPL · grade A · score 91 · confidence 88% · "
        "LONG · R:R 2.30 · 1d"
    )


def test_render_signa_v2_error_is_explicitly_unavailable() -> None:
    assert render_signa_v2({"signa_v2_error": "http_403"}) == (
        "V2 observational · unavailable (http_403)"
    )


def test_render_signa_v2_missing_is_na() -> None:
    assert render_signa_v2({}) == "N/A"
    assert render_signa_v2({"signa_v2_ok": False}) == "N/A"


def test_discord_uses_v2_observation_without_implying_authority() -> None:
    result = ScoreResult(
        ticker="AAPL",
        direction="LONG",
        score=7,
        pattern="2-1-2",
        components={"vwap": 2, "trend": 2, "volume": 2, "session": 1, "signa": 0},
        raw={
            "ticker": "AAPL",
            "direction": "LONG",
            "setup_status": "TRIGGERED",
            "price": 105.0,
            "signa_v2_ok": True,
            "signa_v2_symbol": "AAPL",
            "signa_v2_timeframe": "1d",
            "signa_v2_grade": "A",
            "signa_v2_score": 91,
            "signa_v2_confidence": 88,
            "signa_v2_direction": "LONG",
            "signa_v2_reward_to_risk": 2.3,
        },
    )

    embed = build_discord_payload(result)["embeds"][0]
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert fields["Signa Context"].startswith("V2 observational")
    assert "grade A" in fields["Signa Context"]
    assert "confidence 88%" in fields["Signa Context"]
    assert result.components["signa"] == 0


def test_discord_keeps_legacy_signa_line_alongside_v2() -> None:
    result = ScoreResult(
        ticker="AAPL",
        direction="LONG",
        score=7,
        pattern="2-1-2",
        components={"vwap": 2, "trend": 2, "volume": 2, "session": 1, "signa": 0},
        raw={
            "ticker": "AAPL",
            "direction": "LONG",
            "setup_status": "TRIGGERED",
            "price": 105.0,
            "signa_symbol": "AAPL",
            "signa_grade": "B",
            "signa_score": 72,
            "signa_daily_direction": "UP",
            "signa_v2_ok": True,
            "signa_v2_symbol": "AAPL",
            "signa_v2_timeframe": "1d",
            "signa_v2_grade": "A",
            "signa_v2_score": 91,
            "signa_v2_direction": "LONG",
        },
    )

    embed = build_discord_payload(result)["embeds"][0]
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    legacy_line, v2_line = fields["Signa Context"].split("\n")
    assert legacy_line == "Observational · AAPL · grade B · score 72 · UP"
    assert v2_line == "V2 observational · AAPL · grade A · score 91 · LONG · 1d"
