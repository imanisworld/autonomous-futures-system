from options_manager.contracts import ContractCandidate, ContractSelectionPolicy, ContractSelectionRequest, shortlist_contracts


def _policy(**overrides):
    values=dict(max_premium_per_share=3.0,max_spread_percent=10.0,min_volume=10,min_open_interest=50,min_dte=30,max_theta_abs=0.10,min_abs_delta=0.30,max_abs_delta=0.70)
    values.update(overrides); return ContractSelectionPolicy(**values)


def _candidate(**overrides):
    values=dict(symbol="AAPL 2026-10-16 C 230",ticker="AAPL",direction="CALL",expiration="2026-10-16",dte=44,strike=230.0,premium=2.5,bid=2.4,ask=2.6,volume=100,open_interest=500,delta=0.50,theta=-0.05,iv=0.30,earnings_risk="NONE",event_risk="NONE")
    values.update(overrides); return ContractCandidate(**values)


def test_huge_numeric_candidate_fails_closed_without_overflow():
    result=shortlist_contracts(ContractSelectionRequest(ticker="AAPL",direction="CALL",candidates=(_candidate(strike=10**10000),),policy=_policy()))
    assert result.status=="NO_ELIGIBLE"
    assert result.rejected[0].reason_code=="invalid_strike"


def test_huge_numeric_policy_is_invalid_request_without_overflow():
    result=shortlist_contracts(ContractSelectionRequest(ticker="AAPL",direction="CALL",candidates=(_candidate(),),policy=_policy(max_premium_per_share=10**10000)))
    assert result.status=="INVALID_REQUEST"
