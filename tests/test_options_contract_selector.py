"""Tests for the deterministic advisory-only contract selector."""

from __future__ import annotations

import json
from pathlib import Path

from options_manager.contracts.selector import (
    OptionChainRow,
    select_contract,
    selector_rule_from_mapping,
)

RULE_PATH = Path("options_manager/contracts/selector_rule_v1.json")


def _rule():
    return selector_rule_from_mapping(json.loads(RULE_PATH.read_text()))


def _row(**overrides):
    fields = dict(
        contract_id="SPY261120C00550000",
        expiration="2026-11-20",
        strike=550.0,
        right="CALL",
        bid=4.80,
        ask=5.00,
        quote_ts="2026-09-18T14:00:00+00:00",
        volume=500,
        open_interest=1500,
        delta=0.50,
        dte=63,
    )
    fields.update(overrides)
    return OptionChainRow(**fields)


def _select(chain, **overrides):
    params = dict(
        rule=_rule(),
        decision_ts="2026-09-18T14:01:00+00:00",
        underlying_price=550.0,
        direction="CALL",
        chain=chain,
    )
    params.update(overrides)
    return select_contract(**params)


def test_rule_file_loads_and_is_conservative():
    rule = _rule()
    assert rule.rule_id == "selector-v1"
    assert rule.min_dte == 14
    assert rule.preferred_min_dte == 45
    assert rule.min_volume == 100
    assert rule.min_open_interest == 500
    assert rule.max_spread_percent == 20.0


def test_same_inputs_are_deterministic_and_chain_order_independent():
    a = _row(contract_id="A", strike=550.0, delta=0.50)
    b = _row(contract_id="B", strike=552.0, delta=0.52)
    first = _select([a, b])
    second = _select([a, b])
    permuted = _select([b, a])
    assert first == second == permuted
    assert first.contract_id == "A"


def test_prefers_45_plus_dte_when_available():
    short = _row(
        contract_id="SHORT",
        expiration="2026-10-16",
        dte=28,
        delta=0.50,
        strike=550.0,
    )
    preferred = _row(
        contract_id="PREFERRED",
        expiration="2026-11-06",
        dte=49,
        delta=0.56,
        strike=555.0,
    )
    result = _select([short, preferred])
    assert result.contract_id == "PREFERRED"
    assert result.selection_reason == "preferred_dte_delta_liquidity_rank"


def test_falls_back_to_14_to_44_dte_only_when_no_preferred_contract_exists():
    row = _row(
        contract_id="FALLBACK",
        expiration="2026-10-16",
        dte=28,
    )
    result = _select([row])
    assert result.contract_id == "FALLBACK"
    assert result.selection_reason == "fallback_min_dte_delta_liquidity_rank"


def test_below_minimum_dte_is_rejected():
    result = _select([_row(dte=7)])
    assert result.status == "NO_CONTRACT"
    assert result.candidates_excluded_by_reason["dte_too_short"] == 1


def test_wide_spread_is_rejected():
    result = _select([_row(bid=1.0, ask=2.0)])
    assert result.status == "NO_CONTRACT"
    assert result.candidates_excluded_by_reason["spread_too_wide"] == 1


def test_low_volume_is_rejected():
    result = _select([_row(volume=99)])
    assert result.status == "NO_CONTRACT"
    assert result.candidates_excluded_by_reason["volume_too_low"] == 1


def test_low_open_interest_is_rejected():
    result = _select([_row(open_interest=499)])
    assert result.status == "NO_CONTRACT"
    assert result.candidates_excluded_by_reason["open_interest_too_low"] == 1


def test_delta_outside_frozen_range_is_rejected():
    result = _select([_row(delta=0.85)])
    assert result.status == "NO_CONTRACT"
    assert result.candidates_excluded_by_reason["delta_out_of_range"] == 1


def test_future_quote_cannot_win_no_hindsight():
    known = _row(contract_id="KNOWN", delta=0.58, strike=555.0)
    future_better = _row(
        contract_id="FUTURE",
        delta=0.50,
        strike=550.0,
        quote_ts="2026-09-18T14:02:00+00:00",
    )
    result = _select([future_better, known])
    assert result.contract_id == "KNOWN"
    assert result.candidates_excluded_by_reason["future_quote"] == 1


def test_wrong_option_right_is_rejected():
    result = _select([_row(right="PUT")])
    assert result.status == "NO_CONTRACT"
    assert result.candidates_excluded_by_reason["wrong_right"] == 1


def test_put_selection_uses_absolute_delta():
    row = _row(
        contract_id="PUT",
        right="PUT",
        delta=-0.50,
        strike=550.0,
    )
    result = _select([row], direction="PUT")
    assert result.status == "SELECTED"
    assert result.contract_id == "PUT"


def test_empty_chain_fails_closed():
    result = _select([])
    assert result.status == "NO_CONTRACT"
    assert result.reason_code == "no_eligible_contract"


def test_invalid_rule_booleans_do_not_coerce_to_numbers():
    raw = json.loads(RULE_PATH.read_text())
    raw["min_dte"] = True
    try:
        selector_rule_from_mapping(raw)
    except ValueError as exc:
        assert "min_dte" in str(exc)
    else:
        raise AssertionError("boolean min_dte must be rejected")
