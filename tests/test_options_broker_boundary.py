"""
tests/test_options_broker_boundary.py

Phase 9 inert broker boundary schema tests. Pure/deterministic conversion
of a Phase 7 PreparedOrderTicket into a local OptionsBrokerPreviewRequest,
and independent re-validation into an OptionsBrokerPreviewResult — no
broker, no Robinhood, no Tradovate, no IBKR, no HTTP, no Discord, no file
writes, no storage, no real preview, no order placement.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import options_manager.broker_boundary as broker_boundary_module
from options_manager.broker_boundary import (
    REAL_PREVIEW_BLOCKED_WARNING,
    OptionsBrokerPreviewRequest,
    OptionsBrokerPreviewResult,
    build_preview_request,
    validate_preview_boundary,
)
from options_manager.config import OptionsManagerConfig
from options_manager.order_ticket import PreparedOrderTicket


def _ticket(**overrides) -> PreparedOrderTicket:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = dict(
        ticket_id="ticket-1",
        confirmation_id="conf-1",
        ticker="BAC",
        direction="CALL",
        order_action="BUY_TO_OPEN",
        quantity=1,
        contract_strike=60.00,
        contract_expiry=date.today() + timedelta(days=30),
        limit_price=1.95,
        estimated_notional=195.0,
        account_tag="agentic_micro_account",
        source="claude_session",
        dry_run_only=True,
        executable=False,
        broker=None,
        broker_order_id=None,
        created_at=now,
        expires_at=now + timedelta(seconds=120),
        warnings=[],
    )
    base.update(overrides)
    return PreparedOrderTicket(**base)


def _request(**overrides) -> OptionsBrokerPreviewRequest:
    base = dict(
        ticket_id="ticket-1",
        confirmation_id="conf-1",
        ticker="BAC",
        direction="CALL",
        order_action="BUY_TO_OPEN",
        quantity=1,
        contract_strike=60.00,
        contract_expiry=date.today() + timedelta(days=30),
        limit_price=1.95,
        estimated_notional=195.0,
        account_tag="agentic_micro_account",
        source="claude_session",
        dry_run_only=True,
        executable=False,
    )
    base.update(overrides)
    return OptionsBrokerPreviewRequest(**base)


def _config(**overrides) -> OptionsManagerConfig:
    return replace(OptionsManagerConfig(), **overrides)


def test_valid_non_executable_ticket_produces_preview_ready():
    request = build_preview_request(_ticket(), _config())
    result = validate_preview_boundary(request, _config())
    assert result.preview_ready is True
    assert result.status == "PREVIEW_READY"
    assert result.failed_stage is None
    assert result.ticket_id == "ticket-1"


def test_executable_request_rejects():
    request = _request(executable=True)
    result = validate_preview_boundary(request, _config())
    assert result.status == "REJECTED"
    assert result.failed_stage == "executable"


def test_dry_run_only_false_rejects():
    request = _request(dry_run_only=False)
    result = validate_preview_boundary(request, _config())
    assert result.status == "REJECTED"
    assert result.failed_stage == "dry_run_only"


def test_missing_ticket_id_data_blocked():
    request = _request(ticket_id="")
    result = validate_preview_boundary(request, _config())
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "ticket_id"


def test_missing_confirmation_id_data_blocked():
    request = _request(confirmation_id="")
    result = validate_preview_boundary(request, _config())
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "confirmation_id"


def test_order_action_other_than_buy_to_open_rejects():
    request = _request(order_action="SELL_TO_CLOSE")
    result = validate_preview_boundary(request, _config())
    assert result.status == "REJECTED"
    assert result.failed_stage == "order_action"


def test_quantity_below_one_rejects():
    request = _request(quantity=0)
    result = validate_preview_boundary(request, _config())
    assert result.status == "REJECTED"
    assert result.failed_stage == "quantity"


def test_quantity_above_cap_rejects():
    request = _request(quantity=3)
    result = validate_preview_boundary(request, _config(broker_boundary_max_contracts=2))
    assert result.status == "REJECTED"
    assert result.failed_stage == "quantity"


def test_limit_price_zero_or_negative_rejects():
    request = _request(limit_price=0.0)
    result = validate_preview_boundary(request, _config())
    assert result.status == "REJECTED"
    assert result.failed_stage == "limit_price"


def test_limit_price_above_cap_rejects():
    request = _request(limit_price=5.0)
    result = validate_preview_boundary(request, _config(broker_boundary_max_limit_price=3.0))
    assert result.status == "REJECTED"
    assert result.failed_stage == "limit_price"


def test_estimated_notional_zero_or_negative_rejects():
    request = _request(estimated_notional=0.0)
    result = validate_preview_boundary(request, _config())
    assert result.status == "REJECTED"
    assert result.failed_stage == "notional"


def test_estimated_notional_above_cap_rejects():
    request = _request(estimated_notional=1000.0)
    result = validate_preview_boundary(request, _config(broker_boundary_max_notional=300.0))
    assert result.status == "REJECTED"
    assert result.failed_stage == "notional"


def test_disallowed_account_tag_rejects():
    request = _request(account_tag="some_other_account")
    result = validate_preview_boundary(request, _config())
    assert result.status == "REJECTED"
    assert result.failed_stage == "account_tag"


def test_broker_boundary_disabled_blocks_before_evaluating_malformed_data():
    malformed_request = _request(
        ticket_id="",
        confirmation_id="",
        executable=True,
        dry_run_only=False,
        order_action="SELL_TO_CLOSE",
        quantity=-1,
        limit_price=-5.0,
        estimated_notional=-100.0,
        account_tag="bad_account",
    )
    result = validate_preview_boundary(
        malformed_request, _config(broker_boundary_enabled=False)
    )
    assert result.status == "REJECTED"
    assert result.failed_stage == "broker_boundary_disabled"


def test_allow_real_preview_true_still_does_not_call_broker():
    request = _request()
    result = validate_preview_boundary(
        request, _config(broker_boundary_allow_real_preview=True)
    )
    assert result.status == "PREVIEW_READY"
    assert result.broker is None
    assert result.broker_order_id is None
    assert result.submitted is False
    assert result.executable is False


def test_allow_real_preview_true_adds_warning():
    request = _request()
    result = validate_preview_boundary(
        request, _config(broker_boundary_allow_real_preview=True)
    )
    assert REAL_PREVIEW_BLOCKED_WARNING in result.warnings


def test_allow_real_preview_false_does_not_add_warning():
    request = _request()
    result = validate_preview_boundary(
        request, _config(broker_boundary_allow_real_preview=False)
    )
    assert REAL_PREVIEW_BLOCKED_WARNING not in result.warnings


def test_result_executable_is_always_false():
    result = validate_preview_boundary(_request(), _config())
    assert result.executable is False


def test_result_submitted_is_always_false():
    result = validate_preview_boundary(_request(), _config())
    assert result.submitted is False


def test_result_broker_is_always_none():
    result = validate_preview_boundary(_request(), _config())
    assert result.broker is None


def test_result_broker_order_id_is_always_none():
    result = validate_preview_boundary(_request(), _config())
    assert result.broker_order_id is None


def test_build_preview_request_refuses_executable_ticket():
    with pytest.raises(ValueError):
        build_preview_request(_ticket(executable=True), _config())


def test_build_preview_request_refuses_dry_run_only_false_ticket():
    with pytest.raises(ValueError):
        build_preview_request(_ticket(dry_run_only=False), _config())


def test_build_preview_request_refuses_ticket_broker_not_none():
    with pytest.raises(ValueError):
        build_preview_request(_ticket(broker="robinhood"), _config())


def test_build_preview_request_refuses_ticket_broker_order_id_not_none():
    with pytest.raises(ValueError):
        build_preview_request(_ticket(broker_order_id="abc123"), _config())


def test_build_preview_request_maps_fields_correctly():
    request = build_preview_request(_ticket(), _config())
    assert request.ticket_id == "ticket-1"
    assert request.confirmation_id == "conf-1"
    assert request.ticker == "BAC"
    assert request.quantity == 1
    assert request.dry_run_only is True
    assert request.executable is False


def test_defensive_check_blocks_preview_ready_if_executable_tampered(monkeypatch):
    """Prove the post-construction defensive re-check actually fires, not
    just that the literal `executable=False` can never be wrong — same
    tamper-test pattern used for Phase 5/6/7's own defensive re-checks."""

    class _TamperedOptionsBrokerPreviewResult(OptionsBrokerPreviewResult):
        def __setattr__(self, name, value):
            if name == "executable":
                value = True
            super().__setattr__(name, value)

    monkeypatch.setattr(
        broker_boundary_module, "OptionsBrokerPreviewResult", _TamperedOptionsBrokerPreviewResult
    )
    result = validate_preview_boundary(_request(), _config())
    assert result.status == "REJECTED"
    assert result.failed_stage == "executable"


