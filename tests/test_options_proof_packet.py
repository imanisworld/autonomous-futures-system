"""
tests/test_options_proof_packet.py

options_manager/validation/proof_packet.py tests. Proves the forward-
only proof-packet template requires a complete pre-trade thesis,
contract, liquidity, and risk picture before it can be considered valid,
that it never fabricates a missing pre-trade field from a post-trade
outcome, and that it cannot promote anything to
`FixtureStatus.CLEAN_COMPLETE_FIXTURE`.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import options_manager.validation.proof_packet as proof_packet_module
from options_manager.validation import (
    ProofPacket,
    ProofPacketStatus,
    validate_proof_packet,
)

_SCANNED_MODULES = (proof_packet_module,)

_FORBIDDEN_IMPORT_FRAGMENTS = (
    "execution",
    "webhook",
    "alert_ranker",
    "options_companion",
    "risk_engine",
    "requests",
    "httpx",
    "urllib",
    "socket",
    "aiohttp",
    "websocket",
    "robin_stocks",
    "ib_insync",
    "ibapi",
)

_FORBIDDEN_CREDENTIAL_IDENTIFIERS = (
    "api_key",
    "apikey",
    "credential",
    "secret",
    "password",
    "token",
)


def _module_source() -> str:
    return Path(proof_packet_module.__file__).read_text()


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _complete_packet(**overrides) -> ProofPacket:
    base = dict(
        ticker="TEST",
        created_at="2026-07-08T09:30:00-04:00",
        direction="CALL",
        setup_type="support-hold continuation",
        timeframe="5m",
        entry_trigger="reclaim above $105 with volume",
        underlying_invalidation="close below $102",
        premium_stop="close below $1.00",
        target_1="$108",
        target_2="$112",
        expiration="2026-08-21",
        strike=110.0,
        premium=1.50,
        bid=1.45,
        ask=1.55,
        spread_percent=6.7,
        volume=500,
        open_interest=2000,
        max_contracts=5,
        max_dollar_risk=750.0,
        spy_context="above VWAP, trending",
        qqq_context="above VWAP, trending",
        gex_context="positive gamma above $100",
        signa_context="not used",
        source_references=("screenshot_2026-07-08.png",),
        status=ProofPacketStatus.WATCHING,
    )
    base.update(overrides)
    return ProofPacket(**base)


# --- 1. status enum ---------------------------------------------------------------------------------


def test_proof_packet_status_has_expected_members():
    assert {s.value for s in ProofPacketStatus} == {
        "watching",
        "triggered",
        "invalidated",
        "active",
        "exited",
        "expired",
    }


# --- 2. a fully-populated packet validates -----------------------------------------------------------


def test_complete_packet_is_valid():
    is_valid, errors = validate_proof_packet(_complete_packet())
    assert is_valid
    assert errors == ()


# --- 3. required setup/trigger/invalidation/target/liquidity/risk fields reject when missing ---------


def test_missing_entry_trigger_rejects():
    packet = _complete_packet(entry_trigger="")
    is_valid, errors = validate_proof_packet(packet)
    assert not is_valid
    assert any("entry_trigger" in e for e in errors)


def test_missing_invalidation_rejects():
    packet = _complete_packet(underlying_invalidation="")
    is_valid, errors = validate_proof_packet(packet)
    assert not is_valid
    assert any("underlying_invalidation" in e for e in errors)


def test_missing_premium_stop_rejects():
    packet = _complete_packet(premium_stop="   ")
    is_valid, errors = validate_proof_packet(packet)
    assert not is_valid
    assert any("premium_stop" in e for e in errors)


def test_missing_target_rejects():
    packet = _complete_packet(target_1="", target_2="")
    is_valid, errors = validate_proof_packet(packet)
    assert not is_valid
    assert any("target_1" in e for e in errors)
    assert any("target_2" in e for e in errors)


def test_missing_contract_liquidity_rejects():
    packet = _complete_packet(bid=0.0, ask=0.0, volume=-1, open_interest=-1)
    is_valid, errors = validate_proof_packet(packet)
    assert not is_valid
    assert any("bid" in e for e in errors)
    assert any("ask" in e for e in errors)
    assert any("volume" in e for e in errors)
    assert any("open_interest" in e for e in errors)


def test_missing_risk_fields_rejects():
    packet = _complete_packet(max_contracts=0, max_dollar_risk=0.0)
    is_valid, errors = validate_proof_packet(packet)
    assert not is_valid
    assert any("max_contracts" in e for e in errors)
    assert any("max_dollar_risk" in e for e in errors)


def test_missing_source_references_rejects():
    packet = _complete_packet(source_references=())
    is_valid, errors = validate_proof_packet(packet)
    assert not is_valid
    assert any("source_references" in e for e in errors)


def test_validation_never_partially_valid():
    """A packet missing multiple fields reports every failure, not just
    the first one hit."""
    packet = _complete_packet(entry_trigger="", underlying_invalidation="", target_1="")
    is_valid, errors = validate_proof_packet(packet)
    assert not is_valid
    assert len(errors) >= 3


# --- 4. post-trade outcome fields are optional and never used to fill in pre-trade fields -------------


def test_post_trade_fields_default_to_none_or_empty():
    packet = _complete_packet()
    assert packet.actual_entry_time is None
    assert packet.actual_entry_premium is None
    assert packet.actual_exit_time is None
    assert packet.actual_exit_premium is None
    assert packet.realized_pnl_dollars is None
    assert packet.realized_pnl_percent is None
    assert packet.outcome_notes == ""


def test_post_trade_outcome_alone_does_not_make_an_incomplete_packet_valid():
    """A packet with a full post-trade outcome but a missing pre-trade
    field (no invalidation) must still fail validation -- the outcome is
    never used to backfill a missing pre-trade claim."""
    packet = _complete_packet(
        underlying_invalidation="",
        status=ProofPacketStatus.EXITED,
        actual_entry_time="2026-07-08T09:35:00-04:00",
        actual_entry_premium=1.52,
        actual_exit_time="2026-07-08T15:00:00-04:00",
        actual_exit_premium=3.00,
        realized_pnl_dollars=740.0,
        realized_pnl_percent=97.4,
    )
    is_valid, errors = validate_proof_packet(packet)
    assert not is_valid
    assert any("underlying_invalidation" in e for e in errors)


# --- 5. no fixture-status coupling / no promotion path -------------------------------------------------


def test_proof_packet_status_enum_has_no_clean_fixture_option():
    """A packet's own lifecycle status can never itself be a fixture
    status -- promotion, if it ever happens, is a separate human call
    made in fixture_status.py against a different model entirely."""
    assert "CLEAN_COMPLETE_FIXTURE" not in {s.name for s in ProofPacketStatus}
    assert "clean_complete_fixture" not in {s.value for s in ProofPacketStatus}


def test_proof_packet_module_has_no_fixture_status_import():
    imported = _imported_modules(proof_packet_module)
    assert not any("fixture_status" in name for name in imported)


def test_proof_packet_has_no_promotion_function():
    import inspect

    function_names = [
        name for name, obj in vars(proof_packet_module).items() if inspect.isfunction(obj)
    ]
    assert not any("promote" in name.lower() for name in function_names)


# --- 6. required fields have no defaults (always an explicit human call) ------------------------------


def test_core_required_fields_have_no_default_value():
    fields_by_name = {f.name: f for f in dataclasses.fields(ProofPacket)}
    for name in (
        "ticker",
        "entry_trigger",
        "underlying_invalidation",
        "target_1",
        "target_2",
        "status",
        "source_references",
    ):
        field_def = fields_by_name[name]
        assert field_def.default is dataclasses.MISSING
        assert field_def.default_factory is dataclasses.MISSING


def test_constructing_a_packet_without_required_fields_fails():
    import pytest

    with pytest.raises(TypeError):
        ProofPacket(ticker="TEST")


# --- 7. no scanner import or scanner call path exists, no broker/execution/network/credential/I-O -----


def test_proof_packet_module_has_no_scanner_import():
    imported = _imported_modules(proof_packet_module)
    assert not any("options_manager.scanner" in name for name in imported)
    assert not any(name == "options_manager.strategies" for name in imported)


def test_proof_packet_module_has_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_proof_packet_module_has_no_cross_boundary_imports_outside_options_manager():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "enum", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_proof_packet_module_has_no_credential_identifiers():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_proof_packet_module_does_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_proof_packet_module_has_no_network_call_text():
    source = _module_source().lower()
    for forbidden in ("httpx.", "requests.", "socket."):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_proof_packet_module_has_no_clock_access():
    source = _module_source()
    for forbidden in ("datetime.now(", "time.time(", "date.today("):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


# --- 8. nothing in scanner/execution/broker paths imports this module ---------------------------------


def test_no_scanner_execution_or_broker_module_imports_proof_packet():
    repo_root = Path(__file__).resolve().parent.parent
    scanned_dirs = [
        repo_root / "options_manager" / "scanner",
        repo_root / "execution",
        repo_root / "webhook",
    ]
    offenders = []
    for directory in scanned_dirs:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.py"):
            text = path.read_text()
            if "proof_packet" in text:
                offenders.append(str(path))
    assert not offenders, f"proof_packet must not be referenced from: {offenders}"
