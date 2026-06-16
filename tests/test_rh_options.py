from __future__ import annotations

from datetime import datetime, timedelta, timezone

from unittest.mock import patch

from alert_ranker.rh_options import (
    RHAdvisoryBroker,
    _parse_rh_inputs,
    build_candidate_embed,
    check_open_positions,
    compute_gex_walls,
    evaluate_messy_rh_options_text,
    evaluate_rh_options,
    kill_switch,
    manage_rh_options_position,
    morning_check,
    parse_messy_rh_options_text,
    rank_option_contracts,
    sample_rh_options_text,
)
from alert_ranker.storage import ScanStorage


def _valid_inputs(**overrides):
    data = {
        "ticker": "SPY",
        "direction": "LONG",
        "contract_type": "CALL",
        "signa_score": 82,
        "signa_grade": "A",
        "signa_daily_direction": "BULLISH",
        "signa_weekly_direction": "BULLISH",
        "gex_regime": "LOW_PINNING",
        "gex_support_wall": 495.0,
        "gex_resistance_wall": 510.0,
        "current_price": 500.0,
        "premium": 2.20,
        "expiry_date": "2026-07-07",
        "dte": 18,
        "strike": 505.0,
        "earnings_date": None,
        "option_volume": 850,
        "open_interest": 12000,
        "nine_ma": 498.5,
    }
    data.update(overrides)
    return data


def _evaluate(body=None, **overrides):
    raw = _valid_inputs(**overrides) if body is None else body
    return evaluate_rh_options(_parse_rh_inputs(raw))


def test_clean_bullish_setup_returns_trade_with_order_ticket():
    result = _evaluate()

    assert result["decision"] == "TRADE"
    assert result["failed_gates"] == []
    assert result["warnings"] == []
    ticket = result["order_ticket"]
    assert ticket["action"] == "Buy to open"
    assert ticket["ticker"] == "SPY"
    assert ticket["strike"] == 505.0
    assert ticket["expiry"] == "2026-07-07"
    assert ticket["contract_type"] == "CALL"
    assert ticket["quantity"] == 1
    assert ticket["limit_debit"] == 2.20
    assert ticket["stop_premium"] == 1.10
    assert ticket["target_premium"] == 4.40
    assert ticket["invalidation_level"] == 495.0
    assert ticket["management_notes"]
    assert result["broker_preview"]["status"] == "ADVISORY_ONLY"


def test_low_signa_score_rejects():
    result = _evaluate(signa_score=65)

    assert result["decision"] == "NO_TRADE"
    assert "signa_score_too_low" in result["failed_gates"]


def test_c_grade_rejects():
    result = _evaluate(signa_grade="C")

    assert result["decision"] == "NO_TRADE"
    assert "signa_grade_below_b" in result["failed_gates"]


def test_weekly_opposing_daily_rejects():
    result = _evaluate(signa_daily_direction="BULLISH", signa_weekly_direction="BEARISH")

    assert result["decision"] == "NO_TRADE"
    assert "direction_conflict" in result["failed_gates"]


def test_non_low_pinning_gex_rejects():
    result = _evaluate(gex_regime="HIGH_PINNING")

    assert result["decision"] == "NO_TRADE"
    assert "gex_regime_not_low_pinning" in result["failed_gates"]


def test_earnings_inside_5_days_rejects():
    inputs = _parse_rh_inputs(_valid_inputs(earnings_date="2026-06-17"))
    result = evaluate_rh_options(inputs, now=datetime(2026, 6, 15, tzinfo=timezone.utc))

    assert result["decision"] == "NO_TRADE"
    assert "earnings_too_close" in result["failed_gates"]


def test_expiry_same_day_passes():
    # 0DTE is valid — DTE-agnostic system, R:R decides
    result = _evaluate(dte=0)
    assert "expiry_too_close" not in result["failed_gates"]