def test_defensive_check_blocks_preview_ready_if_submitted_tampered(monkeypatch):
    class _TamperedOptionsBrokerPreviewResult(OptionsBrokerPreviewResult):
        def __setattr__(self, name, value):
            if name == "submitted":
                value = True
            super().__setattr__(name, value)

    monkeypatch.setattr(
        broker_boundary_module, "OptionsBrokerPreviewResult", _TamperedOptionsBrokerPreviewResult
    )
    result = validate_preview_boundary(_request(), _config())
    assert result.status == "REJECTED"
    assert result.failed_stage == "submitted"


def test_defensive_check_blocks_preview_ready_if_broker_tampered(monkeypatch):
    class _TamperedOptionsBrokerPreviewResult(OptionsBrokerPreviewResult):
        def __setattr__(self, name, value):
            if name == "broker":
                value = "robinhood"
            super().__setattr__(name, value)

    monkeypatch.setattr(
        broker_boundary_module, "OptionsBrokerPreviewResult", _TamperedOptionsBrokerPreviewResult
    )
    result = validate_preview_boundary(_request(), _config())
    assert result.status == "REJECTED"
    assert result.failed_stage == "broker"


def test_broker_boundary_does_not_write_futures_journal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    validate_preview_boundary(_request(), _config())

    today = date.today().isoformat()
    assert not (tmp_path / "logs" / f"journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs" / f"options_journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs").exists()


