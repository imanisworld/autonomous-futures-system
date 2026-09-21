"""Daily setups are scored on their own lane's confirmation, not the 30m VWAP/EMA20.

Box audit 2026-09-21: of the 64 promoted Daily rows that reached the Discord
decision since 2026-09-08, 30 were zeroed by ``against_vwap``/``against_trend``
on sub-0.1% intraday wobbles (SPY LONG 2-2 reversal at 758.97 vs 30m VWAP
759.44). The intraday checks are meaningless for a Daily setup; the Daily lane
already proves SPY/QQQ daily-trend alignment before promotion.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from alert_ranker.discord import _context_card_text
from alert_ranker.scorer import score_setup

NY = ZoneInfo("America/New_York")
NOON = datetime(2026, 9, 16, 12, 22, tzinfo=NY)  # outside the NY-open bonus window


def _spy_daily_reversal(**overrides):
    # Real vetoed row: scans 2026-09-16T14:22Z SPY LONG strat_222_reversal.
    row = {
        "ticker": "SPY",
        "direction": "LONG",
        "pattern": "strat_222_reversal",
        "setup_timeframe": "1D",
        "timeframe": "1D",
        "setup_type": "DAILY_222_REVERSAL",
        "setup_status": "TRIGGERED",
        "setup_market_status": "VALID",
        "setup_market_reason": "spy=bullish;qqq=bullish;hourly=missing",
        "setup_entry_trigger": 760.35,
        "price": 758.97,
        "vwap": 759.441846,
        "ema20": 758.818627867627,
        "volume_ratio": None,
        "iv_rank": None,
    }
    row.update(overrides)
    return row


def test_daily_reversal_below_intraday_vwap_is_not_vetoed():
    result = score_setup(_spy_daily_reversal(), now=NOON)
    assert result.reason == ""
    assert result.score == 7  # pattern 3 + market alignment 4, same as passing Daily rows
    assert result.components["market_alignment"] == 4
    # Intraday readings recorded as informational zeros, never scored for Daily.
    assert result.components["vwap"] == 0
    assert result.components["trend"] == 0
    assert result.raw["intraday_filters_applied"] is False


def test_daily_row_below_intraday_ema20_is_not_vetoed():
    # Real vetoed row: 2026-09-16T16:47Z MRK LONG strat_222_reversal (trend fail).
    row = _spy_daily_reversal(
        ticker="MRK", price=144.03, vwap=144.0422, ema20=144.0338, setup_entry_trigger=144.15,
        setup_market_reason="spy=bullish;qqq=bullish;hourly=two_up",
    )
    result = score_setup(row, now=NOON)
    assert result.reason == ""
    assert result.score == 7


def test_daily_row_scores_the_same_when_intraday_checks_would_have_passed():
    # Awarding alignment instead of vwap+trend must not inflate the passing rows.
    result = score_setup(_spy_daily_reversal(price=761.0, vwap=759.44, ema20=758.8), now=NOON)
    assert result.score == 7
    assert result.components["vwap"] == 0 and result.components["trend"] == 0


def test_daily_row_without_market_alignment_is_vetoed_with_its_own_reason():
    result = score_setup(_spy_daily_reversal(setup_market_status="NOT_ALIGNED"), now=NOON)
    assert result.score == 0
    assert result.reason == "against_market"
    assert result.components["market_alignment"] == 0


def test_thirty_minute_setup_keeps_the_intraday_veto():
    row = _spy_daily_reversal(
        setup_timeframe="30m", timeframe="30m", setup_type="STRAT_212_CONTINUATION",
        pattern="strat_212",
    )
    row.pop("setup_market_status")
    result = score_setup(row, now=NOON)
    assert result.reason == "against_vwap"
    assert result.score == 0
    assert "market_alignment" not in result.components

    passing = score_setup({**row, "price": 761.0}, now=NOON)
    assert passing.reason == ""
    assert passing.components["vwap"] == 2 and passing.components["trend"] == 2
    assert passing.score == 7


def test_discord_context_card_labels_daily_intraday_filters_as_not_applied():
    daily = score_setup(_spy_daily_reversal(), now=NOON)
    text = _context_card_text(daily, "New York")
    assert "SPY/QQQ alignment pass" in text
    assert "intraday filters not applied" in text
    assert "VWAP fail" not in text

    intraday = score_setup(
        _spy_daily_reversal(setup_timeframe="30m", timeframe="30m", price=761.0), now=NOON
    )
    text = _context_card_text(intraday, "New York")
    assert "VWAP pass" in text and "Trend pass" in text
    assert "alignment" not in text
