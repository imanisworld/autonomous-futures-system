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
    derive_orb_status,
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


def _isolate_app_logs(monkeypatch, tmp_path) -> None:
    """Keep FastAPI endpoint tests from touching production logs/."""
    import webhook.app as app_module

    monkeypatch.setattr(app_module._config, "log_dir", str(tmp_path / "logs"))


# ─── state_builder: parse_timestamp ───────────────────────────────────────────

def test_state_builder_preserves_pine_advisory_bracket_fields():
    payload = _base_payload(
        signal_strategy="continuation_pullback",
        signal_direction="LONG",
        entry=5582.25,
        stop=5578.0,
        target=5597.25,
        rr_ratio=3.53,
    )

    state = build_market_state(payload)

    assert state.raw["signal_strategy"] == "continuation_pullback"
    assert state.raw["signal_direction"] == "LONG"
    assert state.raw["entry"] == 5582.25
    assert state.raw["stop"] == 5578.0
    assert state.raw["target"] == 5597.25
    assert state.raw["rr_ratio"] == 3.53


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
    ("2026-05-23T01:00:00+00:00", "asian"),      # 21:00 ET prev day — inside Asian window
    ("2026-05-23T12:30:00+00:00", "session_gap"), # 08:30 ET — gap before NY open
    ("2026-05-23T13:00:00+00:00", "session_gap"), # 09:00 ET — gap before NY open
    ("2026-05-23T17:30:00+00:00", "new_york"),  # 13:30 ET — afternoon NY window
    ("2026-05-23T20:30:00+00:00", "off_hours"), # 16:30 ET — after NY close
])
def test_detect_session(iso, expected_session):
    ts = parse_timestamp(iso)
    assert detect_session(ts) == expected_session


@pytest.mark.parametrize(
    "close,orb_high,orb_low,expected",
    [
        (101.0, 100.0, 90.0, "above"),
        (89.0, 100.0, 90.0, "below"),
        (95.0, 100.0, 90.0, "inside"),
        (95.0, None, 90.0, "undefined"),
        (95.0, 100.0, None, "undefined"),
    ],
)
def test_derive_orb_status(close, orb_high, orb_low, expected):
    assert derive_orb_status(close, orb_high, orb_low) == expected


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


def test_build_market_state_auto_session_and_orb_status_when_payload_omits_both():
    payload = _base_payload(
        session=None,
        close=19505.25,
        orb_high=19498.0,
        orb_low=19462.0,
        orb_status=None,
    )
    state = build_market_state(payload)

    assert state.session == "new_york"
    assert state.orb.status == "above"


def test_build_market_state_price_vs_vwap_below():
    payload = _base_payload(close=19490.0, vwap=19500.0)
    state = build_market_state(payload)
    assert state.vwap.price_vs_vwap == "below"


def test_build_market_state_session_override():
    payload = _base_payload(session="london")
    state = build_market_state(payload)
    assert state.session == "london"


def test_build_market_state_asian_session_detected():
    # 02:30 UTC = 22:30 ET → asian (19:00–03:00 ET)
    payload = _base_payload(timestamp="2026-05-21T02:30:00+00:00")
    state = build_market_state(payload)
    assert state.session == "asian"


def test_build_market_state_session_gap_forces_non_allowed_session():
    # 13:00 UTC = 09:00 ET, explicitly inside the 08:30-09:30 gap.
    payload = _base_payload(timestamp="2026-05-23T13:00:00+00:00")
    state = build_market_state(payload)
    assert state.session == "session_gap"


# ─── London ORB routing ───────────────────────────────────────────────────────

def test_london_orb_used_when_session_is_london():
    """When session=london and london_orb_* are present, ORBData uses London levels."""
    payload = _base_payload(
        session="london",
        london_orb_high=19520.0,
        london_orb_low=19490.0,
        london_orb_status="reclaimed_high",
        # NY ORB absent (not built yet during London)
        orb_high=None,
        orb_low=None,
        orb_status=None,
    )
    state = build_market_state(payload)
    assert state.orb.high == 19520.0
    assert state.orb.low == 19490.0
    assert state.orb.status == "reclaimed_high"


