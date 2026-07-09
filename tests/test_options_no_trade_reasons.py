"""
tests/test_options_no_trade_reasons.py

options_manager/validation/no_trade_reasons.py tests. Proves rejected/
skipped trade ideas can be recorded as structured evidence -- both
derived automatically from a proof_packet_intake.IntakeResult and
recorded purely from manual judgment (CHASING_CANDLE, EMOTIONAL_TRADE) --
without ever raising on malformed input, and without touching a
scanner/broker/execution path or the system clock.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.validation.no_trade_reasons as no_trade_reasons_module
from options_manager.validation.proof_packet_intake import IntakeResult
from options_manager.validation.no_trade_reasons import (
    NoTradeDecision,
    NoTradeDecisionResult,
    NoTradeReason,
    build_no_trade_decision_from_intake,
    reasons_from_intake_result,
    record_no_trade_decision,
)

_SCANNED_MODULES = (no_trade_reasons_module,)

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

_EXPECTED_REASON_VALUES = {
    "missing_setup",
    "missing_trigger",
    "missing_invalidation",
    "missing_target",
    "missing_contract_data",
    "wide_spread",
    "low_volume",
    "low_open_interest",
    "too_short_dte",
    "premium_too_expensive",
    "risk_too_high",
    "against_spy_qqq_context",
    "against_gex_regime",
    "no_source_reference",
    "chasing_candle",
    "emotional_trade",
    "other",
}


def _module_source() -> str:
    return Path(no_trade_reasons_module.__file__).read_text()


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _manual_payload(**overrides) -> dict:
    payload = dict(
        ticker="ORCL",
        attempted_direction="CALL",
        timestamp_or_session="2026-07-09 09:40 ET",
        reasons=["chasing_candle"],
        blocking=True,
        notes="entry would have been chasing a 3rd green 5m bar",
        source="manual",
    )
    payload.update(overrides)
    return payload


# --- 1. enum shape -------------------------------------------------------------------------------------


def test_no_trade_reason_has_expected_members():
    assert {r.value for r in NoTradeReason} == _EXPECTED_REASON_VALUES


# --- 2. reasons_from_intake_result() maps field-level failures correctly -------------------------------


def test_valid_intake_result_maps_to_no_reasons():
    intake_result = IntakeResult(valid=True)
    assert reasons_from_intake_result(intake_result) == ()


def test_missing_invalidation_maps_to_missing_invalidation_reason():
    intake_result = IntakeResult(valid=False, missing_fields=("underlying_invalidation",))
    reasons = reasons_from_intake_result(intake_result)
    assert NoTradeReason.MISSING_INVALIDATION in reasons


def test_missing_target_maps_to_missing_target_reason():
    intake_result = IntakeResult(valid=False, missing_fields=("target_1", "target_2"))
    reasons = reasons_from_intake_result(intake_result)
    assert NoTradeReason.MISSING_TARGET in reasons
    # target_1 and target_2 both map to the same reason -- no duplicates:
    assert reasons.count(NoTradeReason.MISSING_TARGET) == 1


def test_missing_liquidity_fields_map_to_specific_reasons():
    intake_result = IntakeResult(
        valid=False,
        missing_fields=("bid", "ask", "volume", "open_interest", "spread_percent"),
    )
    reasons = set(reasons_from_intake_result(intake_result))
    assert NoTradeReason.MISSING_CONTRACT_DATA in reasons  # bid/ask
    assert NoTradeReason.LOW_VOLUME in reasons
    assert NoTradeReason.LOW_OPEN_INTEREST in reasons
    assert NoTradeReason.WIDE_SPREAD in reasons


def test_missing_source_reference_maps_to_no_source_reference_reason():
    intake_result = IntakeResult(valid=False, missing_fields=("source_references",))
    reasons = reasons_from_intake_result(intake_result)
    assert reasons == (NoTradeReason.NO_SOURCE_REFERENCE,)


def test_blocking_reasons_are_also_parsed_for_present_but_invalid_fields():
    """A field can be present but structurally invalid (e.g. strike=0)
    and only show up in blocking_reasons, not missing_fields."""
    intake_result = IntakeResult(
        valid=False, blocking_reasons=("missing/invalid strike", "missing/invalid bid")
    )
    reasons = reasons_from_intake_result(intake_result)
    assert NoTradeReason.MISSING_CONTRACT_DATA in reasons


def test_unparseable_blocking_reason_falls_back_to_other():
    intake_result = IntakeResult(valid=False, blocking_reasons=("something completely different",))
    assert reasons_from_intake_result(intake_result) == (NoTradeReason.OTHER,)


def test_invalid_with_no_detail_still_reports_other_not_empty():
    intake_result = IntakeResult(valid=False)
    assert reasons_from_intake_result(intake_result) == (NoTradeReason.OTHER,)


# --- 3. build_no_trade_decision_from_intake() ------------------------------------------------------------


def test_build_from_intake_marks_blocking_true_when_invalid():
    intake_result = IntakeResult(valid=False, missing_fields=("underlying_invalidation",))
    decision = build_no_trade_decision_from_intake(
        ticker="ORCL",
        attempted_direction="CALL",
        timestamp_or_session="2026-07-09 09:40 ET",
        intake_result=intake_result,
    )
    assert isinstance(decision, NoTradeDecision)
    assert decision.blocking is True
    assert NoTradeReason.MISSING_INVALIDATION in decision.reasons
    assert decision.source == "proof_packet_intake"


def test_build_from_intake_marks_blocking_false_when_valid():
    intake_result = IntakeResult(valid=True)
    decision = build_no_trade_decision_from_intake(
        ticker="ORCL",
        attempted_direction="CALL",
        timestamp_or_session="2026-07-09 09:40 ET",
        intake_result=intake_result,
    )
    assert decision.blocking is False
    assert decision.reasons == ()


# --- 4. multiple reasons can be attached to one decision --------------------------------------------------


def test_multiple_reasons_can_be_attached_to_one_decision():
    payload = _manual_payload(reasons=["chasing_candle", "emotional_trade", "against_gex_regime"])
    result = record_no_trade_decision(payload)
    assert result.valid
    assert result.decision.reasons == (
        NoTradeReason.CHASING_CANDLE,
        NoTradeReason.EMOTIONAL_TRADE,
        NoTradeReason.AGAINST_GEX_REGIME,
    )


def test_emotional_and_chasing_reasons_can_be_represented_manually():
    result = record_no_trade_decision(_manual_payload(reasons=["EMOTIONAL_TRADE"]))
    assert result.valid
    assert result.decision.reasons == (NoTradeReason.EMOTIONAL_TRADE,)


def test_reason_enum_member_accepted_directly_alongside_strings():
    result = record_no_trade_decision(
        _manual_payload(reasons=[NoTradeReason.RISK_TOO_HIGH, "wide_spread"])
    )
    assert result.valid
    assert result.decision.reasons == (NoTradeReason.RISK_TOO_HIGH, NoTradeReason.WIDE_SPREAD)


# --- 5. manual recording succeeds on a complete payload -----------------------------------------------


def test_complete_manual_payload_is_valid():
    result = record_no_trade_decision(_manual_payload())
    assert isinstance(result, NoTradeDecisionResult)
    assert result.valid
    assert result.errors == ()
    assert isinstance(result.decision, NoTradeDecision)
    assert result.decision.ticker == "ORCL"
    assert result.decision.blocking is True


def test_blocking_accepts_common_boolean_strings():
    result = record_no_trade_decision(_manual_payload(blocking="true"))
    assert result.valid
    assert result.decision.blocking is True

    result = record_no_trade_decision(_manual_payload(blocking="false"))
    assert result.valid
    assert result.decision.blocking is False


def test_notes_and_source_default_to_empty_string_when_absent():
    payload = _manual_payload()
    del payload["notes"]
    del payload["source"]
    result = record_no_trade_decision(payload)
    assert result.valid
    assert result.decision.notes == ""
    assert result.decision.source == ""


# --- 6. malformed input returns structured failure, never an exception --------------------------------


def test_non_dict_payload_returns_structured_failure_not_exception():
    for bad_payload in (None, "not a dict", 42, ["a", "list"]):
        result = record_no_trade_decision(bad_payload)
        assert isinstance(result, NoTradeDecisionResult)
        assert not result.valid
        assert result.errors
        assert "malformed payload" in result.errors[0]


def test_missing_required_fields_returns_structured_failure():
    result = record_no_trade_decision({})
    assert not result.valid
    assert any("ticker" in e for e in result.errors)
    assert any("attempted_direction" in e for e in result.errors)
    assert any("timestamp_or_session" in e for e in result.errors)
    assert any("reasons" in e for e in result.errors)
    assert any("blocking" in e for e in result.errors)
    assert result.decision is None


def test_unknown_reason_string_returns_structured_failure_not_exception():
    result = record_no_trade_decision(_manual_payload(reasons=["not_a_real_reason"]))
    assert not result.valid
    assert any("not_a_real_reason" in e for e in result.errors)
    assert result.decision is None


def test_invalid_blocking_value_returns_structured_failure_not_exception():
    result = record_no_trade_decision(_manual_payload(blocking="maybe"))
    assert not result.valid
    assert any("blocking" in e for e in result.errors)


def test_never_reports_partial_valid_for_multiple_problems():
    result = record_no_trade_decision({"reasons": ["not_real"], "blocking": "maybe"})
    assert not result.valid
    assert len(result.errors) >= 4


# --- 7. no scanner/broker/execution import, no clock access, no I/O -------------------------------------


def test_no_trade_reasons_module_has_no_scanner_import():
    imported = _imported_modules(no_trade_reasons_module)
    assert not any("options_manager.scanner" in name for name in imported)
    assert not any(name == "options_manager.strategies" for name in imported)


def test_no_trade_reasons_module_has_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_no_trade_reasons_module_has_no_cross_boundary_imports_outside_options_manager():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "enum", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_no_trade_reasons_module_has_no_credential_identifiers():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_no_trade_reasons_module_does_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_no_trade_reasons_module_has_no_network_call_text():
    source = _module_source().lower()
    for forbidden in ("httpx.", "requests.", "socket."):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_no_trade_reasons_module_has_no_clock_access():
    source = _module_source()
    for forbidden in ("datetime.now(", "time.time(", "date.today("):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_no_trade_reasons_module_has_no_fixture_status_import():
    imported = _imported_modules(no_trade_reasons_module)
    assert not any("fixture_status" in name for name in imported)


def test_no_trade_reasons_module_has_no_order_action_verbs():
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


def test_no_scanner_execution_or_broker_module_imports_no_trade_reasons():
    """Checks actual import statements, not a raw substring search --
    this codebase already has unrelated identifiers like
    `top_no_trade_reasons` (a scanner report field predating this
    module) that would false-positive on a plain text search."""
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
            if any("no_trade_reasons" in name for name in imported):
                offenders.append(str(path))
    assert not offenders, f"no_trade_reasons must not be imported from: {offenders}"


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
