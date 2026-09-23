"""Plain-English wording for the options-side Discord messages (presentation only)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from alert_ranker import plain_text as pt
from alert_ranker import rh_options
from alert_ranker.discord import build_discord_payload
from alert_ranker.scorer import ScoreResult
from notifications import plain_english as pe
from options_manager.models import OptionTradePacket
from options_manager.notify import build_advisory_payload, build_payload
from options_manager.validation.advisory_decision import AdvisoryDecisionResult, AdvisoryVerdict
from options_manager.validation.contract_quality_gate import GateVerdict

TODAY = date(2026, 9, 22)


def test_expiry_wording_never_uses_dte_or_iso():
    for helper in (pt.expires, pe.expires):
        assert helper("2026-09-22", today=TODAY) == "expires today"
        assert helper("2026-09-23", today=TODAY) == "expires tomorrow, Wed Sep 23"
        assert helper("2026-09-25", today=TODAY) == "expires Fri Sep 25 (3 days)"
        assert helper(None, dte=0) == "expires today"
        assert helper(None, dte=5) == "expires in 5 days"
        assert helper(None) == ""


def test_osi_symbol_is_spelled_out():
    parsed = pt.parse_osi("QQQ260923C00741000")
    assert parsed == {"underlying": "QQQ", "expiry": date(2026, 9, 23), "kind": "call", "strike": 741.0}
    assert pt.parse_osi("QQQ $741 Call") is None


def test_scanner_card_moves_osi_and_policy_id_to_footer():
    osi = "QQQ" + (date.today() + timedelta(days=3)).strftime("%y%m%d") + "C00741000"
    result = ScoreResult(
        ticker="QQQ", direction="LONG", score=8, pattern="strat_222_reversal",
        components={"strat_pattern": 3, "vwap": 2, "trend": 2, "signa": 0},
        raw={
            "setup_status": "TRIGGERED", "paper_policy_id": "OPTIONS_PAPER_V1",
            "paper_policy_status": "VALID", "contract": osi, "strike": 741,
            "expiry": (date.today() + timedelta(days=3)).isoformat(), "dte": 3,
            "premium_stop": 1.05, "planned_risk_dollars": 105.0,
            "projected_aggregate_open_planned_risk": 210.0,
        },
    )
    embed = build_discord_payload(result)["embeds"][0]
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    body = " ".join([embed["title"], embed["description"]] + list(fields.values()))
    assert fields["Option"].startswith("QQQ 741 call, expires ")
    assert "(3 days)" in fields["Option"]
    assert osi not in body and "OPTIONS_PAPER_V1" not in body and "DTE" not in body
    assert osi in embed["footer"]["text"] and "OPTIONS_PAPER_V1" in embed["footer"]["text"]
    assert "Most you'd lose: $105.00" in fields["Risk"]
    assert embed["footer"]["text"].startswith("READ ONLY")


def _pos():
    return SimpleNamespace(
        id=1, ticker="QQQ", direction="LONG", status="OPEN",
        selected_contract={"contract_type": "CALL", "strike": 741, "expiry": "2099-01-16",
                           "limit_debit": 1.5, "stop_premium": 1.05, "target_premium": 1.95},
        setup_inputs={},
    )


def test_stop_hit_is_urgent_plain_english():
    embed = rh_options._build_position_hit_embed(_pos(), 1.02, "STOP_HIT")
    assert embed["title"] == "🛑 Stop-loss hit on QQQ 741 call — close it in Robinhood"
    values = " ".join(f["value"] for f in embed["fields"])
    assert "Close it in Robinhood now" in values
    assert "LONG" not in embed["title"] and "2099-01-16" not in values


def test_kill_switch_still_says_close_now():
    sent: list[dict] = []
    original = rh_options._post_discord
    rh_options._post_discord = lambda url, payload: sent.append(payload) or True
    try:
        storage = SimpleNamespace(
            latest_shadow_setups=lambda status, limit: [_pos()],
            update_shadow_outcome=lambda *a, **k: None,
        )
        out = rh_options.kill_switch(storage, "https://discord.test")
    finally:
        rh_options._post_discord = original
    assert out["cancelled_ids"] == [1]
    embed = sent[0]["embeds"][0]
    assert embed["title"].startswith("🚨 KILL SWITCH")
    assert "Robinhood now" in embed["title"]
    assert "Close it in Robinhood now" in embed["fields"][0]["value"]


def test_options_manager_packet_card_is_readable_not_key_value():
    packet = OptionTradePacket(
        ticker="QQQ", direction="CALL", entry_price=741.2, price_target=745.0, signa_score=82,
        signa_grade="A", signa_bias="BULLISH", gex_regime="x", gex_wall_above=None, gex_wall_below=None,
        contract_strike=741.0, contract_expiry=date.today() + timedelta(days=3),
        created_at=datetime.now(timezone.utc), status="PENDING",
    )
    embed = build_payload(packet)["embeds"][0]
    assert embed["title"] == "📝 QQQ 741 call idea ready for review"
    assert "=" not in embed["description"] and "[options_manager]" not in embed["title"]
    assert "bets the price goes up" in embed["description"]
    assert "Advice only · no order is placed automatically" in embed["description"]


def test_options_manager_advisory_keeps_manual_only_boundary():
    result = AdvisoryDecisionResult(
        verdict=AdvisoryVerdict.TAKE, proof_valid=True, contract_verdict=GateVerdict.PASS,
        next_required_action="Proceed manually per this advisory verdict; no automated action follows.",
    )
    embed = build_advisory_payload(result, {"ticker": "QQQ", "direction": "LONG"})["embeds"][0]
    assert embed["title"] == "✅ QQQ Buy — advice: take it (you place it yourself)"
    assert "no automated action follows" in embed["description"]
    assert "no order is placed automatically" in embed["description"]
