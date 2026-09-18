"""Tests for the deterministic advisory-only contract selector."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from options_manager.contracts.selector import (
    ContractSelectionInput,
    OptionChainRow,
    select_contract,
    select_contract_from_serialized_input,
    selection_input_from_json,
    selection_input_json,
    selection_result_json,
    selector_rule_from_mapping,
)

RULE_PATH = Path("options_manager/contracts/selector_rule_v1.json")


def _rule():
    return selector_rule_from_mapping(json.loads(RULE_PATH.read_text()))


def _rule_sha():
    return hashlib.sha256(RULE_PATH.read_bytes()).hexdigest()


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
        rule_sha256=_rule_sha(),
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
    assert first.rule_sha256 == _rule_sha()


def test_nearest_eligible_preferred_expiration_is_selected_before_strike_rank():
    nearer = _row(
        contract_id="NEARER",
        expiration="2026-11-06",
        dte=49,
        delta=0.58,
        strike=555.0,
    )
    farther_better_delta = _row(
        contract_id="FARTHER",
        expiration="2026-12-18",
        dte=91,
        delta=0.50,
        strike=550.0,
    )
    result = _select([farther_better_delta, nearer])
    assert result.contract_id == "NEARER"
    assert result.expiration == "2026-11-06"
    assert result.selection_reason == (
        "nearest_preferred_expiration_then_delta_liquidity_rank"
    )


def test_falls_back_to_nearest_14_to_44_dte_expiration_when_no_preferred_exists():
    farther = _row(
        contract_id="FARTHER",
        expiration="2026-10-30",
        dte=42,
        delta=0.50,
        strike=550.0,
    )
    nearer = _row(
        contract_id="NEARER",
        expiration="2026-10-02",
        dte=14,
        delta=0.58,
        strike=555.0,
    )
    result = _select([farther, nearer])
    assert result.contract_id == "NEARER"
    assert result.dte == 14
    assert result.selection_reason == (
        "nearest_min_dte_expiration_then_delta_liquidity_rank"
    )


def test_strike_rank_is_applied_only_within_selected_expiration():
    worse = _row(contract_id="WORSE", strike=560.0, delta=0.60)
    better = _row(contract_id="BETTER", strike=552.0, delta=0.51)
    result = _select([worse, better])
    assert result.contract_id == "BETTER"


def test_expiration_must_be_canonical_iso_date():
    result = _select([_row(expiration="11/20/2026")])
    assert result.status == "NO_CONTRACT"
    assert result.candidates_excluded_by_reason["invalid_expiration"] == 1


def test_supplied_dte_must_match_expiration_and_decision_date():
    result = _select([_row(expiration="2026-11-20", dte=62)])
    assert result.status == "NO_CONTRACT"
    assert result.candidates_excluded_by_reason["dte_expiration_mismatch"] == 1


def test_below_minimum_dte_is_rejected():
    result = _select([_row(expiration="2026-09-25", dte=7)])
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


def test_invalid_rule_sha_fails_closed():
    result = _select([_row()], rule_sha256="not-a-sha")
    assert result.status == "NO_CONTRACT"
    assert result.reason_code == "invalid_rule_sha256"


def test_serialized_output_is_byte_stable_for_identical_inputs():
    first = _select([_row(contract_id="A"), _row(contract_id="B", strike=552.0)])
    second = _select([_row(contract_id="A"), _row(contract_id="B", strike=552.0)])
    encoded_first = selection_result_json(first).encode("utf-8")
    encoded_second = selection_result_json(second).encode("utf-8")
    assert encoded_first == encoded_second
    assert hashlib.sha256(encoded_first).hexdigest() == hashlib.sha256(
        encoded_second
    ).hexdigest()


def test_invalid_rule_booleans_do_not_coerce_to_numbers():
    raw = json.loads(RULE_PATH.read_text())
    raw["min_dte"] = True
    try:
        selector_rule_from_mapping(raw)
    except ValueError as exc:
        assert "min_dte" in str(exc)
    else:
        raise AssertionError("boolean min_dte must be rejected")



def _serialized_input(chain, **overrides):
    values = dict(
        rule_sha256=_rule_sha(),
        decision_ts="2026-09-18T14:01:00+00:00",
        underlying_price=550.0,
        direction="CALL",
        chain=tuple(chain),
    )
    values.update(overrides)
    return selection_input_json(ContractSelectionInput(**values)).encode("utf-8")


def test_selector_replay_forward_golden_parity_uses_identical_serialized_input_bytes():
    payload = _serialized_input(
        [
            _row(contract_id="A", strike=550.0, delta=0.50),
            _row(contract_id="B", strike=552.0, delta=0.52),
        ]
    )

    replay = select_contract_from_serialized_input(rule=_rule(), payload=payload)
    forward = select_contract_from_serialized_input(rule=_rule(), payload=payload)

    assert replay == forward
    assert replay.status == "SELECTED"
    assert replay.contract_id == "A"
    assert selection_result_json(replay).encode("utf-8") == selection_result_json(
        forward
    ).encode("utf-8")


def test_serialized_selector_input_round_trip_is_byte_stable():
    payload = _serialized_input([_row(contract_id="A")])
    parsed = selection_input_from_json(payload)
    assert selection_input_json(parsed).encode("utf-8") == payload


def test_serialized_selector_input_preserves_no_hindsight_behavior():
    payload = _serialized_input(
        [
            _row(contract_id="KNOWN", delta=0.58, strike=555.0),
            _row(
                contract_id="FUTURE",
                delta=0.50,
                strike=550.0,
                quote_ts="2026-09-18T14:02:00+00:00",
            ),
        ]
    )
    result = select_contract_from_serialized_input(rule=_rule(), payload=payload)
    assert result.contract_id == "KNOWN"
    assert result.candidates_excluded_by_reason["future_quote"] == 1


def test_serialized_selector_schema_drift_fails_closed():
    raw = json.loads(_serialized_input([_row()]))
    raw["unexpected"] = True
    payload = (json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )

    result = select_contract_from_serialized_input(rule=_rule(), payload=payload)
    assert result.status == "NO_CONTRACT"
    assert result.reason_code == "serialized_input_invalid"


def test_serialized_selector_chain_row_schema_drift_fails_closed():
    raw = json.loads(_serialized_input([_row()]))
    del raw["chain"][0]["quote_ts"]
    payload = (json.dumps(raw, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )

    result = select_contract_from_serialized_input(rule=_rule(), payload=payload)
    assert result.status == "NO_CONTRACT"
    assert result.reason_code == "serialized_input_invalid"
