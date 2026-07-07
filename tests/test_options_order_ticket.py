"""
tests/test_options_order_ticket.py

Phase 7 controlled order ticket preparation tests. Pure/deterministic
construction of a local, non-executable order ticket from a Phase 6
HumanConfirmedOrderPrep — no broker, no Robinhood, no Tradovate, no HTTP,
no Discord, no file writes, no storage, no order preview/placement.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import options_manager.order_ticket as order_ticket_module
from options_manager.config import OptionsManagerConfig
from options_manager.dry_run_review import OptionOrderIntent
from options_manager.human_confirm import HumanConfirmedOrderPrep
from options_manager.order_ticket import (
    OrderTicketResult,
    PreparedOrderTicket,
    build_order_ticket,
    compute_ticket_id,
)


def _order_intent(**overrides) -> OptionOrderIntent:
    base = dict(
        ticker="BAC",
        direction="CALL",
        order_action="BUY_TO_OPEN",
        quantity=1,
        contract_strike=60.00,
        contract_expiry=date.today() + timedelta(days=30),
        max_premium=2.00,
        estimated_limit_price=1.95,
        estimated_notional=195.0,
        account_tag="agentic_micro_account",
        source="claude_session",
        dry_run_only=True,
        created_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return OptionOrderIntent(**base)


def _confirmed_prep(**overrides) -> HumanConfirmedOrderPrep:
    intent = overrides.pop("order_intent", _order_intent())
    base = dict(
        approved_for_order_prep=True,
        status="CONFIRMED",
        failed_stage=None,
        reason="",
        order_intent=intent,
        confirmation_id="conf-1",
        warnings=[],
    )
    base.update(overrides)
    return HumanConfirmedOrderPrep(**base)


def _config(**overrides) -> OptionsManagerConfig:
    return replace(OptionsManagerConfig(), **overrides)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_confirmed_prep_creates_ticket_ready():
    result = build_order_ticket(_confirmed_prep(), _config(), now=NOW)
    assert result.ticket_created is True
    assert result.status == "TICKET_READY"
    assert result.failed_stage is None
    assert result.ticket is not None
    assert result.ticket.confirmation_id == "conf-1"


def test_rejected_prep_rejects():
    prep = _confirmed_prep(
        approved_for_order_prep=False,
        status="REJECTED",
        failed_stage="approval_text",
        reason="bad phrase",
        order_intent=None,
        confirmation_id=None,
    )
    result = build_order_ticket(prep, _config(), now=NOW)
    assert result.ticket_created is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "human_confirm"
    assert result.ticket is None


def test_expired_prep_expires():
    prep = _confirmed_prep(
        approved_for_order_prep=False,
        status="EXPIRED",
        failed_stage="expired",
        reason="confirmation expired",
        order_intent=None,
        confirmation_id=None,
    )
    result = build_order_ticket(prep, _config(), now=NOW)
    assert result.ticket_created is False
    assert result.status == "EXPIRED"
    assert result.ticket is None


def test_used_prep_rejects():
    prep = _confirmed_prep(
        approved_for_order_prep=False,
        status="USED",
        failed_stage="used",
        reason="confirmation already used",
        order_intent=None,
        confirmation_id=None,
    )
    result = build_order_ticket(prep, _config(), now=NOW)
    assert result.ticket_created is False
    assert result.status == "REJECTED"
    assert result.ticket is None


def test_data_blocked_prep_propagates():
    prep = _confirmed_prep(
        approved_for_order_prep=False,
        status="DATA_BLOCKED",
        failed_stage="order_intent",
        reason="review_result.order_intent is missing",
        order_intent=None,
        confirmation_id=None,
    )
    result = build_order_ticket(prep, _config(), now=NOW)
    assert result.ticket_created is False
    assert result.status == "DATA_BLOCKED"
    assert result.ticket is None


def test_missing_order_intent_data_blocked():
    prep = _confirmed_prep(order_intent=None)
    result = build_order_ticket(prep, _config(), now=NOW)
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "order_intent"


def test_missing_confirmation_id_data_blocked():
    prep = _confirmed_prep(confirmation_id="")
    result = build_order_ticket(prep, _config(), now=NOW)
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "confirmation_id"

    prep_none = _confirmed_prep(confirmation_id=None)
    result_none = build_order_ticket(prep_none, _config(), now=NOW)
    assert result_none.status == "DATA_BLOCKED"
    assert result_none.failed_stage == "confirmation_id"


def test_dry_run_only_false_rejects():
    intent = _order_intent(dry_run_only=False)
    prep = _confirmed_prep(order_intent=intent)
    result = build_order_ticket(prep, _config(), now=NOW)
    assert result.status == "REJECTED"
    assert result.failed_stage == "dry_run_only"


def test_executable_is_always_false():
    result = build_order_ticket(_confirmed_prep(), _config(), now=NOW)
    assert result.ticket.executable is False


def test_broker_is_always_none():
    result = build_order_ticket(_confirmed_prep(), _config(), now=NOW)
    assert result.ticket.broker is None


def test_broker_order_id_is_always_none():
    result = build_order_ticket(_confirmed_prep(), _config(), now=NOW)
    assert result.ticket.broker_order_id is None


def test_order_action_other_than_buy_to_open_rejects():
    intent = _order_intent(order_action="SELL_TO_CLOSE")
    prep = _confirmed_prep(order_intent=intent)
    result = build_order_ticket(prep, _config(), now=NOW)
    assert result.status == "REJECTED"
    assert result.failed_stage == "order_action"


def test_quantity_below_one_rejects():
    intent = _order_intent(quantity=0)
    prep = _confirmed_prep(order_intent=intent)
    result = build_order_ticket(prep, _config(), now=NOW)
    assert result.status == "REJECTED"
    assert result.failed_stage == "quantity"


def test_quantity_above_cap_rejects():
    intent = _order_intent(quantity=3)
    prep = _confirmed_prep(order_intent=intent)
    result = build_order_ticket(prep, _config(order_ticket_max_contracts=2), now=NOW)
    assert result.status == "REJECTED"
    assert result.failed_stage == "quantity"


def test_notional_above_cap_rejects():
    intent = _order_intent(estimated_notional=1000.0)
    prep = _confirmed_prep(order_intent=intent)
    result = build_order_ticket(prep, _config(order_ticket_max_notional=300.0), now=NOW)
    assert result.status == "REJECTED"
    assert result.failed_stage == "notional"


def test_limit_price_zero_or_negative_rejects():
    intent = _order_intent(estimated_limit_price=0.0)
    prep = _confirmed_prep(order_intent=intent)
    result = build_order_ticket(prep, _config(), now=NOW)
    assert result.status == "REJECTED"
    assert result.failed_stage == "limit_price"


def test_limit_price_above_cap_rejects():
    intent = _order_intent(estimated_limit_price=5.0)
    prep = _confirmed_prep(order_intent=intent)
    result = build_order_ticket(prep, _config(order_ticket_max_limit_price=3.0), now=NOW)
    assert result.status == "REJECTED"
    assert result.failed_stage == "limit_price"


def test_disallowed_account_tag_rejects():
    intent = _order_intent(account_tag="some_other_account")
    prep = _confirmed_prep(order_intent=intent)
    result = build_order_ticket(prep, _config(), now=NOW)
    assert result.status == "REJECTED"
    assert result.failed_stage == "account_tag"


def test_naive_now_data_blocked():
    result = build_order_ticket(_confirmed_prep(), _config(), now=datetime.now())
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "timestamp"


def test_ttl_zero_or_negative_rejects():
    result = build_order_ticket(
        _confirmed_prep(), _config(order_ticket_ttl_seconds=0), now=NOW
    )
    assert result.status == "REJECTED"
    assert result.failed_stage == "ttl"

    result_negative = build_order_ticket(
        _confirmed_prep(), _config(order_ticket_ttl_seconds=-5), now=NOW
    )
    assert result_negative.status == "REJECTED"
    assert result_negative.failed_stage == "ttl"


def test_expires_at_is_now_plus_ttl():
    result = build_order_ticket(
        _confirmed_prep(), _config(order_ticket_ttl_seconds=120), now=NOW
    )
    assert result.ticket.created_at == NOW
    assert result.ticket.expires_at == NOW + timedelta(seconds=120)


def test_ticket_id_deterministic_given_identical_inputs():
    id_a = compute_ticket_id(_confirmed_prep(), NOW)
    id_b = compute_ticket_id(_confirmed_prep(), NOW)
    assert id_a == id_b


def test_ticket_id_changes_when_confirmation_id_changes():
    id_a = compute_ticket_id(_confirmed_prep(confirmation_id="conf-1"), NOW)
    id_b = compute_ticket_id(_confirmed_prep(confirmation_id="conf-2"), NOW)
    assert id_a != id_b


def test_ticket_id_changes_when_order_terms_change():
    id_a = compute_ticket_id(_confirmed_prep(), NOW)
    changed_intent = _order_intent(estimated_limit_price=2.50, estimated_notional=250.0)
    id_b = compute_ticket_id(_confirmed_prep(order_intent=changed_intent), NOW)
    assert id_a != id_b


def test_ticket_id_changes_when_now_changes():
    id_a = compute_ticket_id(_confirmed_prep(), NOW)
    id_b = compute_ticket_id(_confirmed_prep(), NOW + timedelta(seconds=1))
    assert id_a != id_b


def test_order_ticket_disabled_blocks_before_evaluating_malformed_data():
    malformed_prep = _confirmed_prep(
        approved_for_order_prep=False,
        status="REJECTED",
        failed_stage="approval_text",
        reason="x",
        order_intent=None,
        confirmation_id=None,
    )
    result = build_order_ticket(
        malformed_prep, _config(order_ticket_enabled=False), now=datetime.now()
    )
    assert result.ticket_created is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "order_ticket_disabled"


def test_defensive_check_blocks_ticket_ready_if_executable_tampered(monkeypatch):
    """Prove the post-construction defensive re-check actually fires, not
    just that the literal `executable=False` can never be wrong — same
    tamper-test pattern used for Phase 5's dry_run_only re-check."""

    class _TamperedPreparedOrderTicket(PreparedOrderTicket):
        def __setattr__(self, name, value):
            if name == "executable":
                value = True
            super().__setattr__(name, value)

    monkeypatch.setattr(order_ticket_module, "PreparedOrderTicket", _TamperedPreparedOrderTicket)
    result = build_order_ticket(_confirmed_prep(), _config(), now=NOW)
    assert result.status == "REJECTED"
    assert result.failed_stage == "executable"
    assert result.ticket is None