def test_expiry_friday_no_longer_a_hard_gate():
    # Friday expiry used to be blocked; now accepted if R:R qualifies
    today = datetime.now(timezone.utc).date()
    days_until_friday = (4 - today.weekday()) % 7
    days_until_friday = days_until_friday or 7
    next_friday = today + timedelta(days=days_until_friday)
    result = _evaluate(expiry_date=next_friday.isoformat(), dte=3)
    assert "expiry_friday" not in result["failed_gates"]


def test_premium_over_cap_rejects():
    result = _evaluate(premium=3.00)

    assert result["decision"] == "NO_TRADE"
    assert "premium_over_cap" in result["failed_gates"]
    assert result["risk_result"]["failed_rule"] == "per_contract_premium"


def test_dte_does_not_trigger_watch():
    # Short DTE is now valid — system is DTE-agnostic, R:R decides
    result = _evaluate(dte=5)
    assert result["failed_gates"] == []
    assert not any("dte_outside" in w for w in result["warnings"])


def test_price_not_near_support_wall_is_watch():
    result = _evaluate(current_price=520.0)

    assert result["decision"] == "WATCH"
    assert "price_not_near_support_wall" in result["warnings"]


def test_rh_broker_stub_never_submits():
    result = RHAdvisoryBroker().submit_order({"ticker": "SPY"})

    assert result["status"] == "NOT_IMPLEMENTED"


def test_rh_broker_stub_preview_returns_advisory_only():
    ticket = {"ticker": "SPY"}
    result = RHAdvisoryBroker().preview_order(ticket)

    assert result["status"] == "ADVISORY_ONLY"
    assert result["ticket"] == ticket


def test_shadow_journal_stores_trade_decision(tmp_path):
    storage = ScanStorage(tmp_path / "options_scanner.sqlite")
    result = evaluate_rh_options(_parse_rh_inputs(_valid_inputs()), storage=storage)

    assert isinstance(result["shadow_id"], int)
    stored = storage.latest_shadow_setups(limit=1)[0]
    assert stored.id == result["shadow_id"]
    assert stored.selected_contract["ticker"] == "SPY"


def test_no_trade_not_journaled(tmp_path):
    storage = ScanStorage(tmp_path / "options_scanner.sqlite")
    result = evaluate_rh_options(_parse_rh_inputs(_valid_inputs(signa_score=40)), storage=storage)

    assert result["decision"] == "NO_TRADE"
    assert result["shadow_id"] is None
    assert storage.latest_shadow_setups(limit=1) == []


def test_multiple_hard_gates_all_collected():
    result = _evaluate(signa_score=40, signa_grade="C", gex_regime="HIGH_PINNING")

    assert result["decision"] == "NO_TRADE"
    assert "signa_score_too_low" in result["failed_gates"]
    assert "signa_grade_below_b" in result["failed_gates"]
    assert "gex_regime_not_low_pinning" in result["failed_gates"]


def test_messy_text_parser_extracts_sample_notes():
    parsed = parse_messy_rh_options_text(
        sample_rh_options_text(),
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )

    assert parsed["missing_fields"] == []
    assert parsed["parsed"]["ticker"] == "SPY"
    assert parsed["parsed"]["direction"] == "LONG"
    assert parsed["parsed"]["contract_type"] == "CALL"
    assert parsed["parsed"]["signa_score"] == 82
    assert parsed["parsed"]["signa_grade"] == "A"
    assert parsed["parsed"]["gex_regime"] == "LOW_PINNING"
    assert parsed["parsed"]["gex_support_wall"] == 495
    assert parsed["parsed"]["gex_resistance_wall"] == 510
    assert parsed["parsed"]["current_price"] == 500
    assert parsed["parsed"]["premium"] == 2.2
    assert parsed["parsed"]["strike"] == 505
    assert parsed["parsed"]["expiry_date"] == "2026-07-07"


