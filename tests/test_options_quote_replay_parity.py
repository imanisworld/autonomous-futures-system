"""Golden parity fixtures for retained option-quote replay/forward consumption."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from alert_ranker.paper_v1 import choose_contract
from options_manager.quotes.replay import contract_market_snapshot_from_retained_quote, executable_quote_projection, quote_record_from_json_line
from options_manager.quotes.retention import QuoteRetentionInput, quote_record_json, retain_quote, retention_rule_from_mapping
from options_manager.config import OptionsManagerConfig
from options_manager.contract_quality import ContractQualityResult
from options_manager.models import OptionTradePacket
from options_manager.paper_sim import simulate_round_trip
from options_manager.risk_gate import RiskGateResult

RULE_PATH = Path("options_manager/quotes/quote_retention_rule_v1.json")


def _payload(**overrides) -> bytes:
    raw = json.loads(RULE_PATH.read_text())
    rule = retention_rule_from_mapping(raw)
    rule_sha = hashlib.sha256(RULE_PATH.read_bytes()).hexdigest()
    values = dict(contract_id="SPY261120C00550000", underlying="SPY", expiration="2026-11-20", strike=550.0, right="CALL", bid=4.8, ask=5.0, quote_ts="2026-09-18T14:00:00+00:00", decision_ts="2026-09-18T14:01:00+00:00", source="fixture:option_chain_snapshot", volume=500, open_interest=1500, delta=0.5, iv=0.25)
    values.update(overrides)
    return quote_record_json(retain_quote(QuoteRetentionInput(**values), rule=rule, rule_sha256=rule_sha)).encode()


def test_replay_and_forward_use_identical_serialized_projection():
    frozen = _payload()
    replay = executable_quote_projection(frozen)
    forward = executable_quote_projection(frozen)
    assert replay == forward
    assert replay["contract_id"] == "SPY261120C00550000"
    assert replay["quote_ts"] == "2026-09-18T14:00:00+00:00"
    assert replay["source"] == "fixture:option_chain_snapshot"
    assert replay["status"] == "OK"
    assert replay["reason_code"] == "quote_retained"
    assert replay["bid"] == 4.8
    assert replay["ask"] == 5.0
    assert replay["executable"] is True


def test_replay_and_forward_match_through_actual_contract_selector():
    frozen = _payload()

    def select(payload: bytes):
        projected = executable_quote_projection(payload)
        contract = SimpleNamespace(**projected)
        return choose_contract([contract], option_type="CALL", underlying_price=551.0)

    replay = select(frozen)
    forward = select(frozen)
    assert replay == forward
    assert replay.valid
    assert replay.contract is not None
    assert replay.contract.symbol == "SPY261120C00550000"
    assert replay.contract.bid == 4.8
    assert replay.contract.ask == 5.0
    assert replay.contract.quote_timestamp == "2026-09-18T14:00:00+00:00"
    assert replay.contract.quote_source == "fixture:option_chain_snapshot"


def test_no_retained_quote_record_fails_closed_identically_through_selector():
    """Absence is not synthesized into a MISSING quote or reconstructed quote."""
    replay = choose_contract([], option_type="CALL", underlying_price=551.0)
    forward = choose_contract([], option_type="CALL", underlying_price=551.0)
    assert replay == forward
    assert replay.status == "DATA_INVALID"
    assert replay.reason == "no_liquid_contract"
    assert replay.contract is None


@pytest.mark.parametrize("overrides,expected_status", [
    ({"quote_ts": None}, "MISSING"),
    ({"quote_ts": "2026-09-18T13:40:00+00:00"}, "STALE"),
    ({"bid": 1.0, "ask": 2.0}, "WIDE_SPREAD"),
])
def test_non_ok_frozen_records_fail_closed_identically(overrides, expected_status):
    frozen = _payload(**overrides)
    replay = executable_quote_projection(frozen)
    forward = executable_quote_projection(frozen)
    assert replay == forward
    assert replay["status"] == expected_status
    assert replay["executable"] is False
    assert replay["bid"] is None
    assert replay["ask"] is None

    # The actual paper selector must also refuse the non-executable projection.
    decision = choose_contract(
        [SimpleNamespace(**replay)], option_type="CALL", underlying_price=551.0
    )
    assert decision.status == "DATA_INVALID"
    assert "missing_or_invalid_bid_ask" in decision.reason


def test_adapter_rejects_schema_drift_instead_of_reconstructing():
    row = json.loads(_payload())
    del row["quote_ts"]
    with pytest.raises(ValueError, match="schema mismatch"):
        quote_record_from_json_line((json.dumps(row) + "\n").encode())


def _fill_result(entry_payload: bytes, exit_payload: bytes):
    packet = OptionTradePacket(
        ticker="SPY", direction="CALL", entry_price=551.0, price_target=560.0, signa_score=80,
        signa_grade="A", signa_bias="BULLISH", gex_regime="NEUTRAL", gex_wall_above=None,
        gex_wall_below=None, contract_strike=550.0, contract_expiry=date(2026, 11, 20),
        max_premium=10.0, max_contracts=1, account_tag="parity_fixture",
        source="frozen_quote_fixture", created_at=datetime(2026, 9, 18, 14, 1, tzinfo=timezone.utc),
        status="PENDING", rejection_reason=None)
    risk = RiskGateResult(approved=True, status="APPROVED", failed_rule=None, reason="", warnings=[])
    quality = ContractQualityResult(approved=True, status="APPROVED", failed_rule=None, reason="", warnings=[])
    cfg = replace(OptionsManagerConfig(), paper_sim_require_approved_risk=False, paper_sim_require_approved_quality=False, paper_sim_entry_fill="ASK", paper_sim_exit_fill="BID", paper_sim_slippage_percent=0.0, paper_sim_per_contract_fee=0.0)
    return simulate_round_trip(packet, contract_market_snapshot_from_retained_quote(entry_payload), contract_market_snapshot_from_retained_quote(exit_payload), risk, quality, cfg)

def test_replay_and_forward_match_through_actual_executable_fill_consumer():
    entry = _payload(bid=4.8, ask=5.0)
    exit_quote = _payload(bid=5.8, ask=6.0, quote_ts="2026-09-18T14:02:00+00:00", decision_ts="2026-09-18T14:03:00+00:00")
    replay = _fill_result(entry, exit_quote)
    forward = _fill_result(entry, exit_quote)
    assert replay == forward
    assert replay.status == "SIMULATED"
    assert replay.simulated_entry_price == 5.0
    assert replay.simulated_exit_price == 5.8

@pytest.mark.parametrize("overrides,expected_status", [({"quote_ts": None}, "MISSING"), ({"quote_ts": "2026-09-18T13:40:00+00:00"}, "STALE"), ({"bid": 1.0, "ask": 2.0}, "WIDE_SPREAD")])
def test_non_ok_retained_entry_quote_fails_closed_through_fill_consumer(overrides, expected_status):
    frozen = _payload(**overrides)
    assert quote_record_from_json_line(frozen).status == expected_status
    result = _fill_result(frozen, _payload())
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "entry_snapshot"
    assert "ask is missing" in result.reason
