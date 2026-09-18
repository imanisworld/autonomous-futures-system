"""Canonical quote-retention bridge tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from options_manager.quotes import retention_rule_from_mapping
from options_manager.validation.contract_quality_gate import ContractQualityInput
from options_manager.validation.quote_retention_gate import check_quote_retention_intake

RULE_PATH = Path("options_manager/quotes/quote_retention_rule_v1.json")


def _rule():
    return retention_rule_from_mapping(json.loads(RULE_PATH.read_text()))


def _sha():
    return hashlib.sha256(RULE_PATH.read_bytes()).hexdigest()


def _contract(**overrides):
    values = dict(
        ticker="ORCL",
        direction="CALL",
        expiration="2026-11-07",
        strike=110.0,
        premium=2.10,
        bid=2.05,
        ask=2.15,
        spread_percent=4.8,
        volume=800,
        open_interest=3000,
        dte=50,
        max_contracts=1,
        max_dollar_risk=100.0,
        distance_to_target=5.0,
        iv_event_risk="none",
        theta_risk="low",
        premium_stop=1.60,
        trade_style="swing",
    )
    values.update(overrides)
    return ContractQualityInput(**values)


def _payload(**overrides):
    values = dict(
        contract_id="ORCL261107C00110000",
        underlying="ORCL",
        expiration="2026-11-07",
        strike=110.0,
        right="CALL",
        bid=2.05,
        ask=2.15,
        quote_ts="2026-09-18T14:00:00+00:00",
        decision_ts="2026-09-18T14:01:00+00:00",
        source="fixture:option_chain_snapshot",
        volume=800,
        open_interest=3000,
        delta=0.50,
        iv=0.30,
    )
    values.update(overrides)
    return values


def _check(payload=None, contract=None):
    return check_quote_retention_intake(
        _payload() if payload is None else payload,
        contract=_contract() if contract is None else contract,
        rule=_rule(),
        rule_sha256=_sha(),
    )


def test_ok_quote_matching_contract_approves():
    result = _check()
    assert result.approved is True
    assert result.record is not None
    assert result.record.status == "OK"


def test_stale_quote_blocks():
    result = _check(_payload(quote_ts="2026-09-18T13:00:00+00:00"))
    assert result.approved is False
    assert result.record.status == "STALE"
    assert any("quote status STALE" in reason for reason in result.blocking_reasons)


def test_missing_quote_timestamp_blocks_and_record_survives_for_journal():
    result = _check(_payload(quote_ts=None))
    assert result.approved is False
    assert result.record.status == "MISSING"
    assert "quote_ts" in result.record.missing_fields


def test_quote_contract_strike_mismatch_blocks():
    result = _check(_payload(strike=111.0))
    assert result.approved is False
    assert any("mismatch for strike" in reason for reason in result.blocking_reasons)


def test_quote_contract_bid_mismatch_blocks():
    result = _check(_payload(bid=1.95))
    assert result.approved is False
    assert any("mismatch for bid" in reason for reason in result.blocking_reasons)


def test_missing_contract_record_blocks_even_when_quote_is_ok():
    result = check_quote_retention_intake(
        _payload(),
        contract=None,
        rule=_rule(),
        rule_sha256=_sha(),
    )
    assert result.approved is False
    assert result.record.status == "OK"
    assert any("contract quality record unavailable" in r for r in result.blocking_reasons)
