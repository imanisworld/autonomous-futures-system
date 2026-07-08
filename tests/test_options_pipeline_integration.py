"""
tests/test_options_pipeline_integration.py

Phase 10 — options pipeline integration audit (test-only).

Proves the full Phase 1-9 chain composes cleanly end-to-end using only
supplied objects: build_packet -> evaluate_packet -> evaluate_contract_quality
-> simulate_round_trip -> build_dry_run_review -> build_confirmation_request
-> (caller-bridged) ConfirmationRecord -> confirm_order_intent ->
build_order_ticket -> build_preview_request -> validate_preview_boundary.

This file adds NO production code and introduces no new capability. It only
composes existing pure functions with plain supplied data to verify:
  - the happy path reaches PREVIEW_READY with submitted=False,
    executable=False, broker=None, broker_order_id=None
  - a rejection at any major gate correctly stops the chain before reaching
    later stages
  - the caller-side bridge from ConfirmationRequest -> ConfirmationRecord
    (no helper function exists for this by design) works when done by hand
  - build_preview_request is only ever called after confirming
    OrderTicketResult.status == "TICKET_READY" and .ticket is not None
    (per the Phase 10 Step 0 audit finding — testing build_preview_request(
    None, ...) directly is explicitly out of scope here; that is a future
    broker_boundary.py hardening test, not a pipeline integration test)
  - no journal writes, no LIVE_OPTIONS_TRADING_ENABLED mutation, no network
    calls anywhere across the whole chain
"""

from __future__ import annotations

import ast
import socket
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from options_manager.broker_boundary import (
    REAL_PREVIEW_BLOCKED_WARNING,
    build_preview_request,
    validate_preview_boundary,
)
from options_manager.config import OptionsManagerConfig
from options_manager.contract_quality import (
    ContractMarketSnapshot,
    evaluate_contract_quality,
)
from options_manager.dry_run_review import build_dry_run_review
from options_manager.human_confirm import ConfirmationRecord, build_confirmation_request, confirm_order_intent
from options_manager.order_ticket import build_order_ticket
from options_manager.packet_builder import build_packet
from options_manager.paper_sim import simulate_round_trip
from options_manager.risk_gate import evaluate_packet

REQUIRED_PHRASE = "CONFIRM DRY RUN ORDER PREP"


# --- shared builders ----------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_journal_and_discord(tmp_path, monkeypatch):
    """Redirect Phase 1 journal writes to a throwaway dir and mock Discord for
    every test in this file — the only I/O boundary anywhere in the chain is
    build_packet(); every later stage is provably I/O-free on its own."""
    import options_manager.journal as journal_mod

    monkeypatch.setattr(
        "options_manager.packet_builder.log_packet",
        lambda packet: journal_mod.log_packet(packet, journal_dir=str(tmp_path)),
    )
    monkeypatch.setattr(
        "options_manager.notify._default_send", lambda url, payload: True
    )
    return tmp_path


def _raw_packet_input(**overrides) -> dict:
    base = {
        "ticker": "BAC",
        "direction": "CALL",
        "entry_price": 60.11,
        "price_target": 62.50,
        "signa_score": 78,
        "signa_grade": "B",
        "signa_bias": "BULLISH",
        "gex_regime": "LOW_PINNING",
        "gex_wall_above": None,
        "gex_wall_below": None,
        "contract_strike": 60.00,
        "contract_expiry": date.today() + timedelta(days=30),
        "max_premium": 2.00,
        "max_contracts": 1,
    }
    base.update(overrides)
    return base


def _entry_snapshot(now: datetime, **overrides) -> ContractMarketSnapshot:
    base = dict(
        ticker="BAC",
        contract_symbol="BAC240119C60",
        bid=1.90,
        ask=1.95,
        last=1.92,
        volume=500,
        open_interest=1000,
        implied_volatility=0.35,
        delta=0.45,
        theta=-0.05,
        underlying_price=60.11,
        quote_timestamp=now,
        provider="mock",
        is_snapshot_complete=True,
    )
    base.update(overrides)
    return ContractMarketSnapshot(**base)