def test_messy_text_evaluates_to_trade():
    result = evaluate_messy_rh_options_text(
        sample_rh_options_text(),
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )

    assert result["decision"] == "TRADE"
    assert result["order_ticket"]["ticker"] == "SPY"
    assert result["parsed"]["missing_fields"] == []


def test_messy_text_missing_fields_returns_needs_more_info():
    result = evaluate_messy_rh_options_text("SPY bullish Signa 82 A")

    assert result["decision"] == "NEEDS_MORE_INFO"
    assert "missing_fields" in result["warnings"][0]
    assert "premium" in result["parsed"]["missing_fields"]


def test_manage_position_exits_when_premium_hits_stop(tmp_path):
    storage = ScanStorage(tmp_path / "options_scanner.sqlite")
    evaluated = evaluate_rh_options(_parse_rh_inputs(_valid_inputs()), storage=storage)
    setup = storage.get_shadow_setup(evaluated["shadow_id"])

    result = manage_rh_options_position(setup, current_premium=1.0)

    assert result["action"] == "EXIT"
    assert "at/below stop" in result["reasons"][0]


def test_manage_position_invalidates_when_underlying_breaks_level(tmp_path):
    storage = ScanStorage(tmp_path / "options_scanner.sqlite")
    evaluated = evaluate_rh_options(_parse_rh_inputs(_valid_inputs()), storage=storage)
    setup = storage.get_shadow_setup(evaluated["shadow_id"])

    result = manage_rh_options_position(setup, current_price=494.0, current_premium=2.0)

    assert result["action"] == "INVALIDATED"
    assert "broke invalidation" in result["reasons"][0]


def test_manage_position_trims_when_target_hit(tmp_path):
    storage = ScanStorage(tmp_path / "options_scanner.sqlite")
    evaluated = evaluate_rh_options(_parse_rh_inputs(_valid_inputs()), storage=storage)
    setup = storage.get_shadow_setup(evaluated["shadow_id"])

    result = manage_rh_options_position(setup, current_price=504.0, current_premium=4.5)

    assert result["action"] == "TRIM"
    assert "reached target" in result["reasons"][0]


def test_manage_position_holds_when_levels_intact(tmp_path):
    storage = ScanStorage(tmp_path / "options_scanner.sqlite")
    evaluated = evaluate_rh_options(_parse_rh_inputs(_valid_inputs()), storage=storage)
    setup = storage.get_shadow_setup(evaluated["shadow_id"])

    result = manage_rh_options_position(setup, current_price=500.0, current_premium=2.5)

    assert result["action"] == "HOLD"
    assert "remain intact" in result["reasons"][0]


# ── Liquidity gate tests ──────────────────────────────────────────────────────

def test_low_option_volume_rejects():
    result = _evaluate(option_volume=50, open_interest=5000)
    assert result["decision"] == "NO_TRADE"
    assert "low_option_volume" in result["failed_gates"]


def test_low_open_interest_rejects():
    result = _evaluate(option_volume=500, open_interest=200)
    assert result["decision"] == "NO_TRADE"
    assert "low_open_interest" in result["failed_gates"]


def test_no_liquidity_data_warns():
    result = _evaluate(option_volume=None, open_interest=None, nine_ma=None)
    assert result["decision"] == "WATCH"
    assert "no_liquidity_data_provided" in result["warnings"]


def test_good_liquidity_does_not_warn():
    result = _evaluate(option_volume=500, open_interest=5000)
    assert "no_liquidity_data_provided" not in result["warnings"]


# ── 9MA scalp/hold tests ──────────────────────────────────────────────────────

def test_9ma_extended_gives_scalp_ticket():
    # price 3% above 9MA — should be SCALP with 1.5x target
    result = _evaluate(current_price=510.0, nine_ma=495.0)
    ticket = result["order_ticket"]
    assert ticket["trade_style"] == "SCALP"
    assert ticket["target_premium"] == round(2.20 * 1.5, 2)


