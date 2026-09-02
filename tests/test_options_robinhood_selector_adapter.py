"""Robinhood read-only rows -> selector ContractCandidate, fail closed at the boundary.

Payloads below are the real shapes returned by the read-only Robinhood
instrument and quote tools on 2026-09-02 (SPY 2026-10-16 760 call).
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

import options_manager.adapters.robinhood_selector as adapter_module
from options_manager.adapters.robinhood_selector import PROVIDER, build_selector_candidates, contract_symbol
from options_manager.contracts import ContractSelectionPolicy, ContractSelectionRequest, shortlist_contracts

RETRIEVED = "2026-09-02T13:26:10+00:00"
INSTRUMENT = {
    "id": "cbc9a4a6-2096-4152-8c73-91589d3b41e7",
    "chain_id": "c277b118-58d9-4060-8dc5-a3b5898955cb",
    "chain_symbol": "SPY",
    "underlying_type": "equity",
    "expiration_date": "2026-10-16",
    "sellout_datetime": "2026-10-16T19:45:00+00:00",
    "strike_price": "760.0000",
    "type": "call",
    "state": "active",
    "tradability": "tradable",
    "trade_value_multiplier": "100.0000",
}
QUOTE = {
    "instrument_id": "cbc9a4a6-2096-4152-8c73-91589d3b41e7",
    "ask_price": "16.500000",
    "ask_size": 2,
    "bid_price": "16.380000",
    "bid_size": 25,
    "break_even_price": "776.440000",
    "adjusted_mark_price": "16.440000",
    "mark_price": "16.440000",
    "previous_close_price": "19.590000",
    "implied_volatility": "0.129566",
    "delta": "0.569061",
    "gamma": "0.011408",
    "rho": "0.507609",
    "theta": "-0.196118",
    "vega": "1.044234",
    "open_interest": 7683,
    "volume": 1401,
    "updated_at": "2026-09-01T20:14:59.96075539Z",
}


def _build(instruments=None, quotes=None, **kwargs):
    params = dict(ticker="SPY", direction="CALL", retrieved_at=RETRIEVED)
    params.update(kwargs)
    return build_selector_candidates(instruments if instruments is not None else [INSTRUMENT], quotes if quotes is not None else [QUOTE], **params)


def test_real_payload_maps_into_a_selector_candidate_with_provenance():
    build = _build()
    assert build.rejected == ()
    record = build.records[0]
    candidate = record.candidate
    assert candidate.symbol == "SPY 2026-10-16 C 760" == contract_symbol("SPY", "2026-10-16", "CALL", 760.0)
    assert (candidate.ticker, candidate.direction, candidate.expiration) == ("SPY", "CALL", "2026-10-16")
    assert candidate.dte == (date(2026, 10, 16) - date(2026, 9, 2)).days == 44
    assert candidate.strike == 760.0
    assert (candidate.bid, candidate.ask) == (16.38, 16.5)
    assert candidate.volume == 1401 and candidate.open_interest == 7683
    assert candidate.delta == pytest.approx(0.569061) and candidate.theta == pytest.approx(-0.196118) and candidate.iv == pytest.approx(0.129566)
    assert candidate.earnings_risk is None and candidate.event_risk is None  # unresolved stays unresolved
    assert record.mark == 16.44 and record.planned_entry_premium is None  # mark != planned premium
    assert record.provider == PROVIDER and record.instrument_id == INSTRUMENT["id"]
    assert record.provider_updated_at == "2026-09-01T20:14:59.960755+00:00"
    assert record.provenance["quote_updated_at"] == record.provider_updated_at
    assert record.provenance["retrieved_at"] == RETRIEVED and record.provenance["as_of"] == "2026-09-02"
    assert "selector" in record.provenance["spread_definition"]


def test_direction_filter_and_put_mapping():
    put = {**INSTRUMENT, "id": "put-1", "type": "put"}
    put_quote = {**QUOTE, "instrument_id": "put-1", "delta": "-0.43"}
    calls = _build([INSTRUMENT, put], [QUOTE, put_quote], direction="CALL")
    assert [r.candidate.symbol for r in calls.records] == ["SPY 2026-10-16 C 760"]
    assert calls.rejected[0].reason_code == "direction_mismatch"
    puts = _build([INSTRUMENT, put], [QUOTE, put_quote], direction="PUT")
    assert puts.records[0].candidate.direction == "PUT" and puts.records[0].candidate.delta == pytest.approx(-0.43)


@pytest.mark.parametrize(
    "instrument, quote, code",
    [
        ({**INSTRUMENT, "id": ""}, QUOTE, "missing_instrument_id"),
        ({**INSTRUMENT, "chain_symbol": "QQQ"}, QUOTE, "ticker_mismatch"),
        ({**INSTRUMENT, "type": "warrant"}, QUOTE, "unknown_type"),
        ({**INSTRUMENT, "state": "expired"}, QUOTE, "not_tradable"),
        ({**INSTRUMENT, "tradability": "untradable"}, QUOTE, "not_tradable"),
        ({**INSTRUMENT, "expiration_date": "soon"}, QUOTE, "missing_expiration"),
        (INSTRUMENT, {**QUOTE, "instrument_id": "other"}, "missing_quote"),
        (INSTRUMENT, {**QUOTE, "delta": "nan"}, "non_finite_delta"),
        (INSTRUMENT, {**QUOTE, "implied_volatility": "inf"}, "non_finite_iv"),
        (INSTRUMENT, {**QUOTE, "bid_price": "-inf"}, "non_finite_bid"),
        ({**INSTRUMENT, "strike_price": "0"}, QUOTE, "missing_strike"),
        (INSTRUMENT, {**QUOTE, "updated_at": "2026-09-01T20:14:59"}, "bad_updated_at"),
        (INSTRUMENT, {**QUOTE, "updated_at": "2026-09-02T13:30:00Z"}, "future_updated_at"),
    ],
)
def test_bad_rows_are_rejected_with_a_reason_not_coerced(instrument, quote, code):
    build = _build([instrument], [quote])
    assert build.records == ()
    assert build.rejected[0].reason_code == code, build.rejected


def test_missing_greeks_stay_none_and_the_selector_rejects_them():
    build = _build([INSTRUMENT], [{k: v for k, v in QUOTE.items() if k not in ("delta", "theta", "implied_volatility")}])
    candidate = build.records[0].candidate
    assert candidate.delta is None and candidate.theta is None and candidate.iv is None
    result = shortlist_contracts(ContractSelectionRequest(ticker="SPY", direction="CALL", candidates=build.candidates, policy=_policy()))
    assert result.status == "NO_ELIGIBLE"


def _policy(**overrides):
    base = dict(max_premium_per_share=20.0, max_spread_percent=5.0, min_volume=100, min_open_interest=500, min_dte=14, max_theta_abs=0.5, min_abs_delta=0.3, max_abs_delta=0.7)
    base.update(overrides)
    return ContractSelectionPolicy(**base)


def test_end_to_end_unresolved_event_risk_fails_closed_then_resolved_selects():
    unresolved = _build()
    blocked = shortlist_contracts(ContractSelectionRequest(ticker="SPY", direction="CALL", candidates=unresolved.candidates, policy=_policy()))
    assert blocked.status == "NO_ELIGIBLE" and blocked.rejected[0].reason_code == "event_risk_missing"
    resolved = _build(earnings_risk="NONE", event_risk="LOW")
    result = shortlist_contracts(ContractSelectionRequest(ticker="SPY", direction="CALL", candidates=resolved.candidates, policy=_policy(preferred_abs_delta=0.55)))
    assert result.status in ("SOLE_ELIGIBLE", "SHORTLIST", "CAUTION_ONLY"), result  # selector vocabulary
    assert result.eligible and result.eligible[0].candidate.symbol == "SPY 2026-10-16 C 760"
    # spread is the selector's (midpoint) definition, not computed by the adapter
    assert result.eligible[0].spread_percent == pytest.approx((16.5 - 16.38) / ((16.5 + 16.38) / 2) * 100)


def test_as_of_overrides_dte_reference_and_naive_retrieved_at_is_refused():
    assert _build(as_of=date(2026, 10, 1)).records[0].candidate.dte == 15
    with pytest.raises(ValueError):
        _build(retrieved_at="2026-09-02T13:26:10")
    with pytest.raises(ValueError):
        _build(direction="LONG")


def test_adapter_is_pure_and_inside_the_options_manager_boundary():
    source = Path(adapter_module.__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module)
    for name in imported:
        assert name.split(".")[0] in {"dataclasses", "datetime", "typing", "__future__", "options_manager"}, name
    for forbidden in ("os.", "os.environ", "getenv", "subprocess", "requests", "httpx", "open(", "socket", "place_order", "submit", "execution", "alert_ranker", "options_companion", "api_key", "token"):
        assert forbidden not in source, forbidden
