"""Malformed/nonfinite input guards for the advisory contract shortlist."""

from __future__ import annotations

import math

from options_manager.contracts import (
    ContractCandidate,
    ContractSelectionPolicy,
    ContractSelectionRequest,
    shortlist_contracts,
)


def _policy(**overrides):
    values = dict(
        max_premium_per_share=3.0,
        max_spread_percent=10.0,
        min_volume=100,
        min_open_interest=500,
        min_dte=14,
        max_theta_abs=0.10,
        min_abs_delta=0.35,
        max_abs_delta=0.70,
        preferred_abs_delta=None,
    )
    values.update(overrides)
    return ContractSelectionPolicy(**values)


def _candidate(**overrides):
    values = dict(
        symbol="AAPL-C100",
        ticker="AAPL",
        direction="CALL",
        expiration="2026-10-16",
        dte=45,
        strike=100.0,
        premium=2.0,
        bid=1.95,
        ask=2.05,
        volume=1000,
        open_interest=2000,
        delta=0.50,
        theta=-0.03,
        iv=0.40,
        earnings_risk="NONE",
        event_risk="NONE",
    )
    values.update(overrides)
    return ContractCandidate(**values)


def _run(candidate, policy=None):
    return shortlist_contracts(
        ContractSelectionRequest(
            ticker="AAPL",
            direction="CALL",
            candidates=(candidate,),
            policy=policy or _policy(),
        )
    )


def test_nonfinite_market_data_cannot_bypass_numeric_checks():
    for field in ("strike", "premium", "bid", "ask", "delta", "theta", "iv"):
        result = _run(_candidate(**{field: math.nan}))
        assert result.status == "NO_ELIGIBLE", field
        assert result.rejected[0].reason_code == f"invalid_{field}", field


def test_nonfinite_integer_like_market_fields_fail_closed_too():
    for field in ("dte", "volume", "open_interest"):
        result = _run(_candidate(**{field: math.inf}))
        assert result.status == "NO_ELIGIBLE", field
        assert result.rejected[0].reason_code == f"invalid_{field}", field


def test_lowercase_or_unknown_event_risk_cannot_slip_past_uppercase_validator_contract():
    lowercase = _run(_candidate(event_risk="high"))
    assert lowercase.status == "NO_ELIGIBLE"
    assert lowercase.rejected[0].reason_code == "event_risk_invalid"

    unknown = _run(_candidate(earnings_risk="UNKNOWN"))
    assert unknown.status == "NO_ELIGIBLE"
    assert unknown.rejected[0].reason_code == "event_risk_invalid"


def test_malformed_delta_policy_with_preference_returns_invalid_request_not_exception():
    policy = _policy(
        min_abs_delta="bad",
        max_abs_delta=0.70,
        preferred_abs_delta=0.50,
    )
    result = _run(_candidate(), policy=policy)
    assert result.status == "INVALID_REQUEST"
    assert any("delta range" in reason for reason in result.blocking_reasons)
    assert any("preferred_abs_delta" in reason for reason in result.blocking_reasons)