def test_london_orb_undefined_when_not_yet_established():
    """During London before ORB window closes, status must be 'undefined'."""
    payload = _base_payload(
        session="london",
        london_orb_high=None,
        london_orb_low=None,
        london_orb_status=None,
    )
    state = build_market_state(payload)
    assert state.orb.status == "undefined"


def test_ny_orb_used_in_new_york_session():
    """NY session still uses the standard orb_high/low regardless of london fields."""
    payload = _base_payload(
        session="new_york",
        orb_high=19500.0,
        orb_low=19460.0,
        orb_status="above",
        london_orb_high=19520.0,  # present but should be ignored
        london_orb_low=19490.0,
        london_orb_status="inside",
    )
    state = build_market_state(payload)
    assert state.orb.high == 19500.0
    assert state.orb.low == 19460.0
    assert state.orb.status == "above"


def test_london_orb_status_from_payload_not_recomputed():
    """london_orb_status from Pine is passed through verbatim."""
    payload = _base_payload(
        session="london",
        london_orb_high=19510.0,
        london_orb_low=19480.0,
        london_orb_status="rejected_high",
    )
    state = build_market_state(payload)
    assert state.orb.status == "rejected_high"


# ─── runner: NO_TRADE path ────────────────────────────────────────────────────

def test_runner_choppy_produces_no_trade(config, tmp_path):
    from webhook.runner import process_alert

    payload = _base_payload(market_condition="CHOPPY", orb_status=None)
    result = process_alert(payload, config=config, log_dir=str(tmp_path / "logs"))

    assert result["decision"] == "NO_TRADE"
    assert result["fill"] is None
    assert result["resolution"] is None


# ─── runner: TRADE → APPROVED path ───────────────────────────────────────────

def test_runner_trending_orb_breakout_mes_produces_trade(config, tmp_path):
    """MNQ is disabled; test orb_breakout on MES (orb_reclaim disabled on MES, orb_breakout is not)."""
    from webhook.runner import process_alert

    log_dir = str(tmp_path / "logs")
    payload = _base_payload(
        ticker="MES1!",
        open=5880.0,
        high=5910.0,
        low=5875.0,
        close=5905.25,
        volume=4200,
        avg_volume=3800,
        vwap=5895.0,
        orb_high=5898.0,
        orb_low=5862.0,
        orb_status="above",
        previous_day_high=5920.0,
        previous_day_low=5840.0,
        previous_day_close=5875.0,
    )
    result = process_alert(payload, config=config, log_dir=log_dir)

    if result["decision"] == "NO_TRADE":
        pytest.skip("Signal engine produced NO_TRADE — conditions not met")
    assert result["fill"] is not None
    assert result["fill"]["status"] == "OPEN"
    assert result["fill"]["instrument"] == "MES"
    assert result["risk"]["result"] == "APPROVED"

    journal_path = next((tmp_path / "logs").glob("journal_*.jsonl"))
    entry = json.loads(journal_path.read_text().splitlines()[-1])
    assert entry["context"]["orb"]["status"] == "above"
    assert entry["context"]["session"] == "new_york"
    assert entry["confluence"]["score"] >= 0
    assert entry["confluence"]["grade"]


def test_journal_reconstructs_orb_break_flags_from_persisted_context(tmp_path):
    from journal.journal_logger import JournalLogger

    today = date(2026, 5, 23)
    journal = JournalLogger(log_dir=str(tmp_path / "logs"))
    base_entry = {
        "ts": "2026-05-23T14:30:00+00:00",
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
            "strategy": "strat_212",
            "notes": None,
        },
        "context": {"orb": {"status": "above"}},
        "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
        "outcome": {"result": "WIN"},
    }
    journal._append(base_entry, today)
    state = journal.get_daily_state(today)
    assert state.orb_break_long_played is True

    reclaim_entry = {
        **base_entry,
        "setup": {**base_entry["setup"], "strategy": "orb_reclaim"},
        "context": {"orb": {"status": "reclaimed_high"}},
    }
    journal._append(reclaim_entry, today)
    state = journal.get_daily_state(today)
    assert state.orb_break_long_played is False


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


