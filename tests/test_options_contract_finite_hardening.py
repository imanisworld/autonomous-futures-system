"""Proof that NaN / inf cannot pass either shared contract validator.

Before this hardening, evaluate_contract_constraints() returned VALID for a
NaN or inf in most numeric inputs (the None checks passed and every
comparison against NaN is False), and evaluate_contract_quality() returned
WARN for a NaN strike/premium/bid/ask/spread for the same reason.
"""

from __future__ import annotations

import math

import pytest

from options_manager.contracts import ContractConstraintsInputs, evaluate_contract_constraints
from options_manager.validation.contract_quality_gate import (
    ContractQualityInput,
    GateVerdict,
    evaluate_contract_quality,
)

_NON_FINITE = (float("nan"), float("inf"), float("-inf"))


def _constraints(**overrides):
    base = dict(
        direction="CALL",
        ticker="AAPL",
        expiration="2026-10-16",
        dte=44,
        strike=100.0,
        premium=2.0,
        bid=1.95,
        ask=2.05,
        spread_percent=5.0,
        volume=1000,
        open_interest=2000,
        delta=0.5,
        theta=-0.03,
        iv=0.3,
        max_premium=3.0,
        max_spread_percent=10.0,
        min_volume=100,
        min_open_interest=500,
        min_dte=14,
        max_theta_abs=0.10,
        earnings_risk="NONE",
        event_risk="NONE",
    )
    base.update(overrides)
    return ContractConstraintsInputs(**base)


def _quality(**overrides):
    base = dict(
        ticker="AAPL",
        direction="CALL",
        expiration="2026-10-16",
        strike=100.0,
        premium=2.0,
        bid=1.95,
        ask=2.05,
        spread_percent=5.0,
        volume=1000,
        open_interest=2000,
        dte=44,
        max_contracts=1,
        max_dollar_risk=300.0,
        distance_to_target=3.0,
        iv_event_risk="none",
        theta_risk="none",
    )
    base.update(overrides)
    return ContractQualityInput(**base)


def test_baselines_are_clean():
    assert evaluate_contract_constraints(_constraints()).status == "VALID"
    assert evaluate_contract_quality(_quality()).verdict is not GateVerdict.BLOCK


@pytest.mark.parametrize(
    "field",
    [
        "dte",
        "strike",
        "premium",
        "bid",
        "ask",
        "spread_percent",
        "volume",
        "open_interest",
        "delta",
        "theta",
        "iv",
        "max_premium",
        "max_spread_percent",
        "min_volume",
        "min_open_interest",
        "min_dte",
        "max_theta_abs",
    ],
)
@pytest.mark.parametrize("value", _NON_FINITE, ids=["nan", "inf", "-inf"])
def test_contract_constraints_reject_non_finite(field, value):
    result = evaluate_contract_constraints(_constraints(**{field: value}))
    assert result.status == "INVALID"
    assert result.confirmed is False
    assert result.reason_code == f"non_finite_{field}"


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
@pytest.mark.parametrize("value", _NON_FINITE, ids=["nan", "inf", "-inf"])
def test_contract_quality_gate_blocks_non_finite(field, value):
    result = evaluate_contract_quality(_quality(**{field: value}))
    assert result.verdict is GateVerdict.BLOCK
    assert f"non-finite {field}" in result.blocking_reasons


def test_finite_values_are_unaffected():
    assert math.isfinite(_constraints().premium)
    assert evaluate_contract_constraints(_constraints(premium=2.5)).status == "VALID"
    assert evaluate_contract_quality(_quality(premium=2.5)).verdict is not GateVerdict.BLOCK
