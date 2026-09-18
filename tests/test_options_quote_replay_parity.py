"""Golden parity fixtures for retained option-quote replay/forward consumption."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from options_manager.quotes.replay import executable_quote_projection, quote_record_from_json_line
from options_manager.quotes.retention import QuoteRetentionInput, quote_record_json, retain_quote, retention_rule_from_mapping

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
    assert replay == forward == {
        "contract_id": "SPY261120C00550000",
        "decision_ts": "2026-09-18T14:01:00+00:00",
        "quote_ts": "2026-09-18T14:00:00+00:00",
        "source": "fixture:option_chain_snapshot",
        "status": "OK",
        "reason_code": "quote_retained",
        "bid": 4.8,
        "ask": 5.0,
        "executable": True,
    }


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


def test_adapter_rejects_schema_drift_instead_of_reconstructing():
    row = json.loads(_payload())
    del row["quote_ts"]
    with pytest.raises(ValueError, match="schema mismatch"):
        quote_record_from_json_line((json.dumps(row) + "\n").encode())