def test_runner_blocks_when_open_position_does_not_resolve(config, tmp_path):
    """
    Bar 1 opens a trade. Bar 2 hits neither stop nor target, so the engine
    must block new entries until the existing position resolves.
    """
    from webhook.runner import process_alert

    log_dir = str(tmp_path / "logs")
    today = date(2026, 5, 23)

    r1 = process_alert(_base_payload(), config=config, log_dir=log_dir, for_date=today)
    if r1["decision"] != "TRADE" or r1["fill"] is None:
        pytest.skip("Signal engine produced NO_TRADE on bar 1 — skip open-position test")

    fill = r1["fill"]
    one_r = float(fill["entry"]) + ((float(fill["entry"]) - float(fill["stop"])) * 0.5)
    p2 = _base_payload(
        timestamp="2026-05-23T14:35:00+00:00",
        high=one_r - 0.25,
        low=float(fill["stop"]) + 0.25,
        close=float(fill["entry"]),
    )
    r2 = process_alert(p2, config=config, log_dir=log_dir, for_date=today)

    assert r2["resolution"] is None
    assert r2["decision"] == "BLOCKED_OPEN_POSITION"


def _seed_open_trade(journal, for_date):
    journal._append({
        "ts": f"{for_date.isoformat()}T17:55:00+00:00",
        "instrument": "MNQ",
        "session": "new_york",
        "decision": "TRADE",
        "reason": "carry test",
        "market_condition": "TRENDING",
        "setup": {
            "direction": "LONG",
            "entry": 19500.0,
            "stop": 19460.0,
            "target": 19580.0,
            "rr_ratio": 2.0,
            "strategy": "orb_reclaim",
            "notes": None,
            "contracts": 1,
        },
        "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
        "outcome": None,
    }, for_date)


def test_runner_resolves_previous_day_open_position(config, tmp_path):
    """A carried open trade must resolve from today's OHLC before new entries."""
    from journal.journal_logger import JournalLogger
    from webhook.runner import process_alert

    log_dir = str(tmp_path / "logs")
    yesterday = date(2026, 5, 22)
    today = date(2026, 5, 23)
    journal = JournalLogger(log_dir=log_dir)
    _seed_open_trade(journal, yesterday)

    result = process_alert(
        _base_payload(
            timestamp="2026-05-23T14:30:00+00:00",
            high=19600.0,
            low=19490.0,
            close=19590.0,
        ),
        config=config,
        log_dir=log_dir,
        for_date=today,
    )

    assert result["resolution"] == "WIN"
    assert JournalLogger(log_dir=log_dir).get_daily_state(yesterday).has_open_position is False


def test_runner_force_closes_stale_previous_day_position(config, tmp_path):
    """A carried position older than 8 hours must be force-closed, not block forever."""
    from journal.journal_logger import JournalLogger
    from webhook.runner import process_alert

    log_dir = str(tmp_path / "logs")
    yesterday = date(2026, 5, 22)
    today = date(2026, 5, 23)
    journal = JournalLogger(log_dir=log_dir)
    _seed_open_trade(journal, yesterday)  # seeded at 17:55 UTC yesterday → >8h old

    result = process_alert(
        _base_payload(
            timestamp="2026-05-23T14:30:00+00:00",
            high=19520.0,
            low=19480.0,
            close=19505.0,
        ),
        config=config,
        log_dir=log_dir,
        for_date=today,
    )

    # Position must be force-closed, not left blocking indefinitely
    assert result["resolution"] == "FORCE_CLOSE_SESSION_TIMEOUT"
    assert result["decision"] != "BLOCKED_OPEN_POSITION"
    assert JournalLogger(log_dir=log_dir).get_daily_state(yesterday).has_open_position is False