def test_broker_boundary_does_not_mutate_live_options_flag(monkeypatch):
    monkeypatch.delenv("LIVE_OPTIONS_TRADING_ENABLED", raising=False)
    validate_preview_boundary(_request(), _config())
    import os

    assert os.getenv("LIVE_OPTIONS_TRADING_ENABLED") is None


def test_validate_preview_boundary_requires_explicit_config():
    with pytest.raises(TypeError):
        validate_preview_boundary(_request())


def test_build_preview_request_requires_explicit_config():
    with pytest.raises(TypeError):
        build_preview_request(_ticket())


def _broker_boundary_ast():
    path = Path(broker_boundary_module.__file__)
    return ast.parse(path.read_text())


def _broker_boundary_imported_modules() -> list[str]:
    tree = _broker_boundary_ast()
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _broker_boundary_referenced_identifiers() -> set[str]:
    tree = _broker_boundary_ast()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_broker_boundary_has_no_forbidden_imports():
    # Real imports only — ast.Import/ImportFrom nodes never include
    # docstrings or comments, so this can't false-positive on descriptive
    # text.
    modules = _broker_boundary_imported_modules()
    forbidden_module_fragments = (
        "execution",
        "webhook",
        "risk_engine",
        "alert_ranker",
        "httpx",
        "requests",
        "urllib",
        "socket",
        "live_lock",
    )
    for module in modules:
        for forbidden in forbidden_module_fragments:
            assert forbidden not in module, f"broker_boundary.py must not import {module!r}"


def test_broker_boundary_has_no_broker_notify_or_network_identifiers():
    # Real identifiers referenced in code (Name/Attribute nodes) — again,
    # docstrings/comments are not part of the AST and can't trigger this.
    identifiers = {name.lower() for name in _broker_boundary_referenced_identifiers()}
    forbidden = {
        "place_order",
        "submit_order",
        "cancel_order",
        "preview_order",
        "robinhood",
        "tradovate",
        "ibkr",
        "notify",
        "notify_packet",
        "log_packet",
        "broker_client",
        "from_env",
        "getenv",
        "load_dotenv",
        "environ",
        "open",
        "assert_live_options_trading_disabled",
    }
    overlap = identifiers & forbidden
    assert not overlap, f"broker_boundary.py references forbidden identifiers: {overlap}"


def test_broker_boundary_module_has_no_journal_or_config_file_reads():
    path = Path(broker_boundary_module.__file__)
    source = path.read_text()
    assert "open(" not in source
    assert ".write(" not in source
    assert "mkdir" not in source