def test_9ma_near_gives_swing_ticket():
    # price only 0.5% above 9MA — should be SWING with 2.0x target
    result = _evaluate(current_price=500.0, nine_ma=497.5)
    ticket = result["order_ticket"]
    assert ticket["trade_style"] == "SWING"
    assert ticket["target_premium"] == round(2.20 * 2.0, 2)


def test_9ma_no_data_defaults_to_swing():
    result = _evaluate(nine_ma=None, option_volume=500, open_interest=5000)
    ticket = result["order_ticket"]
    assert ticket["trade_style"] == "SWING"
    assert ticket["target_premium"] == round(2.20 * 2.0, 2)


def test_price_below_9ma_warns_for_long():
    result = _evaluate(current_price=493.0, nine_ma=498.5)
    assert "price_below_9ma_for_long_setup" in result["warnings"]


def test_0dte_gives_scalp_intraday_ticket():
    result = _evaluate(dte=0, expiry_date="2026-07-07")
    ticket = result["order_ticket"]
    assert ticket["trade_style"] == "SCALP_INTRADAY"
    assert ticket["target_premium"] == round(2.20 * 1.30, 2)
    assert ticket["stop_premium"] == round(2.20 * 0.35, 2)


def test_3dte_gives_scalp_intraday_ticket():
    result = _evaluate(dte=3, expiry_date="2026-07-07")
    ticket = result["order_ticket"]
    assert ticket["trade_style"] == "SCALP_INTRADAY"


def test_14dte_extended_9ma_gives_scalp():
    # 14 DTE + price extended >2% above 9MA → SCALP (not SCALP_INTRADAY)
    result = _evaluate(dte=14, expiry_date="2026-07-07", current_price=510.0, nine_ma=495.0)
    ticket = result["order_ticket"]
    assert ticket["trade_style"] == "SCALP"
    assert ticket["target_premium"] == round(2.20 * 1.50, 2)


def test_30dte_gives_swing_ticket():
    result = _evaluate(dte=30, expiry_date="2026-08-01")
    ticket = result["order_ticket"]
    assert ticket["trade_style"] == "SWING"
    assert ticket["target_premium"] == round(2.20 * 2.00, 2)


def test_9ma_note_in_management_notes():
    result = _evaluate(current_price=510.0, nine_ma=495.0)
    notes = result["order_ticket"]["management_notes"]
    nine_ma_note = next((n for n in notes if "9MA" in n), None)
    assert nine_ma_note is not None
    assert "SCALP" in nine_ma_note


# ── rank_option_contracts tests ───────────────────────────────────────────────

def _make_candidate(strike, premium, dte=21, vol=500, oi=3000, expiry="2026-07-18"):
    return {"strike": strike, "premium": premium, "dte": dte, "option_volume": vol, "open_interest": oi, "expiry_date": expiry}


def test_rank_returns_contracts_sorted_by_rr():
    # NEM-like: price 105.8, GEX wall 110
    # 105C should have higher intrinsic at target than 108C, so higher R:R
    candidates = [
        _make_candidate(108, 1.20),
        _make_candidate(105, 2.80),
        _make_candidate(107, 1.60),
    ]
    ranked = rank_option_contracts(
        candidates,
        direction="LONG",
        current_price=105.8,
        gex_resistance_wall=110.0,
    )
    assert len(ranked) > 0
    # All ranked contracts should have rr computed and in descending order
    rrs = [c["rr"] for c in ranked]
    assert rrs == sorted(rrs, reverse=True)


def test_rank_filters_low_volume():
    candidates = [
        _make_candidate(105, 1.50, vol=50),   # below 100 vol threshold — filtered
        _make_candidate(107, 1.20, vol=500),
    ]
    ranked = rank_option_contracts(
        candidates, direction="LONG", current_price=105.8, gex_resistance_wall=110.0
    )
    assert all(c["strike"] != 105 for c in ranked)


