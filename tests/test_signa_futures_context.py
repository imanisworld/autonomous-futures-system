from __future__ import annotations

import importlib
import sys

from context.signa_futures_context import (
    DEFAULT_TIMEFRAMES,
    SignaProxyObservation,
    build_futures_signa_context,
    needed_proxies,
    proxies_for_futures,
)
from sources.signa_observation import SignaActionCardObservation


def obs(symbol: str, timeframe: str, direction: str, *, ok: bool = True, error=None):
    return SignaProxyObservation(
        proxy=symbol,
        timeframe=timeframe,
        observation=SignaActionCardObservation(
            ok=ok,
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            grade="B",
            score=66.0,
            confidence=70.0,
            error=error,
        ),
    )


def test_proxy_mapping_is_context_only():
    assert proxies_for_futures("MNQ1!") == ("QQQ",)
    assert proxies_for_futures("MESZ26") == ("SPY",)
    assert proxies_for_futures("M2K") == ("IWM",)
    assert proxies_for_futures("RTY") == ("IWM",)
    assert proxies_for_futures("MGC") == ("GLD",)
    assert proxies_for_futures("MCL") == ("USO", "XLE")


def test_needed_proxies_adds_regime_context_once():
    proxies = needed_proxies(["MNQ", "MES"])
    assert proxies[:2] == ("QQQ", "SPY")
    assert proxies.count("QQQ") == 1
    assert "TLT" in proxies
    assert "VIX" in proxies

def test_ftfc_aligned_context_record_is_non_authoritative():
    observations = [obs("QQQ", timeframe, "LONG") for timeframe in DEFAULT_TIMEFRAMES]
    context = build_futures_signa_context("MNQ", observations, trade_direction="LONG")
    record = context.to_record()
    assert record["ftfc_state"] == "SIGNA_FTFC_LONG"
    assert record["primary_bias"] == "LONG"
    assert record["aligned_with_trade_direction"] is True
    assert "SIGNA_TRADE_ALIGNED" in record["tags"]
    assert record["gate_authoritative"] is False
    assert record["broker_evaluated"] is False
    assert record["risk_evaluated"] is False
    assert record["trade_authorized"] is False

def test_mixed_context_records_conflict_without_blocking():
    observations = [
        obs("QQQ", "1h", "LONG"),
        obs("QQQ", "4h", "SHORT"),
        obs("QQQ", "1d", "LONG"),
    ]
    context = build_futures_signa_context("MNQ", observations, trade_direction="SHORT")
    record = context.to_record()
    assert record["ftfc_state"] == "SIGNA_MIXED"
    assert record["aligned_with_trade_direction"] is False
    assert "SIGNA_TRADE_CONFLICT" in record["tags"]
    assert "SIGNA_CONTEXT_MISSING" not in record["tags"]

def test_errors_and_missing_are_recorded_fail_soft():
    context = build_futures_signa_context(
        "MNQ",
        [obs("QQQ", "1h", "", ok=False, error="http_429")],
        trade_direction="LONG",
    )
    record = context.to_record()
    assert record["ftfc_state"] == "SIGNA_INCOMPLETE"
    assert "SIGNA_CONTEXT_MISSING" in record["tags"]
    assert "SIGNA_CONTEXT_ERRORS" in record["tags"]
    assert record["errors"] == ["QQQ:1h:http_429"]

def test_module_import_does_not_import_execution_or_risk_modules():
    for name in list(sys.modules):
        if name == "context.signa_futures_context":
            del sys.modules[name]
    before = set(sys.modules)
    importlib.import_module("context.signa_futures_context")
    imported = set(sys.modules) - before
    forbidden = {
        "execution.tradovate_broker",
        "execution.broker_interface",
        "execution.paper_broker",
        "risk.risk_engine",
        "strategy.signal_engine",
    }
    assert forbidden.isdisjoint(imported)

from pathlib import Path
from types import SimpleNamespace

from sources.signa_snapshot_store import SignaSnapshotStore
from context.signa_futures_context import build_signa_futures_context


