"""
tests/test_options_packet_builder.py

Phase 1 options_manager tests. Packet-level validation only — no broker,
no Robinhood, no Tradovate, no real Discord calls.
"""

from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from options_manager import journal as journal_mod
from options_manager import packet_builder as packet_builder_module
from options_manager.app import SECRET_HEADER
from options_manager.app import app as fastapi_app
from options_manager.live_lock import (
    LiveOptionsTradingBlockedError,
    assert_live_options_trading_disabled,
)
from options_manager.packet_builder import build_packet

client = TestClient(fastapi_app)


def _valid_raw_input(**overrides) -> dict:
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


@pytest.fixture(autouse=True)
def _mock_discord(monkeypatch):
    """Never allow a real Discord call from this test module."""
    sent = []

    def _fake_send(webhook_url: str, payload: dict) -> bool:
        sent.append((webhook_url, payload))
        return True

    monkeypatch.setattr("options_manager.notify._default_send", _fake_send)
    return sent


@pytest.fixture(autouse=True)
def _isolated_journal_dir(tmp_path, monkeypatch):
    """Redirect all journal writes to a throwaway directory for every test,
    so tests never write into the real repo's logs/ directory."""
    monkeypatch.setattr(
        "options_manager.packet_builder.log_packet",
        lambda packet: journal_mod.log_packet(packet, journal_dir=str(tmp_path)),
    )
    return tmp_path


def test_valid_call_packet_target_above_entry_is_pending():
    packet = build_packet(_valid_raw_input())
    assert packet.status == "PENDING"
    assert packet.rejection_reason is None


def test_signa_score_below_minimum_is_rejected():
    packet = build_packet(_valid_raw_input(signa_score=25))
    assert packet.status == "REJECTED"
    assert "score" in packet.rejection_reason


def test_grade_c_is_rejected():
    packet = build_packet(_valid_raw_input(signa_grade="C"))
    assert packet.status == "REJECTED"
    assert "grade" in packet.rejection_reason.lower()


def test_expiry_too_close_is_rejected():
    packet = build_packet(
        _valid_raw_input(contract_expiry=date.today() + timedelta(days=7))
    )
    assert packet.status == "REJECTED"
    assert "expiry" in packet.rejection_reason.lower()


def test_premium_above_ceiling_is_rejected():
    packet = build_packet(_valid_raw_input(max_premium=3.50))
    assert packet.status == "REJECTED"
    assert "premium" in packet.rejection_reason.lower()


def test_contracts_above_ceiling_is_capped_not_rejected():
    packet = build_packet(_valid_raw_input(max_contracts=3))
    assert packet.status == "PENDING"
    assert packet.max_contracts == 2


def test_missing_price_target_is_rejected():
    raw = _valid_raw_input()
    del raw["price_target"]
    packet = build_packet(raw)
    assert packet.status == "REJECTED"
    assert "price_target" in packet.rejection_reason.lower()


def test_call_with_target_below_entry_is_rejected():
    packet = build_packet(
        _valid_raw_input(direction="CALL", entry_price=60.0, price_target=59.0)
    )
    assert packet.status == "REJECTED"
    assert "price_target" in packet.rejection_reason.lower()


def test_put_with_target_above_entry_is_rejected():
    packet = build_packet(
        _valid_raw_input(
            direction="PUT",
            signa_bias="BEARISH",
            entry_price=60.0,
            price_target=61.0,
        )
    )
    assert packet.status == "REJECTED"
    assert "price_target" in packet.rejection_reason.lower()


def test_journal_writes_only_to_options_journal_file(_isolated_journal_dir):
    build_packet(_valid_raw_input())

    today = date.today().isoformat()
    options_path = _isolated_journal_dir / f"options_journal_{today}.jsonl"
    futures_path = _isolated_journal_dir / f"journal_{today}.jsonl"

    assert options_path.exists()
    assert not futures_path.exists()


def test_journal_never_writes_futures_journal_filename(_isolated_journal_dir):
    build_packet(_valid_raw_input())

    today = date.today().isoformat()
    path = _isolated_journal_dir / f"options_journal_{today}.jsonl"

    assert path.exists()
    assert path.name != f"journal_{today}.jsonl"


def test_discord_notify_is_mocked_no_real_webhook_call(monkeypatch):
    calls = []

    def _fake_send(webhook_url: str, payload: dict) -> bool:
        calls.append((webhook_url, payload))
        return True

    monkeypatch.setattr("options_manager.notify._default_send", _fake_send)
    monkeypatch.setenv("OPTIONS_MANAGER_DISCORD_WEBHOOK_URL", "https://discord.example/webhook")

    build_packet(_valid_raw_input())

    assert len(calls) == 1
    assert calls[0][0] == "https://discord.example/webhook"


def test_live_options_trading_enabled_true_raises(monkeypatch):
    monkeypatch.setenv("LIVE_OPTIONS_TRADING_ENABLED", "true")
    with pytest.raises(LiveOptionsTradingBlockedError):
        assert_live_options_trading_disabled()


def test_live_options_trading_disabled_or_unset_does_not_raise(monkeypatch):
    monkeypatch.delenv("LIVE_OPTIONS_TRADING_ENABLED", raising=False)
    assert_live_options_trading_disabled() is None

    monkeypatch.setenv("LIVE_OPTIONS_TRADING_ENABLED", "false")
    assert_live_options_trading_disabled() is None


