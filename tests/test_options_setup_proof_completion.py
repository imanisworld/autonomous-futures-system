from __future__ import annotations

from alert_ranker.bar_context import MarketContext, SymbolContext
from alert_ranker.setup_proof import complete_setup_proof


def _ticker(**overrides):
    data = {
        "available": True,
        "setup_sequence_confirmed": True,
        "setup_status": "INVALID",
        "setup_reason_code": "missing_target_1",
        "setup_direction": "CALL",
        "setup_entry_trigger": 100.0,
        "setup_invalidation": 98.0,
        "strat_sequence": "strat_212",
        "hourly_candle_type": "two_up",
        "daily_candle_type": "two_up",
        "setup_resistance_levels": (101.5, 104.0, 110.0),
        "setup_support_levels": (96.0, 94.0),
    }
    data.update(overrides)
    return data


def _index(close=101.0, vwap=100.0, ema20=99.0, **overrides):
    data = {
        "available": True,
        "close": close,
        "vwap": vwap,
        "ema20": ema20,
    }
    data.update(overrides)
    return data


def test_confirmed_call_completes_only_with_real_targets_and_full_context():
    result = complete_setup_proof(_ticker(), _index(), _index(close=501, vwap=500, ema20=499))
    assert result["setup_status"] == "TRIGGERED"
    assert result["setup_reason_code"] == "setup_proof_complete"
    assert result["direction"] == "LONG"
    assert result["entry_trigger"] == 100.0
    assert result["underlying_invalidation"] == 98.0
    assert result["target_1"] == 101.5
    assert result["target_2"] == 104.0
    assert result["underlying_rr_1"] == 0.75
    assert result["underlying_rr_2"] == 2.0
    assert result["ftfc"] is True
    assert result["market_context_status"] == "ALIGNED"
    assert result["setup_target_source"] == "prior_completed_hourly_and_daily_levels"


def test_confirmed_put_uses_support_levels_in_nearest_first_order():
    ticker = _ticker(
        setup_direction="PUT",
        setup_entry_trigger=100.0,
        setup_invalidation=102.0,
        hourly_candle_type="two_down",
        daily_candle_type="two_down",
        setup_resistance_levels=(104.0,),
        setup_support_levels=(99.0, 97.5, 95.0),
    )
    bearish = _index(close=99.0, vwap=100.0, ema20=101.0)
    result = complete_setup_proof(ticker, bearish, bearish)
    assert result["setup_status"] == "TRIGGERED"
    assert result["direction"] == "SHORT"
    assert result["target_1"] == 99.0
    assert result["target_2"] == 97.5


def test_no_sequence_cannot_be_promoted_by_good_market_context():
    result = complete_setup_proof(
        _ticker(setup_sequence_confirmed=False, setup_status="NO_TRADE"),
        _index(),
        _index(),
    )
    assert result == {}


def test_missing_second_real_target_fails_closed():
    result = complete_setup_proof(
        _ticker(setup_resistance_levels=(101.5, 99.0, 98.0)),
        _index(),
        _index(),
    )
    assert result["setup_status"] == "INVALID"
    assert result["setup_reason_code"] == "target_levels_missing"
    assert result["setup_suppression_reason"] == "setup_proof_incomplete:target_levels_missing"


def test_spy_or_qqq_not_aligned_waits_instead_of_triggering():
    mixed = _index(close=100.0, vwap=100.5, ema20=99.5)
    result = complete_setup_proof(_ticker(), _index(), mixed)
    assert result["setup_status"] == "WATCH"
    assert result["setup_reason_code"] == "spy_qqq_not_aligned"
    assert result["market_context_status"] == "WAIT"


def test_htf_not_fully_aligned_waits_instead_of_triggering():
    result = complete_setup_proof(
        _ticker(daily_candle_type="inside_bar"),
        _index(),
        _index(),
    )
    assert result["setup_status"] == "WATCH"
    assert result["setup_reason_code"] == "htf_not_aligned"
    assert result["ftfc"] is False


def test_market_context_flattener_applies_completion_but_keeps_authority_evidence():
    ticker = SymbolContext(
        symbol="AAPL",
        available=True,
        close=101.0,
        vwap=100.0,
        ema20=99.0,
        strat_sequence="strat_212",
        hourly_candle_type="two_up",
        daily_candle_type="two_up",
        setup_resistance_levels=(101.5, 104.0),
        setup_support_levels=(97.0, 95.0),
        setup_status="INVALID",
        setup_reason_code="missing_target_1",
        setup_direction="CALL",
        setup_entry_trigger=100.0,
        setup_invalidation=98.0,
        setup_sequence_confirmed=True,
        setup_suppression_reason="setup_proof_incomplete:missing_target_1",
    )
    spy = SymbolContext(symbol="SPY", available=True, close=501, vwap=500, ema20=499)
    qqq = SymbolContext(symbol="QQQ", available=True, close=451, vwap=450, ema20=449)
    context = MarketContext(
        available=True,
        reason="",
        feed="sip",
        requested_as_of="2026-09-08T15:00:00+00:00",
        information_cutoff="2026-09-08T14:44:00+00:00",
        delay_buffer_seconds=960,
        timeframe="30m",
        ticker=ticker,
        spy=spy,
        qqq=qqq,
    )
    fields = context.to_scanner_fields()
    assert fields["setup_status"] == "TRIGGERED"
    assert fields["pattern"] == "strat_212"
    assert fields["target_1"] == 101.5
    assert fields["target_2"] == 104.0
    assert fields["setup_authority_status"] == "INVALID"
    assert fields["setup_authority_reason_code"] == "missing_target_1"
