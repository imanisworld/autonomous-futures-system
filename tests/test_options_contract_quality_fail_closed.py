"""Fail-open regressions in the canonical contract quality gate.

NaN compares False against every threshold, so before this fix a NaN spread,
premium, bid/ask, or target distance passed the gate quietly. A caller could
also understate ``spread_percent`` relative to its own bid/ask.
"""

import pytest

from options_manager.validation.contract_quality_gate import (
    ContractQualityInput,
    GateVerdict,
    check_contract_quality_intake,
    evaluate_contract_quality,
)

NAN = float("nan")
INF = float("inf")


def _payload(**overrides):
    payload = dict(
        ticker="ORCL",
        direction="CALL",
        expiration="2026-12-18",
        strike=210.0,
        premium=2.10,
        bid=2.05,
        ask=2.15,
        spread_percent=4.8,
        volume=800,
        open_interest=3000,
        dte=60,
        max_contracts=2,
        max_dollar_risk=150.0,
        distance_to_target=5.0,
        iv_event_risk="none",
        theta_risk="low",
        premium_stop=1.60,
        trade_style="swing",
    )
    payload.update(overrides)
    return payload


def _contract(**overrides):
    return ContractQualityInput(**_payload(**overrides))


def test_baseline_contract_passes():
    assert evaluate_contract_quality(_contract()).verdict == GateVerdict.PASS


@pytest.mark.parametrize(
    "field",
    [
        "strike",
        "premium",
        "bid",
        "ask",
        "spread_percent",
        "max_dollar_risk",
        "distance_to_target",
        "premium_stop",
    ],
)
@pytest.mark.parametrize("bad", [NAN, INF, -INF])
def test_non_finite_numbers_block(field, bad):
    result = evaluate_contract_quality(_contract(**{field: bad}))
    assert result.verdict == GateVerdict.BLOCK
    assert any(field in reason and "finite" in reason for reason in result.blocking_reasons)


@pytest.mark.parametrize("field", ["spread_percent", "premium", "distance_to_target", "bid"])
def test_intake_nan_string_blocks(field):
    """float('nan') coerces cleanly, so intake alone did not catch it."""
    result = check_contract_quality_intake(_payload(**{field: "nan"}))
    assert result.verdict == GateVerdict.BLOCK


def test_bool_in_numeric_field_blocks():
    result = evaluate_contract_quality(_contract(volume=True))
    assert result.verdict == GateVerdict.BLOCK


def test_understated_spread_is_judged_on_the_quote():
    # bid 1.00 / ask 1.40 is a 33% spread; caller claims 4.8%.
    result = evaluate_contract_quality(_contract(bid=1.00, ask=1.40, premium=1.20, premium_stop=0.90))
    assert result.verdict == GateVerdict.BLOCK
    assert any("spread too wide" in reason for reason in result.blocking_reasons)


def test_overstated_spread_still_blocks():
    result = evaluate_contract_quality(_contract(spread_percent=25.0))
    assert result.verdict == GateVerdict.BLOCK


def test_crossed_market_blocks():
    result = evaluate_contract_quality(_contract(bid=2.20, ask=2.15))
    assert result.verdict == GateVerdict.BLOCK
    assert any("crossed market" in reason for reason in result.blocking_reasons)


def test_low_liquidity_never_passes_quietly():
    for overrides in ({"volume": 5}, {"open_interest": 10}):
        assert evaluate_contract_quality(_contract(**overrides)).verdict == GateVerdict.BLOCK


@pytest.mark.parametrize(
    "dte,exceptional,expected",
    [
        (60, False, GateVerdict.PASS),
        (45, False, GateVerdict.PASS),
        (30, False, GateVerdict.WARN),
        (14, False, GateVerdict.WARN),
        (7, False, GateVerdict.BLOCK),
        (0, False, GateVerdict.BLOCK),
        (0, True, GateVerdict.WARN),
    ],
)
def test_swing_dte_policy(dte, exceptional, expected):
    result = evaluate_contract_quality(_contract(dte=dte, dte_exceptional=exceptional))
    assert result.verdict == expected


def test_target_beyond_supplied_expected_move_warns_not_blocks():
    result = evaluate_contract_quality(_contract(distance_to_target=9.0, expected_move_percent=4.0))
    assert result.verdict == GateVerdict.WARN
    assert any("target feasibility" in w for w in result.warnings)


def test_target_within_expected_move_and_absent_move_are_silent():
    assert evaluate_contract_quality(
        _contract(distance_to_target=3.0, expected_move_percent=4.0)
    ).verdict == GateVerdict.PASS
    assert evaluate_contract_quality(_contract()).verdict == GateVerdict.PASS


def test_intake_coerces_expected_move_and_rejects_garbage():
    ok = check_contract_quality_intake(_payload(expected_move_percent="4.0", distance_to_target=9.0))
    assert ok.verdict == GateVerdict.WARN
    bad = check_contract_quality_intake(_payload(expected_move_percent="lots"))
    assert bad.verdict == GateVerdict.BLOCK


# ── integration pass: every required field fails closed when missing ────────

REQUIRED = [
    "ticker", "direction", "expiration", "strike", "premium", "bid", "ask",
    "spread_percent", "volume", "open_interest", "dte", "max_contracts",
    "max_dollar_risk", "distance_to_target", "iv_event_risk", "theta_risk",
]


@pytest.mark.parametrize("field", REQUIRED)
@pytest.mark.parametrize("missing", [None, ""])
def test_intake_missing_required_field_blocks(field, missing):
    payload = _payload()
    payload[field] = missing
    result = check_contract_quality_intake(payload)
    assert result.verdict == GateVerdict.BLOCK
    assert any(field in reason for reason in result.blocking_reasons)


@pytest.mark.parametrize("field", REQUIRED)
def test_intake_absent_required_field_blocks(field):
    payload = _payload()
    del payload[field]
    assert check_contract_quality_intake(payload).verdict == GateVerdict.BLOCK


@pytest.mark.parametrize(
    "overrides",
    [
        {"premium": 0}, {"bid": 0}, {"ask": 0}, {"volume": 0}, {"open_interest": 0},
        {"dte": -1}, {"strike": 0}, {"max_contracts": 0}, {"max_dollar_risk": 0},
    ],
)
def test_zero_or_negative_contract_facts_block(overrides):
    assert evaluate_contract_quality(_contract(**overrides)).verdict == GateVerdict.BLOCK


@pytest.mark.parametrize(
    "overrides",
    [
        {"direction": "STRADDLE"},
        {"iv_event_risk": "extreme"},
        {"theta_risk": "?"},
        {"trade_style": "lottery"},
        {"dte_exceptional": "maybe"},
        {"dte": "soon"},
    ],
)
def test_unsupported_contract_state_blocks(overrides):
    assert check_contract_quality_intake(_payload(**overrides)).verdict == GateVerdict.BLOCK


def test_non_mapping_payload_blocks():
    for payload in (None, [], "SPY 500C", 42):
        assert check_contract_quality_intake(payload).verdict == GateVerdict.BLOCK


def test_gate_is_advisory_only_and_cannot_submit():
    import ast
    from pathlib import Path

    from options_manager.validation import contract_quality_gate as gate

    tree = ast.parse(Path(gate.__file__).read_text())
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
    }
    assert not {"submit_order", "place_order", "broker", "requests", "httpx"} & names
