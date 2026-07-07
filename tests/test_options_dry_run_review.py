"""
tests/test_options_dry_run_review.py

Phase 5 dry-run order review tests. Pure/deterministic construction of a
local OptionOrderIntent review object — no broker, no Robinhood, no
Tradovate, no HTTP, no Discord, no file writes, no order preview/placement.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import options_manager.dry_run_review as dry_run_review_module
from options_manager.config import OptionsManagerConfig
from options_manager.contract_quality import ContractMarketSnapshot, ContractQualityResult
from options_manager.dry_run_review import DryRunReviewResult, build_dry_run_review
from options_manager.models import OptionTradePacket
from options_manager.paper_sim import PaperSimResult
from options_manager.risk_gate import RiskGateResult


def _packet(**overrides) -> OptionTradePacket:
    base = dict(
        ticker="BAC",
        direction="CALL",
        entry_price=60.11,
        price_target=62.50,
        signa_score=78,
        signa_grade="B",
        signa_bias="BULLISH",
        gex_regime="LOW_PINNING",
        gex_wall_above=None,
        gex_wall_below=None,
        contract_strike=60.00,
        contract_expiry=date.today() + timedelta(days=30),
        max_premium=2.00,
        max_contracts=1,
        account_tag="agentic_micro_account",
        source="claude_session",
        created_at=datetime.now(timezone.utc),
        status="PENDING",
        rejection_reason=None,
    )
    base.update(overrides)
    return OptionTradePacket(**base)


def _snapshot(**overrides) -> ContractMarketSnapshot:
    base = dict(
        ticker="BAC",
        contract_symbol="BAC260821C00060000",
        bid=1.90,
        ask=1.95,
        last=1.93,
        volume=500,
        open_interest=1000,
        implied_volatility=0.35,
        delta=0.45,
        theta=-0.03,
        underlying_price=60.20,
        quote_timestamp=datetime.now(timezone.utc),
        provider="mock",
        is_snapshot_complete=True,
    )
    base.update(overrides)
    return ContractMarketSnapshot(**base)


def _risk_result(**overrides) -> RiskGateResult:
    base = dict(approved=True, status="APPROVED", failed_rule=None, reason="", warnings=[])
    base.update(overrides)
    return RiskGateResult(**base)


def _quality_result(**overrides) -> ContractQualityResult:
    base = dict(approved=True, status="APPROVED", failed_rule=None, reason="", warnings=[])
    base.update(overrides)
    return ContractQualityResult(**base)


def _paper_sim_result(**overrides) -> PaperSimResult:
    base = dict(
        approved_for_sim=True,
        status="SIMULATED",
        failed_stage=None,
        reason="",
        simulated_entry_price=1.95,
        simulated_exit_price=2.90,
        simulated_contracts=1,
        simulated_gross_pnl=95.0,
        simulated_fees=0.0,
        simulated_net_pnl=95.0,
        warnings=[],
    )
    base.update(overrides)
    return PaperSimResult(**base)


def _config(**overrides) -> OptionsManagerConfig:
    return replace(OptionsManagerConfig(), **overrides)


def test_valid_pipeline_builds_review_ready():
    result = build_dry_run_review(
        _packet(), _risk_result(), _quality_result(), _paper_sim_result(), _snapshot(), _config()
    )
    assert result.approved_for_review is True
    assert result.status == "REVIEW_READY"
    assert result.failed_stage is None
    assert result.order_intent is not None
    assert result.estimated_notional == pytest.approx(195.0)


def test_order_intent_dry_run_only_is_true():
    result = build_dry_run_review(
        _packet(), _risk_result(), _quality_result(), _paper_sim_result(), _snapshot(), _config()
    )
    assert result.order_intent.dry_run_only is True


def test_order_action_is_buy_to_open():
    result = build_dry_run_review(
        _packet(), _risk_result(), _quality_result(), _paper_sim_result(), _snapshot(), _config()
    )
    assert result.order_intent.order_action == "BUY_TO_OPEN"


def test_non_pending_packet_rejects():
    packet = _packet(status="REJECTED", rejection_reason="some prior reason")
    result = build_dry_run_review(
        packet, _risk_result(), _quality_result(), _paper_sim_result(), _snapshot(), _config()
    )
    assert result.approved_for_review is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "packet_status"
    assert result.order_intent is None


def test_risk_gate_rejected_rejects_with_failed_stage_risk_gate():
    risk_result = _risk_result(approved=False, status="REJECTED", failed_rule="premium_cap", reason="too rich")
    result = build_dry_run_review(
        _packet(), risk_result, _quality_result(), _paper_sim_result(), _snapshot(), _config()
    )
    assert result.approved_for_review is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "risk_gate"
    assert "premium_cap" in result.reason


def test_risk_gate_data_blocked_propagates_as_data_blocked_risk_gate():
    risk_result = _risk_result(
        approved=False, status="DATA_BLOCKED", failed_rule="gex_regime_missing", reason="no data"
    )
    result = build_dry_run_review(
        _packet(), risk_result, _quality_result(), _paper_sim_result(), _snapshot(), _config()
    )
    assert result.approved_for_review is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "risk_gate"


def test_quality_gate_rejected_rejects_with_failed_stage_contract_quality():
    quality_result = _quality_result(
        approved=False, status="REJECTED", failed_rule="spread_too_wide", reason="too wide"
    )
    result = build_dry_run_review(
        _packet(), _risk_result(), quality_result, _paper_sim_result(), _snapshot(), _config()
    )
    assert result.approved_for_review is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "contract_quality"
    assert "spread_too_wide" in result.reason


def test_quality_gate_data_blocked_propagates_as_data_blocked_contract_quality():
    quality_result = _quality_result(
        approved=False, status="DATA_BLOCKED", failed_rule="quote_missing", reason="no quote"
    )
    result = build_dry_run_review(
        _packet(), _risk_result(), quality_result, _paper_sim_result(), _snapshot(), _config()
    )
    assert result.approved_for_review is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "contract_quality"


def test_paper_sim_rejected_rejects_with_failed_stage_paper_sim():
    paper_sim_result = _paper_sim_result(
        approved_for_sim=False, status="REJECTED", failed_stage="entry_snapshot", reason="bad fill"
    )
    result = build_dry_run_review(
        _packet(), _risk_result(), _quality_result(), paper_sim_result, _snapshot(), _config()
    )
    assert result.approved_for_review is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "paper_sim"
    assert "entry_snapshot" in result.reason


def test_paper_sim_data_blocked_propagates_as_data_blocked_paper_sim():
    paper_sim_result = _paper_sim_result(
        approved_for_sim=False, status="DATA_BLOCKED", failed_stage="exit_snapshot", reason="no exit data"
    )
    result = build_dry_run_review(
        _packet(), _risk_result(), _quality_result(), paper_sim_result, _snapshot(), _config()
    )
    assert result.approved_for_review is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "paper_sim"


def test_paper_sim_precondition_skippable_via_config():
    paper_sim_result = _paper_sim_result(
        approved_for_sim=False, status="REJECTED", failed_stage="entry_snapshot", reason="bad fill"
    )
    result = build_dry_run_review(
        _packet(),
        _risk_result(),
        _quality_result(),
        paper_sim_result,
        _snapshot(),
        _config(dry_run_require_paper_simulated=False),
    )
    assert result.approved_for_review is True
    assert result.status == "REVIEW_READY"


def test_missing_ask_data_blocked():
    result = build_dry_run_review(
        _packet(), _risk_result(), _quality_result(), _paper_sim_result(), _snapshot(ask=None), _config()
    )
    assert result.approved_for_review is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "snapshot"
    assert result.order_intent is None


def test_ask_zero_or_negative_rejects():
    result_zero = build_dry_run_review(
        _packet(), _risk_result(), _quality_result(), _paper_sim_result(), _snapshot(ask=0.0), _config()
    )
    assert result_zero.status == "REJECTED"
    assert result_zero.failed_stage == "limit_price"

    result_negative = build_dry_run_review(
        _packet(), _risk_result(), _quality_result(), _paper_sim_result(), _snapshot(ask=-1.0), _config()
    )
    assert result_negative.status == "REJECTED"
    assert result_negative.failed_stage == "limit_price"


def test_ask_above_packet_max_premium_rejects():
    result = build_dry_run_review(
        _packet(max_premium=1.50),
        _risk_result(),
        _quality_result(),
        _paper_sim_result(),
        _snapshot(ask=2.00),
        _config(),
    )
    assert result.status == "REJECTED"
    assert result.failed_stage == "limit_price"


def test_quantity_below_one_rejects():
    # max_contracts=0 bypasses packet_builder's own floor since we construct
    # OptionTradePacket directly here, exercising dry_run_review's own defensive check.
    result = build_dry_run_review(
        _packet(max_contracts=0),
        _risk_result(),
        _quality_result(),
        _paper_sim_result(),
        _snapshot(),
        _config(),
    )
    assert result.status == "REJECTED"
    assert result.failed_stage == "quantity"


def test_quantity_above_dry_run_max_contracts_rejects():
    result = build_dry_run_review(
        _packet(max_contracts=3),
        _risk_result(),
        _quality_result(),
        _paper_sim_result(),
        _snapshot(),
        _config(dry_run_max_contracts=2),
    )
    assert result.status == "REJECTED"
    assert result.failed_stage == "quantity"


def test_estimated_notional_above_cap_rejects():
    result = build_dry_run_review(
        _packet(max_contracts=1, max_premium=3.00),
        _risk_result(),
        _quality_result(),
        _paper_sim_result(),
        _snapshot(ask=2.50),
        _config(dry_run_max_notional=200.00),
    )
    assert result.status == "REJECTED"
    assert result.failed_stage == "notional"


def test_invalid_account_tag_rejects():
    result = build_dry_run_review(
        _packet(account_tag="live_margin_account"),
        _risk_result(),
        _quality_result(),
        _paper_sim_result(),
        _snapshot(),
        _config(),
    )
    assert result.status == "REJECTED"
    assert result.failed_stage == "account_tag"


def test_dry_run_disabled_rejects_with_failed_stage_dry_run_disabled():
    result = build_dry_run_review(
        _packet(),
        _risk_result(),
        _quality_result(),
        _paper_sim_result(),
        _snapshot(),
        _config(dry_run_enabled=False),
    )
    assert result.approved_for_review is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "dry_run_disabled"
    assert result.order_intent is None


def test_dry_run_disabled_blocks_before_evaluating_malformed_data():
    # Proves the kill switch is truly first: even with a non-PENDING packet,
    # rejected risk/quality/paper-sim results, and a missing snapshot ask —
    # every other precondition failing simultaneously — dry_run_enabled=False
    # must still be the reported reason, not any of those other failures.
    malformed_packet = _packet(status="REJECTED", rejection_reason="whatever")
    malformed_risk = _risk_result(approved=False, status="REJECTED", failed_rule="x", reason="y")
    malformed_quality = _quality_result(
        approved=False, status="DATA_BLOCKED", failed_rule="z", reason="w"
    )
    malformed_paper_sim = _paper_sim_result(
        approved_for_sim=False, status="REJECTED", failed_stage="q", reason="r"
    )
    malformed_snapshot = _snapshot(ask=None)

    result = build_dry_run_review(
        malformed_packet,
        malformed_risk,
        malformed_quality,
        malformed_paper_sim,
        malformed_snapshot,
        _config(dry_run_enabled=False),
    )
    assert result.approved_for_review is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "dry_run_disabled"
    assert result.order_intent is None


def test_defensive_check_blocks_review_ready_if_dry_run_only_is_false(monkeypatch):
    # Proves the post-construction defensive re-check actually works: even if
    # a hypothetical future bug caused OptionOrderIntent to be built with
    # dry_run_only=False, build_dry_run_review must catch it and refuse to
    # return REVIEW_READY rather than trusting the object it just built.
    real_intent_cls = dry_run_review_module.OptionOrderIntent

    def _tampered_intent(**kwargs):
        kwargs["dry_run_only"] = False
        return real_intent_cls(**kwargs)

    monkeypatch.setattr(dry_run_review_module, "OptionOrderIntent", _tampered_intent)

    result = build_dry_run_review(
        _packet(), _risk_result(), _quality_result(), _paper_sim_result(), _snapshot(), _config()
    )
    assert result.approved_for_review is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "dry_run_only"


def test_invalid_order_action_config_rejects():
    result = build_dry_run_review(
        _packet(),
        _risk_result(),
        _quality_result(),
        _paper_sim_result(),
        _snapshot(),
        _config(dry_run_order_action="SELL_TO_CLOSE"),
    )
    assert result.status == "REJECTED"
    assert result.failed_stage == "order_action"


def test_dry_run_review_does_not_write_futures_journal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    build_dry_run_review(
        _packet(), _risk_result(), _quality_result(), _paper_sim_result(), _snapshot(), _config()
    )

    today = date.today().isoformat()
    assert not (tmp_path / "logs" / f"journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs" / f"options_journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs" / f"options_dry_run_review_{today}.jsonl").exists()
    assert not (tmp_path / "logs").exists()


def test_dry_run_review_does_not_write_options_journal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    build_dry_run_review(
        _packet(max_contracts=2),
        _risk_result(),
        _quality_result(),
        _paper_sim_result(),
        _snapshot(),
        _config(),
    )
    assert not (tmp_path / "logs").exists()


def _dry_run_review_ast():
    path = Path(dry_run_review_module.__file__)
    return ast.parse(path.read_text())


def _dry_run_review_imported_modules() -> list[str]:
    tree = _dry_run_review_ast()
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _dry_run_review_referenced_identifiers() -> set[str]:
    tree = _dry_run_review_ast()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_dry_run_review_has_no_forbidden_imports():
    # Real imports only — ast.Import/ImportFrom nodes never include docstrings
    # or comments, so this can't false-positive on descriptive text.
    modules = _dry_run_review_imported_modules()
    forbidden_module_fragments = (
        "execution",
        "webhook",
        "risk_engine",
        "alert_ranker",
        "httpx",
        "requests",
        "urllib",
        "socket",
    )
    for module in modules:
        for forbidden in forbidden_module_fragments:
            assert forbidden not in module, f"dry_run_review.py must not import {module!r}"


def test_dry_run_review_has_no_broker_notify_or_network_identifiers():
    # Real identifiers referenced in code (Name/Attribute nodes) — again,
    # docstrings/comments are not part of the AST and can't trigger this.
    identifiers = {name.lower() for name in _dry_run_review_referenced_identifiers()}
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
    }
    overlap = identifiers & forbidden
    assert not overlap, f"dry_run_review.py references forbidden identifiers: {overlap}"


def test_dry_run_review_module_has_no_journal_or_config_file_reads():
    path = Path(dry_run_review_module.__file__)
    source = path.read_text()
    assert "open(" not in source
    assert ".write(" not in source
    assert "mkdir" not in source


def test_build_dry_run_review_requires_explicit_config():
    with pytest.raises(TypeError):
        build_dry_run_review(
            _packet(), _risk_result(), _quality_result(), _paper_sim_result(), _snapshot()
        )
