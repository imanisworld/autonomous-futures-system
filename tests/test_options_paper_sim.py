"""
tests/test_options_paper_sim.py

Phase 4 options paper simulation tests. Pure/deterministic round-trip
simulation using supplied entry/exit snapshots — no broker, no Robinhood,
no Tradovate, no HTTP, no Discord, no file writes, no provider fetching.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import options_manager.paper_sim as paper_sim_module
from options_manager.config import OptionsManagerConfig
from options_manager.contract_quality import ContractMarketSnapshot, ContractQualityResult
from options_manager.models import OptionTradePacket
from options_manager.paper_sim import PaperSimResult, simulate_round_trip
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
        ask=2.10,
        last=2.05,
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


def _entry_snapshot(**overrides) -> ContractMarketSnapshot:
    base = dict(bid=1.90, ask=2.10, last=2.05)
    base.update(overrides)
    return _snapshot(**base)


def _exit_snapshot(**overrides) -> ContractMarketSnapshot:
    base = dict(bid=2.90, ask=3.10, last=2.95)
    base.update(overrides)
    return _snapshot(**base)


def _risk_result(**overrides) -> RiskGateResult:
    base = dict(approved=True, status="APPROVED", failed_rule=None, reason="", warnings=[])
    base.update(overrides)
    return RiskGateResult(**base)


def _quality_result(**overrides) -> ContractQualityResult:
    base = dict(approved=True, status="APPROVED", failed_rule=None, reason="", warnings=[])
    base.update(overrides)
    return ContractQualityResult(**base)


def _config(**overrides) -> OptionsManagerConfig:
    return replace(OptionsManagerConfig(), **overrides)


def test_valid_round_trip_simulates_with_ask_entry_and_bid_exit():
    result = simulate_round_trip(
        _packet(), _entry_snapshot(), _exit_snapshot(), _risk_result(), _quality_result(), _config()
    )
    assert result.approved_for_sim is True
    assert result.status == "SIMULATED"
    assert result.failed_stage is None
    assert result.simulated_entry_price == 2.10
    assert result.simulated_exit_price == 2.90


def test_non_pending_packet_rejects_before_sim():
    packet = _packet(status="REJECTED", rejection_reason="some prior reason")
    result = simulate_round_trip(
        packet, _entry_snapshot(), _exit_snapshot(), _risk_result(), _quality_result(), _config()
    )
    assert result.approved_for_sim is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "packet_status"


def test_risk_gate_rejected_rejects_with_failed_stage_risk_gate():
    risk_result = _risk_result(approved=False, status="REJECTED", failed_rule="premium_cap", reason="too rich")
    result = simulate_round_trip(
        _packet(), _entry_snapshot(), _exit_snapshot(), risk_result, _quality_result(), _config()
    )
    assert result.approved_for_sim is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "risk_gate"
    assert "premium_cap" in result.reason


def test_risk_gate_data_blocked_propagates_as_data_blocked_risk_gate():
    risk_result = _risk_result(
        approved=False, status="DATA_BLOCKED", failed_rule="gex_regime_missing", reason="no data"
    )
    result = simulate_round_trip(
        _packet(), _entry_snapshot(), _exit_snapshot(), risk_result, _quality_result(), _config()
    )
    assert result.approved_for_sim is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "risk_gate"


def test_quality_gate_rejected_rejects_with_failed_stage_contract_quality():
    quality_result = _quality_result(
        approved=False, status="REJECTED", failed_rule="spread_too_wide", reason="too wide"
    )
    result = simulate_round_trip(
        _packet(), _entry_snapshot(), _exit_snapshot(), _risk_result(), quality_result, _config()
    )
    assert result.approved_for_sim is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "contract_quality"
    assert "spread_too_wide" in result.reason


def test_quality_gate_data_blocked_propagates_as_data_blocked_contract_quality():
    quality_result = _quality_result(
        approved=False, status="DATA_BLOCKED", failed_rule="quote_missing", reason="no quote"
    )
    result = simulate_round_trip(
        _packet(), _entry_snapshot(), _exit_snapshot(), _risk_result(), quality_result, _config()
    )
    assert result.approved_for_sim is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "contract_quality"


def test_require_approved_risk_false_skips_risk_gate_check():
    risk_result = _risk_result(approved=False, status="REJECTED", failed_rule="premium_cap", reason="too rich")
    result = simulate_round_trip(
        _packet(),
        _entry_snapshot(),
        _exit_snapshot(),
        risk_result,
        _quality_result(),
        _config(paper_sim_require_approved_risk=False),
    )
    assert result.approved_for_sim is True
    assert result.status == "SIMULATED"


def test_require_approved_quality_false_skips_quality_gate_check():
    quality_result = _quality_result(
        approved=False, status="REJECTED", failed_rule="spread_too_wide", reason="too wide"
    )
    result = simulate_round_trip(
        _packet(),
        _entry_snapshot(),
        _exit_snapshot(),
        _risk_result(),
        quality_result,
        _config(paper_sim_require_approved_quality=False),
    )
    assert result.approved_for_sim is True
    assert result.status == "SIMULATED"


def test_missing_entry_ask_data_blocked():
    result = simulate_round_trip(
        _packet(), _entry_snapshot(ask=None), _exit_snapshot(), _risk_result(), _quality_result(), _config()
    )
    assert result.approved_for_sim is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "entry_snapshot"


def test_missing_exit_bid_data_blocked():
    result = simulate_round_trip(
        _packet(), _entry_snapshot(), _exit_snapshot(bid=None), _risk_result(), _quality_result(), _config()
    )
    assert result.approved_for_sim is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_stage == "exit_snapshot"


def test_entry_fill_ask_uses_entry_snapshot_ask():
    result = simulate_round_trip(
        _packet(), _entry_snapshot(ask=2.25), _exit_snapshot(), _risk_result(), _quality_result(), _config()
    )
    assert result.simulated_entry_price == 2.25


def test_exit_fill_bid_uses_exit_snapshot_bid():
    result = simulate_round_trip(
        _packet(), _entry_snapshot(), _exit_snapshot(bid=3.35), _risk_result(), _quality_result(), _config()
    )
    assert result.simulated_exit_price == 3.35


def test_entry_fill_mid_uses_midpoint():
    result = simulate_round_trip(
        _packet(),
        _entry_snapshot(bid=1.90, ask=2.10),
        _exit_snapshot(),
        _risk_result(),
        _quality_result(),
        _config(paper_sim_entry_fill="MID"),
    )
    assert result.simulated_entry_price == 2.00


def test_exit_fill_mid_uses_midpoint():
    result = simulate_round_trip(
        _packet(),
        _entry_snapshot(),
        _exit_snapshot(bid=2.90, ask=3.10),
        _risk_result(),
        _quality_result(),
        _config(paper_sim_exit_fill="MID"),
    )
    assert result.simulated_exit_price == 3.00


def test_entry_fill_last_requires_last_present():
    result_ok = simulate_round_trip(
        _packet(),
        _entry_snapshot(last=2.05),
        _exit_snapshot(),
        _risk_result(),
        _quality_result(),
        _config(paper_sim_entry_fill="LAST"),
    )
    assert result_ok.status == "SIMULATED"
    assert result_ok.simulated_entry_price == 2.05

    result_missing = simulate_round_trip(
        _packet(),
        _entry_snapshot(last=None),
        _exit_snapshot(),
        _risk_result(),
        _quality_result(),
        _config(paper_sim_entry_fill="LAST"),
    )
    assert result_missing.status == "DATA_BLOCKED"
    assert result_missing.failed_stage == "entry_snapshot"


def test_exit_fill_last_requires_last_present():
    result_ok = simulate_round_trip(
        _packet(),
        _entry_snapshot(),
        _exit_snapshot(last=2.95),
        _risk_result(),
        _quality_result(),
        _config(paper_sim_exit_fill="LAST"),
    )
    assert result_ok.status == "SIMULATED"
    assert result_ok.simulated_exit_price == 2.95

    result_missing = simulate_round_trip(
        _packet(),
        _entry_snapshot(),
        _exit_snapshot(last=None),
        _risk_result(),
        _quality_result(),
        _config(paper_sim_exit_fill="LAST"),
    )
    assert result_missing.status == "DATA_BLOCKED"
    assert result_missing.failed_stage == "exit_snapshot"


def test_gross_pnl_math_correct_for_winning_trade():
    packet = _packet(max_contracts=1)
    result = simulate_round_trip(
        packet,
        _entry_snapshot(ask=2.00),
        _exit_snapshot(bid=3.00),
        _risk_result(),
        _quality_result(),
        _config(),
    )
    assert result.status == "SIMULATED"
    assert result.simulated_gross_pnl == 100.0
    assert result.simulated_fees == 0.0
    assert result.simulated_net_pnl == 100.0


def test_losing_trade_pnl_correct():
    packet = _packet(max_contracts=1)
    result = simulate_round_trip(
        packet,
        _entry_snapshot(ask=2.00),
        _exit_snapshot(bid=0.50),
        _risk_result(),
        _quality_result(),
        _config(),
    )
    assert result.status == "SIMULATED"
    assert result.simulated_gross_pnl == -150.0
    assert result.simulated_net_pnl == -150.0


def test_fees_deducted_correctly():
    packet = _packet(max_contracts=2)
    result = simulate_round_trip(
        packet,
        _entry_snapshot(ask=2.00),
        _exit_snapshot(bid=3.00),
        _risk_result(),
        _quality_result(),
        _config(paper_sim_per_contract_fee=0.65),
    )
    assert result.status == "SIMULATED"
    assert result.simulated_gross_pnl == 200.0
    assert result.simulated_fees == pytest.approx(2.6)
    assert result.simulated_net_pnl == pytest.approx(197.4)


def test_net_pnl_math_correct():
    packet = _packet(max_contracts=1)
    result = simulate_round_trip(
        packet,
        _entry_snapshot(ask=2.00),
        _exit_snapshot(bid=3.00),
        _risk_result(),
        _quality_result(),
        _config(paper_sim_per_contract_fee=1.00),
    )
    assert result.simulated_net_pnl == pytest.approx(
        result.simulated_gross_pnl - result.simulated_fees
    )


def test_zero_exit_bid_accepted_as_valid_worthless_exit():
    packet = _packet(max_contracts=1)
    result = simulate_round_trip(
        packet,
        _entry_snapshot(ask=2.00),
        _exit_snapshot(bid=0.0),
        _risk_result(),
        _quality_result(),
        _config(),
    )
    assert result.approved_for_sim is True
    assert result.status == "SIMULATED"
    assert result.simulated_exit_price == 0.0
    assert result.simulated_gross_pnl == -200.0


def test_simulated_contracts_equals_packet_max_contracts():
    packet = _packet(max_contracts=2)
    result = simulate_round_trip(
        packet, _entry_snapshot(), _exit_snapshot(), _risk_result(), _quality_result(), _config()
    )
    assert result.simulated_contracts == 2


def test_invalid_entry_fill_mode_rejects():
    result = simulate_round_trip(
        _packet(),
        _entry_snapshot(),
        _exit_snapshot(),
        _risk_result(),
        _quality_result(),
        _config(paper_sim_entry_fill="OPEN"),
    )
    assert result.approved_for_sim is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "fill_model"
    assert "fill" in result.reason.lower()


def test_invalid_exit_fill_mode_rejects():
    result = simulate_round_trip(
        _packet(),
        _entry_snapshot(),
        _exit_snapshot(),
        _risk_result(),
        _quality_result(),
        _config(paper_sim_exit_fill="CLOSE"),
    )
    assert result.approved_for_sim is False
    assert result.status == "REJECTED"
    assert result.failed_stage == "fill_model"


def test_paper_sim_does_not_write_futures_journal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    simulate_round_trip(
        _packet(), _entry_snapshot(), _exit_snapshot(), _risk_result(), _quality_result(), _config()
    )

    today = date.today().isoformat()
    assert not (tmp_path / "logs" / f"journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs" / f"options_journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs" / f"options_paper_sim_{today}.jsonl").exists()
    assert not (tmp_path / "logs").exists()


def test_paper_sim_does_not_write_options_journal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    simulate_round_trip(
        _packet(max_contracts=2),
        _entry_snapshot(),
        _exit_snapshot(),
        _risk_result(),
        _quality_result(),
        _config(paper_sim_per_contract_fee=0.50),
    )
    assert not (tmp_path / "logs").exists()


def _paper_sim_ast():
    path = Path(paper_sim_module.__file__)
    return ast.parse(path.read_text())


def _paper_sim_imported_modules() -> list[str]:
    tree = _paper_sim_ast()
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _paper_sim_referenced_identifiers() -> set[str]:
    tree = _paper_sim_ast()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_paper_sim_has_no_forbidden_imports():
    # Real imports only — ast.Import/ImportFrom nodes never include docstrings
    # or comments, so this can't false-positive on descriptive text.
    modules = _paper_sim_imported_modules()
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
            assert forbidden not in module, f"paper_sim.py must not import {module!r}"


def test_paper_sim_has_no_broker_notify_or_network_identifiers():
    # Real identifiers referenced in code (Name/Attribute nodes) — again,
    # docstrings/comments are not part of the AST and can't trigger this.
    identifiers = {name.lower() for name in _paper_sim_referenced_identifiers()}
    forbidden = {
        "place_order",
        "submit_order",
        "cancel_order",
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
    assert not overlap, f"paper_sim.py references forbidden identifiers: {overlap}"


def test_paper_sim_module_has_no_journal_or_config_file_reads():
    path = Path(paper_sim_module.__file__)
    source = path.read_text()
    assert "open(" not in source
    assert ".write(" not in source
    assert "mkdir" not in source


def test_simulate_round_trip_requires_explicit_config():
    with pytest.raises(TypeError):
        simulate_round_trip(
            _packet(), _entry_snapshot(), _exit_snapshot(), _risk_result(), _quality_result()
        )