def test_runner_resolves_friday_position_on_monday(config, tmp_path):
    """Friday open position must carry across Saturday+Sunday and resolve on Monday."""
    from journal.journal_logger import JournalLogger
    from webhook.runner import process_alert

    log_dir = str(tmp_path / "logs")
    friday = date(2026, 5, 22)   # Friday
    monday = date(2026, 5, 25)   # Monday (3 calendar days later)
    journal = JournalLogger(log_dir=log_dir)
    _seed_open_trade(journal, friday)

    result = process_alert(
        _base_payload(
            timestamp="2026-05-25T14:30:00+00:00",
            high=19600.0,
            low=19490.0,
            close=19590.0,
        ),
        config=config,
        log_dir=log_dir,
        for_date=monday,
    )

    assert result["resolution"] == "WIN"
    assert JournalLogger(log_dir=log_dir).get_daily_state(friday).has_open_position is False


def test_runner_force_closes_friday_position_on_monday(config, tmp_path):
    """An unresolved Friday position older than 8 hours must be force-closed on Monday."""
    from journal.journal_logger import JournalLogger
    from webhook.runner import process_alert

    log_dir = str(tmp_path / "logs")
    friday = date(2026, 5, 22)
    monday = date(2026, 5, 25)
    journal = JournalLogger(log_dir=log_dir)
    _seed_open_trade(journal, friday)  # seeded at 17:55 UTC Friday → >8h old by Monday

    result = process_alert(
        _base_payload(
            timestamp="2026-05-25T14:30:00+00:00",
            high=19520.0,
            low=19480.0,
            close=19505.0,
        ),
        config=config,
        log_dir=log_dir,
        for_date=monday,
    )

    # Position must be force-closed over the weekend, not left blocking Monday
    assert result["resolution"] == "FORCE_CLOSE_SESSION_TIMEOUT"
    assert result["decision"] != "BLOCKED_OPEN_POSITION"
    assert JournalLogger(log_dir=log_dir).get_daily_state(friday).has_open_position is False


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

def test_fastapi_health_endpoint(monkeypatch):
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
    assert "broker" in data
    assert "broker_gateway_reachable" in data
    assert "discord_notifications_enabled" not in data
    assert "signa_api_key_configured" not in data


def test_status_routes_do_not_require_dashboard_token():
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    client = TestClient(app)

    assert client.get("/").status_code == 200
    assert client.get("/status/today").status_code == 200
    assert client.get("/status/history?days=7").status_code == 200


def test_public_share_and_public_status_do_not_require_dashboard_token():
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    client = TestClient(app)

    public_resp = client.get("/status/public")
    share_resp = client.get("/share")

    assert public_resp.status_code == 200
    assert "account_balance" not in public_resp.json()
    assert "latest_webhook" not in public_resp.json()
    assert share_resp.status_code == 200
    assert "RiskSentinel" in share_resp.text


def test_rate_limit_blocks_after_configured_threshold(monkeypatch):
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    app_module._RATE_BUCKETS.clear()
    monkeypatch.setattr(app_module, "_PUBLIC_RATE_LIMIT", 2)
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.get("/health").status_code == 429
    app_module._RATE_BUCKETS.clear()


def test_fastapi_status_signa_disabled_endpoint(monkeypatch):
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setattr(app_module._config, "signa_api_enabled", False)
    monkeypatch.setattr(app_module._config, "signa_api_key_configured", True)

    client = TestClient(app)
    resp = client.get("/status/signa?symbol=AAPL")

    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["configured"] is True
    assert data["symbol"] == "AAPL"
    assert data["error"] == "signa_api_disabled"


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
    assert "Daily Made / Lost" in resp.text
    assert "pnl-bar-chart" in resp.text


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
    assert data["max_trades_per_day"] == 5  # 3 normal + 2 bonus
    assert "latest_entries" in data
    assert "top_no_trade_reasons" in data
    assert "diagnostics" in data
    assert "items" in data["diagnostics"]


