"""Phase-1 advisory contract shortlist tests."""

from __future__ import annotations

import ast
import math
from dataclasses import replace
from pathlib import Path

import options_manager.contracts.selector as selector_module
from options_manager.contracts import (
    ContractCandidate,
    ContractSelectionPolicy,
    ContractSelectionRequest,
    shortlist_contracts,
)


def _policy(**overrides) -> ContractSelectionPolicy:
    fields = dict(
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
    fields.update(overrides)
    return ContractSelectionPolicy(**fields)


def _candidate(symbol="AAPL261016C00100000", **overrides) -> ContractCandidate:
    fields = dict(
        symbol=symbol,
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
    fields.update(overrides)
    return ContractCandidate(**fields)


def _request(*candidates, policy=None, **overrides) -> ContractSelectionRequest:
    fields = dict(
        ticker="AAPL",
        direction="CALL",
        candidates=tuple(candidates or (_candidate(),)),
        policy=policy or _policy(),
    )
    fields.update(overrides)
    return ContractSelectionRequest(**fields)


def test_single_clean_candidate_is_sole_eligible_not_execution_instruction():
    result = shortlist_contracts(_request(_candidate()))
    assert result.status == "SOLE_ELIGIBLE"
    assert result.selected is not None
    assert result.selected.candidate.symbol == "AAPL261016C00100000"
    assert result.selected.validator_status == "VALID"
    assert result.blocking_reasons == ()


def test_multiple_clean_contracts_remain_shortlist_without_explicit_preferred_delta():
    first = _candidate("AAPL-C100", delta=0.48, strike=100.0)
    second = _candidate("AAPL-C102", delta=0.55, strike=102.0)
    result = shortlist_contracts(_request(first, second))
    assert result.status == "SHORTLIST"
    assert result.selected is None
    assert {item.candidate.symbol for item in result.eligible} == {"AAPL-C100", "AAPL-C102"}


def test_explicit_preferred_delta_can_choose_transparent_shadow_candidate():
    first = _candidate("AAPL-C100", delta=0.48, strike=100.0)
    second = _candidate("AAPL-C102", delta=0.56, strike=102.0)
    third = _candidate("AAPL-C104", delta=0.63, strike=104.0)
    result = shortlist_contracts(
        _request(first, second, third, policy=_policy(preferred_abs_delta=0.55))
    )
    assert result.status == "PREFERRED_CANDIDATE"
    assert result.selected is not None
    assert result.selected.candidate.symbol == "AAPL-C102"
    assert math.isclose(result.selected.delta_distance, 0.01)


def test_delta_range_is_explicit_and_fails_closed():
    low_delta = _candidate(delta=0.12)
    result = shortlist_contracts(_request(low_delta))
    assert result.status == "NO_ELIGIBLE"
    assert result.rejected[0].reason_code == "delta_out_of_range"


def test_missing_theta_and_iv_are_rejected_by_existing_contract_authority():
    result = shortlist_contracts(_request(_candidate(theta=None, iv=None)))
    assert result.status == "NO_ELIGIBLE"
    assert result.rejected[0].reason_code in {"missing_theta", "missing_iv"}
    assert result.rejected[0].validator_status == "INVALID"


def test_missing_event_risk_is_not_silently_assumed_clear():
    result = shortlist_contracts(_request(_candidate(event_risk=None)))
    assert result.status == "NO_ELIGIBLE"
    assert result.rejected[0].reason_code == "event_risk_missing"


def test_high_event_risk_is_rejected_by_existing_validator():
    result = shortlist_contracts(_request(_candidate(event_risk="HIGH")))
    assert result.status == "NO_ELIGIBLE"
    assert result.rejected[0].reason_code == "event_risk_high"


def test_low_liquidity_and_wide_spread_do_not_pass_quietly():
    low_volume = shortlist_contracts(_request(_candidate(volume=50)))
    assert low_volume.status == "NO_ELIGIBLE"
    assert low_volume.rejected[0].reason_code == "volume_too_low"

    wide = shortlist_contracts(_request(_candidate(bid=1.70, ask=2.30)))
    assert wide.status == "NO_ELIGIBLE"
    assert wide.rejected[0].reason_code == "spread_too_wide"


def test_missing_or_crossed_quote_has_no_midpoint_fallback():
    missing = shortlist_contracts(_request(_candidate(bid=None, ask=2.05)))
    assert missing.status == "NO_ELIGIBLE"
    assert missing.rejected[0].reason_code == "missing_spread_percent"

    crossed = shortlist_contracts(_request(_candidate(bid=2.05, ask=2.00)))
    assert crossed.status == "NO_ELIGIBLE"
    assert crossed.rejected[0].reason_code == "missing_spread_percent"


def test_caution_only_contracts_are_never_auto_selected():
    # ~9% spread is within the explicit 10% cap but deliberately lands in the
    # existing validator's near-limit CAUTION band.
    caution = _candidate(bid=1.91, ask=2.09, volume=1000, open_interest=2000)
    result = shortlist_contracts(_request(caution))
    assert result.status == "CAUTION_ONLY"
    assert result.selected is None
    assert result.eligible[0].validator_status == "CAUTION"
    assert result.warnings


def test_clean_candidate_outranks_caution_candidate_even_if_caution_delta_is_closer():
    clean = _candidate("AAPL-C100", delta=0.52, bid=1.95, ask=2.05)
    caution = _candidate("AAPL-C101", delta=0.55, bid=1.91, ask=2.09, strike=101.0)
    result = shortlist_contracts(
        _request(clean, caution, policy=_policy(preferred_abs_delta=0.55))
    )
    assert result.status == "SOLE_ELIGIBLE"
    assert result.selected is not None
    assert result.selected.candidate.symbol == "AAPL-C100"
    assert len(result.eligible) == 2


def test_wrong_ticker_or_direction_cannot_enter_shortlist():
    wrong_ticker = shortlist_contracts(_request(_candidate(ticker="MSFT")))
    assert wrong_ticker.status == "NO_ELIGIBLE"
    assert wrong_ticker.rejected[0].reason_code == "ticker_mismatch"

    wrong_direction = shortlist_contracts(_request(_candidate(direction="PUT")))
    assert wrong_direction.status == "NO_ELIGIBLE"
    assert wrong_direction.rejected[0].reason_code == "direction_mismatch"


def test_policy_has_no_silent_numeric_fallbacks():
    invalid = shortlist_contracts(
        _request(_candidate(), policy=_policy(max_spread_percent=math.inf))
    )
    assert invalid.status == "INVALID_REQUEST"
    assert any("max_spread_percent" in reason for reason in invalid.blocking_reasons)

    bad_delta = shortlist_contracts(
        _request(_candidate(), policy=_policy(min_abs_delta=0.80, max_abs_delta=0.70))
    )
    assert bad_delta.status == "INVALID_REQUEST"
    assert any("delta range" in reason for reason in bad_delta.blocking_reasons)


def test_empty_candidate_set_fails_closed():
    result = shortlist_contracts(
        ContractSelectionRequest(
            ticker="AAPL", direction="CALL", candidates=(), policy=_policy()
        )
    )
    assert result.status == "INVALID_REQUEST"
    assert "at least one contract candidate is required" in result.blocking_reasons


def test_shortlist_module_is_pure_and_has_no_provider_broker_or_execution_imports():
    tree = ast.parse(Path(selector_module.__file__).read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    forbidden = (
        "alert_ranker",
        "httpx",
        "requests",
        "socket",
        "webhook",
        "discord",
        "broker",
        "execution",
        "robinhood",
        "alpaca",
        "public",
        "tastytrade",
    )
    for module in imports:
        lowered = module.lower()
        assert not any(fragment in lowered for fragment in forbidden), module
