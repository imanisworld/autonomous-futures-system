from __future__ import annotations

import pytest

from execution.broker_interface import BracketOrder
from execution.contract_identity import (
    CONTRACT_IDENTITY_MISMATCH,
    CONTRACT_IDENTITY_UNKNOWN,
    CONTRACT_IDENTITY_UNNORMALIZABLE,
    contract_identity_block_reason,
    contract_identity_enforced,
    contract_identity_observation,
    normalize_contract_symbol,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MNQZ6", "MNQZ6"),
        ("MNQZ2026", "MNQZ6"),
        ("CME_MINI:MNQZ2026", "MNQZ6"),
        ("mesm2027", "MESM7"),
        (" MESU6 ", "MESU6"),
    ],
)
def test_normalize_contract_symbol_accepts_supported_dated_contracts(raw, expected):
    assert normalize_contract_symbol(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "MNQ",
        "MNQ1!",
        "CME_MINI:MNQ1!",
        '{"symbol":"CME_MINI:MNQ1!"}',
        "MCLV6",
        "MNQF6",
        "MNQZZ26",
        123,
    ],
)
def test_normalize_contract_symbol_rejects_unknown_or_continuous_symbols(raw):
    assert normalize_contract_symbol(raw) is None


def test_contract_identity_flag_is_observe_only_by_default():
    assert contract_identity_enforced({}) is False
    assert contract_identity_enforced({"CONTRACT_IDENTITY_GUARD": ""}) is False
    assert contract_identity_enforced({"CONTRACT_IDENTITY_GUARD": "observe"}) is False


@pytest.mark.parametrize("raw", ["1", "true", "YES", "enabled", "fail_closed"])
def test_contract_identity_flag_accepts_explicit_enforcement_values(raw):
    assert contract_identity_enforced({"CONTRACT_IDENTITY_GUARD": raw}) is True


def test_contract_identity_observe_mode_never_blocks():
    obs = contract_identity_observation("MNQZ6", "MNQU6", enforce=False)
    assert obs.hint_normalized == "MNQZ6"
    assert obs.routed_normalized == "MNQU6"
    assert obs.block_reason is None
    assert obs.enforcement_enabled is False


@pytest.mark.parametrize("hint", [None, "", "   "])
def test_enforced_contract_identity_blocks_missing_hint_as_unknown(hint):
    assert (
        contract_identity_block_reason(hint, "MNQZ6", enforce=True)
        == CONTRACT_IDENTITY_UNKNOWN
    )


@pytest.mark.parametrize(
    ("hint", "routed"),
    [
        ("MNQ1!", "MNQZ6"),
        ("MNQZ6", "MNQ1!"),
        ('{"symbol":"CME_MINI:MNQ1!"}', "MNQZ6"),
    ],
)
def test_enforced_contract_identity_blocks_unnormalizable_values(hint, routed):
    assert (
        contract_identity_block_reason(hint, routed, enforce=True)
        == CONTRACT_IDENTITY_UNNORMALIZABLE
    )


def test_enforced_contract_identity_blocks_mismatch():
    assert (
        contract_identity_block_reason("CME_MINI:MNQZ2026", "MNQU6", enforce=True)
        == CONTRACT_IDENTITY_MISMATCH
    )


def test_enforced_contract_identity_allows_normalized_match():
    assert contract_identity_block_reason("CME_MINI:MNQZ2026", "MNQZ6", enforce=True) is None


def test_bracket_order_carries_optional_contract_hint_without_affecting_existing_callers():
    legacy = BracketOrder(
        instrument="MNQ",
        direction="LONG",
        entry=20000.0,
        stop=19990.0,
        target=20030.0,
        rr_ratio=3.0,
        strategy="orb_breakout",
    )
    hinted = BracketOrder(
        instrument="MNQ",
        direction="LONG",
        entry=20000.0,
        stop=19990.0,
        target=20030.0,
        rr_ratio=3.0,
        strategy="orb_breakout",
        contract_hint="CME_MINI:MNQZ2026",
    )

    assert legacy.contract_hint is None
    assert hinted.contract_hint == "CME_MINI:MNQZ2026"
