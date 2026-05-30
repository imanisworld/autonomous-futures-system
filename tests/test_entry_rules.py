from __future__ import annotations

from alert_ranker.entry_rules import evaluate_entry_rules


def base_payload(**overrides):
    data = {
        "direction": "LONG",
        "zone_type": "demand",
        "zone_state": "fresh",
        "zone_touched": True,
        "price": 101,
        "ema20": 100,
        "ftfc_direction": "UP",
        "confirmation": "bullish_rejection",
    }
    data.update(overrides)
    return data


def test_calls_require_demand_zone():
    result = evaluate_entry_rules(base_payload(zone_type="supply"))

    assert result.eligible is False
    assert result.status == "blocked"
    assert result.reason == "calls_require_demand"


def test_puts_require_supply_zone():
    result = evaluate_entry_rules(
        base_payload(
            direction="SHORT",
            zone_type="demand",
            price=99,
            ema20=100,
            ftfc_direction="DOWN",
            confirmation="bearish_rejection",
        )
    )

    assert result.eligible is False
    assert result.reason == "puts_require_supply"


def test_fresh_demand_retest_with_bullish_confirmation_confirms_call_entry():
    result = evaluate_entry_rules(base_payload())

    assert result.eligible is True
    assert result.status == "confirmed"
    assert result.reason == "entry_confirmed"
    assert "demand zone present" in result.notes


def test_zone_must_be_fresh():
    result = evaluate_entry_rules(base_payload(zone_state="used"))

    assert result.eligible is False
    assert result.status == "blocked"
    assert result.reason == "zone_not_fresh"


def test_zone_retest_is_required_before_entry_confirmation():
    result = evaluate_entry_rules(base_payload(zone_touched=False))

    assert result.eligible is False
    assert result.status == "forming"
    assert result.reason == "waiting_for_zone_retest"


def test_confirmation_candle_is_required_after_zone_retest():
    result = evaluate_entry_rules(base_payload(confirmation=""))

    assert result.eligible is False
    assert result.status == "forming"
    assert result.reason == "waiting_for_confirmation_candle"


def test_opposing_ftfc_blocks_entry():
    result = evaluate_entry_rules(base_payload(ftfc_direction="DOWN"))

    assert result.eligible is False
    assert result.status == "blocked"
    assert result.reason == "ftfc_opposes_direction"


def test_above_emas_is_call_territory_below_emas_is_put_territory():
    call_result = evaluate_entry_rules(base_payload(price=101, ema8=100, ema20=99, ema50=98))
    put_result = evaluate_entry_rules(
        base_payload(
            direction="SHORT",
            zone_type="supply",
            price=97,
            ema8=98,
            ema20=99,
            ema50=100,
            ftfc_direction="DOWN",
            confirmation="bearish_rejection",
        )
    )

    assert call_result.eligible is True
    assert put_result.eligible is True
