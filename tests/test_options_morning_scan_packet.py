"""
tests/test_options_morning_scan_packet.py

options_manager/validation/morning_scan_packet.py tests. Proves a
morning scan packet captures broad market context plus zero or more
ticker candidates, blocks/flags the specific failure modes it was built
for (missing market context, missing entry/stop/target, weak or invalid
risk_reward, invalid status), propagates a market-context failure into
every candidate's own evaluation, supports multiple candidates, never
raises on malformed manual input, and never touches a scanner/broker/
execution path or the system clock.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.validation.morning_scan_packet as scan_packet_module
from options_manager.validation.morning_scan_packet import (
    MarketContext,
    MorningScanPacket,
    MorningScanPacketResult,
    TickerCandidate,
    TickerCandidateStatus,
    check_morning_scan_packet_intake,
    evaluate_morning_scan_packet,
)

_SCANNED_MODULES = (scan_packet_module,)

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
    return Path(scan_packet_module.__file__).read_text()


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _clean_market_context_payload(**overrides) -> dict:
    payload = dict(
        scan_date="2026-07-09",
        session="AM",
        gex_regime="positive gamma",
        spy_flip="620",
        qqq_flip="540",
        spy_trend="up",
        qqq_trend="up",
        gap_percent=0.4,
        location_vs_yesterday="above yesterday's high",
        broad_market_direction="bullish",
    )
    payload.update(overrides)
    return payload


def _clean_candidate_payload(**overrides) -> dict:
    payload = dict(
        ticker="ORCL",
        name="Oracle",
        spot=210.5,
        flip=208.0,
        regime="uptrend",
        orb_high=211.2,
        orb_low=209.1,
        res1=213.0,
        res2=216.0,
        sup1=207.5,
        distance_to_res1=2.5,
        distance_to_sup1=3.0,
        volume=1200000,
        signa_grade="A",
        signa_score=8.5,
        entry=211.5,
        stop=209.0,
        target=216.0,
        risk_reward=1.8,
        current_candle_behavior="tight consolidation above ORB high",
        status="watching",
    )
    payload.update(overrides)
    return payload


def _clean_scan_payload(candidates=None, **market_overrides) -> dict:
    return dict(
        market_context=_clean_market_context_payload(**market_overrides),
        candidates=[_clean_candidate_payload()] if candidates is None else candidates,
    )


def _clean_market_context(**overrides) -> MarketContext:
    return MarketContext(**_clean_market_context_payload(**overrides))


def _clean_candidate(**overrides) -> TickerCandidate:
    payload = _clean_candidate_payload(**overrides)
    payload["status"] = TickerCandidateStatus(payload["status"])
    return TickerCandidate(**payload)


# --- 1. a complete scan packet passes --------------------------------------------------------------


def test_complete_scan_packet_passes():
    packet = MorningScanPacket(market_context=_clean_market_context(), candidates=(_clean_candidate(),))
    result = evaluate_morning_scan_packet(packet)
    assert isinstance(result, MorningScanPacketResult)
    assert result.valid
    assert result.market_context_blocking_reasons == ()
    assert len(result.candidate_results) == 1
    assert result.candidate_results[0].valid


def test_complete_scan_packet_via_intake_passes():
    result = check_morning_scan_packet_intake(_clean_scan_payload())
    assert result.valid
    assert isinstance(result.packet, MorningScanPacket)


def test_scan_packet_with_no_candidates_is_valid():
    """Zero candidates is a valid state -- context was captured, nothing
    stood out."""
    packet = MorningScanPacket(market_context=_clean_market_context(), candidates=())
    result = evaluate_morning_scan_packet(packet)
    assert result.valid
    assert result.candidate_results == ()


# --- 2. missing market context fields fail --------------------------------------------------------------


def test_missing_gex_regime_warns_but_does_not_block():
    """GEX is optional enrichment. A blank regime must NOT invalidate the
    packet — that would make a GEX subscription a hard dependency of the
    whole morning scan."""
    packet = MorningScanPacket(market_context=_clean_market_context(gex_regime=""), candidates=())
    result = evaluate_morning_scan_packet(packet)
    assert result.valid
    assert result.market_context_blocking_reasons == ()
    assert any("GEX_UNAVAILABLE" in w for w in result.market_context_warnings)


def test_gex_unavailable_warning_rides_along_on_every_candidate():
    packet = MorningScanPacket(
        market_context=_clean_market_context(gex_regime=""),
        candidates=(_clean_candidate(ticker="ORCL"), _clean_candidate(ticker="AMD")),
    )
    result = evaluate_morning_scan_packet(packet)
    assert result.valid
    for candidate_result in result.candidate_results:
        assert candidate_result.valid
        assert any("GEX_UNAVAILABLE" in w for w in candidate_result.warnings)
        assert not any("missing market context" in r for r in candidate_result.blocking_reasons)


def test_supplied_gex_regime_produces_no_gex_warning():
    packet = MorningScanPacket(
        market_context=_clean_market_context(gex_regime="positive"), candidates=()
    )
    result = evaluate_morning_scan_packet(packet)
    assert result.valid
    assert result.market_context_warnings == ()


def test_missing_spy_qqq_context_fails():
    packet = MorningScanPacket(
        market_context=_clean_market_context(spy_trend="", qqq_trend=""), candidates=()
    )
    result = evaluate_morning_scan_packet(packet)
    assert not result.valid
    assert any("spy_trend" in r for r in result.market_context_blocking_reasons)
    assert any("qqq_trend" in r for r in result.market_context_blocking_reasons)


def test_missing_market_context_propagates_into_every_candidate():
    # Uses a genuinely required field (spy_trend); GEX is optional and is
    # covered separately by the GEX_UNAVAILABLE tests above.
    packet = MorningScanPacket(
        market_context=_clean_market_context(spy_trend=""),
        candidates=(_clean_candidate(ticker="ORCL"), _clean_candidate(ticker="AMD")),
    )
    result = evaluate_morning_scan_packet(packet)
    assert not result.valid
    for candidate_result in result.candidate_results:
        assert not candidate_result.valid
        assert any("missing market context" in r for r in candidate_result.blocking_reasons)


# --- 3. missing entry/stop/target fails --------------------------------------------------------------


def test_missing_entry_fails():
    packet = MorningScanPacket(market_context=_clean_market_context(), candidates=(_clean_candidate(entry=0.0),))
    result = evaluate_morning_scan_packet(packet)
    assert not result.valid
    assert any("missing entry" in r for r in result.candidate_results[0].blocking_reasons)


def test_missing_stop_fails():
    packet = MorningScanPacket(market_context=_clean_market_context(), candidates=(_clean_candidate(stop=0.0),))
    result = evaluate_morning_scan_packet(packet)
    assert not result.valid
    assert any("missing stop" in r for r in result.candidate_results[0].blocking_reasons)


def test_missing_target_fails():
    packet = MorningScanPacket(market_context=_clean_market_context(), candidates=(_clean_candidate(target=0.0),))
    result = evaluate_morning_scan_packet(packet)
    assert not result.valid
    assert any("missing target" in r for r in result.candidate_results[0].blocking_reasons)


# --- 4. weak/invalid R:R fails or warns depending on threshold -----------------------------------------------


def test_invalid_risk_reward_blocks():
    packet = MorningScanPacket(
        market_context=_clean_market_context(), candidates=(_clean_candidate(risk_reward=0.0),)
    )
    result = evaluate_morning_scan_packet(packet)
    assert not result.candidate_results[0].valid
    assert any("risk_reward" in r for r in result.candidate_results[0].blocking_reasons)


def test_risk_reward_below_minimum_blocks():
    packet = MorningScanPacket(
        market_context=_clean_market_context(), candidates=(_clean_candidate(risk_reward=0.7),)
    )
    result = evaluate_morning_scan_packet(packet)
    assert not result.candidate_results[0].valid
    assert any("below the" in r and "minimum" in r for r in result.candidate_results[0].blocking_reasons)


def test_risk_reward_between_minimum_and_preferred_warns_not_blocks():
    packet = MorningScanPacket(
        market_context=_clean_market_context(), candidates=(_clean_candidate(risk_reward=1.2),)
    )
    result = evaluate_morning_scan_packet(packet)
    assert result.candidate_results[0].valid
    assert any("preferred" in w for w in result.candidate_results[0].warnings)


def test_risk_reward_at_or_above_preferred_passes_clean():
    packet = MorningScanPacket(
        market_context=_clean_market_context(), candidates=(_clean_candidate(risk_reward=2.0),)
    )
    result = evaluate_morning_scan_packet(packet)
    assert result.candidate_results[0].valid
    assert result.candidate_results[0].warnings == ()


# --- 5. multiple candidates supported --------------------------------------------------------------------


def test_multiple_candidates_supported_with_independent_results():
    packet = MorningScanPacket(
        market_context=_clean_market_context(),
        candidates=(
            _clean_candidate(ticker="ORCL"),
            _clean_candidate(ticker="AMD", entry=0.0),
        ),
    )
    result = evaluate_morning_scan_packet(packet)
    assert not result.valid
    assert len(result.candidate_results) == 2
    by_ticker = {c.ticker: c for c in result.candidate_results}
    assert by_ticker["ORCL"].valid
    assert not by_ticker["AMD"].valid


def test_multiple_candidates_via_intake():
    payload = _clean_scan_payload(
        candidates=[
            _clean_candidate_payload(ticker="ORCL"),
            _clean_candidate_payload(ticker="AMD"),
            _clean_candidate_payload(ticker="QCOM"),
        ]
    )
    result = check_morning_scan_packet_intake(payload)
    assert result.valid
    assert len(result.candidate_results) == 3
    assert len(result.packet.candidates) == 3


# --- 6. invalid status rejected ------------------------------------------------------------------------------


def test_invalid_status_string_returns_structured_failure_not_exception():
    payload = _clean_scan_payload(candidates=[_clean_candidate_payload(status="not_a_real_status")])
    result = check_morning_scan_packet_intake(payload)
    assert not result.valid
    assert any("status" in r for r in result.candidate_results[0].blocking_reasons)
    assert result.packet is None


def test_status_accepts_case_insensitive_string_via_intake():
    payload = _clean_scan_payload(candidates=[_clean_candidate_payload(status="TRIGGERED")])
    result = check_morning_scan_packet_intake(payload)
    assert result.valid
    assert result.packet.candidates[0].status == TickerCandidateStatus.TRIGGERED


# --- 7. malformed payloads return structured failure, never an exception ---------------------------------------


def test_non_dict_payload_returns_structured_failure_not_exception():
    for bad_payload in (None, "not a dict", 42, ["a", "list"]):
        result = check_morning_scan_packet_intake(bad_payload)
        assert isinstance(result, MorningScanPacketResult)
        assert not result.valid
        assert result.market_context_blocking_reasons
        assert "malformed payload" in result.market_context_blocking_reasons[0]


def test_missing_market_context_key_returns_structured_failure():
    result = check_morning_scan_packet_intake({"candidates": []})
    assert not result.valid
    assert result.market_context_blocking_reasons
    assert result.packet is None


def test_non_dict_market_context_returns_structured_failure_not_exception():
    payload = _clean_scan_payload()
    payload["market_context"] = "not a dict"
    result = check_morning_scan_packet_intake(payload)
    assert not result.valid
    assert any("malformed market_context" in r for r in result.market_context_blocking_reasons)


def test_non_list_candidates_returns_structured_failure_not_exception():
    payload = _clean_scan_payload()
    payload["candidates"] = "not a list"
    result = check_morning_scan_packet_intake(payload)
    assert not result.valid
    assert any("malformed candidates" in r for r in result.candidate_results[0].blocking_reasons)


def test_malformed_candidate_item_returns_structured_failure_not_exception():
    payload = _clean_scan_payload(candidates=[_clean_candidate_payload(), "not a dict"])
    result = check_morning_scan_packet_intake(payload)
    assert not result.valid
    assert any(
        "malformed candidate payload" in r
        for c in result.candidate_results
        for r in c.blocking_reasons
    )


def test_unconvertible_numeric_field_returns_structured_failure_not_exception():
    payload = _clean_scan_payload(candidates=[_clean_candidate_payload(entry="not-a-number")])
    result = check_morning_scan_packet_intake(payload)
    assert not result.valid
    assert any("entry" in r for r in result.candidate_results[0].blocking_reasons)
    assert result.packet is None


def test_unconvertible_market_context_field_returns_structured_failure_not_exception():
    payload = _clean_scan_payload(gap_percent="not-a-number")
    result = check_morning_scan_packet_intake(payload)
    assert not result.valid
    assert any("gap_percent" in r for r in result.market_context_blocking_reasons)
    assert result.packet is None


# --- 8. no scanner/broker/execution import, no clock access, no I/O -------------------------------------------


def test_morning_scan_packet_module_has_no_scanner_import():
    imported = _imported_modules(scan_packet_module)
    assert not any("options_manager.scanner" in name for name in imported)
    assert not any(name == "options_manager.strategies" for name in imported)


def test_morning_scan_packet_module_has_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_morning_scan_packet_module_has_no_cross_boundary_imports_outside_options_manager():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "enum", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_morning_scan_packet_module_has_no_fixture_status_import():
    imported = _imported_modules(scan_packet_module)
    assert not any("fixture_status" in name for name in imported)


def test_morning_scan_packet_module_has_no_credential_identifiers():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_morning_scan_packet_module_does_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_morning_scan_packet_module_has_no_network_call_text():
    source = _module_source().lower()
    for forbidden in ("httpx.", "requests.", "socket."):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_morning_scan_packet_module_has_no_clock_access():
    source = _module_source()
    for forbidden in ("datetime.now(", "time.time(", "date.today("):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_morning_scan_packet_module_has_no_order_action_verbs():
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


def test_no_scanner_execution_or_broker_module_imports_morning_scan_packet():
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
            if any("morning_scan_packet" in name for name in imported):
                offenders.append(str(path))
    assert not offenders, f"morning_scan_packet must not be imported from: {offenders}"


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