def _exit_snapshot(now: datetime, **overrides) -> ContractMarketSnapshot:
    base = dict(
        ticker="BAC",
        contract_symbol="BAC240119C60",
        bid=2.10,
        ask=2.15,
        last=2.12,
        volume=400,
        open_interest=900,
        implied_volatility=0.33,
        delta=0.50,
        theta=-0.04,
        underlying_price=61.00,
        quote_timestamp=now,
        provider="mock",
        is_snapshot_complete=True,
    )
    base.update(overrides)
    return ContractMarketSnapshot(**base)


def _config(**overrides) -> OptionsManagerConfig:
    return replace(OptionsManagerConfig(), **overrides)


def _bridge_confirmation_record(request, *, confirmation_id: str, approved: bool = True, used_at=None) -> ConfirmationRecord:
    """Manually bridge ConfirmationRequest -> ConfirmationRecord.

    No helper function exists for this in options_manager by design (see
    Phase 10 Step 0 audit finding #1) — a human/external system is expected
    to add confirmation_id/approved/used_at after reviewing the request.
    """
    return ConfirmationRecord(
        confirmation_id=confirmation_id,
        intent_id=request.intent_id,
        reviewer=request.reviewer,
        approved=approved,
        approval_text=request.approval_text,
        created_at=request.created_at,
        expires_at=request.expires_at,
        used_at=used_at,
        nonce=request.nonce,
    )


def _run_happy_path(now: datetime, config: OptionsManagerConfig):
    """Runs the full chain and returns every intermediate result, so tests
    can assert on whichever stage they care about."""
    packet = build_packet(_raw_packet_input())
    risk_result = evaluate_packet(packet, config)
    entry_snapshot = _entry_snapshot(now)
    exit_snapshot = _exit_snapshot(now)
    quality_result = evaluate_contract_quality(packet, entry_snapshot, config)
    paper_sim_result = simulate_round_trip(
        packet, entry_snapshot, exit_snapshot, risk_result, quality_result, config
    )
    review_result = build_dry_run_review(
        packet, risk_result, quality_result, paper_sim_result, entry_snapshot, config
    )
    return packet, risk_result, quality_result, paper_sim_result, review_result


# --- happy path ---------------------------------------------------------------


def test_full_happy_path_reaches_preview_ready():
    now = datetime.now(timezone.utc)
    config = _config()

    packet, risk_result, quality_result, paper_sim_result, review_result = _run_happy_path(
        now, config
    )
    assert risk_result.status == "APPROVED"
    assert quality_result.status == "APPROVED"
    assert paper_sim_result.status == "SIMULATED"
    assert review_result.status == "REVIEW_READY"
    assert review_result.order_intent.dry_run_only is True

    confirmation_request = build_confirmation_request(
        review_result, config, reviewer="alice", approval_text=REQUIRED_PHRASE, now=now, nonce="nonce-1"
    )
    confirmation_record = _bridge_confirmation_record(
        confirmation_request, confirmation_id="conf-int-1"
    )

    confirmed_prep = confirm_order_intent(review_result, confirmation_record, config)
    assert confirmed_prep.status == "CONFIRMED"
    assert confirmed_prep.order_intent.dry_run_only is True

    order_ticket_result = build_order_ticket(confirmed_prep, config, now=now)
    assert order_ticket_result.status == "TICKET_READY"

    # Guard: only unwrap the ticket after confirming TICKET_READY and a
    # non-None ticket — the correct caller behavior identified in the Phase
    # 10 Step 0 audit (finding #2), not a check inside broker_boundary.py.
    assert order_ticket_result.status == "TICKET_READY"
    assert order_ticket_result.ticket is not None
    ticket = order_ticket_result.ticket
    assert ticket.executable is False
    assert ticket.dry_run_only is True
    assert ticket.broker is None
    assert ticket.broker_order_id is None

    preview_request = build_preview_request(ticket, config)
    preview_result = validate_preview_boundary(preview_request, config)

    assert preview_result.status == "PREVIEW_READY"
    assert preview_result.preview_ready is True
    assert preview_result.submitted is False
    assert preview_result.executable is False
    assert preview_result.broker is None
    assert preview_result.broker_order_id is None