def test_defensive_check_blocks_ticket_ready_if_broker_tampered(monkeypatch):
    class _TamperedPreparedOrderTicket(PreparedOrderTicket):
        def __setattr__(self, name, value):
            if name == "broker":
                value = "robinhood"
            super().__setattr__(name, value)

    monkeypatch.setattr(order_ticket_module, "PreparedOrderTicket", _TamperedPreparedOrderTicket)
    result = build_order_ticket(_confirmed_prep(), _config(), now=NOW)
    assert result.status == "REJECTED"
    assert result.failed_stage == "broker"
    assert result.ticket is None


def test_account_tag_sourced_only_from_order_intent():
    intent = _order_intent(account_tag="agentic_micro_account")
    prep = _confirmed_prep(order_intent=intent)
    result = build_order_ticket(prep, _config(), now=NOW)
    assert result.ticket.account_tag == "agentic_micro_account"
    # No separate account field exists on PreparedOrderTicket or the config
    # object beyond the allow-list — confirm the dataclass has no such field.
    ticket_fields = {f for f in PreparedOrderTicket.__dataclass_fields__}
    assert "broker_account" not in ticket_fields
    assert "account_id" not in ticket_fields


def test_order_ticket_does_not_write_futures_journal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    build_order_ticket(_confirmed_prep(), _config(), now=NOW)

    today = date.today().isoformat()
    assert not (tmp_path / "logs" / f"journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs" / f"options_journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs").exists()


