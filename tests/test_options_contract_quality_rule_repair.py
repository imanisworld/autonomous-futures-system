from options_manager.validation.contract_quality_gate import (
    ContractQualityInput,
    GateVerdict,
    check_contract_quality_intake,
    evaluate_contract_quality,
)


def _contract(**overrides):
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
        max_contracts=4,
        max_dollar_risk=220.0,
        distance_to_target=5.0,
        iv_event_risk="none",
        theta_risk="low",
        premium_stop=1.60,
        trade_style="swing",
    )
    payload.update(overrides)
    return ContractQualityInput(**payload)


def test_quote_350_means_350_dollars_per_contract_and_blocks():
    result = evaluate_contract_quality(
        _contract(premium=3.50, premium_stop=3.00, max_contracts=1, max_dollar_risk=100.0)
    )
    assert result.verdict == GateVerdict.BLOCK
    assert any("$350.00/contract" in reason for reason in result.blocking_reasons)


def test_quote_350_can_warn_only_with_explicit_expensive_contract_acceptance():
    result = evaluate_contract_quality(
        _contract(
            premium=3.50,
            premium_stop=3.00,
            max_contracts=1,
            max_dollar_risk=100.0,
            premium_risk_accepted=True,
        )
    )
    assert result.verdict == GateVerdict.WARN
    assert any("$350.00/contract" in warning for warning in result.warnings)


def test_stop_defined_risk_is_separate_from_capital_deployed():
    # Debit is $840, but planned loss is $200:
    # ($2.10 - $1.60) * 100 * 4.
    result = evaluate_contract_quality(_contract())
    assert result.verdict == GateVerdict.PASS


def test_stop_defined_risk_over_stated_max_blocks():
    result = evaluate_contract_quality(_contract(max_dollar_risk=150.0))
    assert result.verdict == GateVerdict.BLOCK
    assert any("planned premium-stop risk $200.00" in reason for reason in result.blocking_reasons)


def test_invalid_premium_stop_blocks():
    result = evaluate_contract_quality(_contract(premium_stop=2.10))
    assert result.verdict == GateVerdict.BLOCK
    assert any("premium_stop" in reason for reason in result.blocking_reasons)


def test_30_dte_swing_warns_but_does_not_block():
    result = evaluate_contract_quality(_contract(dte=30))
    assert result.verdict == GateVerdict.WARN
    assert any("preferred 45d" in warning for warning in result.warnings)


def test_30_dte_intraday_does_not_get_swing_duration_warning():
    result = evaluate_contract_quality(_contract(dte=30, trade_style="intraday"))
    assert result.verdict == GateVerdict.PASS


def test_zero_dte_requires_explicit_exception():
    blocked = evaluate_contract_quality(_contract(dte=0))
    accepted = evaluate_contract_quality(_contract(dte=0, dte_exceptional=True))
    assert blocked.verdict == GateVerdict.BLOCK
    assert accepted.verdict == GateVerdict.WARN


def test_intake_coerces_premium_stop_and_trade_style():
    payload = {
        "ticker": "ORCL",
        "direction": "CALL",
        "expiration": "2026-12-18",
        "strike": "210",
        "premium": "2.10",
        "bid": "2.05",
        "ask": "2.15",
        "spread_percent": "4.8",
        "volume": "800",
        "open_interest": "3000",
        "dte": "60",
        "max_contracts": "1",
        "max_dollar_risk": "100",
        "distance_to_target": "5",
        "iv_event_risk": "none",
        "theta_risk": "low",
        "premium_stop": "1.60",
        "trade_style": "SWING",
    }
    result = check_contract_quality_intake(payload)
    assert result.verdict == GateVerdict.PASS
    assert result.contract is not None
    assert result.contract.premium_stop == 1.60
    assert result.contract.trade_style == "swing"