# --- rejection/stop tests -------------------------------------------------------


def test_risk_rejection_stops_before_dry_run_ticket_boundary():
    now = datetime.now(timezone.utc)
    config = _config()

    # premium above the risk cap -> REJECTED at risk_gate.
    packet = build_packet(_raw_packet_input(max_premium=2.99, entry_price=60.11))
    risk_result = evaluate_packet(packet, _config(risk_max_premium=1.00))
    assert risk_result.status == "REJECTED"

    entry_snapshot = _entry_snapshot(now)
    exit_snapshot = _exit_snapshot(now)
    quality_result = evaluate_contract_quality(packet, entry_snapshot, config)
    paper_sim_result = simulate_round_trip(
        packet, entry_snapshot, exit_snapshot, risk_result, quality_result, config
    )
    assert paper_sim_result.status == "REJECTED"
    assert paper_sim_result.failed_stage == "risk_gate"

    review_result = build_dry_run_review(
        packet, risk_result, quality_result, paper_sim_result, entry_snapshot, config
    )
    assert review_result.status == "REJECTED"
    assert review_result.failed_stage == "risk_gate"
    assert review_result.order_intent is None

    # The chain cannot proceed past this point: build_confirmation_request
    # requires a REVIEW_READY review_result and refuses anything else.
    with pytest.raises(ValueError):
        build_confirmation_request(
            review_result, config, reviewer="alice", approval_text=REQUIRED_PHRASE, now=now, nonce="n"
        )


def test_contract_quality_rejection_stops_before_dry_run_ticket_boundary():
    now = datetime.now(timezone.utc)
    config = _config()

    packet = build_packet(_raw_packet_input())
    risk_result = evaluate_packet(packet, config)
    assert risk_result.status == "APPROVED"

    # Wide spread -> REJECTED at contract_quality.
    entry_snapshot = _entry_snapshot(now, bid=1.00, ask=1.90)
    exit_snapshot = _exit_snapshot(now)
    quality_result = evaluate_contract_quality(packet, entry_snapshot, config)
    assert quality_result.status == "REJECTED"
    assert quality_result.failed_rule == "spread_too_wide"

    paper_sim_result = simulate_round_trip(
        packet, entry_snapshot, exit_snapshot, risk_result, quality_result, config
    )
    assert paper_sim_result.status == "REJECTED"
    assert paper_sim_result.failed_stage == "contract_quality"

    review_result = build_dry_run_review(
        packet, risk_result, quality_result, paper_sim_result, entry_snapshot, config
    )
    assert review_result.status == "REJECTED"
    assert review_result.failed_stage == "contract_quality"
    assert review_result.order_intent is None

    with pytest.raises(ValueError):
        build_confirmation_request(
            review_result, config, reviewer="alice", approval_text=REQUIRED_PHRASE, now=now, nonce="n"
        )


def test_paper_sim_rejection_stops_before_ticket_boundary():
    now = datetime.now(timezone.utc)
    config = _config()

    packet = build_packet(_raw_packet_input())
    risk_result = evaluate_packet(packet, config)
    quality_result = evaluate_contract_quality(packet, _entry_snapshot(now), config)
    assert risk_result.status == "APPROVED"
    assert quality_result.status == "APPROVED"

    # Invalid entry fill mode -> REJECTED at paper_sim.
    entry_snapshot = _entry_snapshot(now)
    exit_snapshot = _exit_snapshot(now)
    paper_sim_result = simulate_round_trip(
        packet,
        entry_snapshot,
        exit_snapshot,
        risk_result,
        quality_result,
        _config(paper_sim_entry_fill="NOT_A_MODE"),
    )
    assert paper_sim_result.status == "REJECTED"
    assert paper_sim_result.failed_stage == "fill_model"

    review_result = build_dry_run_review(
        packet,
        risk_result,
        quality_result,
        paper_sim_result,
        entry_snapshot,
        _config(paper_sim_entry_fill="NOT_A_MODE"),
    )
    assert review_result.status == "REJECTED"
    assert review_result.failed_stage == "paper_sim"
    assert review_result.order_intent is None


