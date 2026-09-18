from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from options_manager.quotes.massive_historical import (
    HistoricalOptionIdentity,
    MASSIVE_HISTORICAL_QUOTE_SOURCE,
    normalize_massive_historical_quote,
    sip_timestamp_ns_to_iso8601,
)
from options_manager.quotes.retention import retain_quote, retention_rule_from_mapping

RULE_PATH = Path("options_manager/quotes/quote_retention_rule_v2.json")


def _rule():
    raw = RULE_PATH.read_bytes()
    return retention_rule_from_mapping(json.loads(raw)), hashlib.sha256(raw).hexdigest()


def _identity(**overrides):
    values = dict(
        contract_id="O:AMZN261120C00260000",
        underlying="AMZN",
        expiration="2026-11-20",
        strike=260.0,
        right="CALL",
    )
    values.update(overrides)
    return HistoricalOptionIdentity(**values)


def _quote(**overrides):
    values = dict(
        bid_price=13.75,
        ask_price=14.00,
        sip_timestamp=1788967073207047867,
    )
    values.update(overrides)
    return values


def test_nanosecond_timestamp_is_preserved_in_canonical_iso_text():
    assert sip_timestamp_ns_to_iso8601(1788967073207047867) == (
        "2026-09-09T15:17:53.207047867+00:00"
    )


def test_real_amzn_sample_normalizes_only_historical_quote_facts():
    normalized = normalize_massive_historical_quote(
        identity=_identity(),
        quote_row=_quote(),
        decision_ts="2026-09-09T15:17:57+00:00",
    )
    assert normalized.contract_id == "O:AMZN261120C00260000"
    assert normalized.underlying == "AMZN"
    assert normalized.expiration == "2026-11-20"
    assert normalized.strike == 260.0
    assert normalized.right == "CALL"
    assert normalized.bid == 13.75
    assert normalized.ask == 14.0
    assert normalized.quote_ts == "2026-09-09T15:17:53.207047867+00:00"
    assert normalized.source == MASSIVE_HISTORICAL_QUOTE_SOURCE
    assert normalized.volume is None
    assert normalized.open_interest is None
    assert normalized.delta is None
    assert normalized.iv is None


def test_massive_quote_alone_cannot_become_retention_ok():
    rule, rule_sha = _rule()
    normalized = normalize_massive_historical_quote(
        identity=_identity(),
        quote_row=_quote(),
        decision_ts="2026-09-09T15:17:57+00:00",
    )
    record = retain_quote(normalized, rule=rule, rule_sha256=rule_sha)
    assert record.status == "MISSING"
    assert record.reason_code == "required_field_missing"
    assert set(record.missing_fields) == {"delta", "iv", "open_interest", "volume"}


def test_quote_payload_cannot_smuggle_current_analytics_into_history():
    row = _quote(
        volume=999999,
        open_interest=12528,
        delta=0.99,
        iv=9.99,
    )
    normalized = normalize_massive_historical_quote(
        identity=_identity(),
        quote_row=row,
        decision_ts="2026-09-09T15:17:57+00:00",
    )
    assert normalized.volume is None
    assert normalized.open_interest is None
    assert normalized.delta is None
    assert normalized.iv is None


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, "1788967073207047867"])
def test_invalid_sip_timestamp_type_or_value_fails_closed(value):
    with pytest.raises(ValueError, match="sip_timestamp"):
        sip_timestamp_ns_to_iso8601(value)


def test_missing_sip_timestamp_remains_missing_for_retention():
    rule, rule_sha = _rule()
    normalized = normalize_massive_historical_quote(
        identity=_identity(),
        quote_row=_quote(sip_timestamp=None),
        decision_ts="2026-09-09T15:17:57+00:00",
    )
    record = retain_quote(normalized, rule=rule, rule_sha256=rule_sha)
    assert record.status == "MISSING"
    assert "quote_ts" in record.missing_fields


@pytest.mark.parametrize(
    "identity",
    [
        _identity(contract_id=""),
        _identity(underlying=""),
        _identity(expiration=""),
        _identity(strike=0),
        _identity(strike=float("nan")),
        _identity(right="UNKNOWN"),
    ],
)
def test_invalid_frozen_contract_identity_fails_closed(identity):
    with pytest.raises(ValueError):
        normalize_massive_historical_quote(
            identity=identity,
            quote_row=_quote(),
            decision_ts="2026-09-09T15:17:57+00:00",
        )