def test_rank_filters_low_open_interest():
    candidates = [
        _make_candidate(105, 1.50, oi=100),   # below 500 OI threshold — filtered
        _make_candidate(107, 1.20, oi=3000),
    ]
    ranked = rank_option_contracts(
        candidates, direction="LONG", current_price=105.8, gex_resistance_wall=110.0
    )
    assert all(c["strike"] != 105 for c in ranked)


def test_rank_filters_strikes_above_gex_wall():
    # Strike at 112 > wall at 110: estimated_gain = 0 → filtered (dollar_gain <= 0)
    candidates = [
        _make_candidate(112, 0.40),
        _make_candidate(107, 1.20),
    ]
    ranked = rank_option_contracts(
        candidates, direction="LONG", current_price=105.8, gex_resistance_wall=110.0
    )
    assert all(c["strike"] != 112 for c in ranked)


def test_rank_includes_rr_and_intrinsic_fields():
    candidates = [_make_candidate(105, 2.00)]
    ranked = rank_option_contracts(
        candidates, direction="LONG", current_price=105.8, gex_resistance_wall=110.0
    )
    assert len(ranked) == 1
    c = ranked[0]
    assert "rr" in c
    assert "estimated_gain" in c
    assert "dollar_gain" in c
    assert "dollar_risk" in c
    assert c["rank"] == 1


def test_rank_put_uses_support_wall():
    candidates = [_make_candidate(100, 1.50)]
    ranked = rank_option_contracts(
        candidates, direction="SHORT", current_price=105.8, gex_support_wall=98.0
    )
    assert len(ranked) == 1
    # intrinsic = max(0, 100 - 98) = 2.0
    assert ranked[0]["estimated_gain"] == 2.0


# ── Kill switch & position check ───────────────────────────────────────────

def _seed_open_position(tmp_path):
    """Create a ScanStorage with one OPEN shadow position; return (storage, shadow_id)."""
    storage = ScanStorage(tmp_path / "ks.db")
    result = evaluate_rh_options(_parse_rh_inputs(_valid_inputs()), storage=storage)
    return storage, result["shadow_id"]


def test_kill_switch_no_positions(tmp_path):
    storage = ScanStorage(tmp_path / "empty.db")
    with patch("alert_ranker.rh_options._post_discord", return_value=True) as mock_discord:
        result = kill_switch(storage, "http://fake-discord")
    assert result["positions_found"] == 0
    assert result["message"] == "no_open_positions"
    mock_discord.assert_not_called()


def test_kill_switch_cancels_and_sends_discord(tmp_path):
    storage, shadow_id = _seed_open_position(tmp_path)
    with patch("alert_ranker.rh_options._post_discord", return_value=True) as mock_discord:
        result = kill_switch(storage, "http://fake-discord")
    assert result["positions_found"] == 1
    assert shadow_id in result["cancelled_ids"]
    assert result["discord_sent"] is True
    mock_discord.assert_called_once()
    # position should now be CANCELLED in the journal
    updated = storage.get_shadow_setup(shadow_id)
    assert updated.status == "CANCELLED"


def test_kill_switch_returns_position_summary(tmp_path):
    storage, _ = _seed_open_position(tmp_path)
    with patch("alert_ranker.rh_options._post_discord", return_value=True):
        result = kill_switch(storage, "http://fake-discord")
    pos = result["positions"][0]
    assert pos["ticker"] == "SPY"
    assert pos["stop_premium"] is not None
    assert pos["target_premium"] is not None


def test_check_positions_no_marks_returns_open_list(tmp_path):
    storage, shadow_id = _seed_open_position(tmp_path)
    with patch("alert_ranker.rh_options._post_discord") as mock_discord:
        result = check_open_positions(storage, "http://fake-discord", marks=None)
    assert result["open_count"] == 1
    assert result["hits"] == []
    mock_discord.assert_not_called()