def test_dry_run_review_rejection_stops_before_human_confirm_ticket_boundary():
    now = datetime.now(timezone.utc)
    config = _config()

    # Snapshot missing ask -> DATA_BLOCKED at dry_run_review (needs ask to
    # build the estimated limit price), even though risk/quality/paper_sim
    # can all still pass on a snapshot without ask depending on config.
    packet = build_packet(_raw_packet_input())
    risk_result = evaluate_packet(packet, config)
    entry_snapshot_no_ask = _entry_snapshot(now, ask=None, bid=None)
    quality_result = evaluate_contract_quality(
        packet, entry_snapshot_no_ask, _config(quality_missing_quote_blocks=False)
    )
    assert quality_result.status == "APPROVED"

    paper_sim_result = simulate_round_trip(
        packet,
        entry_snapshot_no_ask,
        _exit_snapshot(now),
        risk_result,
        quality_result,
        _config(quality_missing_quote_blocks=False, paper_sim_entry_fill="LAST"),
    )
    review_result = build_dry_run_review(
        packet,
        risk_result,
        quality_result,
        paper_sim_result,
        entry_snapshot_no_ask,
        _config(quality_missing_quote_blocks=False, paper_sim_entry_fill="LAST"),
    )
    assert review_result.status == "DATA_BLOCKED"
    assert review_result.failed_stage == "snapshot"
    assert review_result.order_intent is None

    with pytest.raises(ValueError):
        build_confirmation_request(
            review_result, config, reviewer="alice", approval_text=REQUIRED_PHRASE, now=now, nonce="n"
        )


def test_malformed_confirmation_cannot_reach_confirmed():
    now = datetime.now(timezone.utc)
    config = _config()

    _, risk_result, quality_result, paper_sim_result, review_result = _run_happy_path(now, config)
    assert review_result.status == "REVIEW_READY"

    confirmation_request = build_confirmation_request(
        review_result, config, reviewer="alice", approval_text=REQUIRED_PHRASE, now=now, nonce="nonce-1"
    )

    # Wrong approval phrase.
    wrong_phrase_record = replace(
        _bridge_confirmation_record(confirmation_request, confirmation_id="conf-bad-1"),
        approval_text="I APPROVE",
    )
    result_wrong_phrase = confirm_order_intent(review_result, wrong_phrase_record, config)
    assert result_wrong_phrase.status == "REJECTED"
    assert result_wrong_phrase.failed_stage == "approval_text"

    # Wrong intent_id (confirmation issued for a different intent).
    wrong_intent_record = replace(
        _bridge_confirmation_record(confirmation_request, confirmation_id="conf-bad-2"),
        intent_id="not-the-real-intent-id",
    )
    result_wrong_intent = confirm_order_intent(review_result, wrong_intent_record, config)
    assert result_wrong_intent.status == "REJECTED"
    assert result_wrong_intent.failed_stage == "intent_mismatch"

    # Expired confirmation.
    expired_record = replace(
        _bridge_confirmation_record(confirmation_request, confirmation_id="conf-bad-3"),
        expires_at=now - timedelta(seconds=1),
    )
    result_expired = confirm_order_intent(review_result, expired_record, config)
    assert result_expired.status == "EXPIRED"

    # None of these malformed confirmations reached CONFIRMED, so the chain
    # correctly cannot proceed to build_order_ticket for any of them.
    for bad_result in (result_wrong_phrase, result_wrong_intent, result_expired):
        assert bad_result.status != "CONFIRMED"
        ticket_result = build_order_ticket(bad_result, config, now=now)
        assert ticket_result.status != "TICKET_READY"
        assert ticket_result.ticket is None


