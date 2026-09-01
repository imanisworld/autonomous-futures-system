import pytest

from options_manager.validation.portfolio_risk_gate import (
    AGGREGATE_RISK_BUDGET_INVALID_CODE,
    AGGREGATE_RISK_BUDGET_MISSING_CODE,
    AGGREGATE_RISK_BUDGET_UNCONFIGURED,
    PortfolioRiskVerdict,
    RiskExposure,
    evaluate_portfolio_risk,
)

# Every test that expects PASS states its budget. There is no implicit one.
BUDGET = 1000.0


def _pos(ticker="AAPL", risk=50.0, capital=200.0, group="mega_cap_tech"):
    return RiskExposure(
        ticker=ticker,
        direction="CALL",
        planned_dollar_risk=risk,
        capital_deployed=capital,
        correlation_group=group,
    )


def test_position_count_is_observed_not_capped():
    opens = [_pos(ticker=f"T{i}", risk=50.0) for i in range(12)]
    result = evaluate_portfolio_risk(
        open_positions=opens, candidate=_pos(risk=100.0), max_aggregate_open_risk_dollars=BUDGET
    )
    assert result.verdict == PortfolioRiskVerdict.PASS
    assert result.open_position_count == 12
    assert result.projected_open_risk == 700.0


def test_aggregate_open_risk_blocks_over_the_configured_budget():
    opens = [_pos(ticker="A", risk=500.0), _pos(ticker="B", risk=400.0)]
    result = evaluate_portfolio_risk(
        open_positions=opens, candidate=_pos(risk=150.0), max_aggregate_open_risk_dollars=BUDGET
    )
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any("aggregate open risk" in reason for reason in result.blocking_reasons)


def test_candidate_trade_risk_blocks_over_300():
    result = evaluate_portfolio_risk(
        open_positions=[], candidate=_pos(risk=301.0), max_aggregate_open_risk_dollars=BUDGET
    )
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any("per-trade cap" in reason for reason in result.blocking_reasons)


def test_capital_deployed_does_not_replace_planned_risk():
    result = evaluate_portfolio_risk(
        open_positions=[_pos(risk=100.0, capital=5000.0)],
        candidate=_pos(risk=100.0, capital=5000.0),
        max_aggregate_open_risk_dollars=BUDGET,
    )
    assert result.verdict == PortfolioRiskVerdict.PASS
    assert result.projected_open_risk == 200.0
    assert result.projected_capital_deployed == 10000.0


def test_correlation_exposure_is_reported_not_auto_blocked():
    result = evaluate_portfolio_risk(
        open_positions=[
            _pos(ticker="AAPL", risk=100.0, group="mega_cap_tech"),
            _pos(ticker="MSFT", risk=125.0, group="mega_cap_tech"),
        ],
        candidate=_pos(ticker="NVDA", risk=150.0, group="mega_cap_tech"),
        max_aggregate_open_risk_dollars=BUDGET,
    )
    assert result.verdict == PortfolioRiskVerdict.PASS
    assert dict(result.correlation_risk)["mega_cap_tech"] == 375.0


def test_limits_are_configurable_without_adding_position_cap():
    opens = [_pos(ticker=f"T{i}", risk=25.0) for i in range(15)]
    result = evaluate_portfolio_risk(
        open_positions=opens,
        candidate=_pos(risk=75.0),
        max_trade_risk_dollars=100.0,
        max_aggregate_open_risk_dollars=500.0,
    )
    assert result.verdict == PortfolioRiskVerdict.PASS
    assert result.open_position_count == 15
    assert result.projected_open_risk == 450.0


def test_no_budget_means_block_not_a_guessed_limit():
    """The whole point of the cleanup: absence is a block, never $1,000."""
    result = evaluate_portfolio_risk(open_positions=[], candidate=_pos(risk=10.0))
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert AGGREGATE_RISK_BUDGET_UNCONFIGURED in result.blocking_reasons
    # The arithmetic still ran, so the operator can see what was unmeasured.
    assert result.projected_open_risk == 10.0
    assert result.candidate_risk == 10.0


def test_no_default_budget_constant_exists():
    import options_manager.validation.portfolio_risk_gate as module

    assert not hasattr(module, "DEFAULT_MAX_AGGREGATE_OPEN_RISK_DOLLARS")
    assert "1000" not in str(module.evaluate_portfolio_risk.__defaults__)


def test_exact_boundary_is_deterministic():
    """Projected risk equal to the budget passes; one cent over blocks.

    The candidate sits exactly at the (untouched) $300 per-trade cap so the
    only thing being measured here is the aggregate boundary.
    """
    opens = [_pos(ticker="A", risk=350.0), _pos(ticker="B", risk=350.0)]
    at_limit = evaluate_portfolio_risk(
        open_positions=opens, candidate=_pos(risk=300.0), max_aggregate_open_risk_dollars=BUDGET
    )
    assert at_limit.verdict == PortfolioRiskVerdict.PASS
    assert at_limit.projected_open_risk == BUDGET

    over = evaluate_portfolio_risk(
        open_positions=[_pos(ticker="A", risk=350.0), _pos(ticker="B", risk=350.01)],
        candidate=_pos(risk=300.0),
        max_aggregate_open_risk_dollars=BUDGET,
    )
    assert over.verdict == PortfolioRiskVerdict.BLOCK
    assert any("exceeds cap" in reason for reason in over.blocking_reasons)
    assert not any("per-trade cap" in reason for reason in over.blocking_reasons)


@pytest.mark.parametrize("budget", (0.0, -1.0, -1000.0))
def test_zero_or_negative_budget_is_invalid_and_never_unlimited(budget):
    result = evaluate_portfolio_risk(
        open_positions=[], candidate=_pos(risk=10.0), max_aggregate_open_risk_dollars=budget
    )
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any(r.startswith(AGGREGATE_RISK_BUDGET_INVALID_CODE) for r in result.blocking_reasons)
    assert not any(r.startswith(AGGREGATE_RISK_BUDGET_MISSING_CODE) for r in result.blocking_reasons)


def test_missing_and_invalid_are_distinct_reason_codes():
    assert AGGREGATE_RISK_BUDGET_UNCONFIGURED.startswith(AGGREGATE_RISK_BUDGET_MISSING_CODE)
    assert AGGREGATE_RISK_BUDGET_MISSING_CODE != AGGREGATE_RISK_BUDGET_INVALID_CODE


def test_debit_and_planned_risk_stay_separate_when_budget_is_missing():
    """Even a blocked result keeps A/B/C/D apart -- nothing collapses."""
    result = evaluate_portfolio_risk(
        open_positions=[_pos(risk=100.0, capital=5000.0)],
        candidate=_pos(risk=100.0, capital=5000.0),
    )
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert result.projected_open_risk == 200.0
    assert result.projected_capital_deployed == 10000.0
    assert result.open_position_count == 1