# --- ticker validation --------------------------------------------------------


def test_ticker_none_is_rejected():
    packet = build_packet(_valid_raw_input(ticker=None))
    assert packet.status == "REJECTED"
    assert "ticker" in packet.rejection_reason.lower()


def test_ticker_empty_string_is_rejected():
    packet = build_packet(_valid_raw_input(ticker=""))
    assert packet.status == "REJECTED"
    assert "ticker" in packet.rejection_reason.lower()


def test_ticker_whitespace_only_is_rejected():
    packet = build_packet(_valid_raw_input(ticker="   "))
    assert packet.status == "REJECTED"
    assert "ticker" in packet.rejection_reason.lower()


def test_valid_ticker_is_still_pending():
    packet = build_packet(_valid_raw_input(ticker="BAC"))
    assert packet.status == "PENDING"
    assert packet.rejection_reason is None


# --- direction validation ---------------------------------------------------


def test_invalid_direction_is_rejected():
    packet = build_packet(_valid_raw_input(direction="SPREAD"))
    assert packet.status == "REJECTED"
    assert "direction" in packet.rejection_reason.lower()


# --- floor validation --------------------------------------------------------


def test_signa_score_negative_is_rejected():
    packet = build_packet(_valid_raw_input(signa_score=-1))
    assert packet.status == "REJECTED"


def test_signa_score_above_100_is_rejected():
    packet = build_packet(_valid_raw_input(signa_score=101))
    assert packet.status == "REJECTED"


def test_entry_price_zero_is_rejected():
    packet = build_packet(_valid_raw_input(entry_price=0))
    assert packet.status == "REJECTED"


def test_contract_strike_zero_is_rejected():
    packet = build_packet(_valid_raw_input(contract_strike=0))
    assert packet.status == "REJECTED"


def test_max_premium_zero_is_rejected():
    packet = build_packet(_valid_raw_input(max_premium=0))
    assert packet.status == "REJECTED"


def test_max_contracts_zero_is_rejected():
    packet = build_packet(_valid_raw_input(max_contracts=0))
    assert packet.status == "REJECTED"


def test_price_target_zero_is_rejected():
    packet = build_packet(_valid_raw_input(price_target=0))
    assert packet.status == "REJECTED"


# --- endpoint hardening ------------------------------------------------------


def _json_safe_raw_input(**overrides) -> dict:
    raw = _valid_raw_input(**overrides)
    if isinstance(raw.get("contract_expiry"), date):
        raw["contract_expiry"] = raw["contract_expiry"].isoformat()
    return raw


def test_endpoint_missing_required_field_returns_400(monkeypatch):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    raw = _json_safe_raw_input()
    del raw["ticker"]

    response = client.post("/options/packet", json=raw)
    body = response.json()

    assert response.status_code == 400
    assert body["error"] == "invalid_packet"
    assert body["detail"] == "missing or malformed packet field"
    assert "'ticker'" not in response.text
    assert "KeyError" not in response.text


def test_endpoint_missing_direction_returns_400(monkeypatch):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    raw = _json_safe_raw_input()
    del raw["direction"]

    response = client.post("/options/packet", json=raw)
    body = response.json()

    assert response.status_code == 400
    assert body["detail"] == "missing or malformed packet field"
    assert "'direction'" not in response.text


def test_endpoint_malformed_field_does_not_leak_exception_text(monkeypatch):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    raw = _json_safe_raw_input(entry_price="not-a-number")

    response = client.post("/options/packet", json=raw)
    body = response.json()

    assert response.status_code == 400
    assert body == {"error": "invalid_packet", "detail": "missing or malformed packet field"}
    assert "could not convert" not in response.text
    assert "not-a-number" not in response.text


def test_endpoint_requires_secret_when_configured(monkeypatch):
    monkeypatch.setenv("OPTIONS_MANAGER_INGEST_SECRET", "s3cr3t")
    raw = _json_safe_raw_input()

    missing_header = client.post("/options/packet", json=raw)
    assert missing_header.status_code == 401

    wrong_header = client.post(
        "/options/packet", json=raw, headers={SECRET_HEADER: "wrong"}
    )
    assert wrong_header.status_code == 401


def test_endpoint_accepts_valid_packet_with_correct_secret(monkeypatch):
    monkeypatch.setenv("OPTIONS_MANAGER_INGEST_SECRET", "s3cr3t")
    raw = _json_safe_raw_input()

    response = client.post(
        "/options/packet", json=raw, headers={SECRET_HEADER: "s3cr3t"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"


# --- structural safety (Phase 10 audit gap closure) --------------------------


def _packet_builder_imported_modules() -> list[str]:
    path = Path(packet_builder_module.__file__)
    tree = ast.parse(path.read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_packet_builder_has_no_forbidden_imports():
    # Real imports only — ast.Import/ImportFrom nodes never include
    # docstrings or comments, so this can't false-positive on descriptive
    # text. Scoped to packet_builder.py's own imports only; journal.py and
    # notify.py retain their existing Phase 1 I/O behavior unchanged.
    modules = _packet_builder_imported_modules()
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
            assert forbidden not in module, f"packet_builder.py must not import {module!r}"