def test_order_ticket_rejection_stops_before_broker_boundary():
    now = datetime.now(timezone.utc)
    config = _config()

    _, risk_result, quality_result, paper_sim_result, review_result = _run_happy_path(now, config)
    confirmation_request = build_confirmation_request(
        review_result, config, reviewer="alice", approval_text=REQUIRED_PHRASE, now=now, nonce="nonce-1"
    )
    confirmation_record = _bridge_confirmation_record(
        confirmation_request, confirmation_id="conf-int-2"
    )
    confirmed_prep = confirm_order_intent(review_result, confirmation_record, config)
    assert confirmed_prep.status == "CONFIRMED"

    # Independent order_ticket cap set stricter than what the intent needs ->
    # REJECTED at order_ticket, even though everything upstream passed.
    strict_ticket_config = _config(order_ticket_max_notional=1.00)
    order_ticket_result = build_order_ticket(confirmed_prep, strict_ticket_config, now=now)
    assert order_ticket_result.status == "REJECTED"
    assert order_ticket_result.failed_stage == "notional"
    assert order_ticket_result.ticket is None

    # Guarded caller behavior: never call build_preview_request when the
    # ticket build did not succeed.
    if order_ticket_result.status == "TICKET_READY" and order_ticket_result.ticket is not None:
        pytest.fail("order_ticket_result unexpectedly TICKET_READY; test setup is wrong")


def test_malformed_ticket_cannot_reach_preview_ready():
    now = datetime.now(timezone.utc)
    config = _config()

    _, risk_result, quality_result, paper_sim_result, review_result = _run_happy_path(now, config)
    confirmation_request = build_confirmation_request(
        review_result, config, reviewer="alice", approval_text=REQUIRED_PHRASE, now=now, nonce="nonce-1"
    )
    confirmation_record = _bridge_confirmation_record(
        confirmation_request, confirmation_id="conf-int-3"
    )
    confirmed_prep = confirm_order_intent(review_result, confirmation_record, config)
    order_ticket_result = build_order_ticket(confirmed_prep, config, now=now)
    assert order_ticket_result.status == "TICKET_READY"
    ticket = order_ticket_result.ticket
    assert ticket is not None

    # A ticket tampered to violate build_preview_request's own invariants is
    # refused (raises), not silently converted into a preview request.
    tampered_executable = replace(ticket, executable=True)
    with pytest.raises(ValueError):
        build_preview_request(tampered_executable, config)

    tampered_not_dry_run = replace(ticket, dry_run_only=False)
    with pytest.raises(ValueError):
        build_preview_request(tampered_not_dry_run, config)

    # A legitimately-built ticket that still fails broker_boundary's own,
    # independent (stricter) caps is rejected there too -- defense in depth,
    # not blind trust of the upstream ticket build.
    preview_request = build_preview_request(ticket, config)
    strict_boundary_config = _config(broker_boundary_max_notional=1.00)
    preview_result = validate_preview_boundary(preview_request, strict_boundary_config)
    assert preview_result.status == "REJECTED"
    assert preview_result.failed_stage == "notional"
    assert preview_result.preview_ready is False


def test_allow_real_preview_true_still_fully_inert():
    now = datetime.now(timezone.utc)
    config = _config()

    _, risk_result, quality_result, paper_sim_result, review_result = _run_happy_path(now, config)
    confirmation_request = build_confirmation_request(
        review_result, config, reviewer="alice", approval_text=REQUIRED_PHRASE, now=now, nonce="nonce-1"
    )
    confirmation_record = _bridge_confirmation_record(
        confirmation_request, confirmation_id="conf-int-4"
    )
    confirmed_prep = confirm_order_intent(review_result, confirmation_record, config)
    order_ticket_result = build_order_ticket(confirmed_prep, config, now=now)
    ticket = order_ticket_result.ticket
    preview_request = build_preview_request(ticket, config)

    preview_result = validate_preview_boundary(
        preview_request, _config(broker_boundary_allow_real_preview=True)
    )
    assert preview_result.status == "PREVIEW_READY"
    assert preview_result.submitted is False
    assert preview_result.executable is False
    assert preview_result.broker is None
    assert preview_result.broker_order_id is None
    assert REAL_PREVIEW_BLOCKED_WARNING in preview_result.warnings


# --- safety tests ---------------------------------------------------------------


