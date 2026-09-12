from __future__ import annotations

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
