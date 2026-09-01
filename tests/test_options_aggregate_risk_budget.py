"""The aggregate open-risk budget has no default and must be configured.

The earlier hardcoded $1,000 was never approved policy. These tests pin the
contract that replaced it: absence blocks, garbage blocks, zero and negative
block, nothing substitutes a number, and the block reaches the advisory
verdict under its own reason code rather than being mislabelled as
"risk too high". Position count stays a metric. Planned risk and full debit
stay separate.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from options_manager.config import OptionsManagerConfig, _as_budget_float
from options_manager.validation.advisory_decision import (
    AdvisoryVerdict,
    check_advisory_decision_intake,
)
from options_manager.validation.no_trade_reasons import NoTradeReason
from options_manager.validation.portfolio_risk_gate import (
    AGGREGATE_RISK_BUDGET_INVALID_CODE,
    AGGREGATE_RISK_BUDGET_MISSING_CODE,
    PortfolioRiskVerdict,
    RiskExposure,
    evaluate_portfolio_risk,
)


def _proof(**overrides) -> dict:
    payload = dict(
        ticker="ORCL",
        created_at="2026-09-01T10:00:00-04:00",
        direction="CALL",
        setup_type="2-1-2 continuation",
        timeframe="30m",
        entry_trigger="break above prior 30m high",
        underlying_invalidation="close below 102",
        premium_stop="1.60",
        target_1="108",
        target_2="112",
        expiration="2026-10-23",
        strike=110.0,
        premium=2.10,
        bid=2.05,
        ask=2.15,
        spread_percent=4.8,
        volume=800,
        open_interest=3000,
        max_contracts=2,
        max_dollar_risk=150.0,
        spy_context="aligned",
        qqq_context="aligned",
        gex_context="GEX_UNAVAILABLE",
        signa_context="observational only",
        source_references=["alert-1"],
        status="triggered",
    )
    payload.update(overrides)
    return payload


def _contract(**overrides) -> dict:
    payload = dict(
        ticker="ORCL",
        direction="CALL",
        expiration="2026-10-23",
        strike=110.0,
        premium=2.10,
        premium_stop=1.60,
        bid=2.05,
        ask=2.15,
        spread_percent=4.8,
        volume=800,
        open_interest=3000,
        dte=52,
        max_contracts=2,
        max_dollar_risk=150.0,
        distance_to_target=5.0,
        iv_event_risk="none",
        theta_risk="low",
        trade_style="swing",
    )
    payload.update(overrides)
    return payload


def _payload(**overrides) -> dict:
    payload = dict(
        proof_packet=_proof(),
        contract_quality=_contract(),
        portfolio_risk={"open_positions": []},
    )
    payload.update(overrides)
    return payload


# ─── config: unset and garbage both read as None ──────────────────────────────


def test_config_default_is_none_not_a_number():
    assert OptionsManagerConfig().max_aggregate_open_risk_dollars is None


def test_env_parsing_keeps_unset_and_invalid_apart(monkeypatch):
    # Genuinely unset or blank: the operator configured nothing.
    for raw in (None, "", "   "):
        assert _as_budget_float(raw) is None, raw
    # Supplied but unparseable: configured, invalid -- carried as nan, never None.
    for raw in ("one thousand", "garbage", "1,000", "$1000"):
        parsed = _as_budget_float(raw)
        assert parsed is not None and math.isnan(parsed), raw
    # Python's float() accepts these; they parse, and the gate rejects them.
    for raw in ("NaN", "nan", "inf", "+inf", "-inf", "Infinity"):
        parsed = _as_budget_float(raw)
        assert parsed is not None and not math.isfinite(parsed), raw
    assert _as_budget_float("1000") == 1000.0
    assert _as_budget_float("0") == 0.0
    assert _as_budget_float("-5") == -5.0

    monkeypatch.delenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", raising=False)
    assert OptionsManagerConfig.from_env().max_aggregate_open_risk_dollars is None
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "750")
    assert OptionsManagerConfig.from_env().max_aggregate_open_risk_dollars == 750.0
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "garbage")
    assert math.isnan(OptionsManagerConfig.from_env().max_aggregate_open_risk_dollars)


# ─── the gate itself is safe against non-finite values, parsing aside ─────────


def _exposure(risk: float) -> RiskExposure:
    return RiskExposure(ticker="AAPL", direction="CALL", planned_dollar_risk=risk)


@pytest.mark.parametrize("budget", (float("nan"), float("inf"), float("-inf")))
def test_direct_gate_call_with_non_finite_budget_blocks(budget):
    result = evaluate_portfolio_risk(
        open_positions=[_exposure(50.0)],
        candidate=_exposure(50.0),
        max_aggregate_open_risk_dollars=budget,
    )
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any(r.startswith(AGGREGATE_RISK_BUDGET_INVALID_CODE) for r in result.blocking_reasons)
    assert not any(r.startswith(AGGREGATE_RISK_BUDGET_MISSING_CODE) for r in result.blocking_reasons)
    # inf must not read as "unlimited": nothing about it is treated as a cap.
    assert not any("exceeds cap" in r for r in result.blocking_reasons)


def test_positive_infinity_is_not_an_unlimited_budget():
    """The specific hole: inf > 0 is True and projected > inf is always False."""
    result = evaluate_portfolio_risk(
        open_positions=[_exposure(250.0), _exposure(250.0), _exposure(250.0)],
        candidate=_exposure(250.0),
        max_aggregate_open_risk_dollars=float("inf"),
    )
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert result.projected_open_risk == 1000.0


def test_finite_positive_budget_still_evaluates_normally():
    under = evaluate_portfolio_risk(
        open_positions=[_exposure(100.0)], candidate=_exposure(100.0),
        max_aggregate_open_risk_dollars=250.0,
    )
    assert under.verdict == PortfolioRiskVerdict.PASS
    over = evaluate_portfolio_risk(
        open_positions=[_exposure(200.0)], candidate=_exposure(100.0),
        max_aggregate_open_risk_dollars=250.0,
    )
    assert over.verdict == PortfolioRiskVerdict.BLOCK
    assert any("exceeds cap $250.00" in r for r in over.blocking_reasons)


@pytest.mark.parametrize("cap", (float("nan"), float("inf")))
def test_per_trade_cap_gets_the_same_finiteness_guard(cap):
    """Same class of hole on the other parameter; the $300 value itself is untouched."""
    result = evaluate_portfolio_risk(
        open_positions=[], candidate=_exposure(10.0),
        max_trade_risk_dollars=cap, max_aggregate_open_risk_dollars=1000.0,
    )
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert "missing/invalid max_trade_risk_dollars" in result.blocking_reasons


def test_config_is_the_only_place_a_budget_can_come_from():
    cfg = replace(OptionsManagerConfig(), max_aggregate_open_risk_dollars=250.0)
    assert cfg.max_aggregate_open_risk_dollars == 250.0
    assert OptionsManagerConfig().max_aggregate_open_risk_dollars is None


# ─── advisory decision: the block propagates under its own name ──────────────


def test_missing_budget_reaches_the_advisory_verdict():
    result = check_advisory_decision_intake(_payload(), require_portfolio_risk=True)
    assert result.verdict == AdvisoryVerdict.AVOID
    assert result.portfolio_verdict == PortfolioRiskVerdict.BLOCK
    assert any(
        r.startswith(f"portfolio risk: {AGGREGATE_RISK_BUDGET_MISSING_CODE}")
        for r in result.blocking_reasons
    )
    # A configuration gap, not a risk judgment.
    assert NoTradeReason.RISK_TOO_HIGH not in result.no_trade_reasons
    assert NoTradeReason.OTHER in result.no_trade_reasons
    assert "portfolio-risk" in result.next_required_action


def test_configured_budget_lets_the_same_payload_take():
    result = check_advisory_decision_intake(
        _payload(), require_portfolio_risk=True, max_aggregate_open_risk_dollars=1000.0
    )
    assert result.verdict == AdvisoryVerdict.TAKE
    assert result.portfolio_verdict == PortfolioRiskVerdict.PASS


def test_over_budget_is_risk_too_high_not_other():
    result = check_advisory_decision_intake(
        _payload(), require_portfolio_risk=True, max_aggregate_open_risk_dollars=50.0
    )
    assert result.verdict == AdvisoryVerdict.AVOID
    assert NoTradeReason.RISK_TOO_HIGH in result.no_trade_reasons


def test_legacy_caller_supplying_a_snapshot_cannot_skip_the_budget():
    """require_portfolio_risk=False is the legacy fixture mode. Supplying a
    snapshot still evaluates it, and evaluating without a budget still blocks."""
    result = check_advisory_decision_intake(_payload(), require_portfolio_risk=False)
    assert result.verdict == AdvisoryVerdict.AVOID
    assert result.portfolio_verdict == PortfolioRiskVerdict.BLOCK


def test_legacy_caller_omitting_the_snapshot_is_unchanged():
    """Old evidence with no portfolio section still evaluates as before; the
    budget requirement attaches to portfolio evaluation, not to its absence."""
    payload = _payload()
    del payload["portfolio_risk"]
    result = check_advisory_decision_intake(payload, require_portfolio_risk=False)
    assert result.portfolio_verdict == PortfolioRiskVerdict.PASS
    assert result.verdict == AdvisoryVerdict.TAKE


def test_position_count_is_telemetry_even_with_a_tight_budget():
    many = [
        {"ticker": f"T{i}", "direction": "CALL", "planned_dollar_risk": 1.0, "capital_deployed": 500.0}
        for i in range(25)
    ]
    result = check_advisory_decision_intake(
        _payload(portfolio_risk={"open_positions": many}),
        require_portfolio_risk=True,
        max_aggregate_open_risk_dollars=200.0,
    )
    # 25 positions, $25 open + $100 candidate = $125 planned, $12,500 + $420 deployed.
    assert result.verdict == AdvisoryVerdict.TAKE
    assert result.portfolio_verdict == PortfolioRiskVerdict.PASS