def test_check_positions_stop_hit_sends_discord(tmp_path):
    storage, shadow_id = _seed_open_position(tmp_path)
    pos = storage.get_shadow_setup(shadow_id)
    stop = pos.selected_contract["stop_premium"]
    with patch("alert_ranker.rh_options._post_discord", return_value=True) as mock_discord:
        result = check_open_positions(storage, "http://fake-discord", marks={str(shadow_id): stop - 0.01})
    assert len(result["hits"]) == 1
    assert result["hits"][0]["hit_type"] == "STOP_HIT"
    mock_discord.assert_called_once()


def test_check_positions_target_hit_sends_discord(tmp_path):
    storage, shadow_id = _seed_open_position(tmp_path)
    pos = storage.get_shadow_setup(shadow_id)
    target = pos.selected_contract["target_premium"]
    with patch("alert_ranker.rh_options._post_discord", return_value=True) as mock_discord:
        result = check_open_positions(storage, "http://fake-discord", marks={str(shadow_id): target + 0.01})
    assert len(result["hits"]) == 1
    assert result["hits"][0]["hit_type"] == "TARGET_HIT"
    mock_discord.assert_called_once()


def test_check_positions_no_hit_no_discord(tmp_path):
    storage, shadow_id = _seed_open_position(tmp_path)
    pos = storage.get_shadow_setup(shadow_id)
    stop = pos.selected_contract["stop_premium"]
    target = pos.selected_contract["target_premium"]
    mid = (stop + target) / 2
    with patch("alert_ranker.rh_options._post_discord") as mock_discord:
        result = check_open_positions(storage, "http://fake-discord", marks={str(shadow_id): mid})
    assert result["hits"] == []
    mock_discord.assert_not_called()


def test_kill_switch_no_discord_url_still_cancels(tmp_path):
    storage, shadow_id = _seed_open_position(tmp_path)
    result = kill_switch(storage, "")  # no Discord URL
    assert result["positions_found"] == 1
    assert result["discord_sent"] is False
    assert storage.get_shadow_setup(shadow_id).status == "CANCELLED"


# ── Morning check ───────────────────────────────────────────────────────────

def test_morning_check_no_positions_sends_flat_discord(tmp_path):
    storage = ScanStorage(tmp_path / "mc.db")
    with patch("alert_ranker.rh_options._post_discord", return_value=True) as mock_discord:
        result = morning_check(storage, "http://fake-discord")
    assert result["open_count"] == 0
    assert result["discord_sent"] is True
    payload = mock_discord.call_args[0][1]
    assert "No Open Positions" in payload["embeds"][0]["title"]


def test_morning_check_with_positions_sends_recap(tmp_path):
    storage, shadow_id = _seed_open_position(tmp_path)
    with patch("alert_ranker.rh_options._post_discord", return_value=True) as mock_discord:
        result = morning_check(storage, "http://fake-discord")
    assert result["open_count"] == 1
    assert result["discord_sent"] is True
    payload = mock_discord.call_args[0][1]
    embed = payload["embeds"][0]
    assert "Morning Check" in embed["title"]
    assert "SPY" in embed["fields"][0]["name"]
    assert "Stop" in embed["fields"][0]["value"]
    assert "Target" in embed["fields"][0]["value"]


# ── compute_gex_walls ────────────────────────────────────────────────────────


def _call(strike, oi):
    return {"strike_price": str(strike), "type": "call", "open_interest": str(oi)}


def _put(strike, oi):
    return {"strike_price": str(strike), "type": "put", "open_interest": str(oi)}


def test_gex_walls_basic():
    chain = [
        _call(505, 8000),
        _call(510, 15000),
        _call(520, 5000),
        _put(498, 9000),
        _put(495, 20000),
        _put(490, 4000),
    ]
    result = compute_gex_walls(chain, current_price=500.0)
    assert result["call_wall"] == 510.0  # highest OI call at/above 500
    assert result["put_wall"] == 495.0   # highest OI put at/below 500


