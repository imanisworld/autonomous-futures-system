from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

from alert_ranker.scorer import score_setup
from alert_ranker.signa_v2_observer import build_signa_v2_observer
from alert_ranker.storage import ScanStorage
from sources.signa_observation import SignaActionCardObservation


class BaseScanner:
    def __init__(self, config, *args, **kwargs):
        self.config = config

    async def _build_normalized_data(self, ticker, context, now):
        return {
            "ticker": ticker,
            "pattern": "2-1-2",
            "direction": "LONG",
            "price": 105.0,
            "vwap": 100.0,
            "ema20": 101.0,
            "volume_ratio": 1.5,
        }


class FakeV2Client:
    def __init__(self):
        self.calls = []

    def fetch_action_card(self, symbol, timeframe):
        self.calls.append((symbol, timeframe))
        return SignaActionCardObservation(
            ok=True,
            symbol=symbol,
            timeframe=timeframe,
            direction="LONG",
            score=91.0,
            grade="A",
            confidence=88.0,
            entry_low=104.5,
            entry_high=105.5,
            stop_loss=102.0,
            targets=(110.0, 114.0),
            reward_to_risk=2.3,
            component_scores={"flow": 92.0, "technicals": 84.0},
            model_version="v2-test",
            retrieved_at="2026-09-12T14:00:00+00:00",
        )


def _cfg(**kwargs):
    base = {
        "signa_symbol_map": {"SPX": "SPY"},
        "signa_base_url": "https://app.getsigna.ai",
        "signa_timeout_seconds": 3.0,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_v2_observer_is_default_off(monkeypatch) -> None:
    monkeypatch.delenv("OPTIONS_SIGNA_V2_OBSERVE_ENABLED", raising=False)
    client = FakeV2Client()
    Scanner = build_signa_v2_observer(BaseScanner)
    scanner = Scanner(_cfg(), signa_v2_client=client)

    data = asyncio.run(scanner._build_normalized_data("SPX", {}, None))

    assert client.calls == []
    assert not any(key.startswith("signa_v2_") for key in data)


def test_v2_observer_appends_namespaced_telemetry_only() -> None:
    client = FakeV2Client()
    Scanner = build_signa_v2_observer(BaseScanner)
    scanner = Scanner(
        _cfg(signa_v2_observe_enabled=True, signa_v2_timeframe="1d"),
        signa_v2_client=client,
    )

    data = asyncio.run(scanner._build_normalized_data("SPX", {}, None))

    assert client.calls == [("SPY", "1d")]
    assert data["signa_v2_ok"] is True
    assert data["signa_v2_grade"] == "A"
    assert data["signa_v2_confidence"] == 88.0
    assert data["signa_v2_reward_to_risk"] == 2.3
    assert data["signa_v2_component_scores"] == {"flow": 92.0, "technicals": 84.0}
    assert "signa_grade" not in data
    assert "signa_score" not in data


def test_v2_telemetry_cannot_change_scanner_score() -> None:
    client = FakeV2Client()
    Scanner = build_signa_v2_observer(BaseScanner)
    enabled = Scanner(
        _cfg(signa_v2_observe_enabled=True, signa_v2_timeframe="1d"),
        signa_v2_client=client,
    )
    disabled = Scanner(_cfg(signa_v2_observe_enabled=False), signa_v2_client=client)

    with_v2 = asyncio.run(enabled._build_normalized_data("AAPL", {}, None))
    without_v2 = asyncio.run(disabled._build_normalized_data("AAPL", {}, None))
    scored_v2 = score_setup(with_v2)
    scored_base = score_setup(without_v2)

    assert scored_v2.score == scored_base.score
    assert scored_v2.direction == scored_base.direction
    assert scored_v2.components["signa"] == 0
    assert scored_base.components["signa"] == 0


def test_v2_telemetry_persists_via_existing_raw_json_storage(tmp_path) -> None:
    client = FakeV2Client()
    Scanner = build_signa_v2_observer(BaseScanner)
    scanner = Scanner(
        _cfg(signa_v2_observe_enabled=True, signa_v2_timeframe="1d"),
        signa_v2_client=client,
    )
    data = asyncio.run(scanner._build_normalized_data("AAPL", {}, None))
    result = score_setup(data)
    db_path = tmp_path / "options_scanner.sqlite"
    storage = ScanStorage(db_path)

    storage.record_scan(
        result,
        source="test",
        alert_sent=False,
        alert_suppression_reason="test_only",
        timestamp=datetime(2026, 9, 12, 14, 0, tzinfo=timezone.utc),
    )

    with sqlite3.connect(db_path) as conn:
        raw_json = conn.execute("SELECT raw_json FROM scans ORDER BY id DESC LIMIT 1").fetchone()[0]
    stored = json.loads(raw_json)
    assert stored["signa_v2_ok"] is True
    assert stored["signa_v2_grade"] == "A"
    assert stored["signa_v2_confidence"] == 88.0
    assert stored["signa_v2_component_scores"]["flow"] == 92.0


def test_v2_observer_fails_soft() -> None:
    class BrokenClient:
        def fetch_action_card(self, symbol, timeframe):
            raise RuntimeError("provider boom")

    Scanner = build_signa_v2_observer(BaseScanner)
    scanner = Scanner(
        _cfg(signa_v2_observe_enabled=True),
        signa_v2_client=BrokenClient(),
    )

    data = asyncio.run(scanner._build_normalized_data("AAPL", {}, None))
    assert data["signa_v2_ok"] is False
    assert data["signa_v2_error"] == "RuntimeError"
    assert data["price"] == 105.0
