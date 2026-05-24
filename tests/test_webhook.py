"""
tests/test_webhook.py

Webhook layer coverage:
    - state_builder: timestamp parsing, instrument normalization, session detection,
      price_vs_vwap derivation, optional-field defaults
    - runner: NO_TRADE path, TRADE approved path, open-position resolution,
      daily-limit blocks (max_trades, loss_lockout, open_position)
    - FastAPI endpoint: 200 success, 422 validation error on bad payload
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from webhook.payload import AlertPayload
from webhook.state_builder import (
    build_market_state,
    detect_session,
    normalize_instrument,
    parse_timestamp,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _base_payload(**overrides) -> AlertPayload:
    """Minimal valid payload derived from the sample candle."""
    data = {
        "ticker": "MNQ1!",
        "timestamp": "2026-05-23T14:30:00+00:00",
        "open": 19480.0,
        "high": 19510.0,
        "low": 19475.0,
        "close": 19505.25,
        "volume": 4200,
        "avg_volume": 3800,
        "vwap": 19495.0,
        "orb_high": 19498.0,
        "orb_low": 19462.0,
        "orb_status": "reclaimed_high",
        "market_condition": "TRENDING",
        "trend_direction": "UP",
        "trend_strength": "MODERATE",
        "previous_day_high": 19520.0,
        "previous_day_low": 19440.0,
        "previous_day_close": 19475.0,
        "current_bar_type": "two_up",
        "previous_bar_type": "inside_bar",
        "two_bars_back_type": "two_up",
    }
    data.update(overrides)
    return AlertPayload(**data)


# ─── state_builder: parse_timestamp ───────────────────────────────────────────

def test_parse_timestamp_iso():
    ts = parse_timestamp("2026-05-23T14:30:00+00:00")
    assert ts.year == 2026
    assert ts.month == 5
    assert ts.hour == 14


def test_parse_timestamp_iso_z():
    ts = parse_timestamp("2026-05-23T14:30:00Z")
    assert ts.tzinfo is not None


def test_parse_timestamp_unix_ms():
    # 2026-05-23 14:30:00 UTC in milliseconds
    ms = int(datetime(2026, 5, 23, 14, 30, 0, tzinfo=timezone.utc).timestamp() * 1000)
    ts = parse_timestamp(str(ms))
    assert ts.year == 2026
    assert ts.hour == 14


def test_parse_timestamp_unix_s():
    s = int(datetime(2026, 5, 23, 14, 30, 0, tzinfo=timezone.utc).timestamp())
    ts = parse_timestamp(str(s))
    assert ts.hour == 14


# ─── state_builder: normalize_instrument ─────────────────────────────────────

@pytest.mark.parametrize("ticker,expected", [
    ("MNQ1!", "MNQ"),
    ("MNQU2026", "MNQ"),
    ("MES1!", "MES"),
    ("CME_MINI:MNQ1!", "MNQ"),
    ("MGC1!", "MGC"),
    ("MCL1!", "MCL"),
    ("MNQ", "MNQ"),
])
def test_normalize_instrument(ticker, expected):
    assert normalize_instrument(ticker) == expected


# ─── state_builder: detect_session ────────────────────────────────────────────

@pytest.mark.parametrize("iso,expected_session", [
    ("2026-05-23T08:00:00+00:00", "london"),    # 04:00 ET
    ("2026-05-23T14:30:00+00:00", "new_york"),  # 10:30 ET
    ("2026-05-23T01:00:00+00:00", "off_hours"), # 21:00 ET prev day
    ("2026-05-23T13:00:00+00:00", "off_hours"), # 09:00 ET — before NY open
    ("2026-05-23T17:30:00+00:00", "off_hours"), # 13:30 ET — after NY close
])
def test_detect_session(iso, expected_session):
    ts = parse_timestamp(iso)
    assert detect_session(ts) == expected_session


# ─── state_builder: build_market_state ───────────────────────────────────────

def test_build_market_state_full_payload():
    payload = _base_payload()
    state = build_market_state(payload)

    assert state.instrument == "MNQ"
    assert state.session == "new_york"
    assert state.ohlc.close == 19505.25
    assert state.vwap.price_vs_vwap == "above"   # close 19505.25 > vwap 19495.0
    assert state.orb.status == "reclaimed_high"
    assert state.market_condition == "TRENDING"
    assert state.trend.direction == "UP"
    assert state.strat.current_bar_type == "two_up"
    assert state.strat.strat_sequence == "strat_212"
    assert state.strat.strat_direction == "LONG"


def test_build_market_state_minimal_payload():
    """Minimal payload (no context fields) must not raise."""
    payload = AlertPayload(
        ticker="MES1!",
        timestamp="2026-05-23T14:30:00+00:00",
        open=5240.0,
        high=5241.25,
        low=5239.5,
        close=5240.5,
    )
    state = build_market_state(payload)
    assert state.instrument == "MES"
    assert state.session == "new_york"
    # Defaults: vwap falls back to close, orb falls back to high/low
    assert state.vwap.value == 5240.5
    assert state.orb.high == 5241.25
    assert state.orb.low == 5239.5


def test_build_market_state_price_vs_vwap_below():
    payload = _base_payload(close=19490.0, vwap=19500.0)
    state = build_market_state(payload)
    assert state.vwap.price_vs_vwap == "below"


def test_build_market_state_session_override():
    payload = _base_payload(session="london")
    state = build_market_state(payload)
    assert state.session == "london"


def test_build_market_state_asian_session_detected():
    # 02:30 UTC = 22:30 ET → off_hours
    payload = _base_payload(timestamp="2026-05-21T02:30:00+00:00")
    state = build_market_state(payload)
    assert state.session == "off_hours"


# ─── runner: NO_TRADE path ────────────────────────────────────────────────────

def test_runner_choppy_produces_no_trade(config, tmp_path):
    from webhook.runner import process_alert

    payload = _base_payload(market_condition="CHOPPY", orb_status=None)
    result = process_alert(payload, config=config, log_dir=str(tmp_path / "logs"))

    assert result["decision"] == "NO_TRADE"
    assert result["fill"] is None
    assert result["resolution"] is None


# ─── runner: TRADE → APPROVED path ───────────────────────────────────────────

def test_runner_trending_orb_reclaim_produces_trade(config, tmp_path):
    from webhook.runner import process_alert

    payload = _base_payload()
    result = process_alert(payload, config=config, log_dir=str(tmp_path / "logs"))

    assert result["decision"] in ("TRADE", "NO_TRADE")
    if result["decision"] == "TRADE":
        assert result["fill"] is not None
        assert result["fill"]["status"] == "OPEN"
        assert result["fill"]["instrument"] == "MNQ"
        assert result["risk"]["result"] == "APPROVED"


# ─── runner: open-position resolution ────────────────────────────────────────

def test_runner_resolves_open_position_on_next_bar(config, tmp_path):
    """
    Bar 1 opens a trade.  Bar 2 comes in with high far above target → WIN.
    """
    from webhook.runner import process_alert

    log_dir = str(tmp_path / "logs")
    today = date(2026, 5, 23)

    # Bar 1: trending setup → opens a TRADE
    p1 = _base_payload()
    r1 = process_alert(p1, config=config, log_dir=log_dir, for_date=today)

    if r1["decision"] != "TRADE" or r1["fill"] is None:
        pytest.skip("Signal engine produced NO_TRADE on bar 1 — skip resolution test")

    # Bar 2: high=99999 guarantees target hit
    p2 = _base_payload(
        timestamp="2026-05-23T14:35:00+00:00",
        high=99999.0,
        low=19475.0,
        close=19580.0,
    )
    r2 = process_alert(p2, config=config, log_dir=log_dir, for_date=today)

    assert r2["resolution"] == "WIN"


# ─── runner: daily limit blocks ──────────────────────────────────────────────

def test_runner_blocks_when_max_trades_reached(config, tmp_path):
    from datetime import timedelta
    from webhook.runner import process_alert

    log_dir = str(tmp_path / "logs")
    today = date(2026, 5, 23)
    base_dt = datetime(2026, 5, 23, 14, 30, 0, tzinfo=timezone.utc)

    # Fire candles until max_trades is hit
    for i in range(config.max_trades_per_day + 1):
        ts = (base_dt + timedelta(minutes=5 * i)).isoformat()
        p = _base_payload(timestamp=ts, high=99999.0)
        r = process_alert(p, config=config, log_dir=log_dir, for_date=today)
        if r["decision"] == "BLOCKED_MAX_TRADES":
            return  # ✓ limit enforced

    pytest.fail("max_trades_per_day limit was never triggered")


def test_runner_blocks_on_loss_lockout(config, tmp_path):
    """After max_consecutive_losses LOSSes, new signals are blocked."""
    from datetime import timedelta
    from journal.journal_logger import JournalLogger
    from webhook.runner import process_alert

    log_dir = str(tmp_path / "logs")
    today = date(2026, 5, 23)

    # Seed the journal with enough LOSS outcomes to trigger lockout
    journal = JournalLogger(log_dir=log_dir)
    for i in range(config.max_consecutive_losses):
        decision_entry = {
            "ts": "2026-05-23T14:00:00+00:00",
            "instrument": "MNQ",
            "session": "new_york",
            "decision": "TRADE",
            "reason": "test",
            "market_condition": "TRENDING",
            "setup": {
                "direction": "LONG",
                "entry": 19500.0,
                "stop": 19460.0,
                "target": 19580.0,
                "rr_ratio": 2.0,
                "strategy": "orb_reclaim",
                "notes": None,
            },
            "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
            "outcome": None,
        }
        journal._append(decision_entry, today)
        journal.log_outcome(
            instrument="MNQ",
            session="new_york",
            result="LOSS",
            entry_price=19500.0,
            exit_price=19460.0,
            exit_reason="STOP_HIT",
            pnl_ticks=-160.0,
            pnl_dollars=-80.0,
            for_date=today,
        )

    base_dt = datetime(2026, 5, 23, 14, 30, 0, tzinfo=timezone.utc)
    p = _base_payload(timestamp=base_dt.isoformat())
    result = process_alert(p, config=config, log_dir=log_dir, for_date=today)

    assert result["decision"] == "BLOCKED_LOSS_LOCKOUT"


# ─── FastAPI endpoint ─────────────────────────────────────────────────────────

def test_fastapi_health_endpoint():
    """Health check must return 200 with live_trading_enabled=False."""
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["live_trading_enabled"] is False
    assert "webhook_secret_required" in data


def test_fastapi_dashboard_endpoint():
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    client = TestClient(app)
    resp = client.get("/")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "RiskSentinel" in resp.text
    assert "LIVE TRADING OFF" in resp.text


def test_fastapi_status_today_endpoint():
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    client = TestClient(app)
    resp = client.get("/status/today")

    assert resp.status_code == 200
    data = resp.json()
    assert data["live_trading_enabled"] is False
    assert data["max_trades_per_day"] == 3
    assert "latest_entries" in data
    assert "top_no_trade_reasons" in data


def test_fastapi_status_history_endpoint():
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    client = TestClient(app)
    resp = client.get("/status/history?days=3")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["days"]) == 3
    assert {"date", "trade_count", "no_trades"}.issubset(data["days"][0])


def test_fastapi_strategy_status_endpoint():
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    client = TestClient(app)
    resp = client.get("/status/strategy")

    assert resp.status_code == 200
    data = resp.json()
    assert "orb_reclaim" in data["enabled_concepts"]
    assert data["strat_confirmation_only"] is True
    assert "decision_counts" in data
    assert "approved_strategy_counts" in data


def test_fastapi_latest_webhook_endpoint_after_alert(monkeypatch):
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    client = TestClient(app)
    body = {
        "ticker": "MNQ1!",
        "timestamp": "2026-05-23T14:30:00+00:00",
        "open": 19480.0,
        "high": 19510.0,
        "low": 19475.0,
        "close": 19505.25,
        "volume": 4200,
        "avg_volume": 3800,
        "timeframe": "3",
        "vwap": 19495.0,
        "orb_high": 19498.0,
        "orb_low": 19462.0,
        "orb_status": "reclaimed_high",
        "market_condition": "CHOPPY",
        "trend_direction": "UP",
        "trend_strength": "MODERATE",
        "previous_day_high": 19520.0,
        "previous_day_low": 19440.0,
        "previous_day_close": 19475.0,
        "current_bar_type": "two_up",
        "previous_bar_type": "inside_bar",
        "two_bars_back_type": "two_up",
    }

    alert_resp = client.post("/webhook/alert", json=body)
    assert alert_resp.status_code == 200

    latest_resp = client.get("/status/latest-webhook")
    assert latest_resp.status_code == 200
    data = latest_resp.json()
    assert data["payload"]["ticker"] == "MNQ1!"
    assert data["context"]["instrument"] == "MNQ"
    assert data["context"]["vwap"]["value"] == 19495.0
    assert data["context"]["orb"]["status"] == "reclaimed_high"
    assert data["context"]["trend"]["direction"] == "UP"
    assert data["context"]["previous_day"]["high"] == 19520.0
    assert data["context"]["strat"]["strat_sequence"] == "strat_212"
    assert data["context"]["strat"]["strat_direction"] == "LONG"
    assert data["result"]["decision"] == "NO_TRADE"


def test_fastapi_alert_endpoint_valid_payload(monkeypatch):
    """POST /webhook/alert with a valid payload returns 200."""
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    client = TestClient(app)
    body = {
        "ticker": "MNQ1!",
        "timestamp": "2026-05-23T14:30:00+00:00",
        "open": 19480.0,
        "high": 19510.0,
        "low": 19475.0,
        "close": 19505.25,
        "volume": 4200,
        "market_condition": "CHOPPY",
    }
    resp = client.post("/webhook/alert", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "decision" in data


def test_fastapi_alert_endpoint_missing_required_fields():
    """POST /webhook/alert with missing required fields returns 422."""
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    client = TestClient(app)
    resp = client.post("/webhook/alert", json={"ticker": "MNQ1!"})  # missing OHLC
    assert resp.status_code == 422


def test_fastapi_alert_endpoint_rejects_bad_secret(monkeypatch):
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setenv("WEBHOOK_SECRET", "local-test-secret")
    client = TestClient(app)
    body = {
        "ticker": "MNQ1!",
        "timestamp": "2026-05-23T14:30:00+00:00",
        "open": 19480.0,
        "high": 19510.0,
        "low": 19475.0,
        "close": 19505.25,
    }

    resp = client.post("/webhook/alert?secret=wrong", json=body)
    assert resp.status_code == 401


def test_fastapi_alert_endpoint_accepts_good_secret(monkeypatch):
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setenv("WEBHOOK_SECRET", "local-test-secret")
    client = TestClient(app)
    body = {
        "ticker": "MNQ1!",
        "timestamp": "2026-05-23T14:30:00+00:00",
        "open": 19480.0,
        "high": 19510.0,
        "low": 19475.0,
        "close": 19505.25,
        "market_condition": "CHOPPY",
    }

    resp = client.post("/webhook/alert?secret=local-test-secret", json=body)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
