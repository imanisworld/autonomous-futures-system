"""
tests/test_options_human_confirm.py

Phase 6 human-confirmed order prep tests. Pure/deterministic verification of
a caller-supplied ConfirmationRecord against a Phase 5 DryRunReviewResult —
no broker, no Robinhood, no Tradovate, no HTTP, no Discord, no file writes,
no storage, no order preview/placement.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import options_manager.human_confirm as human_confirm_module
from options_manager.config import OptionsManagerConfig
from options_manager.dry_run_review import DryRunReviewResult, OptionOrderIntent
from options_manager.human_confirm import (
    ConfirmationRecord,
    ConfirmationRequest,
    compute_intent_id,
    confirm_order_intent,
    build_confirmation_request,
)

REQUIRED_PHRASE = "CONFIRM DRY RUN ORDER PREP"


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


def _review_result(**overrides) -> DryRunReviewResult:
    intent = overrides.pop("order_intent", _order_intent())
    base = dict(
        approved_for_review=True,
        status="REVIEW_READY",
        failed_stage=None,
        reason="",
        order_intent=intent,
        estimated_notional=intent.estimated_notional if intent else None,
        warnings=[],
    )
    base.update(overrides)
    return DryRunReviewResult(**base)


def _confirmation_record(**overrides) -> ConfirmationRecord:
    now = datetime.now(timezone.utc)
    intent = overrides.pop("_intent", _order_intent())
    base = dict(
        confirmation_id="conf-1",
        intent_id=compute_intent_id(intent),
        reviewer="alice",
        approved=True,
        approval_text=REQUIRED_PHRASE,
        created_at=now,
        expires_at=now + timedelta(seconds=300),
        used_at=None,
        nonce="nonce-1",
    )
    base.update(overrides)
    return ConfirmationRecord(**base)


def _config(**overrides) -> OptionsManagerConfig:
    return replace(OptionsManagerConfig(), **overrides)


def test_review_ready_plus_matching_unexpired_confirmation_confirms():
    result = confirm_order_intent(_review_result(), _confirmation_record(), _config())
    assert result.approved_for_order_prep is True
    assert result.status == "CONFIRMED"
    assert result.failed_stage is None
    assert result.order_intent is not None
    assert result.confirmation_id == "conf-1"


def test_dry_run_review_rejected_rejects():
    review = _review_result(
        approved_for_review=False, status="REJECTED", failed_stage="risk_gate", reason="bad risk", order_intent=None
    )
    result = confirm_order_intent(review, _confirmation_record(), _config())
    assert result.approved_for_order_prep is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "dry_run_review"
    assert "risk_gate" in result.reason


def test_dry_run_review_data_blocked_propagates():
    review = _review_result(
        approved_for_review=False, status="DATA_BLOCKED", failed_stage="snapshot", reason="no ask", order_intent=None
    )
    result = confirm_order_intent(review, _confirmation_record(), _config())
    assert result.approved_for_order_prep is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "dry_run_review"


def test_missing_order_intent_data_blocked():
    review = _review_result(order_intent=None, estimated_notional=None)
    result = confirm_order_intent(review, _confirmation_record(), _config())
    assert result.approved_for_order_prep is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "order_intent"


def test_dry_run_only_false_rejects():
    intent = _order_intent(dry_run_only=False)
    review = _review_result(order_intent=intent)
    record = _confirmation_record(_intent=intent)
    result = confirm_order_intent(review, record, _config())
    assert result.approved_for_order_prep is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "dry_run_only"


def test_intent_id_mismatch_rejects():
    result = confirm_order_intent(
        _review_result(), _confirmation_record(intent_id="wrong-id"), _config()
    )
    assert result.approved_for_order_prep is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "intent_mismatch"


def test_expired_confirmation_expires():
    now = datetime.now(timezone.utc)
    record = _confirmation_record(created_at=now - timedelta(seconds=600), expires_at=now - timedelta(seconds=1))
    result = confirm_order_intent(_review_result(), record, _config())
    assert result.approved_for_order_prep is False
    assert result.status == "EXPIRED"
    assert result.failed_stage == "expired"


def test_used_confirmation_rejects_as_used():
    record = _confirmation_record(used_at=datetime.now(timezone.utc))
    result = confirm_order_intent(_review_result(), record, _config())
    assert result.approved_for_order_prep is False
    assert result.status == "USED"
    assert result.failed_stage == "used"


def test_wrong_approval_phrase_rejects():
    record = _confirmation_record(approval_text="I APPROVE THIS")
    result = confirm_order_intent(_review_result(), record, _config())
    assert result.approved_for_order_prep is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "approval_text"


def test_lowercase_phrase_rejected_by_default():
    record = _confirmation_record(approval_text=REQUIRED_PHRASE.lower())
    result = confirm_order_intent(_review_result(), record, _config())
    assert result.status == "REJECTED"
    assert result.failed_stage == "approval_text"

    # Case-insensitive when explicitly configured.
    result_ci = confirm_order_intent(
        _review_result(), record, _config(human_confirm_case_sensitive=False)
    )
    assert result_ci.status == "CONFIRMED"


def test_empty_reviewer_rejects():
    record = _confirmation_record(reviewer="")
    result = confirm_order_intent(_review_result(), record, _config())
    assert result.status == "REJECTED"
    assert result.failed_stage == "reviewer"


def test_empty_nonce_rejects():
    record = _confirmation_record(nonce="")
    result = confirm_order_intent(_review_result(), record, _config())
    assert result.status == "REJECTED"
    assert result.failed_stage == "nonce"


def test_naive_created_at_data_blocked():
    record = _confirmation_record(created_at=datetime.now())
    result = confirm_order_intent(_review_result(), record, _config())
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "timestamp"


def test_naive_expires_at_data_blocked():
    record = _confirmation_record(expires_at=datetime.now())
    result = confirm_order_intent(_review_result(), record, _config())
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "timestamp"


def test_confirmation_maps_to_one_intent_only():
    # A confirmation issued for one intent must not confirm a second, more
    # expensive intent — even one otherwise identical apart from price.
    intent_a = _order_intent(estimated_limit_price=1.95, estimated_notional=195.0)
    intent_b = _order_intent(estimated_limit_price=2.50, estimated_notional=250.0)
    record_for_a = _confirmation_record(_intent=intent_a)

    result_a = confirm_order_intent(_review_result(order_intent=intent_a), record_for_a, _config())
    assert result_a.status == "CONFIRMED"

    result_b = confirm_order_intent(_review_result(order_intent=intent_b), record_for_a, _config())
    assert result_b.status == "REJECTED"
    assert result_b.failed_stage == "intent_mismatch"


def test_reused_confirmation_cannot_approve_second_time():
    record = _confirmation_record()
    result_first = confirm_order_intent(_review_result(), record, _config())
    assert result_first.status == "CONFIRMED"

    # confirm_order_intent never mutates the record itself (it's pure) — the
    # caller is responsible for stamping used_at in their own store. Simulate
    # that here and confirm the second call is rejected.
    used_record = replace(record, used_at=datetime.now(timezone.utc))
    result_second = confirm_order_intent(_review_result(), used_record, _config())
    assert result_second.status == "USED"


def test_human_confirm_disabled_blocks_before_evaluating_malformed_data():
    malformed_review = _review_result(
        approved_for_review=False, status="REJECTED", failed_stage="risk_gate", reason="x", order_intent=None
    )
    malformed_record = _confirmation_record(
        reviewer="", nonce="", approved=False, approval_text="wrong", used_at=datetime.now(timezone.utc)
    )
    result = confirm_order_intent(
        malformed_review, malformed_record, _config(human_confirm_enabled=False)
    )
    assert result.approved_for_order_prep is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "human_confirm_disabled"


def test_build_confirmation_request_is_deterministic():
    review = _review_result()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    request = build_confirmation_request(
        review, _config(), reviewer="alice", approval_text=REQUIRED_PHRASE, now=now, nonce="fixed-nonce"
    )
    assert isinstance(request, ConfirmationRequest)
    assert request.intent_id == compute_intent_id(review.order_intent)
    assert request.created_at == now
    assert request.expires_at == now + timedelta(seconds=300)
    assert request.nonce == "fixed-nonce"
    assert request.reviewer == "alice"


def test_build_confirmation_request_requires_review_ready():
    review = _review_result(
        approved_for_review=False, status="REJECTED", failed_stage="risk_gate", reason="x", order_intent=None
    )
    with pytest.raises(ValueError):
        build_confirmation_request(
            review,
            _config(),
            reviewer="alice",
            approval_text=REQUIRED_PHRASE,
            now=datetime.now(timezone.utc),
            nonce="n",
        )


def test_compute_intent_id_is_deterministic_and_content_sensitive():
    intent = _order_intent()
    assert compute_intent_id(intent) == compute_intent_id(_order_intent())

    changed = _order_intent(estimated_limit_price=99.0, estimated_notional=9900.0)
    assert compute_intent_id(intent) != compute_intent_id(changed)


def test_human_confirm_does_not_write_futures_journal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    confirm_order_intent(_review_result(), _confirmation_record(), _config())

    today = date.today().isoformat()
    assert not (tmp_path / "logs" / f"journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs" / f"options_journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs" / f"options_human_confirm_{today}.jsonl").exists()
    assert not (tmp_path / "logs").exists()


def test_human_confirm_does_not_write_options_journal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    confirm_order_intent(_review_result(), _confirmation_record(), _config())
    assert not (tmp_path / "logs").exists()


def test_human_confirm_does_not_mutate_live_options_flag(monkeypatch):
    monkeypatch.delenv("LIVE_OPTIONS_TRADING_ENABLED", raising=False)
    confirm_order_intent(_review_result(), _confirmation_record(), _config())
    import os

    assert os.getenv("LIVE_OPTIONS_TRADING_ENABLED") is None


def _human_confirm_ast():
    path = Path(human_confirm_module.__file__)
    return ast.parse(path.read_text())


def _human_confirm_imported_modules() -> list[str]:
    tree = _human_confirm_ast()
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _human_confirm_referenced_identifiers() -> set[str]:
    tree = _human_confirm_ast()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_human_confirm_has_no_forbidden_imports():
    # Real imports only — ast.Import/ImportFrom nodes never include docstrings
    # or comments, so this can't false-positive on descriptive text.
    modules = _human_confirm_imported_modules()
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
            assert forbidden not in module, f"human_confirm.py must not import {module!r}"


def test_human_confirm_has_no_broker_notify_or_network_identifiers():
    # Real identifiers referenced in code (Name/Attribute nodes) — again,
    # docstrings/comments are not part of the AST and can't trigger this.
    identifiers = {name.lower() for name in _human_confirm_referenced_identifiers()}
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
        "broker",
        "from_env",
        "getenv",
        "load_dotenv",
        "environ",
        "open",
        "assert_live_options_trading_disabled",
    }
    overlap = identifiers & forbidden
    assert not overlap, f"human_confirm.py references forbidden identifiers: {overlap}"


def test_human_confirm_module_has_no_journal_or_config_file_reads():
    path = Path(human_confirm_module.__file__)
    source = path.read_text()
    assert "open(" not in source
    assert ".write(" not in source
    assert "mkdir" not in source


def test_confirm_order_intent_requires_explicit_config():
    with pytest.raises(TypeError):
        confirm_order_intent(_review_result(), _confirmation_record())
