"""U7: broker-facing futures contract metadata fails closed and matches canon."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import execution.tradovate_broker as tb
from config.futures_contracts import (
    SUPPORTED_ROOTS,
    UnsupportedContractError,
    contract_economics,
)
from execution.broker_interface import BracketOrder

ROOT = Path(__file__).resolve().parents[1]
PRE_U7_BROKER_ROOTS = {"MES", "ES", "MNQ", "NQ", "MGC", "MCL"}


def test_broker_root_set_adds_no_instrument():
    assert tb._BROKER_ECONOMICS_ROOTS == PRE_U7_BROKER_ROOTS
    assert tb._BROKER_ECONOMICS_ROOTS <= set(SUPPORTED_ROOTS)


@pytest.mark.parametrize("root", sorted(PRE_U7_BROKER_ROOTS))
def test_broker_economics_equal_canonical_metadata(root):
    assert tb._broker_economics(root) == contract_economics(root)
    assert tb._broker_economics(f"{root}1!") == contract_economics(root)


@pytest.mark.parametrize(
    "instrument",
    ["M2K", "MBT", "ZZZ", "", "MNQX", "CL", "GC", "M1!NQ", "MNQ1!1!", "1!MNQ"],
)
def test_unknown_root_never_inherits_other_units(instrument):
    with pytest.raises(UnsupportedContractError):
        tb._broker_economics(instrument)
    with pytest.raises(UnsupportedContractError):
        tb._round_to_tick(100.13, instrument)
    order = BracketOrder(
        instrument=instrument or "X", direction="LONG", entry=100.0, stop=99.0,
        target=103.0, rr_ratio=3.0, strategy="t",
    )
    with pytest.raises(UnsupportedContractError):
        tb._rr_preserving_entry_cap(order, instrument)


@pytest.mark.parametrize(
    "instrument,price,expected",
    [("MNQ", 30201.2487, 30201.25), ("MES", 5900.13, 5900.25), ("MGC", 2400.04, 2400.0),
     ("MCL", 70.123, 70.12)],
)
def test_supported_tick_rounding_unchanged(instrument, price, expected):
    assert tb._round_to_tick(price, instrument) == pytest.approx(expected)


@pytest.mark.parametrize("instrument", ["M2K", "M1!NQ"])
def test_unknown_contract_order_is_refused_before_any_broker_contact(monkeypatch, instrument):
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "6")
    broker = tb.TradovateBroker(config=tb.TradovateConfig(expected_account_id=555))

    def boom(*_a, **_k):
        raise AssertionError("broker contacted for an unsupported contract")

    for name in ("_authenticate", "_get", "_post", "_find_contract_id", "_verify_account_for_order"):
        monkeypatch.setattr(broker, name, boom)
    order = BracketOrder(
        instrument=instrument, direction="LONG", entry=2000.0, stop=1990.0, target=2030.0,
        rr_ratio=3.0, strategy="t",
    )
    fill = broker.execute_bracket(order)
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "CONTRACT_METADATA_UNSUPPORTED"


def test_broker_source_has_no_private_tick_tables_or_defaults():
    source = (ROOT / "execution/tradovate_broker.py").read_text(encoding="utf-8")
    assert "_TICK_SIZE" not in source and "_TICK_VALUE" not in source
    assert not re.search(r"\.get\([^)]*,\s*(0\.25|0\.5|0\.50|1\.25|12\.5|5\.0)\s*\)", source)


def test_wide_stop_demo_lane_tick_comes_from_canonical_metadata():
    from context import wide_stop_demo_runtime_core as core
    from context import wide_stop_ledger_paper as contract

    assert core._TICK == contract_economics(contract.INSTRUMENT)[0]
    source = (ROOT / "context/wide_stop_demo_runtime_core.py").read_text(encoding="utf-8")
    assert "/ 0.25" not in source
