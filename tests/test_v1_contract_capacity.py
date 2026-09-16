from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from alert_ranker.market_data import OptionChain, OptionContractQuote
from alert_ranker.v1_contract_capacity import evaluate_symbol_contracts, run_contract_census

NOW = datetime(2026, 9, 15, 14, 0, tzinfo=ZoneInfo("America/New_York"))
EXPIRY = "2026-11-20"


def _contract(
    symbol: str,
    side: str,
    *,
    strike: float,
    bid: float = 2.40,
    ask: float = 2.50,
    volume: float = 200,
    oi: float = 800,
    delta: float = 0.40,
) -> OptionContractQuote:
    return OptionContractQuote(
        symbol=symbol,
        option_type=side,
        strike=strike,
        bid=bid,
        ask=ask,
        mid=(bid + ask) / 2,
        last=(bid + ask) / 2,
        volume=volume,
        open_interest=oi,
        delta=delta,
        implied_volatility=0.40,
    )


class FakeProvider:
    def __init__(self, *, call=None, put=None, chain_error: str | None = None):
        self.call = call or _contract("TSTC", "CALL", strike=101, delta=0.40)
        self.put = put or _contract("TSTP", "PUT", strike=99, delta=-0.40)
        self.chain_error = chain_error
        self.last_error: str | None = None
        self.expiration_calls = 0
        self.chain_calls = 0
        self.snapshot_calls = 0

    async def fetch_option_expirations(self, ticker: str):
        self.expiration_calls += 1
        return [EXPIRY]

    async def fetch_option_chain(self, ticker: str, expiration: str | None = None):
        self.chain_calls += 1
        if self.chain_error:
            self.last_error = self.chain_error
            return OptionChain(ticker, expiration, error=self.chain_error)
        self.last_error = None
        return OptionChain(ticker, expiration, calls=(self.call,), puts=(self.put,))

    async def fetch_market_snapshot(self, ticker: str):
        self.snapshot_calls += 1
        return SimpleNamespace(price=100.0, error=None)


def test_good_contracts_pass_v1_and_300_total_cost_preference():
    provider = FakeProvider()
    result = asyncio.run(evaluate_symbol_contracts(provider, "TST", now=NOW))

    assert result.expiration == EXPIRY
    assert result.dte >= 45
    assert result.call.v1_policy_ok is True
    assert result.put.v1_policy_ok is True
    assert result.call.contract_cost_dollars == 250.0
    assert result.call.planned_risk_dollars == 62.5
    assert result.call.preferred_cost_ok is True
    assert result.viable_sides == 2
    assert result.preferred_cost_viable_sides == 2
    assert provider.expiration_calls == 1
    assert provider.chain_calls == 1
    assert provider.snapshot_calls == 1


def test_500_contract_cost_is_still_valid_under_frozen_v1_risk_rule():
    # This distinction is deliberate: V1 caps planned loss using a 25% premium
    # stop, while the <=$300 total contract cost is an operator preference.
    call = _contract("TSTC", "CALL", strike=101, bid=4.80, ask=5.00, delta=0.40)
    provider = FakeProvider(call=call)
    result = asyncio.run(evaluate_symbol_contracts(provider, "TST", now=NOW))

    assert result.call.contract_cost_dollars == 500.0
    assert result.call.planned_risk_dollars == 125.0
    assert result.call.v1_policy_ok is True
    assert result.call.preferred_cost_ok is False


def test_selected_contract_over_v1_planned_risk_cap_is_not_viable():
    call = _contract("TSTC", "CALL", strike=101, bid=12.50, ask=13.00, delta=0.40)
    provider = FakeProvider(call=call)
    result = asyncio.run(evaluate_symbol_contracts(provider, "TST", now=NOW))

    assert result.call.contract_cost_dollars == 1300.0
    assert result.call.planned_risk_dollars == 325.0
    assert result.call.v1_policy_ok is False
    assert result.call.reason == "planned_risk_outside_v1_cap:325.00"


def test_contract_quality_fail_is_symbol_nonviability_not_operational_failure():
    bad_call = _contract("TSTC", "CALL", strike=101, volume=10)
    bad_put = _contract("TSTP", "PUT", strike=99, volume=10, delta=-0.40)
    provider = FakeProvider(call=bad_call, put=bad_put)
    report = asyncio.run(run_contract_census(provider, ["TST"], now=NOW))

    assert report.verdict == "PASS"
    assert report.operational_failures == 0
    assert report.symbols_no_v1_contract == ("TST",)
    assert report.results[0].call.reason.startswith("no_liquid_contract")


def test_rate_limit_fails_census_closed():
    provider = FakeProvider(chain_error="rate_limited")
    report = asyncio.run(run_contract_census(provider, ["TST"], now=NOW))

    assert report.verdict == "FAIL"
    assert report.reasons == ("operational_failures:1",)
    assert report.operational_failures == 1
    assert report.results[0].operational_error is True


def test_census_never_reports_side_effects():
    provider = FakeProvider()
    report = asyncio.run(run_contract_census(provider, ["TST"], now=NOW))
    payload = report.to_dict()

    assert payload["storage_writes"] == 0
    assert payload["alerts_sent"] == 0
    assert payload["live_watchlist_changed"] is False
    assert payload["contract_fetches"] == 1
