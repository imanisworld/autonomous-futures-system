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
            "signa_v2_strength": 82,
            "signa_v2_factor_count": 5,
            "signa_v2_factor_conflicts": ["gamma"],
            "signa_v2_observation_rating": "B",
            "signa_v2_direction": "LONG",
            "signa_v2_reward_to_risk": 2.3,
        }
    )
    assert text == (
        "V2 observational · AAPL · obs rating B · grade A · score 91 · confidence 88% · "
        "strength 82 · 5 factors · conflicts gamma · LONG · R:R 2.30 · 1d"
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
            "signa_v2_observation_rating": "A",
            "signa_v2_direction": "LONG",
            "signa_v2_reward_to_risk": 2.3,
        },
    )

    embed = build_discord_payload(result)["embeds"][0]
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    signa = fields["Signa (outside opinion)"]
    assert signa.startswith("For info only · Signa v2 (being tested)")
    assert "strong (grade A)" in signa
    assert "obs rating A" in signa
    assert "88% confident" in signa
    assert "R:R" not in signa  # reward-to-risk ratio is jargon; dropped from the card
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
            "signa_v2_observation_rating": "A",
            "signa_v2_direction": "LONG",
        },
    )

    embed = build_discord_payload(result)["embeds"][0]
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    legacy_line, v2_line = fields["Signa (outside opinion)"].split("\n")
    assert legacy_line == "For info only · leaning up, fairly strong (grade B)"
    assert v2_line == "For info only · Signa v2 (being tested): leaning up, strong (grade A) · obs rating A · daily view"


def test_trade_grade_is_display_only_and_uses_existing_validator_state() -> None:
    base = {
        "ticker": "AAPL",
        "direction": "LONG",
        "setup_status": "TRIGGERED",
        "paper_policy_id": "OPTIONS_PAPER_V1",
        "paper_policy_status": "VALID",
        "trade_proof_status": "VALID",
        "price": 105.0,
    }

    def grade_for(score: int, **raw_updates: object) -> str:
        result = ScoreResult(
            ticker="AAPL",
            direction="LONG",
            score=score,
            pattern="2-1-2",
            components={"vwap": 2, "trend": 2, "volume": 2, "session": 1, "signa": 0},
            raw={**base, **raw_updates},
        )
        embed = build_discord_payload(result)["embeds"][0]
        fields = {field["name"]: field["value"] for field in embed["fields"]}
        return fields["Trade grade"]

    assert grade_for(9).startswith("A ·")
    assert grade_for(8).startswith("B ·")
    assert grade_for(9, trade_proof_status="INCOMPLETE").startswith("B ·")
    assert grade_for(6).startswith("C ·")
    assert grade_for(9, paper_policy_status="DATA_INVALID", paper_policy_reason="quote_stale").startswith("F ·")


def test_trade_grade_is_na_before_trigger() -> None:
    result = ScoreResult(
        ticker="AAPL",
        direction="LONG",
        score=9,
        pattern="2-1-2",
        components={"vwap": 2, "trend": 2, "volume": 2, "session": 1, "signa": 0},
        raw={
            "ticker": "AAPL",
            "direction": "LONG",
            "setup_status": "WATCHING",
            "price": 105.0,
        },
    )
    embed = build_discord_payload(result)["embeds"][0]
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert fields["Trade grade"] == "N/A · setup not triggered"
    assert result.score == 9
