"""
tests/test_options_proof_packet_intake.py

options_manager/validation/proof_packet_intake.py tests. Proves the
manual intake helper turns a loose dict payload into a structured
valid/invalid result using the existing `validate_proof_packet()` rules,
never raises regardless of how malformed the payload is, never lets a
post-trade outcome substitute for a missing pre-trade field, and never
touches a scanner/broker/execution path or the system clock.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.validation.proof_packet_intake as intake_module
from options_manager.validation.proof_packet import ProofPacket, ProofPacketStatus
from options_manager.validation.proof_packet_intake import (
    IntakeResult,
    check_proof_packet_intake,
)

_SCANNED_MODULES = (intake_module,)

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
    return Path(intake_module.__file__).read_text()


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _complete_payload(**overrides) -> dict:
    payload = dict(
        ticker="ORCL",
        created_at="2026-07-09T09:35:00-04:00",
        direction="CALL",
        setup_type="2-1-2 continuation",
        timeframe="30m",
        entry_trigger="break above prior 30m high",
        underlying_invalidation="below prior 30m low",
        premium_stop="close below 50% of entry premium",
        target_1="gamma wall",
        target_2="next resistance",
        expiration="2026-08-21",
        strike=210.0,
        premium=2.10,
        bid=2.05,
        ask=2.15,
        spread_percent=4.8,
        volume=800,
        open_interest=3000,
        max_contracts=4,
        max_dollar_risk=220.0,
        spy_context="above VWAP",
        qqq_context="above VWAP",
        gex_context="positive gamma",
        signa_context="not used",
        source_references=("screenshot_2026-07-09.png",),
        status="WATCHING",
    )
    payload.update(overrides)
    return payload


# --- 1. a complete manual payload passes --------------------------------------------------------------


def test_complete_manual_payload_is_valid():
    result = check_proof_packet_intake(_complete_payload())
    assert isinstance(result, IntakeResult)
    assert result.valid
    assert result.missing_fields == ()
    assert result.blocking_reasons == ()
    assert isinstance(result.packet, ProofPacket)
    assert result.packet.status == ProofPacketStatus.WATCHING


def test_status_accepts_case_insensitive_string():
    result = check_proof_packet_intake(_complete_payload(status="watching"))
    assert result.valid
    assert result.packet.status == ProofPacketStatus.WATCHING


def test_direction_is_normalized_and_warned_when_lowercase():
    result = check_proof_packet_intake(_complete_payload(direction="call"))
    assert result.valid
    assert result.packet.direction == "CALL"
    assert any("direction" in w.lower() for w in result.warnings)


# --- 2. missing required fields fail, and are named ----------------------------------------------------


def test_missing_invalidation_fails():
    payload = _complete_payload()
    del payload["underlying_invalidation"]
    result = check_proof_packet_intake(payload)
    assert not result.valid
    assert "underlying_invalidation" in result.missing_fields


def test_missing_target_fails():
    payload = _complete_payload()
    del payload["target_1"]
    del payload["target_2"]
    result = check_proof_packet_intake(payload)
    assert not result.valid
    assert "target_1" in result.missing_fields
    assert "target_2" in result.missing_fields


def test_missing_contract_liquidity_fails():
    payload = _complete_payload()
    del payload["bid"]
    del payload["ask"]
    del payload["volume"]
    del payload["open_interest"]
    result = check_proof_packet_intake(payload)
    assert not result.valid
    for name in ("bid", "ask", "volume", "open_interest"):
        assert name in result.missing_fields


def test_missing_source_reference_fails():
    payload = _complete_payload()
    del payload["source_references"]
    result = check_proof_packet_intake(payload)
    assert not result.valid
    assert "source_references" in result.missing_fields


def test_missing_qqq_context_fails():
    """Matches the worked example in the Increment 25J request -- a
    missing QQQ context must show up as a named failure, not a silent
    pass."""
    payload = _complete_payload()
    del payload["qqq_context"]
    result = check_proof_packet_intake(payload)
    assert not result.valid
    assert "qqq_context" in result.missing_fields


def test_blank_string_field_counts_as_missing_not_present():
    payload = _complete_payload(entry_trigger="   ")
    result = check_proof_packet_intake(payload)
    assert not result.valid
    assert "entry_trigger" in result.missing_fields


# --- 3. blocking reasons surface structural violations from validate_proof_packet() ---------------------


def test_non_positive_strike_is_a_blocking_reason_not_missing():
    payload = _complete_payload(strike=0.0)
    result = check_proof_packet_intake(payload)
    assert not result.valid
    assert "strike" not in result.missing_fields
    assert any("strike" in reason for reason in result.blocking_reasons)


def test_never_reports_partial_valid_for_multiple_problems():
    payload = _complete_payload()
    del payload["underlying_invalidation"]
    del payload["target_1"]
    payload["bid"] = 0.0
    result = check_proof_packet_intake(payload)
    assert not result.valid
    assert len(result.missing_fields) >= 2
    assert any("bid" in reason for reason in result.blocking_reasons)


# --- 4. post-trade outcome cannot substitute for missing pre-trade proof --------------------------------


def test_post_trade_outcome_cannot_substitute_for_missing_pre_trade_field():
    payload = _complete_payload(
        status="EXITED",
        actual_entry_time="2026-07-09T09:40:00-04:00",
        actual_entry_premium=2.12,
        actual_exit_time="2026-07-09T15:00:00-04:00",
        actual_exit_premium=4.50,
        realized_pnl_dollars=952.0,
        realized_pnl_percent=112.3,
    )
    del payload["underlying_invalidation"]
    result = check_proof_packet_intake(payload)
    assert not result.valid
    assert "underlying_invalidation" in result.missing_fields


def test_valid_payload_with_full_post_trade_outcome_still_passes():
    payload = _complete_payload(
        status="EXITED",
        actual_entry_time="2026-07-09T09:40:00-04:00",
        actual_entry_premium=2.12,
        actual_exit_time="2026-07-09T15:00:00-04:00",
        actual_exit_premium=4.50,
        realized_pnl_dollars=952.0,
        realized_pnl_percent=112.3,
    )
    result = check_proof_packet_intake(payload)
    assert result.valid
    assert result.packet.realized_pnl_dollars == 952.0


# --- 5. malformed payloads return a structured failure, never raise ------------------------------------


def test_non_dict_payload_returns_structured_failure_not_exception():
    for bad_payload in (None, "not a dict", 42, ["a", "list"]):
        result = check_proof_packet_intake(bad_payload)
        assert isinstance(result, IntakeResult)
        assert not result.valid
        assert result.blocking_reasons
        assert "malformed payload" in result.blocking_reasons[0]


def test_unconvertible_numeric_field_returns_structured_failure_not_exception():
    payload = _complete_payload(strike="not-a-number")
    result = check_proof_packet_intake(payload)
    assert not result.valid
    assert any("strike" in reason for reason in result.blocking_reasons)
    assert result.packet is None


def test_invalid_status_string_returns_structured_failure_not_exception():
    payload = _complete_payload(status="not-a-real-status")
    result = check_proof_packet_intake(payload)
    assert not result.valid
    assert any("status" in reason for reason in result.blocking_reasons)


def test_invalid_direction_returns_structured_failure_not_exception():
    payload = _complete_payload(direction="SIDEWAYS")
    result = check_proof_packet_intake(payload)
    assert not result.valid
    assert any("direction" in reason for reason in result.blocking_reasons)


def test_unrecognized_keys_produce_a_warning_not_a_failure():
    payload = _complete_payload(typo_field_nmae="oops")
    result = check_proof_packet_intake(payload)
    assert result.valid
    assert any("typo_field_nmae" in w for w in result.warnings)


def test_source_references_accepts_a_single_string():
    payload = _complete_payload(source_references="one_screenshot.png")
    result = check_proof_packet_intake(payload)
    assert result.valid
    assert result.packet.source_references == ("one_screenshot.png",)


# --- 6. no scanner/broker/execution import, no clock access, no I/O ------------------------------------


def test_proof_packet_intake_module_has_no_scanner_import():
    imported = _imported_modules(intake_module)
    assert not any("options_manager.scanner" in name for name in imported)
    assert not any(name == "options_manager.strategies" for name in imported)


def test_proof_packet_intake_module_has_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_proof_packet_intake_module_has_no_cross_boundary_imports_outside_options_manager():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_proof_packet_intake_module_has_no_credential_identifiers():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_proof_packet_intake_module_does_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_proof_packet_intake_module_has_no_network_call_text():
    source = _module_source().lower()
    for forbidden in ("httpx.", "requests.", "socket."):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_proof_packet_intake_module_has_no_clock_access():
    source = _module_source()
    for forbidden in ("datetime.now(", "time.time(", "date.today("):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_proof_packet_intake_module_does_not_reference_clean_complete_fixture():
    assert "CLEAN_COMPLETE_FIXTURE" not in _module_source()


def test_no_scanner_execution_or_broker_module_imports_proof_packet_intake():
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
            if "proof_packet_intake" in text:
                offenders.append(str(path))
    assert not offenders, f"proof_packet_intake must not be referenced from: {offenders}"
