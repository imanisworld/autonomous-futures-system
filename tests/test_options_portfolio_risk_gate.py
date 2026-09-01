from options_manager.validation.portfolio_risk_gate import (
    PortfolioRiskVerdict,
    RiskExposure,
    evaluate_portfolio_risk,
)


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
    result = evaluate_portfolio_risk(open_positions=opens, candidate=_pos(risk=100.0))
    assert result.verdict == PortfolioRiskVerdict.PASS
    assert result.open_position_count == 12
    assert result.projected_open_risk == 700.0


def test_aggregate_open_risk_blocks_over_1000():
    opens = [_pos(ticker="A", risk=500.0), _pos(ticker="B", risk=400.0)]
    result = evaluate_portfolio_risk(open_positions=opens, candidate=_pos(risk=150.0))
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any("aggregate open risk" in reason for reason in result.blocking_reasons)


def test_candidate_trade_risk_blocks_over_300():
    result = evaluate_portfolio_risk(open_positions=[], candidate=_pos(risk=301.0))
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any("per-trade cap" in reason for reason in result.blocking_reasons)


def test_capital_deployed_does_not_replace_planned_risk():
    result = evaluate_portfolio_risk(
        open_positions=[_pos(risk=100.0, capital=5000.0)],
        candidate=_pos(risk=100.0, capital=5000.0),
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