def test_order_ticket_does_not_mutate_live_options_flag(monkeypatch):
    monkeypatch.delenv("LIVE_OPTIONS_TRADING_ENABLED", raising=False)
    build_order_ticket(_confirmed_prep(), _config(), now=NOW)
    import os

    assert os.getenv("LIVE_OPTIONS_TRADING_ENABLED") is None


def test_build_order_ticket_requires_explicit_config():
    with pytest.raises(TypeError):
        build_order_ticket(_confirmed_prep(), now=NOW)


def _order_ticket_ast():
    path = Path(order_ticket_module.__file__)
    return ast.parse(path.read_text())


def _order_ticket_imported_modules() -> list[str]:
    tree = _order_ticket_ast()
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _order_ticket_referenced_identifiers() -> set[str]:
    tree = _order_ticket_ast()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_order_ticket_has_no_forbidden_imports():
    # Real imports only — ast.Import/ImportFrom nodes never include
    # docstrings or comments, so this can't false-positive on descriptive
    # text.
    modules = _order_ticket_imported_modules()
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
            assert forbidden not in module, f"order_ticket.py must not import {module!r}"


def test_order_ticket_has_no_broker_notify_or_network_identifiers():
    # Real identifiers referenced in code (Name/Attribute nodes) — again,
    # docstrings/comments are not part of the AST and can't trigger this.
    identifiers = {name.lower() for name in _order_ticket_referenced_identifiers()}
    forbidden = {
        "place_order",
        "submit_order",
        "cancel_order",
        "preview_order",
        "robinhood",
        "tradovate",
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
        "validate_existing_ticket",
    }
    overlap = identifiers & forbidden
    assert not overlap, f"order_ticket.py references forbidden identifiers: {overlap}"


def test_order_ticket_module_has_no_journal_or_config_file_reads():
    path = Path(order_ticket_module.__file__)
    source = path.read_text()
    assert "open(" not in source
    assert ".write(" not in source
    assert "mkdir" not in source