def test_gex_walls_low_pinning():
    chain = [_call(501, 5000), _put(499, 5000)]
    result = compute_gex_walls(chain, current_price=500.0)
    assert result["regime"] == "LOW_PINNING"


def test_gex_walls_breakout():
    chain = [_call(490, 5000), _put(480, 5000)]
    result = compute_gex_walls(chain, current_price=500.0)
    # price > call_wall (490) → BREAKOUT
    assert result["regime"] == "BREAKOUT"


def test_gex_walls_breakdown():
    chain = [_call(510, 5000), _put(510, 5000)]
    # put wall 510 > price 500 → BREAKDOWN
    result = compute_gex_walls(chain, current_price=500.0)
    assert result["regime"] == "BREAKDOWN"


def test_gex_walls_no_calls():
    chain = [_put(495, 5000)]
    result = compute_gex_walls(chain, current_price=500.0)
    assert result["call_wall"] is None
    assert result["put_wall"] == 495.0
    assert result["confidence"] == "LOW"


def test_gex_walls_empty_chain():
    result = compute_gex_walls([], current_price=500.0)
    assert result["call_wall"] is None
    assert result["put_wall"] is None
    assert result["confidence"] == "LOW"


def test_gex_walls_ignores_bad_rows():
    chain = [
        {"strike_price": "bad", "type": "call", "open_interest": "1000"},
        {"strike_price": "505", "type": "call", "open_interest": "bad"},
        _call(505, 5000),
    ]
    result = compute_gex_walls(chain, current_price=500.0)
    assert result["call_wall"] == 505.0


def test_gex_walls_alt_key_names():
    chain = [
        {"strike": "505", "option_type": "CALL", "open_interest": "5000"},
        {"strike": "495", "option_type": "PUT", "open_interest": "5000"},
    ]
    result = compute_gex_walls(chain, current_price=500.0)
    assert result["call_wall"] == 505.0
    assert result["put_wall"] == 495.0


# ── build_candidate_embed ────────────────────────────────────────────────────


def test_build_candidate_embed_basic():
    embed = build_candidate_embed(
        "SPY", "LONG", 82.0, "A", 500.0,
        call_wall=510.0, put_wall=495.0, regime="LOW_PINNING"
    )
    assert "SPY" in embed["title"]
    assert "LONG" in embed["title"]
    assert embed["color"] == 0x20C783  # green for long
    assert any("Signa" in f["name"] for f in embed["fields"])
    assert any("GEX" in f["name"] for f in embed["fields"])
    assert any("510" in f["value"] or "495" in f["value"] for f in embed["fields"])


def test_build_candidate_embed_bearish_color():
    embed = build_candidate_embed("META", "SHORT", 75.0, "B", 600.0)
    assert embed["color"] == 0xFF4D5A  # red for short


def test_build_candidate_embed_with_strat():
    strat = {"pattern": "22U_REV", "bias": "BULLISH", "bar_types": ["two_down", "two_up"]}
    embed = build_candidate_embed("NVDA", "LONG", 80.0, "A", 900.0, strat=strat)
    strat_field = next(f for f in embed["fields"] if "Strat" in f["name"])
    assert "22U_REV" in strat_field["value"]


def test_build_candidate_embed_with_orb():
    orb = {"status": "above", "orb_high": 905.0, "orb_low": 899.0, "window_minutes": 15}
    embed = build_candidate_embed("NVDA", "LONG", 80.0, "A", 910.0, orb=orb)
    orb_field = next(f for f in embed["fields"] if "ORB" in f["name"])
    assert "ABOVE" in orb_field["value"]


def test_build_candidate_embed_no_walls():
    embed = build_candidate_embed("AAPL", "SHORT", 71.0, "B", 200.0)
    price_field = next(f for f in embed["fields"] if "Price" in f["name"])
    assert "200" in price_field["value"]
    # Walls should not appear when not provided
    assert "wall" not in price_field["value"].lower()