def test_shared_snapshot_store_enriches_futures_context_without_authority(tmp_path):
    db = tmp_path / "options.sqlite"
    stored = SignaSnapshotStore(db).record_snapshot(
        endpoint="/api/v1/signals/QQQ",
        symbol="QQQ",
        timeframe="1d",
        params={"symbol": "QQQ", "timeframe": "1d"},
        retrieved_at="2026-09-20T14:00:00Z",
        data_as_of="2026-09-20T13:55:00Z",
        payload={
            "data_as_of": "2026-09-20T13:55:00Z",
            "data": {
                "signal": {
                    "symbol": "QQQ",
                    "timeframe": "1d",
                    "direction": "bullish",
                    "grade": "A",
                    "score": 82,
                    "confidence": 77,
                }
            },
        },
    )
    state = SimpleNamespace(instrument="MNQ", signa=None)

    record = build_signa_futures_context(
        state,
        futures_direction="LONG",
        timeframe="1d",
        snapshot_db_path=db,
        now="2026-09-20T14:05:00Z",
    )

    assert record["schema_version"] == "signa_futures_context_v2"
    assert record["primary_bias"] == "LONG"
    assert record["aligned_with_trade_direction"] is True
    assert record["snapshot_ids"] == [stored.snapshot_id]
    assert record["snapshot_status"] == "OK"
    assert record["observations"][0]["snapshot_id"] == stored.snapshot_id
    assert record["observations"][0]["grade"] == "A"
    assert record["observations"][0]["score"] == 82.0
    assert record["trade_authorized"] is False
    assert record["gate_authoritative"] is False
    assert record["risk_evaluated"] is False
    assert record["broker_evaluated"] is False


def test_shared_snapshot_lookup_is_read_only_and_missing_db_fails_soft(tmp_path):
    missing = tmp_path / "missing.sqlite"
    state = SimpleNamespace(instrument="MNQ", signa=None)

    record = build_signa_futures_context(
        state,
        futures_direction="LONG",
        timeframe="1d",
        snapshot_db_path=missing,
        now="2026-09-20T14:05:00Z",
    )

    assert not missing.exists()
    assert record["snapshot_status"] == "ERROR"
    assert "SIGNA_CONTEXT_ERRORS" in record["tags"]
    assert record["trade_authorized"] is False


def test_stale_shared_snapshot_is_recorded_without_blocking(tmp_path):
    db = tmp_path / "options.sqlite"
    SignaSnapshotStore(db).record_snapshot(
        endpoint="/api/v1/signals/QQQ",
        symbol="QQQ",
        timeframe="1d",
        params={"symbol": "QQQ", "timeframe": "1d"},
        retrieved_at="2026-09-20T10:00:00Z",
        data_as_of="2026-09-20T09:55:00Z",
        payload={"data": {"signal": {"symbol": "QQQ", "timeframe": "1d", "direction": "bullish"}}},
    )
    state = SimpleNamespace(instrument="MNQ", signa=None)

    record = build_signa_futures_context(
        state,
        futures_direction="LONG",
        timeframe="1d",
        snapshot_db_path=db,
        snapshot_max_age_seconds=60,
        now="2026-09-20T14:05:00Z",
    )

    assert record["snapshot_status"] == "STALE"
    assert record["status"] == "ERROR"
    assert record["observations"][0]["error"] == "snapshot_stale"
    assert record["trade_authorized"] is False


def test_shared_snapshot_reader_prefers_ok_action_card_over_newer_error(tmp_path):
    db = tmp_path / "options.sqlite"
    store = SignaSnapshotStore(db)
    store.record_snapshot(
        endpoint="/api/v1/enhanced-signal",
        symbol="QQQ",
        timeframe="1d",
        params={"symbol": "QQQ", "timeframe": "1d"},
        retrieved_at="2026-09-20T14:10:00Z",
        payload={"error": "ReadTimeout"},
        status="ERROR",
    )
    ok = store.record_snapshot(
        endpoint="/api/v1/signals/QQQ",
        symbol="QQQ",
        timeframe="1d",
        params={"symbol": "QQQ", "timeframe": "1d"},
        retrieved_at="2026-09-20T14:00:00Z",
        payload={
            "data": {
                "signal": {
                    "symbol": "QQQ",
                    "timeframe": "1d",
                    "direction": "bullish",
                    "grade": "A",
                    "score": 88,
                    "confidence": 81,
                }
            }
        },
        status="OK",
    )

    record = build_signa_futures_context(
        SimpleNamespace(instrument="MNQ", signa=None),
        futures_direction="LONG",
        timeframe="1d",
        snapshot_db_path=db,
        now="2026-09-20T14:15:00Z",
    )

    assert record["snapshot_status"] == "OK"
    assert record["snapshot_ids"] == [ok.snapshot_id]
    assert record["primary_bias"] == "LONG"
    assert record["aligned_with_trade_direction"] is True
    assert record["observations"][0]["grade"] == "A"
    assert record["gate_authoritative"] is False
    assert record["trade_authorized"] is False
    assert record["execution_authority"] is False