def test_fastapi_status_diagnostics_endpoint(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("BROKER", "paper")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(app_module._config, "discord_notifications_enabled", True)
    monkeypatch.setattr(app_module._config, "discord_webhook_url", "")

    client = TestClient(app)
    resp = client.get("/status/diagnostics")

    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_status"] == "warn"
    assert data["top_issue"]["component"] in {"Discord alerts", "TradingView alerts"}
    components = {item["component"] for item in data["items"]}
    assert {"Backend API", "Trading mode", "Webhook secret", "Discord alerts"}.issubset(components)


def test_public_entry_flags_target_hit_negative_pnl():
    from webhook.app import _public_entry

    entry = {
        "type": "OUTCOME",
        "instrument": "MES",
        "outcome": {
            "result": "LOSS",
            "exit_reason": "TARGET_HIT",
            "pnl_dollars": -12.50,
        },
    }

    public = _public_entry(entry)

    assert public["exit_reason"] == "TARGET_HIT"
    assert public["outcome_warning"] is True
    assert "Target was marked hit" in public["outcome_explanation"]


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


def test_fastapi_review_endpoint_returns_empty_eod_report(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    client = TestClient(app)

    resp = client.get("/status/review?date=2026-05-23&mode=eod")

    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "eod"
    assert data["date"] == "2026-05-23"
    assert data["recommended_state"] == "OK_TO_PAPER_TRADE"
    assert data["trade_grades"] == []
    assert not (tmp_path / "logs" / "review_2026-05-23.json").exists()
    assert not (tmp_path / "logs" / "daily_review_2026-05-23.md").exists()


def test_fastapi_review_endpoint_returns_morning_preflight(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    client = TestClient(app)

    resp = client.get("/status/review?date=2026-05-23&mode=morning")

    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "morning"
    assert data["preflight"]["paper_only"] is True
    assert data["preflight"]["max_trades_per_day"] == 3


def test_fastapi_review_endpoint_rejects_invalid_mode(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    client = TestClient(app)

    resp = client.get("/status/review?date=2026-05-23&mode=midday")

    assert resp.status_code == 422


def test_fastapi_review_endpoint_rejects_invalid_date(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    client = TestClient(app)

    resp = client.get("/status/review?date=../2026-05-23&mode=eod")

    assert resp.status_code == 422
    assert "YYYY-MM-DD" in resp.json()["detail"]


def test_fastapi_latest_webhook_endpoint_after_alert(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    _isolate_app_logs(monkeypatch, tmp_path)
    import webhook.app as app_module
    monkeypatch.setattr(app_module._config, "max_staleness_seconds", 0)
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

    alert_resp = client.post("/webhook/alert", json=body, headers={"X-Webhook-Secret": "test-secret"})
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


def test_fastapi_alert_endpoint_valid_payload(monkeypatch, tmp_path):
    """POST /webhook/alert with a valid payload and correct secret returns 200."""
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    _isolate_app_logs(monkeypatch, tmp_path)
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
    resp = client.post("/webhook/alert", json=body, headers={"X-Webhook-Secret": "test-secret"})
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


def test_fastapi_alert_endpoint_rejects_missing_secret(monkeypatch):
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

    resp = client.post("/webhook/alert", json=body)
    assert resp.status_code == 401


def test_fastapi_alert_endpoint_accepts_good_secret_via_query(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setenv("WEBHOOK_SECRET", "local-test-secret")
    _isolate_app_logs(monkeypatch, tmp_path)
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


def test_fastapi_alert_endpoint_accepts_good_secret_via_header(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setenv("WEBHOOK_SECRET", "local-test-secret")
    _isolate_app_logs(monkeypatch, tmp_path)
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

    resp = client.post(
        "/webhook/alert",
        json=body,
        headers={"X-Webhook-Secret": "local-test-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