def test_pipeline_writes_no_journal_beyond_options_journal(tmp_path, monkeypatch):
    import options_manager.journal as journal_mod

    monkeypatch.setattr(
        "options_manager.packet_builder.log_packet",
        lambda packet: journal_mod.log_packet(packet, journal_dir=str(tmp_path)),
    )
    monkeypatch.setattr("options_manager.notify._default_send", lambda url, payload: True)

    now = datetime.now(timezone.utc)
    config = _config()
    packet, risk_result, quality_result, paper_sim_result, review_result = _run_happy_path(
        now, config
    )
    confirmation_request = build_confirmation_request(
        review_result, config, reviewer="alice", approval_text=REQUIRED_PHRASE, now=now, nonce="nonce-1"
    )
    confirmation_record = _bridge_confirmation_record(
        confirmation_request, confirmation_id="conf-int-5"
    )
    confirmed_prep = confirm_order_intent(review_result, confirmation_record, config)
    order_ticket_result = build_order_ticket(confirmed_prep, config, now=now)
    preview_request = build_preview_request(order_ticket_result.ticket, config)
    validate_preview_boundary(preview_request, config)

    today = now.date().isoformat()
    assert (tmp_path / f"options_journal_{today}.jsonl").exists()
    assert not (tmp_path / f"journal_{today}.jsonl").exists()


def test_pipeline_does_not_mutate_live_options_flag(monkeypatch):
    monkeypatch.delenv("LIVE_OPTIONS_TRADING_ENABLED", raising=False)

    now = datetime.now(timezone.utc)
    config = _config()
    packet, risk_result, quality_result, paper_sim_result, review_result = _run_happy_path(
        now, config
    )
    confirmation_request = build_confirmation_request(
        review_result, config, reviewer="alice", approval_text=REQUIRED_PHRASE, now=now, nonce="nonce-1"
    )
    confirmation_record = _bridge_confirmation_record(
        confirmation_request, confirmation_id="conf-int-6"
    )
    confirmed_prep = confirm_order_intent(review_result, confirmation_record, config)
    order_ticket_result = build_order_ticket(confirmed_prep, config, now=now)
    preview_request = build_preview_request(order_ticket_result.ticket, config)
    validate_preview_boundary(preview_request, config)

    import os

    assert os.getenv("LIVE_OPTIONS_TRADING_ENABLED") is None


def test_pipeline_never_opens_a_network_socket(monkeypatch):
    """Belt-and-suspenders: prove the full chain (minus Phase 1's own mocked
    Discord call) never touches the network by making any real socket
    creation raise."""

    def _forbidden_socket(*args, **kwargs):
        raise AssertionError("pipeline integration chain attempted to open a real socket")

    monkeypatch.setattr(socket, "socket", _forbidden_socket)
    monkeypatch.setattr("options_manager.notify._default_send", lambda url, payload: True)

    now = datetime.now(timezone.utc)
    config = _config()
    packet, risk_result, quality_result, paper_sim_result, review_result = _run_happy_path(
        now, config
    )
    confirmation_request = build_confirmation_request(
        review_result, config, reviewer="alice", approval_text=REQUIRED_PHRASE, now=now, nonce="nonce-1"
    )
    confirmation_record = _bridge_confirmation_record(
        confirmation_request, confirmation_id="conf-int-7"
    )
    confirmed_prep = confirm_order_intent(review_result, confirmation_record, config)
    order_ticket_result = build_order_ticket(confirmed_prep, config, now=now)
    preview_request = build_preview_request(order_ticket_result.ticket, config)
    preview_result = validate_preview_boundary(preview_request, config)

    assert preview_result.status == "PREVIEW_READY"


def test_integration_test_module_has_no_forbidden_imports():
    # This test file itself must not import from execution/, webhook/,
    # risk/risk_engine.py, or alert_ranker/ — the integration audit's own
    # test harness stays inside the same safety boundary as the code it
    # exercises.
    path = Path(__file__)
    tree = ast.parse(path.read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    forbidden_module_fragments = (
        "execution",
        "webhook",
        "risk_engine",
        "alert_ranker",
    )
    for module in modules:
        for forbidden in forbidden_module_fragments:
            assert forbidden not in module, (
                f"test_options_pipeline_integration.py must not import {module!r}"
            )
