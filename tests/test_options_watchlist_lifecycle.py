"""
tests/test_options_watchlist_lifecycle.py

options_manager/validation/watchlist_lifecycle.py tests. Proves a
watchlist candidate is created in WATCHING, moves only through the
explicit allowed transitions, never transitions out of a terminal
status, requires a reason for SKIPPED, requires the fields a usable
candidate needs (entry_trigger, invalidation, targets), never raises on
malformed manual input, and never touches a scanner/broker/execution
path or the system clock.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.validation.watchlist_lifecycle as lifecycle_module
from options_manager.validation.watchlist_lifecycle import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    WatchlistCandidate,
    WatchlistCandidateResult,
    WatchlistCandidateStatus,
    check_watchlist_candidate_intake,
    create_watchlist_candidate,
    transition_candidate,
)

_SCANNED_MODULES = (lifecycle_module,)

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
    return Path(lifecycle_module.__file__).read_text()


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _clean_creation_kwargs(**overrides) -> dict:
    kwargs = dict(
        ticker="ORCL",
        direction="CALL",
        setup_type="2-1-2 continuation",
        timeframe="30m",
        entry_trigger="break above prior 30m high",
        invalidation="below prior 30m low",
        target_1="gamma wall",
        target_2="next resistance",
        created_at_or_session="2026-07-09 09:35 ET",
        notes="",
        source_reference="chart_screenshot.png",
    )
    kwargs.update(overrides)
    return kwargs


def _watching_candidate(**overrides) -> WatchlistCandidate:
    result = create_watchlist_candidate(**_clean_creation_kwargs(**overrides))
    assert result.valid, result.blocking_reasons
    return result.candidate


def _clean_payload(**overrides) -> dict:
    payload = _clean_creation_kwargs()
    payload["status"] = "watching"
    payload["last_updated_or_session"] = payload["created_at_or_session"]
    payload.update(overrides)
    return payload


# --- 1. a valid WATCHING candidate can be created -----------------------------------------------------


def test_valid_watching_candidate_can_be_created():
    result = create_watchlist_candidate(**_clean_creation_kwargs())
    assert isinstance(result, WatchlistCandidateResult)
    assert result.valid
    assert result.blocking_reasons == ()
    assert isinstance(result.candidate, WatchlistCandidate)
    assert result.candidate.status == WatchlistCandidateStatus.WATCHING
    assert result.candidate.last_updated_or_session == result.candidate.created_at_or_session


def test_valid_watching_candidate_via_intake():
    result = check_watchlist_candidate_intake(_clean_payload())
    assert result.valid
    assert result.candidate.status == WatchlistCandidateStatus.WATCHING


# --- 2. allowed transitions pass -----------------------------------------------------------------------


def test_watching_to_triggered_passes():
    candidate = _watching_candidate()
    result = transition_candidate(
        candidate, WatchlistCandidateStatus.TRIGGERED, last_updated_or_session="2026-07-09 10:00 ET"
    )
    assert result.valid
    assert result.candidate.status == WatchlistCandidateStatus.TRIGGERED


def test_watching_to_invalidated_passes():
    candidate = _watching_candidate()
    result = transition_candidate(
        candidate,
        WatchlistCandidateStatus.INVALIDATED,
        last_updated_or_session="2026-07-09 10:00 ET",
        notes="level broke the wrong way before trigger",
    )
    assert result.valid
    assert result.candidate.status == WatchlistCandidateStatus.INVALIDATED


def test_triggered_to_active_passes():
    candidate = _watching_candidate()
    triggered = transition_candidate(
        candidate, WatchlistCandidateStatus.TRIGGERED, last_updated_or_session="10:00 ET"
    ).candidate
    result = transition_candidate(
        triggered, WatchlistCandidateStatus.ACTIVE, last_updated_or_session="10:05 ET"
    )
    assert result.valid
    assert result.candidate.status == WatchlistCandidateStatus.ACTIVE


def test_active_to_exited_passes():
    candidate = _watching_candidate()
    triggered = transition_candidate(
        candidate, WatchlistCandidateStatus.TRIGGERED, last_updated_or_session="10:00 ET"
    ).candidate
    active = transition_candidate(
        triggered, WatchlistCandidateStatus.ACTIVE, last_updated_or_session="10:05 ET"
    ).candidate
    result = transition_candidate(
        active, WatchlistCandidateStatus.EXITED, last_updated_or_session="14:00 ET", notes="target hit"
    )
    assert result.valid
    assert result.candidate.status == WatchlistCandidateStatus.EXITED


def test_active_to_expired_passes():
    candidate = _watching_candidate()
    triggered = transition_candidate(
        candidate, WatchlistCandidateStatus.TRIGGERED, last_updated_or_session="10:00 ET"
    ).candidate
    active = transition_candidate(
        triggered, WatchlistCandidateStatus.ACTIVE, last_updated_or_session="10:05 ET"
    ).candidate
    result = transition_candidate(
        active, WatchlistCandidateStatus.EXPIRED, last_updated_or_session="16:00 ET", notes="contract expired"
    )
    assert result.valid


def test_any_non_terminal_status_can_transition_to_skipped_with_reason():
    for status in (
        WatchlistCandidateStatus.WATCHING,
        WatchlistCandidateStatus.TRIGGERED,
        WatchlistCandidateStatus.ACTIVE,
    ):
        assert WatchlistCandidateStatus.SKIPPED in ALLOWED_TRANSITIONS[status]


# --- 3. invalid/terminal transitions are rejected -----------------------------------------------------


def test_terminal_to_active_fails():
    candidate = _watching_candidate()
    invalidated = transition_candidate(
        candidate, WatchlistCandidateStatus.INVALIDATED, last_updated_or_session="10:00 ET", notes="broke down"
    ).candidate
    result = transition_candidate(
        invalidated, WatchlistCandidateStatus.ACTIVE, last_updated_or_session="10:05 ET"
    )
    assert not result.valid
    assert any("terminal" in r for r in result.blocking_reasons)
    assert result.candidate is None


def test_exited_to_watching_fails():
    candidate = _watching_candidate()
    triggered = transition_candidate(
        candidate, WatchlistCandidateStatus.TRIGGERED, last_updated_or_session="10:00 ET"
    ).candidate
    active = transition_candidate(
        triggered, WatchlistCandidateStatus.ACTIVE, last_updated_or_session="10:05 ET"
    ).candidate
    exited = transition_candidate(
        active, WatchlistCandidateStatus.EXITED, last_updated_or_session="14:00 ET", notes="target hit"
    ).candidate
    result = transition_candidate(
        exited, WatchlistCandidateStatus.WATCHING, last_updated_or_session="14:05 ET"
    )
    assert not result.valid
    assert any("terminal" in r for r in result.blocking_reasons)


def test_watching_to_active_directly_is_rejected():
    """ACTIVE is only reachable from TRIGGERED, not directly from
    WATCHING -- a candidate must be triggered before it can be active."""
    candidate = _watching_candidate()
    result = transition_candidate(
        candidate, WatchlistCandidateStatus.ACTIVE, last_updated_or_session="10:00 ET"
    )
    assert not result.valid
    assert any("invalid transition" in r for r in result.blocking_reasons)


def test_all_terminal_statuses_have_no_allowed_transitions():
    for status in TERMINAL_STATUSES:
        assert ALLOWED_TRANSITIONS[status] == frozenset()


# --- 4. invalid status rejected -------------------------------------------------------------------------


def test_invalid_status_string_returns_structured_failure_not_exception():
    result = check_watchlist_candidate_intake(_clean_payload(status="not_a_real_status"))
    assert not result.valid
    assert any("status" in r for r in result.blocking_reasons)
    assert result.candidate is None


def test_status_accepts_case_insensitive_string_via_intake():
    result = check_watchlist_candidate_intake(_clean_payload(status="TRIGGERED"))
    assert result.valid
    assert result.candidate.status == WatchlistCandidateStatus.TRIGGERED


# --- 5. missing entry_trigger/invalidation/target fails -------------------------------------------------


def test_missing_entry_trigger_fails():
    result = create_watchlist_candidate(**_clean_creation_kwargs(entry_trigger=""))
    assert not result.valid
    assert any("entry_trigger" in r for r in result.blocking_reasons)
    assert isinstance(result.candidate, WatchlistCandidate)  # structural construction still succeeds


def test_missing_invalidation_fails():
    result = create_watchlist_candidate(**_clean_creation_kwargs(invalidation=""))
    assert not result.valid
    assert any("invalidation" in r for r in result.blocking_reasons)


def test_missing_target_fails():
    result = create_watchlist_candidate(**_clean_creation_kwargs(target_1="", target_2=""))
    assert not result.valid
    assert any("target_1" in r for r in result.blocking_reasons)
    assert any("target_2" in r for r in result.blocking_reasons)


# --- 6. SKIPPED requires a reason -------------------------------------------------------------------------


def test_skipped_transition_without_reason_fails():
    candidate = _watching_candidate()
    result = transition_candidate(
        candidate, WatchlistCandidateStatus.SKIPPED, last_updated_or_session="10:00 ET"
    )
    assert not result.valid
    assert any("reason" in r for r in result.blocking_reasons)
    assert result.candidate is None


def test_skipped_transition_with_reason_passes():
    candidate = _watching_candidate()
    result = transition_candidate(
        candidate,
        WatchlistCandidateStatus.SKIPPED,
        last_updated_or_session="10:00 ET",
        notes="chasing candle, no real setup",
    )
    assert result.valid
    assert result.candidate.status == WatchlistCandidateStatus.SKIPPED


def test_skipped_candidate_via_intake_requires_reason():
    result = check_watchlist_candidate_intake(_clean_payload(status="skipped", notes=""))
    assert not result.valid
    assert any("reason" in r for r in result.blocking_reasons)


def test_skipped_candidate_via_intake_with_reason_passes():
    result = check_watchlist_candidate_intake(
        _clean_payload(status="skipped", notes="wide spread, no liquidity")
    )
    assert result.valid


# --- 7. malformed payload returns structured failure, never an exception --------------------------------


def test_non_dict_payload_returns_structured_failure_not_exception():
    for bad_payload in (None, "not a dict", 42, ["a", "list"]):
        result = check_watchlist_candidate_intake(bad_payload)
        assert isinstance(result, WatchlistCandidateResult)
        assert not result.valid
        assert "malformed payload" in result.blocking_reasons[0]
        assert result.candidate is None


def test_missing_required_field_returns_structured_failure():
    payload = _clean_payload()
    del payload["entry_trigger"]
    result = check_watchlist_candidate_intake(payload)
    assert not result.valid
    assert any("entry_trigger" in r for r in result.blocking_reasons)
    assert result.candidate is None


def test_invalid_direction_returns_structured_failure_not_exception():
    result = check_watchlist_candidate_intake(_clean_payload(direction="SIDEWAYS"))
    assert not result.valid
    assert any("direction" in r for r in result.blocking_reasons)


# --- 8. no scanner/broker/execution import, no clock access, no I/O -------------------------------------------


def test_watchlist_lifecycle_module_has_no_scanner_import():
    imported = _imported_modules(lifecycle_module)
    assert not any("options_manager.scanner" in name for name in imported)
    assert not any(name == "options_manager.strategies" for name in imported)


def test_watchlist_lifecycle_module_has_no_forbidden_imports():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_watchlist_lifecycle_module_has_no_cross_boundary_imports_outside_options_manager():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in ("__future__", "dataclasses", "enum", "typing")
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


def test_watchlist_lifecycle_module_has_no_fixture_status_import():
    imported = _imported_modules(lifecycle_module)
    assert not any("fixture_status" in name for name in imported)


def test_watchlist_lifecycle_module_has_no_credential_identifiers():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text().lower()
        for forbidden in _FORBIDDEN_CREDENTIAL_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_watchlist_lifecycle_module_does_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_watchlist_lifecycle_module_has_no_network_call_text():
    source = _module_source().lower()
    for forbidden in ("httpx.", "requests.", "socket."):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_watchlist_lifecycle_module_has_no_clock_access():
    source = _module_source()
    for forbidden in ("datetime.now(", "time.time(", "date.today("):
        assert forbidden not in source, f"module must not contain {forbidden!r}"


def test_watchlist_lifecycle_module_has_no_order_action_verbs():
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


def test_no_scanner_execution_or_broker_module_imports_watchlist_lifecycle():
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
            if any("watchlist_lifecycle" in name for name in imported):
                offenders.append(str(path))
    assert not offenders, f"watchlist_lifecycle must not be imported from: {offenders}"


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
