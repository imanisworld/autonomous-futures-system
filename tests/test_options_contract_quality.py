"""
tests/test_options_contract_quality.py

Phase 3 contract quality / market data gate tests. Pure/deterministic
evaluation of a supplied ContractMarketSnapshot — no broker, no Robinhood,
no Tradovate, no HTTP, no Discord, no file writes, no provider fetching.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import options_manager.contract_quality as contract_quality_module
from options_manager.config import OptionsManagerConfig
from options_manager.contract_quality import (
    ContractMarketSnapshot,
    evaluate_contract_quality,
)
from options_manager.models import OptionTradePacket


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
        ask=2.00,
        last=1.95,
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


def _config(**overrides) -> OptionsManagerConfig:
    return replace(OptionsManagerConfig(), **overrides)


def test_valid_bac_snapshot_approves():
    result = evaluate_contract_quality(_packet(), _snapshot(), _config())
    assert result.approved is True
    assert result.status == "APPROVED"
    assert result.failed_rule is None


def test_non_pending_packet_rejects():
    packet = _packet(status="REJECTED", rejection_reason="some prior reason")
    result = evaluate_contract_quality(packet, _snapshot(), _config())
    assert result.approved is False
    assert result.status == "REJECTED"
    assert result.failed_rule == "packet_not_pending"


def test_zero_entry_price_rejects_without_crashing():
    # A quality gate must not approve/warn-only on structurally invalid
    # packet data — and this also guarantees no ZeroDivisionError.
    result = evaluate_contract_quality(
        _packet(entry_price=0), _snapshot(), _config()
    )
    assert result.approved is False
    assert result.status == "REJECTED"
    assert result.failed_rule == "entry_price_invalid"
    assert "entry price" in result.reason.lower() or "entry_price" in result.reason


def test_negative_entry_price_rejects_without_crashing():
    result = evaluate_contract_quality(
        _packet(entry_price=-5.0), _snapshot(), _config()
    )
    assert result.approved is False
    assert result.status == "REJECTED"
    assert result.failed_rule == "entry_price_invalid"


def test_naive_quote_timestamp_data_blocked():
    naive_timestamp = datetime.now()  # no tzinfo
    result = evaluate_contract_quality(
        _packet(), _snapshot(quote_timestamp=naive_timestamp), _config()
    )
    assert result.approved is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_rule == "quote_timestamp_not_timezone_aware"


def test_missing_bid_data_blocked():
    result = evaluate_contract_quality(_packet(), _snapshot(bid=None), _config())
    assert result.approved is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_rule == "quote_missing"


def test_missing_ask_data_blocked():
    result = evaluate_contract_quality(_packet(), _snapshot(ask=None), _config())
    assert result.status == "DATA_BLOCKED"
    assert result.failed_rule == "quote_missing"


def test_ask_below_bid_rejects():
    result = evaluate_contract_quality(
        _packet(), _snapshot(bid=2.00, ask=1.90), _config()
    )
    assert result.status == "REJECTED"
    assert result.failed_rule == "bid_ask_invalid"


def test_zero_bid_rejects():
    result = evaluate_contract_quality(_packet(), _snapshot(bid=0.0), _config())
    assert result.status == "REJECTED"
    assert result.failed_rule == "bid_ask_invalid"


def test_spread_above_max_rejects():
    # spread = 1.00, midpoint = 1.50 -> 66.7% spread, way above 20% default
    result = evaluate_contract_quality(
        _packet(), _snapshot(bid=1.00, ask=2.00), _config()
    )
    assert result.status == "REJECTED"
    assert result.failed_rule == "spread_too_wide"


def test_low_volume_rejects():
    result = evaluate_contract_quality(_packet(), _snapshot(volume=10), _config())
    assert result.status == "REJECTED"
    assert result.failed_rule == "volume_too_low"


def test_low_open_interest_rejects():
    result = evaluate_contract_quality(
        _packet(), _snapshot(open_interest=10), _config()
    )
    assert result.status == "REJECTED"
    assert result.failed_rule == "open_interest_too_low"


def test_missing_volume_data_blocked_by_default():
    result = evaluate_contract_quality(_packet(), _snapshot(volume=None), _config())
    assert result.status == "DATA_BLOCKED"
    assert result.failed_rule == "volume_or_oi_missing"


def test_missing_open_interest_data_blocked_by_default():
    result = evaluate_contract_quality(
        _packet(), _snapshot(open_interest=None), _config()
    )
    assert result.status == "DATA_BLOCKED"
    assert result.failed_rule == "volume_or_oi_missing"


def test_ask_above_packet_max_premium_rejects():
    result = evaluate_contract_quality(
        _packet(max_premium=1.50), _snapshot(bid=1.90, ask=2.00), _config()
    )
    assert result.status == "REJECTED"
    assert result.failed_rule == "premium_exceeds_packet_max"


def test_missing_underlying_price_data_blocked_by_default():
    result = evaluate_contract_quality(
        _packet(), _snapshot(underlying_price=None), _config()
    )
    assert result.status == "DATA_BLOCKED"
    assert result.failed_rule == "underlying_price_missing"


def test_underlying_price_mismatch_warns_by_default():
    # entry_price=60.11, underlying_price=70.00 -> ~16% diff, above 3% default
    result = evaluate_contract_quality(
        _packet(entry_price=60.11), _snapshot(underlying_price=70.00), _config()
    )
    assert result.approved is True
    assert result.status == "APPROVED"
    assert any("underlying_price" in w for w in result.warnings)


def test_underlying_price_mismatch_rejects_when_configured():
    result = evaluate_contract_quality(
        _packet(entry_price=60.11),
        _snapshot(underlying_price=70.00),
        _config(quality_reject_underlying_price_mismatch=True),
    )
    assert result.status == "REJECTED"
    assert result.failed_rule == "underlying_price_mismatch"


def test_stale_quote_data_blocked():
    stale_timestamp = datetime.now(timezone.utc) - timedelta(seconds=1000)
    result = evaluate_contract_quality(
        _packet(), _snapshot(quote_timestamp=stale_timestamp), _config()
    )
    assert result.approved is False
    assert result.status == "DATA_BLOCKED"
    assert result.failed_rule == "quote_stale"


def test_missing_quote_timestamp_data_blocked_by_default():
    result = evaluate_contract_quality(
        _packet(), _snapshot(quote_timestamp=None), _config()
    )
    assert result.status == "DATA_BLOCKED"
    assert result.failed_rule == "quote_timestamp_missing"


def test_delta_below_min_rejects():
    result = evaluate_contract_quality(_packet(), _snapshot(delta=0.10), _config())
    assert result.status == "REJECTED"
    assert result.failed_rule == "delta_out_of_range"


def test_delta_above_max_rejects():
    result = evaluate_contract_quality(_packet(), _snapshot(delta=0.90), _config())
    assert result.status == "REJECTED"
    assert result.failed_rule == "delta_out_of_range"


def test_call_delta_negative_sign_warns_only():
    # abs(delta) = 0.45 is within range; only the sign is unexpected for a CALL.
    result = evaluate_contract_quality(
        _packet(direction="CALL"), _snapshot(delta=-0.45), _config()
    )
    assert result.approved is True
    assert result.status == "APPROVED"
    assert any("CALL delta" in w for w in result.warnings)


def test_missing_delta_warns_by_default():
    result = evaluate_contract_quality(_packet(), _snapshot(delta=None), _config())
    assert result.approved is True
    assert any("Greeks" in w for w in result.warnings)


def test_iv_zero_or_negative_rejects():
    result = evaluate_contract_quality(
        _packet(), _snapshot(implied_volatility=0.0), _config()
    )
    assert result.status == "REJECTED"
    assert result.failed_rule == "iv_invalid"

    result_negative = evaluate_contract_quality(
        _packet(), _snapshot(implied_volatility=-0.1), _config()
    )
    assert result_negative.status == "REJECTED"
    assert result_negative.failed_rule == "iv_invalid"


def test_high_iv_warns_by_default():
    result = evaluate_contract_quality(
        _packet(), _snapshot(implied_volatility=1.50), _config()
    )
    assert result.approved is True
    assert any("implied_volatility" in w for w in result.warnings)


def test_missing_greeks_warn_by_default():
    result = evaluate_contract_quality(
        _packet(),
        _snapshot(implied_volatility=None, delta=None, theta=None),
        _config(),
    )
    assert result.approved is True
    assert any("Greeks" in w for w in result.warnings)


def test_missing_greeks_data_blocked_when_configured():
    result = evaluate_contract_quality(
        _packet(),
        _snapshot(implied_volatility=None, delta=None, theta=None),
        _config(quality_missing_greeks_blocks=True),
    )
    assert result.status == "DATA_BLOCKED"
    assert result.failed_rule == "greeks_missing"


def test_empty_provider_data_blocked():
    result = evaluate_contract_quality(_packet(), _snapshot(provider=""), _config())
    assert result.status == "DATA_BLOCKED"
    assert result.failed_rule == "provider_missing"


def test_mock_provider_allowed_by_default():
    result = evaluate_contract_quality(
        _packet(), _snapshot(provider="mock"), _config()
    )
    assert result.approved is True


def test_mock_provider_rejected_when_disallowed():
    result = evaluate_contract_quality(
        _packet(),
        _snapshot(provider="test"),
        _config(quality_allow_mock_provider=False),
    )
    assert result.status == "REJECTED"
    assert result.failed_rule == "provider_not_allowed"


def test_theta_extreme_warns_only():
    # ask=2.00, theta=-0.5 -> ratio 0.25, above default 0.10 threshold, warn only.
    result = evaluate_contract_quality(_packet(), _snapshot(theta=-0.5), _config())
    assert result.approved is True
    assert any("theta" in w for w in result.warnings)


def test_missing_quote_config_off_degrades_to_warning_and_skips_dependent_checks():
    result = evaluate_contract_quality(
        _packet(),
        _snapshot(bid=None, ask=None),
        _config(quality_missing_quote_blocks=False),
    )
    assert result.approved is True
    assert any("bid/ask" in w for w in result.warnings)


def test_contract_quality_does_not_write_futures_journal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    evaluate_contract_quality(_packet(), _snapshot(), _config())

    today = date.today().isoformat()
    assert not (tmp_path / "logs" / f"journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs" / f"options_journal_{today}.jsonl").exists()
    assert not (tmp_path / "logs").exists()


def _contract_quality_ast():
    path = Path(contract_quality_module.__file__)
    return ast.parse(path.read_text())


def _contract_quality_imported_modules() -> list[str]:
    tree = _contract_quality_ast()
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _contract_quality_referenced_identifiers() -> set[str]:
    tree = _contract_quality_ast()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_contract_quality_has_no_forbidden_imports():
    # Real imports only — ast.Import/ImportFrom nodes never include docstrings
    # or comments, so this can't false-positive on descriptive text.
    modules = _contract_quality_imported_modules()
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
            assert forbidden not in module, f"contract_quality.py must not import {module!r}"


def test_contract_quality_has_no_broker_notify_or_network_identifiers():
    # Real identifiers referenced in code (Name/Attribute nodes) — again,
    # docstrings/comments are not part of the AST and can't trigger this.
    identifiers = {name.lower() for name in _contract_quality_referenced_identifiers()}
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
    }
    overlap = identifiers & forbidden
    assert not overlap, f"contract_quality.py references forbidden identifiers: {overlap}"


def test_contract_quality_module_has_no_journal_or_config_file_reads():
    path = Path(contract_quality_module.__file__)
    source = path.read_text()
    assert "open(" not in source
    assert ".write(" not in source
    assert "mkdir" not in source


def test_evaluate_contract_quality_requires_explicit_config():
    with pytest.raises(TypeError):
        evaluate_contract_quality(_packet(), _snapshot())
