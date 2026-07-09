"""
tests/test_options_contract_quality_gate.py

options_manager/validation/contract_quality_gate.py tests. Proves the
standalone advisory contract-quality gate blocks the specific failure
modes it was built for (missing quote data, wide spreads, thin
liquidity, too-short DTE, over-cap premium, over-plan risk, weak
reward-to-risk, elevated IV/event and theta risk), respects the two
explicit human overrides (dte_exceptional, premium_risk_accepted), never
raises on malformed manual input, and never touches a scanner/broker/
execution path or the system clock.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.validation.contract_quality_gate as gate_module
from options_manager.validation.contract_quality_gate import (
    ContractQualityInput,
    ContractQualityResult,
    GateVerdict,
    check_contract_quality_intake,
    evaluate_contract_quality,
)

_SCANNED_MODULES = (gate_module,)

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
    return Path(gate_module.__file__).read_text()


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _clean_payload(**overrides) -> dict:
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
        notes="clean liquid swing setup",
    )
    payload.update(overrides)
    return payload


def _clean_contract(**overrides) -> ContractQualityInput:
    return ContractQualityInput(**_clean_payload(**overrides))


# --- 1. a clean liquid 45+DTE contract passes ----------------------------------------------------------


def test_clean_liquid_contract_passes():
    result = evaluate_contract_quality(_clean_contract())
    assert isinstance(result, ContractQualityResult)
    assert result.verdict == GateVerdict.PASS
    assert result.blocking_reasons == ()
    assert result.warnings == ()


def test_clean_payload_via_intake_passes():
    result = check_contract_quality_intake(_clean_payload())
    assert result.verdict == GateVerdict.PASS
    assert isinstance(result.contract, ContractQualityInput)


# --- 2. missing bid/ask/spread blocks -------------------------------------------------------------------


def test_missing_bid_blocks():
    result = evaluate_contract_quality(_clean_contract(bid=0.0))
    assert result.verdict == GateVerdict.BLOCK
    assert any("bid" in r for r in result.blocking_reasons)


def test_missing_ask_blocks():
    result = evaluate_contract_quality(_clean_contract(ask=0.0))
    assert result.verdict == GateVerdict.BLOCK
    assert any("ask" in r for r in result.blocking_reasons)


def test_missing_spread_percent_blocks():
    result = evaluate_contract_quality(_clean_contract(spread_percent=-1.0))
    assert result.verdict == GateVerdict.BLOCK
    assert any("spread_percent" in r for r in result.blocking_reasons)


def test_wide_spread_blocks():
    result = evaluate_contract_quality(_clean_contract(spread_percent=25.0))
    assert result.verdict == GateVerdict.BLOCK
    assert any("spread too wide" in r for r in result.blocking_reasons)


# --- 3. low liquidity blocks ------------------------------------------------------------------------------


def test_missing_volume_blocks():
    result = evaluate_contract_quality(_clean_contract(volume=0))
    assert result.verdict == GateVerdict.BLOCK
    assert any("volume" in r for r in result.blocking_reasons)


def test_low_volume_blocks():
    result = evaluate_contract_quality(_clean_contract(volume=10))
    assert result.verdict == GateVerdict.BLOCK
    assert any("low volume" in r for r in result.blocking_reasons)


def test_missing_open_interest_blocks():
    result = evaluate_contract_quality(_clean_contract(open_interest=0))
    assert result.verdict == GateVerdict.BLOCK
    assert any("open_interest" in r for r in result.blocking_reasons)


def test_low_open_interest_blocks():
    result = evaluate_contract_quality(_clean_contract(open_interest=50))
    assert result.verdict == GateVerdict.BLOCK
    assert any("low open interest" in r for r in result.blocking_reasons)


# --- 4. DTE too short blocks unless exceptional -----------------------------------------------------------


def test_short_dte_blocks_by_default():
    result = evaluate_contract_quality(_clean_contract(dte=3))
    assert result.verdict == GateVerdict.BLOCK
    assert any("DTE too short" in r for r in result.blocking_reasons)


def test_short_dte_warns_instead_of_blocking_when_exceptional():
    result = evaluate_contract_quality(_clean_contract(dte=3, dte_exceptional=True))
    assert result.verdict == GateVerdict.WARN
    assert not any("DTE too short" in r for r in result.blocking_reasons)
    assert any("DTE below preferred minimum" in w for w in result.warnings)


def test_missing_dte_blocks():
    result = evaluate_contract_quality(_clean_contract(dte=0))
    assert result.verdict == GateVerdict.BLOCK
    assert any("dte" in r for r in result.blocking_reasons)


# --- 5. premium over cap blocks unless accepted -------------------------------------------------------------


def test_premium_over_cap_blocks_by_default():
    result = evaluate_contract_quality(_clean_contract(premium=350.0))
    assert result.verdict == GateVerdict.BLOCK
    assert any("premium" in r and "exceeds" in r for r in result.blocking_reasons)


def test_premium_over_cap_warns_instead_of_blocking_when_risk_accepted():
    result = evaluate_contract_quality(_clean_contract(premium=350.0, premium_risk_accepted=True))
    assert result.verdict == GateVerdict.WARN
    assert not any("exceeds" in r for r in result.blocking_reasons)
    assert any("premium above preferred cap" in w for w in result.warnings)


# --- 6. max dollar risk over plan blocks (no override) -----------------------------------------------------


def test_max_dollar_risk_over_plan_blocks():
    result = evaluate_contract_quality(_clean_contract(max_dollar_risk=500.0))
    assert result.verdict == GateVerdict.BLOCK
    assert any("max_dollar_risk" in r for r in result.blocking_reasons)


# --- 7. weak target/risk blocks ---------------------------------------------------------------------------


def test_weak_distance_to_target_blocks():
    result = evaluate_contract_quality(_clean_contract(distance_to_target=0.5))
    assert result.verdict == GateVerdict.BLOCK
    assert any("distance_to_target" in r for r in result.blocking_reasons)


# --- 8. IV/event risk warns or blocks depending on severity --------------------------------------------------


def test_iv_event_risk_none_has_no_effect():
    result = evaluate_contract_quality(_clean_contract(iv_event_risk="none"))
    assert result.verdict == GateVerdict.PASS


def test_iv_event_risk_moderate_warns():
    result = evaluate_contract_quality(_clean_contract(iv_event_risk="moderate"))
    assert result.verdict == GateVerdict.WARN
    assert any("IV/event risk is MODERATE" in w for w in result.warnings)


def test_iv_event_risk_high_blocks():
    result = evaluate_contract_quality(_clean_contract(iv_event_risk="high"))
    assert result.verdict == GateVerdict.BLOCK
    assert any("IV/event risk is HIGH" in r for r in result.blocking_reasons)


# --- 9. theta risk warns or blocks depending on severity ------------------------------------------------------


def test_theta_risk_moderate_warns():
    result = evaluate_contract_quality(_clean_contract(theta_risk="moderate"))
    assert result.verdict == GateVerdict.WARN
    assert any("theta risk is MODERATE" in w for w in result.warnings)


def test_theta_risk_high_blocks():
    result = evaluate_contract_quality(_clean_contract(theta_risk="high"))
    assert result.verdict == GateVerdict.BLOCK
    assert any("theta risk is HIGH" in r for r in result.blocking_reasons)


# --- 10. multiple failures reported together, never partial -------------------------------------------------


def test_multiple_blocking_reasons_reported_together():
    result = evaluate_contract_quality(
        _clean_contract(bid=0.0, volume=0, dte=1, premium=1000.0, max_dollar_risk=999.0)
    )
    assert result.verdict == GateVerdict.BLOCK
    assert len(result.blocking_reasons) >= 5


# --- 11. malformed manual payload returns structured failure, never an exception ------------------------------


def test_non_dict_payload_returns_structured_failure_not_exception():
    for bad_payload in (None, "not a dict", 42, ["a", "list"]):
        result = check_contract_quality_intake(bad_payload)
        assert isinstance(result, ContractQualityResult)
        assert result.verdict == GateVerdict.BLOCK
        assert result.contract is None
        assert "malformed payload" in result.blocking_reasons[0]


def test_missing_required_field_returns_structured_failure():
    payload = _clean_payload()
    del payload["strike"]
    result = check_contract_quality_intake(payload)
    assert result.verdict == GateVerdict.BLOCK
    assert any("strike" in r for r in result.blocking_reasons)
    assert result.contract is None


def test_unconvertible_numeric_field_returns_structured_failure_not_exception():
    result = check_contract_quality_intake(_clean_payload(strike="not-a-number"))
    assert result.verdict == GateVerdict.BLOCK
    assert any("strike" in r for r in result.blocking_reasons)
    assert result.contract is None


def test_invalid_severity_value_returns_structured_failure_not_exception():
    result = check_contract_quality_intake(_clean_payload(iv_event_risk="catastrophic"))
    assert result.verdict == GateVerdict.BLOCK
    assert any("iv_event_risk" in r for r in result.blocking_reasons)


def test_invalid_direction_returns_structured_failure_not_exception():
    result = check_contract_quality_intake(_clean_payload(direction="SIDEWAYS"))
    assert result.verdict == GateVerdict.BLOCK
    assert any("direction" in r for r in result.blocking_reasons)


def test_dte_exceptional_string_is_coerced_via_intake():
    result = check_contract_quality_intake(_clean_payload(dte=3, dte_exceptional="true"))
    assert result.verdict == GateVerdict.WARN
    assert result.contract.dte_exceptional is True


def test_invalid_boolean_override_returns_structured_failure_not_exception():
    result = check_contract_quality_intake(_clean_payload(dte_exceptional="maybe"))
    assert result.verdict == GateVerdict.BLOCK
    assert any("dte_exceptional" in r for r in result.blocking_reasons)


# --- 12. no scanner/broker/execution import, no clock access, no I/O -----------------------------------------


def test_contract_quality_gate_module_has_no_scanner_import():
    imported = _imported_modules(gate_module)
    assert not any("options_manager.scanner" in name for name in imported)
    assert not any(name == "options_manager.strategies" for name in imported)


def test_contract_quality_gate_module_has_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_contract_quality_gate_module_has_no_cross_boundary_imports_outside_options_manager():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "enum", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_contract_quality_gate_module_has_no_fixture_status_import():
    imported = _imported_modules(gate_module)
    assert not any("fixture_status" in name for name in imported)


def test_contract_quality_gate_module_has_no_credential_identifiers():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_contract_quality_gate_module_does_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_contract_quality_gate_module_has_no_network_call_text():
    source = _module_source().lower()
    for forbidden in ("httpx.", "requests.", "socket."):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_contract_quality_gate_module_has_no_clock_access():
    source = _module_source()
    for forbidden in ("datetime.now(", "time.time(", "date.today("):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_contract_quality_gate_module_has_no_order_action_verbs():
    source = _module_source()
    for forbidden in (
        "place_order",
        "submit_order",
        "cancel_order",
        "replace_order",
        "execute_order",
        "live_order",
    ):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_no_scanner_execution_or_broker_module_imports_contract_quality_gate():
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
            if any("contract_quality_gate" in name for name in imported):
                offenders.append(str(path))
    assert not offenders, f"contract_quality_gate must not be imported from: {offenders}"


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
