"""
tests/test_options_advisory_decision.py

options_manager/validation/advisory_decision.py tests. Proves the
advisory decision coordinator combines proof_packet_intake,
contract_quality_gate, watchlist_lifecycle, and morning_scan_packet into
one TAKE/WAIT/AVOID verdict per the documented decision order, builds
no_trade_reasons automatically on AVOID, never raises on malformed
input, and contains no order/action field of any kind and no scanner/
broker/execution coupling.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.validation.advisory_decision as advisory_decision_module
from options_manager.validation.advisory_decision import (
    AdvisoryDecisionResult,
    AdvisoryVerdict,
    check_advisory_decision_intake,
)
from options_manager.validation.contract_quality_gate import GateVerdict
from options_manager.validation.no_trade_reasons import NoTradeReason
from options_manager.validation.watchlist_lifecycle import WatchlistCandidateStatus

_SCANNED_MODULES = (advisory_decision_module,)

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

_FORBIDDEN_ORDER_ACTION_IDENTIFIERS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "replace_order",
    "execute_order",
    "live_order",
)


def _module_source() -> str:
    return Path(advisory_decision_module.__file__).read_text()


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _clean_proof_packet_payload(**overrides) -> dict:
    payload = dict(
        ticker="ORCL",
        created_at="2026-07-09T09:35:00-04:00",
        direction="CALL",
        setup_type="2-1-2 continuation",
        timeframe="30m",
        entry_trigger="break above prior 30m high",
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
        max_dollar_risk=250.0,
        spy_context="above VWAP",
        qqq_context="above VWAP",
        gex_context="positive gamma",
        signa_context="not used",
        source_references=("screenshot.png",),
        status="watching",
    )
    payload.update(overrides)
    return payload


def _clean_contract_quality_payload(**overrides) -> dict:
    payload = dict(
        ticker="ORCL",
        direction="CALL",
        expiration="2026-08-21",
        strike=210.0,
        premium=2.10,
        bid=2.05,
        ask=2.15,
        spread_percent=4.8,
        volume=800,
        open_interest=3000,
        dte=45,
        max_contracts=4,
        max_dollar_risk=220.0,
        distance_to_target=5.0,
        iv_event_risk="none",
        theta_risk="low",
        notes="",
    )
    payload.update(overrides)
    return payload


def _clean_watchlist_payload(**overrides) -> dict:
    payload = dict(
        ticker="ORCL",
        direction="CALL",
        setup_type="2-1-2 continuation",
        timeframe="30m",
        entry_trigger="break above prior 30m high",
        invalidation="below prior 30m low",
        target_1="gamma wall",
        target_2="next resistance",
        status="watching",
        created_at_or_session="2026-07-09 09:35 ET",
        last_updated_or_session="2026-07-09 09:35 ET",
        notes="",
        source_reference="chart_screenshot.png",
    )
    payload.update(overrides)
    return payload


def _clean_decision_payload(**overrides) -> dict:
    payload = dict(
        proof_packet=_clean_proof_packet_payload(),
        contract_quality=_clean_contract_quality_payload(),
    )
    payload.update(overrides)
    return payload


# --- 1. complete valid proof packet + passing contract returns TAKE ------------------------------------


def test_complete_valid_proof_and_contract_returns_take():
    result = check_advisory_decision_intake(_clean_decision_payload())
    assert isinstance(result, AdvisoryDecisionResult)
    assert result.verdict == AdvisoryVerdict.TAKE
    assert result.proof_valid
    assert result.contract_verdict == GateVerdict.PASS
    assert result.no_trade_reasons == ()


# --- 2. proof packet failures return AVOID with mapped no-trade reasons ---------------------------------


def test_proof_missing_invalidation_returns_avoid_with_no_trade_reason():
    payload = _clean_decision_payload(
        proof_packet=_clean_proof_packet_payload(underlying_invalidation="")
    )
    result = check_advisory_decision_intake(payload)
    assert result.verdict == AdvisoryVerdict.AVOID
    assert not result.proof_valid
    assert NoTradeReason.MISSING_INVALIDATION in result.no_trade_reasons


def test_proof_missing_target_returns_avoid():
    payload = _clean_decision_payload(
        proof_packet=_clean_proof_packet_payload(target_1="", target_2="")
    )
    result = check_advisory_decision_intake(payload)
    assert result.verdict == AdvisoryVerdict.AVOID
    assert NoTradeReason.MISSING_TARGET in result.no_trade_reasons


# --- 3. contract quality BLOCK/WARN drive AVOID/WAIT ------------------------------------------------------


def test_contract_block_returns_avoid():
    payload = _clean_decision_payload(contract_quality=_clean_contract_quality_payload(bid=0.0))
    result = check_advisory_decision_intake(payload)
    assert result.verdict == AdvisoryVerdict.AVOID
    assert result.contract_verdict == GateVerdict.BLOCK
    assert result.no_trade_reasons  # non-empty


def test_contract_warn_returns_wait():
    payload = _clean_decision_payload(
        contract_quality=_clean_contract_quality_payload(iv_event_risk="moderate")
    )
    result = check_advisory_decision_intake(payload)
    assert result.verdict == AdvisoryVerdict.WAIT
    assert result.contract_verdict == GateVerdict.WARN


def test_contract_warn_with_risk_accepted_returns_take():
    payload = _clean_decision_payload(
        contract_quality=_clean_contract_quality_payload(iv_event_risk="moderate"),
        risk_accepted=True,
    )
    result = check_advisory_decision_intake(payload)
    assert result.verdict == AdvisoryVerdict.TAKE


# --- 4. watchlist status drives WAIT/AVOID/TAKE -----------------------------------------------------------


def test_watchlist_watching_returns_wait_even_if_proof_and_contract_pass():
    payload = _clean_decision_payload(watchlist_candidate=_clean_watchlist_payload(status="watching"))
    result = check_advisory_decision_intake(payload)
    assert result.verdict == AdvisoryVerdict.WAIT
    assert result.watchlist_status == WatchlistCandidateStatus.WATCHING


def test_watchlist_triggered_with_passing_proof_and_contract_returns_take():
    payload = _clean_decision_payload(watchlist_candidate=_clean_watchlist_payload(status="triggered"))
    result = check_advisory_decision_intake(payload)
    assert result.verdict == AdvisoryVerdict.TAKE
    assert result.watchlist_status == WatchlistCandidateStatus.TRIGGERED


def test_watchlist_invalidated_returns_avoid():
    payload = _clean_decision_payload(
        watchlist_candidate=_clean_watchlist_payload(status="invalidated", notes="broke down")
    )
    result = check_advisory_decision_intake(payload)
    assert result.verdict == AdvisoryVerdict.AVOID
    assert result.watchlist_status == WatchlistCandidateStatus.INVALIDATED


def test_watchlist_skipped_exited_expired_all_return_avoid():
    for status in ("skipped", "exited", "expired"):
        notes = "reason" if status == "skipped" else ""
        payload = _clean_decision_payload(
            watchlist_candidate=_clean_watchlist_payload(status=status, notes=notes)
        )
        result = check_advisory_decision_intake(payload)
        assert result.verdict == AdvisoryVerdict.AVOID, status


# --- 5. morning scan packet failure returns WAIT --------------------------------------------------------


def test_invalid_morning_scan_packet_returns_wait():
    payload = _clean_decision_payload(morning_scan_packet={"market_context": {}, "candidates": []})
    result = check_advisory_decision_intake(payload)
    assert result.verdict == AdvisoryVerdict.WAIT


# --- 6. malformed payloads return structured failure, never an exception ---------------------------------


def test_non_dict_payload_returns_structured_failure_not_exception():
    for bad_payload in (None, "not a dict", 42, ["a", "list"]):
        result = check_advisory_decision_intake(bad_payload)
        assert isinstance(result, AdvisoryDecisionResult)
        assert result.verdict == AdvisoryVerdict.AVOID
        assert "malformed payload" in result.blocking_reasons[0]


def test_malformed_proof_packet_section_returns_avoid_not_exception():
    payload = _clean_decision_payload(proof_packet="not a dict")
    result = check_advisory_decision_intake(payload)
    assert result.verdict == AdvisoryVerdict.AVOID
    assert not result.proof_valid


def test_malformed_contract_quality_section_returns_avoid_not_exception():
    payload = _clean_decision_payload(contract_quality="not a dict")
    result = check_advisory_decision_intake(payload)
    assert result.verdict == AdvisoryVerdict.AVOID
    assert result.contract_verdict == GateVerdict.BLOCK


def test_malformed_watchlist_section_returns_wait_not_exception():
    payload = _clean_decision_payload(watchlist_candidate="not a dict")
    result = check_advisory_decision_intake(payload)
    assert result.verdict == AdvisoryVerdict.WAIT
    assert result.watchlist_status is None


def test_invalid_risk_accepted_value_falls_back_safely_not_exception():
    payload = _clean_decision_payload(
        contract_quality=_clean_contract_quality_payload(iv_event_risk="moderate"),
        risk_accepted="not-a-boolean",
    )
    result = check_advisory_decision_intake(payload)
    assert result.verdict == AdvisoryVerdict.WAIT  # falls back to risk_accepted=False, not an exception


# --- 7. no order/action fields exist anywhere on the result or module ------------------------------------


def test_advisory_decision_result_has_no_order_action_fields():
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(AdvisoryDecisionResult)}
    for forbidden in ("order", "order_id", "ticket", "submit", "place", "execute", "broker_order"):
        assert forbidden not in field_names


def test_advisory_decision_module_has_no_order_action_verbs():
    source = _module_source()
    for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_advisory_decision_module_has_no_fixture_status_import():
    imported = _imported_modules(advisory_decision_module)
    assert not any("fixture_status" in name for name in imported)


# --- 8. no scanner/broker/execution import, no clock access, no I/O -------------------------------------------


def test_advisory_decision_module_has_no_scanner_import():
    imported = _imported_modules(advisory_decision_module)
    assert not any("options_manager.scanner" in name for name in imported)
    assert not any(name == "options_manager.strategies" for name in imported)


def test_advisory_decision_module_has_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_advisory_decision_module_has_no_cross_boundary_imports_outside_options_manager():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "enum", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_advisory_decision_module_has_no_credential_identifiers():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_advisory_decision_module_does_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_advisory_decision_module_has_no_network_call_text():
    source = _module_source().lower()
    for forbidden in ("httpx.", "requests.", "socket."):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_advisory_decision_module_has_no_clock_access():
    source = _module_source()
    for forbidden in ("datetime.now(", "time.time(", "date.today("):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_no_scanner_execution_or_broker_module_imports_advisory_decision():
    """Checks actual import statements, not a raw substring search."""
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
            imported = _imported_modules_at_path(path)
            if any("advisory_decision" in name for name in imported):
                offenders.append(str(path))
    assert not offenders, f"advisory_decision must not be imported from: {offenders}"


def _imported_modules_at_path(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
            modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return modules
