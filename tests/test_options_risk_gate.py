"""
tests/test_options_risk_gate.py

Phase 2 options risk gate tests. Pure/deterministic re-validation of a
PENDING OptionTradePacket — no broker, no Robinhood, no Tradovate, no HTTP,
no Discord, no file writes.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import options_manager.risk_gate as risk_gate_module
from options_manager.config import OptionsManagerConfig
from options_manager.models import OptionTradePacket
from options_manager.risk_gate import evaluate_packet


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


def _config(**overrides) -> OptionsManagerConfig:
    return replace(OptionsManagerConfig(), **overrides)


def test_valid_bac_packet_approves():
    result = evaluate_packet(_packet(), _config())
    assert result.approved is True
    assert result.status == "APPROVED"
    assert result.failed_rule is None


def test_rejected_packet_status_fails():
    packet = _packet(status="REJECTED", rejection_reason="some prior reason")
    result = evaluate_packet(packet, _config())
    assert result.approved is False
    assert result.status == "REJECTED"
    assert result.failed_rule == "packet_not_pending"


def test_premium_over_max_rejects():
    result = evaluate_packet(_packet(max_premium=2.5), _config(risk_max_premium=2.0))
    assert result.status == "REJECTED"
    assert result.failed_rule == "premium_cap"


def test_contracts_over_max_rejects():
    result = evaluate_packet(_packet(max_contracts=2), _config(risk_max_contracts=1))
    assert result.status == "REJECTED"
    assert result.failed_rule == "contracts_cap"


def test_total_premium_risk_over_max_rejects():
    # 2.00 * 100 * 1 = 200, cap 150 -> reject
    result = evaluate_packet(
        _packet(max_premium=2.00, max_contracts=1),
        _config(risk_max_total_premium_dollars=150.00),
    )
    assert result.status == "REJECTED"
    assert result.failed_rule == "total_premium_risk"


def test_expiry_too_close_rejects():
    result = evaluate_packet(
        _packet(contract_expiry=date.today() + timedelta(days=7)),
        _config(risk_min_dte_days=14),
    )
    assert result.status == "REJECTED"
    assert result.failed_rule == "min_dte"


def test_call_target_below_entry_rejects():
    result = evaluate_packet(
        _packet(direction="CALL", entry_price=60.0, price_target=59.0), _config()
    )
    assert result.status == "REJECTED"
    assert result.failed_rule == "target_direction_mismatch"


def test_put_target_above_entry_rejects():
    result = evaluate_packet(
        _packet(
            direction="PUT",
            signa_bias="BEARISH",
            entry_price=60.0,
            price_target=61.0,
        ),
        _config(),
    )
    assert result.status == "REJECTED"
    assert result.failed_rule == "target_direction_mismatch"


def test_low_signa_score_rejects():
    result = evaluate_packet(_packet(signa_score=10), _config(risk_min_signa_score=30))
    assert result.status == "REJECTED"
    assert result.failed_rule == "signa_score_min"


def test_grade_c_rejects():
    result = evaluate_packet(_packet(signa_grade="C"), _config())
    assert result.status == "REJECTED"
    assert result.failed_rule == "signa_grade_not_allowed"


def test_signa_bias_mismatch_rejects():
    result = evaluate_packet(
        _packet(direction="CALL", signa_bias="BEARISH"), _config()
    )
    assert result.status == "REJECTED"
    assert result.failed_rule == "signa_bias_mismatch"


def test_empty_gex_regime_rejects_by_default():
    result = evaluate_packet(
        _packet(gex_regime=""), _config(risk_reject_empty_gex_regime=True)
    )
    assert result.approved is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_rule == "gex_regime_missing"


def test_empty_gex_regime_approves_with_warning_when_not_rejecting():
    result = evaluate_packet(
        _packet(gex_regime=""), _config(risk_reject_empty_gex_regime=False)
    )
    assert result.approved is True
    assert result.status == "APPROVED"
    assert any("gex_regime" in w for w in result.warnings)


def test_unknown_gex_regime_approves_with_warning():
    result = evaluate_packet(_packet(gex_regime="UNKNOWN"), _config())
    assert result.approved is True
    assert result.status == "APPROVED"
    assert any("UNKNOWN" in w for w in result.warnings)


def test_unrecognized_nonempty_gex_regime_approves_with_warning():
    result = evaluate_packet(_packet(gex_regime="SOME_NEW_PROVIDER_LABEL"), _config())
    assert result.approved is True
    assert result.status == "APPROVED"
    assert any("SOME_NEW_PROVIDER_LABEL" in w for w in result.warnings)


def test_known_gex_regimes_approve_with_no_warning():
    for regime in ("LOW_PINNING", "HIGH_PINNING", "NEG_GAMMA", "POS_GAMMA"):
        result = evaluate_packet(_packet(gex_regime=regime), _config())
        assert result.approved is True
        assert result.warnings == []


def test_invalid_account_tag_rejects():
    result = evaluate_packet(_packet(account_tag="live_margin_account"), _config())
    assert result.status == "REJECTED"
    assert result.failed_rule == "account_tag_not_allowed"


def test_risk_gate_does_not_write_futures_journal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    evaluate_packet(_packet(), _config())

    today = date.today().isoformat()
    assert not (tmp_path / "logs" / f"journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs" / f"options_journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs").exists()


def _risk_gate_ast():
    path = Path(risk_gate_module.__file__)
    return ast.parse(path.read_text())


def _risk_gate_imported_modules() -> list[str]:
    tree = _risk_gate_ast()
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _risk_gate_referenced_identifiers() -> set[str]:
    tree = _risk_gate_ast()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_risk_gate_has_no_forbidden_imports():
    # Real imports only — ast.Import/ImportFrom nodes never include docstrings
    # or comments, so this can't false-positive on descriptive text.
    modules = _risk_gate_imported_modules()
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
            assert forbidden not in module, f"risk_gate.py must not import {module!r}"


def test_risk_gate_has_no_broker_or_order_or_notify_identifiers():
    # Real identifiers referenced in code (Name/Attribute nodes) — again,
    # docstrings/comments are not part of the AST and can't trigger this.
    identifiers = {name.lower() for name in _risk_gate_referenced_identifiers()}
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
    }
    overlap = identifiers & forbidden
    assert not overlap, f"risk_gate.py references forbidden identifiers: {overlap}"


def test_risk_gate_module_has_no_journal_or_config_file_reads():
    path = Path(risk_gate_module.__file__)
    source = path.read_text()
    assert "open(" not in source
    assert ".write(" not in source
    assert "mkdir" not in source


def test_evaluate_packet_requires_explicit_config():
    # config has no default — omitting it must fail loudly at call time,
    # never silently fall back to reading env/.env.
    with pytest.raises(TypeError):
        evaluate_packet(_packet())


def test_risk_gate_does_not_call_config_from_env():
    # Structural guarantee via real AST Name/Attribute nodes only (never
    # docstrings/comments, so this can't false-positive on descriptive text).
    # Catches any reintroduction of the from_env()/getenv/load_dotenv fallback
    # this fix removed.
    identifiers = _risk_gate_referenced_identifiers()
    for forbidden in ("from_env", "getenv", "load_dotenv", "environ"):
        assert forbidden not in identifiers, (
            f"risk_gate.py must not reference {forbidden!r} — "
            "config must always be passed in explicitly by the caller"
        )
