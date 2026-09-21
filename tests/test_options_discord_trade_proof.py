"""
tests/test_options_discord_trade_proof.py

Trade-proof semantics at the Discord boundary. INCOMPLETE (optional checks
with no implementation) is a caveat rendered in the alert, not a block —
blocking on it made every options alert unreachable from #526 onward. An
explicit negative verdict still fails closed.
"""
from __future__ import annotations

from alert_ranker.discord import (
    _trade_proof_block_reason,
    _unchecked_text,
    build_discord_payload,
)
from alert_ranker.scorer import ScoreResult


def _result(**raw) -> ScoreResult:
    base = {
        "ticker": "AAPL",
        "setup_status": "TRIGGERED",
        "paper_policy_id": "OPTIONS_PAPER_V1",
        "paper_policy_status": "VALID",
        "contract": "AAPL261023C00130000",
        "price": 128.0,
        "stop": 125.0,
        "target": 133.0,
    }
    base.update(raw)
    return ScoreResult("AAPL", "LONG", 8, "strat_212", {"strat_pattern": 3}, base, "")


def test_incomplete_trade_proof_does_not_block():
    r = _result(
        trade_proof_status="INCOMPLETE",
        trade_proof_reason="event_risk_unavailable;flip_context_unavailable",
    )
    assert _trade_proof_block_reason(r) == ""


def test_valid_and_absent_trade_proof_do_not_block():
    assert _trade_proof_block_reason(_result(trade_proof_status="VALID")) == ""
    assert _trade_proof_block_reason(_result()) == ""


def test_negative_trade_proof_verdict_still_blocks():
    for status in ("FAILED", "BLOCKED", "INVALID", "REJECTED"):
        r = _result(trade_proof_status=status, trade_proof_reason="earnings_in_2d")
        assert _trade_proof_block_reason(r) == f"trade_proof_{status.lower()}:earnings_in_2d"


def test_unchecked_caveat_rendered_only_when_incomplete():
    r = _result(
        trade_proof_status="INCOMPLETE",
        trade_proof_reason="event_risk_unavailable;flip_context_unavailable",
    )
    assert _unchecked_text(r) == "Not checked: event risk, flip context"
    assert _unchecked_text(_result(trade_proof_status="VALID")) == "N/A"

    fields = {f["name"]: f["value"] for f in build_discord_payload(r)["embeds"][0]["fields"]}
    assert fields["Unchecked"] == "Not checked: event risk, flip context"
    valid_fields = {
        f["name"] for f in build_discord_payload(_result(trade_proof_status="VALID"))["embeds"][0]["fields"]
    }
    assert "Unchecked" not in valid_fields  # N/A fields are dropped from the embed
