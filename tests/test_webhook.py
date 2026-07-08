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
from dataclasses import replace
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
        "timeframe": "15",   # live alerts run on the validated 15m chart
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


def test_status_opportunities_summarizes_direction_roles(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from adaptive.opportunity_tracker import OpportunityCandidate, OpportunityStore
    from webhook.app import app

    _isolate_app_logs(monkeypatch, tmp_path)
    store = OpportunityStore(log_dir=str(tmp_path / "logs" / "opportunities"))
    store.record_candidate(
        OpportunityCandidate(
            candidate_id="MES:b1:orb_reclaim:LONG",
            source_bar_id="b1",
            detected_at="2026-07-01T14:00:00+00:00",
            instrument="MES",
            session="new_york",
            timeframe="15",
            strategy="orb_reclaim",
            direction="LONG",
            entry=7500,
            stop=7495,
            target=7515,
            direction_role="PRIMARY",
            selected=True,
        ),
        for_date=date.today(),
    )

    payload = TestClient(app).get(
        f"/status/opportunities?date={date.today().isoformat()}"
    ).json()

    assert payload["candidate_count"] == 1
    assert payload["primary_candidates"] == 1
    assert payload["selected_candidates"] == 1


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


@pytest.mark.parametrize("ticker,expected", [
    ("MES1!", "MES"),
    ("MNQ1!", "MNQ"),
    ("CME_MINI:MNQ1!", "MNQ"),
    ("MESU2026", "MES"),     # real contract suffix (month code + year)
    ("MNQ", "MNQ"),
    ("ESTC", None),          # Elastic stock — NOT the ES future (the prefix bug)
    ("NQXX", None),
    ("AAPL", None),
    ("ES1!", "ES"),          # e-mini recognized (then RISK_REJECTED downstream)
])
def test_futures_root_exact_not_prefix(ticker, expected):
    from webhook.state_builder import futures_root
    roots = ("MNQ", "MES", "ES", "NQ", "MGC", "MCL")
    assert futures_root(ticker, roots) == expected


# ─── state_builder: detect_session ────────────────────────────────────────────

@pytest.mark.parametrize("iso,expected_session", [
    ("2026-05-23T08:00:00+00:00", "london"),    # 04:00 ET
    ("2026-05-23T14:30:00+00:00", "new_york"),  # 10:30 ET
    ("2026-05-23T01:00:00+00:00", "asian"),      # 21:00 ET prev day — overnight asian
    ("2026-05-23T12:30:00+00:00", "london"),    # 08:30 ET — old gap, now folded into london
    ("2026-05-23T13:00:00+00:00", "london"),    # 09:00 ET — old gap, now london
    ("2026-05-23T17:30:00+00:00", "new_york"),  # 13:30 ET — afternoon NY
    ("2026-05-23T20:30:00+00:00", "new_york"),  # 16:30 ET — late NY (extended to 17:00)
    ("2026-05-23T22:15:00+00:00", "asian"),     # 18:15 ET — the daily reopen (was off_hours)
    ("2026-05-23T21:30:00+00:00", "off_hours"), # 17:30 ET — maintenance halt (market closed)
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


def test_payload_accepts_bare_int_timestamp_from_pine():
    """Pine's str.tostring(time) sends Unix ms as a bare JSON number — the model
    must coerce it to a string (regression for the 422 'Input should be a valid
    string' that blocked every live alert)."""
    payload = AlertPayload(
        ticker="MES1!",
        timestamp=1780447500000,   # bare int, not a string
        open=5950.0, high=5955.0, low=5948.0, close=5952.0,
    )
    assert payload.timestamp == "1780447500000"
    assert parse_timestamp(payload.timestamp).year == 2026


def test_payload_accepts_mnq_above_30k():
    """MNQ now trades above the old 30k cap — must not reject a valid bar."""
    payload = AlertPayload(
        ticker="MNQ1!",
        timestamp="1780447500000",
        open=30713.5, high=30720.0, low=30709.0, close=30712.5,
    )
    assert payload.close == 30712.5


def test_payload_still_rejects_cross_instrument_price():
    """The sanity guard must still catch an MNQ-scale price on an MES chart."""
    with pytest.raises(Exception):
        AlertPayload(ticker="MES1!", timestamp="1780447500000",
                     open=30000.0, high=30001.0, low=29999.0, close=30712.5)


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
    # Missing structural levels FAIL CLOSED: the level value carries a harmless
    # placeholder for the typed dataclass, but every derived comparison is
    # "undefined" so no strategy gate can be satisfied off a fabricated level.
    assert state.vwap.price_vs_vwap == "undefined"
    assert state.vwap.holding is False
    assert state.orb.status == "undefined"
    assert state.previous_day.price_vs_pdh == "undefined"
    assert state.previous_day.price_vs_pdl == "undefined"


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



def test_build_market_state_asian_session_detected():
    # 02:30 UTC = 22:30 ET → asian (19:00–03:00 ET)
    payload = _base_payload(timestamp="2026-05-21T02:30:00+00:00")
    state = build_market_state(payload)
    assert state.session == "asian"


def test_build_market_state_pre_open_folds_into_london():
    # 13:00 UTC = 09:00 ET — the old 08:30-09:30 gap is now folded into london
    # (a tradeable session) under 24h coverage.
    payload = _base_payload(timestamp="2026-05-23T13:00:00+00:00")
    state = build_market_state(payload)
    assert state.session == "london"


# ─── London ORB routing ───────────────────────────────────────────────────────

def test_london_orb_used_when_session_is_london():
    """When session=london and london_orb_* are present, ORBData uses London levels."""
    payload = _base_payload(
        timestamp="2026-05-23T09:00:00+00:00",  # 05:00 ET → london
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
        timestamp="2026-05-23T09:00:00+00:00",  # 05:00 ET → london
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
        timestamp="2026-05-23T09:00:00+00:00",  # 05:00 ET → london
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


def test_runner_blocks_duplicate_bar_timestamp(config, tmp_path):
    from webhook.runner import process_alert

    log_dir = str(tmp_path / "logs")
    payload = _base_payload(market_condition="CHOPPY", orb_status=None)

    first = process_alert(payload, config=config, log_dir=log_dir)
    second = process_alert(payload, config=config, log_dir=log_dir)

    assert first["decision"] != "BLOCKED_DUPLICATE_BAR"
    assert second["decision"] == "BLOCKED_DUPLICATE_BAR"
    assert "Duplicate bar" in second["failed_gates"][0]


def test_runner_invalid_timestamp_fails_closed(config, tmp_path):
    from webhook.runner import process_alert

    cfg = replace(config, max_staleness_seconds=60)
    log_dir = str(tmp_path / "logs")
    payload = _base_payload(timestamp="not-a-timestamp")

    result = process_alert(payload, config=cfg, log_dir=log_dir)

    assert result["decision"] == "BLOCKED_DATA_QUALITY"
    assert "Invalid bar timestamp" in result["failed_gates"][0]

    journal_path = next((tmp_path / "logs").glob("journal_*.jsonl"))
    entry = json.loads(journal_path.read_text().splitlines()[-1])
    assert entry["decision"] == "BLOCKED_DATA_QUALITY"
    assert "Invalid bar timestamp" in entry["reason"]
    assert entry["failed_gates"] == result["failed_gates"]
    assert "risk_check" not in entry
    assert entry["setup"] is None


# ─── runner: TRADE → APPROVED path ───────────────────────────────────────────

def test_runner_trending_orb_breakout_mes_produces_trade(config, tmp_path):
    """MNQ is disabled; test orb_breakout on MES (orb_reclaim disabled on MES, orb_breakout is not).

    The shared `config` fixture's enabled_concepts doesn't include orb_breakout
    (it's tuned for other tests), so it's added here explicitly. The bracket
    (entry 5898.5 / stop 5896.0 / target 5904.0 — derived from orb_high=5898.0,
    tick=0.25, default orb_stop_ticks=8, MES max_stop_ticks=40, 2.2R target) is
    computed from _try_orb_breakout's own arithmetic; close=5900.0 sits inside
    the stop/target band so the setup isn't rejected by the ENTRY_DETACHED_FROM_PRICE
    staleness gate, and volume=5000/avg=3800 (1.32x) clears the >=1.2x confirmation
    gate. These values are the deterministic happy path, not a synthetic guess.
    """
    from webhook.runner import process_alert

    cfg = replace(config, enabled_concepts=config.enabled_concepts + ["orb_breakout"])
    log_dir = str(tmp_path / "logs")
    payload = _base_payload(
        ticker="MES1!",
        open=5885.0,
        high=5901.0,
        low=5880.0,
        close=5900.0,
        volume=5000,
        avg_volume=3800,
        vwap=5895.0,
        orb_high=5898.0,
        orb_low=5862.0,
        orb_status="above",
        previous_day_high=5920.0,
        previous_day_low=5840.0,
        previous_day_close=5875.0,
    )
    result = process_alert(payload, config=cfg, log_dir=log_dir)

    assert result["decision"] == "TRADE"
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


def test_process_alert_writes_runner_shadow_proof(config, tmp_path, monkeypatch):
    """The proof must come from the live per-bar runner path, not replay."""
    from ops.runner_shadow_evidence import runner_shadow_status
    from webhook.runner import process_alert

    monkeypatch.setenv("RUNNER_SHADOW_ENABLED", "true")
    log_dir = str(tmp_path / "logs")
    today = date(2026, 5, 23)
    r1 = process_alert(_base_payload(), config=config, log_dir=log_dir, for_date=today)
    if r1["decision"] != "TRADE" or r1["fill"] is None:
        pytest.skip("Signal engine produced NO_TRADE on bar 1")

    fill = r1["fill"]
    p2 = _base_payload(
        timestamp="2026-05-23T14:35:00+00:00",
        high=float(fill["entry"]) + 0.25,
        low=float(fill["stop"]) + 0.25,
        close=float(fill["entry"]),
    )
    r2 = process_alert(p2, config=config, log_dir=log_dir, for_date=today)

    assert r2["decision"] == "BLOCKED_OPEN_POSITION"
    proof = runner_shadow_status(log_dir)
    assert proof["recent"] is True
    assert proof["latest"]["source"] == "process_alert"
    assert proof["latest"]["instrument"] == r1["instrument"]


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
        import webhook.app as app_module
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
    assert data["status_label"] == "DISABLED"
    assert "no Signa API calls" in data["display"]


def test_fastapi_status_signa_formats_connected_signal(monkeypatch):
    try:
        from fastapi.testclient import TestClient
        import sources.signa_client as signa_module
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    class FakeSignal:
        ok = True
        grade = "A"
        score = 98.0
        action = "BUY"
        risk_rating = "MODERATE"
        error = None

        def to_dict(self):
            return {
                "symbol": "AAPL",
                "ok": True,
                "grade": self.grade,
                "score": self.score,
                "daily_direction": "WAIT",
                "weekly_direction": None,
                "action": self.action,
                "confidence": 92.0,
                "risk_rating": self.risk_rating,
                "error": self.error,
            }

    class FakeClient:
        configured = True

        def __init__(self, **kwargs):
            pass

        def fetch_signal(self, symbol):
            return FakeSignal()

    monkeypatch.setattr(app_module._config, "signa_api_enabled", True)
    monkeypatch.setattr(app_module._config, "signa_api_key_configured", True)
    monkeypatch.setattr(signa_module, "SignaClient", FakeClient)

    resp = TestClient(app).get("/status/signa?symbol=AAPL")

    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["configured"] is True
    assert data["status_label"] == "CONNECTED"
    assert data["message"] == "Signa read-only signal check is connected."
    assert data["display"] == "CONNECTED · grade A · score 98 · BUY · MODERATE"


def test_fastapi_status_signa_formats_failed_signal(monkeypatch):
    try:
        from fastapi.testclient import TestClient
        import sources.signa_client as signa_module
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    class FakeSignal:
        ok = False
        grade = None
        score = None
        action = None
        risk_rating = None
        error = "http_401"

        def to_dict(self):
            return {
                "symbol": "AAPL",
                "ok": False,
                "grade": None,
                "score": None,
                "daily_direction": None,
                "weekly_direction": None,
                "action": None,
                "confidence": None,
                "risk_rating": None,
                "error": self.error,
            }

    class FakeClient:
        configured = True

        def __init__(self, **kwargs):
            pass

        def fetch_signal(self, symbol):
            return FakeSignal()

    monkeypatch.setattr(app_module._config, "signa_api_enabled", True)
    monkeypatch.setattr(app_module._config, "signa_api_key_configured", True)
    monkeypatch.setattr(signa_module, "SignaClient", FakeClient)

    resp = TestClient(app).get("/status/signa?symbol=AAPL")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status_label"] == "UNAVAILABLE"
    assert data["message"] == "Signa read-only signal check failed: http_401."
    assert data["next_step"]
    assert data["display"] == "UNAVAILABLE · http_401"


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
    # Tabbed single-page shell: global status bar, bottom nav, and the five tabs.
    assert 'id="statusbar"' in resp.text
    assert 'class="bottom"' in resp.text
    assert 'data-tab="futures"' in resp.text
    assert 'data-tab="options"' in resp.text
    assert "Options Lab" in resp.text
    # Embedded JSON view-model the client renders from.
    assert 'id="init-data"' in resp.text
    # Ops modal is present; force-open confirmation modal + close-all path live
    # in the client script (rendered only when manual controls are enabled).
    assert 'id="force-modal"' in resp.text
    assert 'id="ops-modal"' in resp.text
    assert 'data-ops="preflight"' in resp.text
    assert 'data-ops="discord"' in resp.text
    assert "CLOSE ALL " in resp.text
    # Options Lab demo data must be unmistakably simulated.
    assert "OPTIONS LAB · DEMO DATA" in resp.text
    assert "SIMULATED" in resp.text


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
    assert data["max_trades_per_day"] == 9999  # trade-count throttle RIPPED 2026-06-17 (algo, not a person)
    assert "latest_entries" in data
    assert "top_no_trade_reasons" in data
    assert "diagnostics" in data
    assert "items" in data["diagnostics"]
    assert "runner_shadow" in data
    assert data["runner_shadow"]["live_trailing_blocked"] is True
    assert "live_preflight" in data
    assert "live_box_drift_guard" in data


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
    monkeypatch.setattr(
        app_module,
        "live_box_drift_report",
        lambda **_: {
            "ok": False,
            "status": "warn",
            "summary": "test pins are not configured",
            "proof_critical_runtime_overrides": [],
            "unpinned_runtime_overrides": [],
        },
    )

    client = TestClient(app)
    resp = client.get("/status/diagnostics")

    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_status"] == "warn"
    assert data["top_issue"]["component"] in {
        "Discord alerts",
        "TradingView feed",
        "Live box guard",
        "Ops automation: Health Digest",
    }
    assert {job["job"] for job in data["ops_automations"]["jobs"]} == {
        "health_digest",
        "backup_proof_data",
        "weekly_review",
    }
    components = {item["component"] for item in data["items"]}
    assert {
        "Backend API",
        "Trading mode",
        "Webhook secret",
        "Discord alerts",
        "Quality gates",
        "Configured windows",
        "Live box guard",
        "Runner shadow proof",
    }.issubset(components)


def test_demo_execution_mode_is_ok_not_warn(monkeypatch, tmp_path):
    """PAPER_MODE=false + live off + a demo broker = intended demo execution.
    The Trading mode diagnostic must read 'ok' and must NOT advise setting
    PAPER_MODE=true (that would disable demo execution)."""
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(app_module._config, "paper_mode", False)
    monkeypatch.setattr(app_module._config, "live_trading_enabled", False)

    client = TestClient(app)
    resp = client.get("/status/diagnostics")

    assert resp.status_code == 200
    mode = next(i for i in resp.json()["items"] if i["component"] == "Trading mode")
    assert mode["status"] == "ok"
    assert "Demo execution active" in mode["message"]
    assert "PAPER_MODE=true" not in (mode.get("next_step") or "")


def test_status_diagnostics_reports_bad_tradovate_env(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "cid: 13833, secret: pasted")

    client = TestClient(app)
    resp = client.get("/status/diagnostics")

    assert resp.status_code == 200
    items = resp.json()["items"]
    tradovate = next(item for item in items if item["component"] == "Tradovate config")
    assert tradovate["status"] == "error"
    assert "numeric CID only" in tradovate["message"]


def test_diagnostics_live_preflight_exception_message_not_exposed(monkeypatch, tmp_path):
    """Live-preflight failures used to interpolate the raw exception object
    (f"...{exc}") straight into diagnostic text that flows into
    /status/diagnostics, /status/today, and the "/" dashboard's embedded
    init-data — a CodeQL py/stack-trace-exposure finding. The exception's
    message must never reach any of those public responses; only a generic
    "unavailable" message should."""
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(app_module._config, "discord_notifications_enabled", False)

    marker = "SECRET_INTERNAL_STACK_TRACE_MARKER_9f3c"

    def _boom():
        raise RuntimeError(marker)

    from execution import live_preflight
    monkeypatch.setattr(live_preflight, "live_order_status", _boom)

    client = TestClient(app)

    diag_resp = client.get("/status/diagnostics")
    assert diag_resp.status_code == 200
    assert marker not in diag_resp.text
    preflight_item = next(i for i in diag_resp.json()["items"] if i["component"] == "Live preflight")
    assert preflight_item["status"] == "warn"
    assert "unavailable" in preflight_item["message"].lower()

    today_resp = client.get("/status/today")
    assert today_resp.status_code == 200
    assert marker not in today_resp.text
    assert today_resp.json()["live_preflight"]["reason"] == "unavailable"

    root_resp = client.get("/")
    assert root_resp.status_code == 200
    assert marker not in root_resp.text


def test_status_diagnostics_tradovate_config_ok_is_not_session_auth(monkeypatch, tmp_path):
    """A parsing Tradovate config must read 'ok' AND explicitly disclaim that it
    proves an authenticated broker session. Config-valid != session-active: the
    'Tradovate config' check only proves env vars parse, never that the live
    Tradovate session is authenticated (that's /status/broker-account)."""
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("TRADOVATE_USERNAME", "demo-user")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "demo-pass")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "13833")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "11111111-2222-3333-4444-555555555555")

    client = TestClient(app)
    resp = client.get("/status/diagnostics")

    assert resp.status_code == 200
    cfg = next(i for i in resp.json()["items"] if i["component"] == "Tradovate config")
    assert cfg["status"] == "ok"
    # The OK message must NOT imply the broker session is authenticated.
    assert "not the active broker session" in cfg["message"]


def test_status_diagnostics_labels_tradingview_stale_as_feed_not_api(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("BROKER", "paper")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(app_module._config, "discord_notifications_enabled", False)
    # Force an active futures session so the absent feed deterministically warns
    # (a stale feed is only a fault when bars are expected).
    monkeypatch.setattr(app_module, "_feed_window_active", lambda *a, **k: True)

    client = TestClient(app)
    resp = client.get("/status/diagnostics")

    assert resp.status_code == 200
    items = resp.json()["items"]
    backend = next(item for item in items if item["component"] == "Backend API")
    feed = next(item for item in items if item["component"] == "TradingView feed")
    assert backend["status"] == "ok"  # API ok WHILE feed is degraded
    assert feed["status"] == "warn"
    assert "backend API is still online" in feed["message"]


def test_status_diagnostics_feed_idle_outside_session_is_info_not_warn(monkeypatch, tmp_path):
    """Outside the active futures session, an absent/stale feed is expected idle,
    not a fault — it must render info, never warn (no crying stale overnight/weekends)."""
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("BROKER", "paper")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(app_module._config, "discord_notifications_enabled", False)
    # Outside the session window — no bars expected.
    monkeypatch.setattr(app_module, "_feed_window_active", lambda *a, **k: False)

    client = TestClient(app)
    resp = client.get("/status/diagnostics")

    assert resp.status_code == 200
    feed = next(i for i in resp.json()["items"] if i["component"] == "TradingView feed")
    assert feed["status"] == "info"
    assert "no" in feed["message"].lower()  # "...none is expected right now."


def test_broker_account_endpoint_offloads_and_decorates(monkeypatch, tmp_path):
    """The broker-account route runs its blocking broker fetch off the event loop
    (asyncio.to_thread) and returns the UI-safe decorated summary."""
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("BROKER", "tradovate")
    app_module._ACCOUNT_CACHE.clear()  # ignore any cached summary from other tests
    monkeypatch.setattr(app_module, "_account_summary_blocking",
                        lambda: {"ok": True, "equity": 50000.0})

    client = TestClient(app)
    resp = client.get("/status/broker-account")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "active" in (data.get("message") or "").lower()  # decorated, UI-safe
    assert data["reliability"]["state"] == "STARTING"
    app_module._ACCOUNT_CACHE.clear()


def test_tradovate_reliability_status_endpoint():
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    resp = TestClient(app).get("/status/tradovate-reliability")

    assert resp.status_code == 200
    assert {"state", "ready", "last_successful_heartbeat"} <= set(resp.json())


def test_live_preflight_status_endpoint(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        from execution import live_preflight
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setattr(live_preflight, "DEFAULT_STATE_PATH", tmp_path / "preflight.json")

    resp = TestClient(app).get("/status/live-preflight")

    assert resp.status_code == 200
    assert resp.json()["ready"] is False


def test_live_preflight_arm_requires_prior_pass(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        from execution import live_preflight
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    monkeypatch.setattr(live_preflight, "DEFAULT_STATE_PATH", tmp_path / "preflight.json")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")

    resp = TestClient(app).post(
        "/admin/live-preflight/arm",
        headers={"X-Webhook-Secret": "test-secret"},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "preflight_required"


def test_live_preflight_run_endpoint_passes_clean_broker(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        from execution import live_preflight
        from execution.tradovate_broker import AUTH_HEALTHY, AuthResult
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    class FakeBroker:
        def reliability_heartbeat(self):
            return AuthResult(AUTH_HEALTHY)

        def _get(self, path):
            return []

    monkeypatch.setattr(live_preflight, "DEFAULT_STATE_PATH", tmp_path / "preflight.json")
    monkeypatch.setattr(live_preflight, "reliability_snapshot", lambda: {
        "state": "HEALTHY",
        "ready": True,
        "last_successful_heartbeat": datetime.now(timezone.utc).isoformat(),
    })
    monkeypatch.setattr(live_preflight, "live_box_drift_report", lambda **_: {"ok": True, "summary": "guard ok"})
    monkeypatch.setattr(app_module, "_TV_BROKER", FakeBroker())
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")

    resp = TestClient(app).post(
        "/admin/live-preflight/run",
        headers={"X-Webhook-Secret": "test-secret"},
    )

    assert resp.status_code == 200
    assert resp.json()["passed"] is True


def test_admin_test_discord_endpoint(monkeypatch):
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    class Result:
        sent = True
        reason = "sent"

    sent = []
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(app_module._config, "discord_webhook_url", "https://discord.test/hook")
    monkeypatch.setattr(
        app_module,
        "send_discord_alert",
        lambda config, content: sent.append(content) or Result(),
    )

    resp = TestClient(app).post(
        "/admin/test-discord",
        headers={"X-Webhook-Secret": "test-secret"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "reason": "sent"}
    assert sent and "DISCORD TEST" in sent[0]


def test_diagnostics_items_carry_stable_codes(monkeypatch, tmp_path):
    """Every diagnostic item exposes a machine-readable `code`, and rename-sensitive
    components are pinned so consumers can key off code, not the display label."""
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(app_module, "_feed_window_active", lambda *a, **k: True)

    items = TestClient(app).get("/status/diagnostics").json()["items"]
    assert all(i.get("code") for i in items)  # no item missing a code
    by_label = {i["component"]: i["code"] for i in items}
    assert by_label.get("TradingView feed") == "tradingview_feed"
    assert by_label.get("Tradovate config") == "tradovate_config"
    assert by_label.get("Live box guard") == "live_box_guard"
    # The pinned code is independent of the (renameable) display label.
    assert app_module._diagnostic("ok", "TradingView alerts", "x")["code"] == "tradingview_feed"


def test_diagnostics_warn_when_range_observe_has_no_recent_evidence(monkeypatch, tmp_path):
    try:
        import webhook.app as app_module
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module._config, "range_observe_enabled", True)
    journal_dir = Path(app_module._config.log_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    rows = [
        {
            "ts": f"{today.isoformat()}T14:{minute:02d}:00+00:00",
            "instrument": "MNQ",
            "decision": "CONFIG_BLOCKED",
            "config_block": "TIMEFRAME_MISMATCH",
            "reason": "Expected 15m alert, received 5m.",
        }
        for minute in range(3)
    ]
    (journal_dir / f"journal_{today.isoformat()}.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n"
    )

    data = app_module._diagnostics_payload(today)
    item = next(i for i in data["items"] if i["code"] == "range_observe_evidence")
    assert item["status"] == "warn"
    assert "RANGE_OBSERVE_ENABLED is loaded true" in item["message"]
    assert "TradingView alert timeframe" in item["next_step"]


def test_diagnostics_do_not_warn_when_recent_range_evidence_exists(monkeypatch, tmp_path):
    try:
        import webhook.app as app_module
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module._config, "range_observe_enabled", True)
    journal_dir = Path(app_module._config.log_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()
    row = {
        "ts": f"{today.isoformat()}T14:30:00+00:00",
        "instrument": "MNQ",
        "decision": "NO_TRADE",
        "wall_context": {"ok": True},
        "range_signal": {"signal_type": "RANGE_REJECT"},
    }
    (journal_dir / f"journal_{today.isoformat()}.jsonl").write_text(json.dumps(row) + "\n")

    data = app_module._diagnostics_payload(today)
    item = next(i for i in data["items"] if i["code"] == "range_observe_evidence")
    assert item["status"] == "ok"
    assert "carry range evidence" in item["message"]


def test_backend_console_uses_tf_freshness_and_non_live_labels(monkeypatch):
    """Regression for the embedded console: freshness keyed off the timeframe (no
    hardcoded 6m), and 'API: LIVE'/'LIVE' badge reworded so they can't read as
    live-trading state."""
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    body = TestClient(app).get("/").text
    assert "FRESH_MAX_MIN" in body
    assert "mins <= 6" not in body                 # hardcoded 6m removed
    assert "['BACKEND:', 'ONLINE'" in body         # was ['API:', 'LIVE', ...]
    assert "['API:', 'LIVE'" not in body
    assert "🟢 CONSOLE ONLINE" in body
    # The last ambiguous standalone LIVE chip is gone (header now CONSOLE ONLINE).
    assert '<span class="live-chip">CONSOLE ONLINE</span>' in body
    assert '<span class="live-chip">LIVE</span>' not in body
    # Console freshness now reads the server-provided feed window/threshold.
    assert "feed_window_active" in body
    assert "feed_stale_after_minutes" in body


def test_status_today_exposes_feed_window_fields(monkeypatch, tmp_path):
    """/status/today carries the one shared feed window + stale threshold so the
    dashboards stop deciding feed health with their own clocks."""
    try:
        from fastapi.testclient import TestClient
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    data = TestClient(app).get("/status/today").json()
    assert isinstance(data["feed_window_active"], bool)
    expected_tf = data["expected_timeframe_minutes"]
    assert data["feed_stale_after_minutes"] == expected_tf * 2 + 1


def test_broker_account_not_authenticated_gets_ui_safe_message():
    import webhook.app as app_module

    summary = {"ok": False, "error": "not_authenticated"}
    app_module._decorate_broker_account_status(summary)

    assert summary["error"] == "not_authenticated"
    assert summary["status_label"] == "SESSION NOT ACTIVE"
    assert "not authenticated" in summary["message"]
    assert "separate from TradingView alert freshness" in summary["next_step"]


def test_webhook_alert_fast_acks_and_processes_in_background(monkeypatch, tmp_path):
    """The /webhook/alert handler MUST return immediately and run the slow
    pipeline in the background. If it ever blocks on process_alert again,
    TradingView's webhook client times out (nginx 499) and bars are dropped —
    the all-day outage of 2026-06-04. Regression lock for commit 3fc7b55.
    """
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")

    def _process_alert(payload, config=None, log_dir=None, **kw):
        return {"ok": True, "decision": "NO_TRADE", "context": {"instrument": "MNQ"}}

    monkeypatch.setattr(app_module, "process_alert", _process_alert)

    client = TestClient(app)
    body = {
        "ticker": "MNQ1!",
        "timestamp": "2026-05-23T14:30:00+00:00",
        "timeframe": "15",
        "open": 19480.0, "high": 19510.0, "low": 19475.0, "close": 19505.25,
    }
    resp = client.post("/webhook/alert?secret=test-secret", json=body)

    assert resp.status_code == 200
    data = resp.json()
    # Fast-ACK contract: it ACKNOWLEDGES (queued) instead of returning the
    # pipeline result inline. The old blocking handler returned {"ok",**result}
    # which included "decision"/"resolution"; if that regresses, this fails.
    assert data.get("queued") is True
    assert data.get("ticker") == "MNQ1!"
    assert "decision" not in data
    assert "resolution" not in data


def test_webhook_alert_rejects_bad_secret_before_queueing(monkeypatch, tmp_path):
    """Auth still happens synchronously, before anything is queued."""
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
        from webhook.app import app
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")

    called = {"n": 0}
    monkeypatch.setattr(app_module, "process_alert",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))

    client = TestClient(app)
    # Valid payload (passes body validation) but wrong secret → 401 in-handler,
    # before anything is handed off for processing.
    body = {"ticker": "MNQ1!", "timestamp": "2026-05-23T14:30:00+00:00",
            "timeframe": "15", "open": 19480.0, "high": 19510.0, "low": 19475.0, "close": 19505.25}
    resp = client.post("/webhook/alert?secret=wrong", json=body)
    assert resp.status_code == 401
    assert called["n"] == 0


def test_doctor_command_prints_diagnostics(monkeypatch, tmp_path, capsys):
    import scripts.doctor as doctor
    import webhook.app as app_module

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("BROKER", "paper")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(app_module._config, "discord_notifications_enabled", False)
    monkeypatch.setattr(app_module._config, "signa_api_enabled", False)
    monkeypatch.setattr(
        app_module,
        "live_box_drift_report",
        lambda **_: {
            "ok": True,
            "status": "ok",
            "summary": "test guard verified",
            "proof_critical_runtime_overrides": [],
            "unpinned_runtime_overrides": [],
            "security_runtime": {
                "manual_endpoint": {"effectively_inert": True},
                "webhook_secret_rotation": {
                    "primary_configured": True,
                    "rotation_ready": True,
                    "configured_env_names": [
                        "WEBHOOK_SECRET",
                        "TRADINGVIEW_WEBHOOK_SECRET",
                    ],
                    "distinct_configured_count": 2,
                },
            },
        },
    )

    exit_code = doctor.main([])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "RiskSentinel doctor:" in out
    assert "Backend API" in out
    assert "Webhook secret" in out
    assert "Runner shadow proof" in out


def test_diagnostics_report_active_proof_runtime_override(monkeypatch, tmp_path):
    import webhook.app as app_module
    from ops.live_box_guard import PROOF_CRITICAL_RUNTIME_OVERRIDES

    _isolate_app_logs(monkeypatch, tmp_path)
    for name in PROOF_CRITICAL_RUNTIME_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"EXPECTED_PROOF_{name}", raising=False)
    monkeypatch.setenv("BROKER", "paper")
    monkeypatch.setenv("EXPECTED_PROOF_BROKER", "paper")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("VWAP_ENTRY_MAX_DISTANCE_TICKS", "12")
    monkeypatch.setenv("EXPECTED_PROOF_VWAP_ENTRY_MAX_DISTANCE_TICKS", "12")

    payload = app_module._diagnostics_payload(date.today())
    item = next(
        item for item in payload["items"]
        if item["component"] == "Proof runtime overrides"
    )

    assert item["status"] == "ok"
    assert "VWAP_ENTRY_MAX_DISTANCE_TICKS=12 (pinned)" in item["message"]


def test_manual_open_action_is_removed(monkeypatch, tmp_path):
    """Manual force-OPEN was removed entirely — the endpoint must reject it,
    and the bypass helpers must no longer exist."""
    import webhook.app as app_module
    from fastapi.testclient import TestClient

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setattr(app_module, "_configured_webhook_secret", lambda: "test-secret")
    monkeypatch.setattr(app_module._config, "enable_manual_execution_controls", True)

    # The force-open code paths and their bypass helpers are gone.
    assert not hasattr(app_module, "_manual_open")
    assert not hasattr(app_module, "_manual_open_enabled")
    assert not hasattr(app_module, "_current_market_price")

    client = TestClient(app_module.app)
    resp = client.post(
        "/webhook/manual",
        json={"action": "OPEN", "direction": "LONG", "instrument": "MES",
              "entry": 5900.0, "stop": 5893.0, "target": 5915.0},
        headers={"X-Webhook-Secret": "test-secret"},
    )
    assert resp.status_code == 410
    assert "removed" in resp.json()["detail"].lower()


def test_manual_endpoint_is_inert_when_controls_disabled(monkeypatch, tmp_path):
    """A valid client-visible secret cannot dispatch actions while controls are off."""
    import webhook.app as app_module
    from fastapi.testclient import TestClient

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "_configured_webhook_secret", lambda: "test-secret")
    monkeypatch.setattr(app_module._config, "enable_manual_execution_controls", False)
    close_called = False

    def fake_close_all():
        nonlocal close_called
        close_called = True
        return {"ok": True}

    monkeypatch.setattr(app_module, "_manual_close_all", fake_close_all)

    resp = TestClient(app_module.app).post(
        "/webhook/manual",
        json={"action": "CLOSE_ALL"},
        headers={"X-Webhook-Secret": "test-secret"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Manual execution controls are disabled."
    assert close_called is False


def test_manual_endpoint_dispatches_when_controls_enabled(monkeypatch, tmp_path):
    import webhook.app as app_module
    from fastapi.testclient import TestClient

    _isolate_app_logs(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "_configured_webhook_secret", lambda: "test-secret")
    monkeypatch.setattr(app_module._config, "enable_manual_execution_controls", True)
    monkeypatch.setattr(app_module, "_manual_close_all", lambda: {"ok": True, "action": "CLOSE_ALL"})

    resp = TestClient(app_module.app).post(
        "/webhook/manual",
        json={"action": "CLOSE_ALL"},
        headers={"X-Webhook-Secret": "test-secret"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "action": "CLOSE_ALL"}


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

    assert public["exit_reason"] == "LOSS_RECORDED"
    assert public["raw_exit_reason"] == "TARGET_HIT"
    assert public["outcome_warning"] is True
    assert "negative P&L means this was not a profit target" in public["outcome_explanation"]


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


def test_status_history_realized_pnl_is_daily_not_cumulative(monkeypatch):
    """Regression: a single win must not be double-counted across days.

    `_dashboard_payload.realized_pnl_dollars` is the cumulative account figure
    (account_balance - starting_balance), so a win persists in it on every later
    day. Frontends SUM the history array for the "7D P&L" total, so the endpoint
    must emit each day's OWN P&L (today_pnl_dollars) under realized_pnl_dollars,
    not the running total. One +$55 win across two days = +$55, not +$110.
    """
    from datetime import timedelta

    try:
        from fastapi.testclient import TestClient
        import webhook.app as appmod
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    # Day 1 ago: the +$55 win actually happened. Today: no new trade, but the
    # cumulative account figure still carries that win. Days >=2: nothing.
    def fake_payload(for_date):
        offset = (date.today() - for_date).days
        daily = 55.0 if offset == 1 else 0.0
        cumulative = 55.0 if offset <= 1 else 0.0
        return {
            "date": for_date.isoformat(),
            "trade_count": 1 if offset == 1 else 0,
            "max_trades_per_day": 5,
            "consecutive_losses": 0,
            "has_open_position": False,
            "no_trades": 0,
            "wins": 1 if offset == 1 else 0,
            "losses": 0,
            "realized_pnl_dollars": cumulative,   # running account total
            "today_pnl_dollars": daily,           # that day's own P&L
            "win_rate": 100.0 if offset == 1 else 0.0,
        }

    monkeypatch.setattr(appmod, "_dashboard_payload", fake_payload)

    client = TestClient(appmod.app)
    days = client.get("/status/history?days=7").json()["days"]

    # Per-day value is the daily increment, and the running total is still exposed.
    by_date = {d["date"]: d for d in days}
    win_day = (date.today() - timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    assert by_date[win_day]["realized_pnl_dollars"] == 55.0
    assert by_date[today]["realized_pnl_dollars"] == 0.0            # not the carried 55
    assert by_date[today]["cumulative_realized_pnl_dollars"] == 55.0

    # The number frontends actually display: sum of daily P&L == one win, not two.
    assert sum(d["realized_pnl_dollars"] for d in days) == 55.0


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
    assert data["preflight"]["max_trades_per_day"] == 9999  # trade-count throttle RIPPED 2026-06-17 (algo, not a person)


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


def test_fastapi_proof_mnq_30_endpoint_is_read_only(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "journal_2026-06-23.jsonl").write_text(
        "\n".join([
            json.dumps({
                "ts": "2026-06-23T15:15:00+00:00",
                "instrument": "MNQ",
                "decision": "TRADE",
                "risk_check": {"result": "APPROVED"},
                "setup": {
                    "direction": "SHORT",
                    "strategy": "orb_reclaim",
                    "entry": 29805.25,
                    "stop": 29807.25,
                    "target": 29790.25,
                    "contracts": 1,
                },
            }),
            json.dumps({
                "ts": "2026-06-23T15:30:00+00:00",
                "type": "OUTCOME",
                "instrument": "MNQ",
                "outcome": {
                    "result": "WIN",
                    "exit_reason": "TARGET_HIT",
                    "pnl_dollars": 22.5,
                    "contracts": 1,
                },
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    async def _fake_broker_account_summary_payload():
        return {
            "ok": False,
            "error": "broker_not_tradovate",
            "realized_pnl": None,
        }

    monkeypatch.setattr(app_module, "_broker_account_summary_payload", _fake_broker_account_summary_payload)

    client = TestClient(app_module.app)
    resp = client.get("/status/proof/mnq-30?freeze_ts=2026-06-23T12:00:00Z")

    assert resp.status_code == 200
    data = resp.json()
    assert data["proof_name"] == "next_30_mnq_resolved_trades"
    assert data["resolved_mnq_trades"] == 1
    assert data["remaining_to_target"] == 29
    assert data["runtime_sources"]["journal_dir"] == str(log_dir)
    assert data["runtime_sources"]["status_today"] == "/status/today"
    assert data["runtime_sources"]["broker_account"] == "/status/broker-account"
    assert "broker P&L alone" in data["source_of_truth_rule"]
    assert data["trades"][0]["strategy"] == "orb_reclaim"
    assert not (log_dir / "review_2026-06-23.json").exists()
    assert not (log_dir / "daily_review_2026-06-23.md").exists()


def test_fastapi_proof_mnq_30_endpoint_rejects_bad_freeze_ts(monkeypatch, tmp_path):
    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
    except ImportError:
        pytest.skip("fastapi[testclient] not installed")

    _isolate_app_logs(monkeypatch, tmp_path)

    resp = TestClient(app_module.app).get("/status/proof/mnq-30?freeze_ts=nope")

    assert resp.status_code == 422
    assert "freeze_ts" in resp.json()["detail"]


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
        "timeframe": "15",
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

    # Unauthenticated (no site-gate session): public-safe summary only, no raw
    # payload/context (GEX/HTF/ICC/strat internals never leak here).
    public_resp = client.get("/status/latest-webhook")
    assert public_resp.status_code == 200
    public_data = public_resp.json()
    assert public_data["sanitized"] is True
    assert public_data["symbol"] == "MNQ1!"
    assert public_data["timeframe"] == "15"
    assert "payload" not in public_data
    assert "context" not in public_data

    # With a valid site-gate session: full raw payload/context, unchanged from
    # the endpoint's pre-existing (authenticated) behavior.
    client.cookies.set("vp_access", app_module._gate_token())
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
    assert data["queued"] is True
    assert data["ticker"] == "MNQ1!"


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
