from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from alert_ranker.market_data import (
    OptionChain,
    OptionContractQuote,
    PUBLIC_OPTION_CHAIN_SOURCE,
)
from alert_ranker.options_selector_input import (
    serialized_selector_input_from_option_chains,
)
from options_manager.contracts import (
    select_contract_from_serialized_input,
    selection_input_from_json,
    selector_rule_from_mapping,
)

RULE_PATH = Path("options_manager/contracts/selector_rule_v1.json")


def _rule():
    return selector_rule_from_mapping(json.loads(RULE_PATH.read_text()))


def _rule_sha():
    return hashlib.sha256(RULE_PATH.read_bytes()).hexdigest()


def _quote(**overrides):
    values = dict(
        symbol="SPY261120C00550000",
        option_type="CALL",
        strike=550.0,
        bid=4.8,
        ask=5.0,
        mid=4.9,
        last=4.9,
        volume=500.0,
        open_interest=1500.0,
        delta=0.5,
        implied_volatility=0.25,
        quote_timestamp="2026-09-18T14:00:00+00:00",
        bid_timestamp="2026-09-18T14:00:00+00:00",
        ask_timestamp="2026-09-18T14:00:01+00:00",
        source=PUBLIC_OPTION_CHAIN_SOURCE,
    )
    values.update(overrides)
    return OptionContractQuote(**values)


def _chain(
    expiration="2026-11-20",
    *,
    calls=None,
    puts=None,
    underlying="SPY",
    error=None,
):

    return OptionChain(
        underlying=underlying,
        expiration=expiration,
        calls=tuple(calls if calls is not None else (_quote(),)),
        puts=tuple(puts or ()),
        error=error,
    )


def _payload(chains, **overrides):
    values = dict(
        rule_sha256=_rule_sha(),
        decision_ts="2026-09-18T14:01:00+00:00",
        underlying_price=550.0,
        direction="CALL",
    )
    values.update(overrides)
    return serialized_selector_input_from_option_chains(chains, **values)


def test_real_provider_count_types_normalize_losslessly():
    parsed = selection_input_from_json(_payload([_chain()]))
    row = parsed.chain[0]
    assert row.volume == 500
    assert type(row.volume) is int
    assert row.open_interest == 1500
    assert type(row.open_interest) is int
    assert row.dte == 63


def test_chain_and_row_permutation_produce_identical_bytes():
    near = _chain(
        "2026-11-06",
        calls=[
            _quote(symbol="B", strike=552.0, delta=0.52),
            _quote(symbol="A", strike=550.0, delta=0.50),
        ],
    )
    far = _chain(
        "2026-12-18",
        calls=[_quote(symbol="C", strike=550.0, delta=0.50)],
    )
    permuted_near = _chain(
        "2026-11-06",
        calls=list(reversed(near.calls)),
    )
    assert _payload([near, far]) == _payload([far, permuted_near])


def test_multiple_expirations_feed_canonical_selector():
    near = _chain(
        "2026-11-06",
        calls=[_quote(symbol="NEAR", strike=555.0, delta=0.58)],
    )
    far = _chain(
        "2026-12-18",
        calls=[_quote(symbol="FAR", strike=550.0, delta=0.50)],
    )

    result = select_contract_from_serialized_input(
        rule=_rule(),
        payload=_payload([far, near]),
    )
    assert result.status == "SELECTED"
    assert result.contract_id == "NEAR"
    assert result.expiration == "2026-11-06"


def test_fractional_provider_count_is_not_silently_coerced():
    payload = _payload([_chain(calls=[_quote(volume=500.5)])])
    parsed = selection_input_from_json(payload)
    assert parsed.chain[0].volume == 500.5

    result = select_contract_from_serialized_input(rule=_rule(), payload=payload)
    assert result.status == "NO_CONTRACT"
    assert result.candidates_excluded_by_reason["invalid_volume"] == 1


def test_missing_quote_timestamp_remains_invalid():
    payload = _payload([_chain(calls=[_quote(quote_timestamp=None)])])
    parsed = selection_input_from_json(payload)
    assert parsed.chain[0].quote_ts is None

    result = select_contract_from_serialized_input(rule=_rule(), payload=payload)
    assert result.status == "NO_CONTRACT"
    assert result.candidates_excluded_by_reason["invalid_quote_timestamp"] == 1


def test_future_quote_is_excluded_by_selector():
    payload = _payload(
        [_chain(calls=[_quote(quote_timestamp="2026-09-18T14:02:00+00:00")])]
    )
    result = select_contract_from_serialized_input(rule=_rule(), payload=payload)
    assert result.status == "NO_CONTRACT"
    assert result.candidates_excluded_by_reason["future_quote"] == 1


@pytest.mark.parametrize(
    ("chains", "message"),
    [
        ([], "at least one option chain"),
        ([_chain(error="provider_error")], "snapshot is blocked"),
        (
            [_chain(underlying="SPY"), _chain("2026-12-18", underlying="QQQ")],
            "one non-empty underlying",
        ),
        ([_chain(), _chain()], "duplicate option chain expiration"),
    ],
)
def test_invalid_chain_population_fails_closed(chains, message):
    with pytest.raises(ValueError, match=message):
        _payload(chains)


def test_naive_decision_timestamp_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        _payload([_chain()], decision_ts="2026-09-18T14:01:00")
