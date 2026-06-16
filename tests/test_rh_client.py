"""Tests for alert_ranker.rh_client — mark fetching and token refresh."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from alert_ranker.rh_client import RHClient
from alert_ranker.rh_options import auto_check_positions
from alert_ranker.storage import ScanStorage


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_response(status_code: int, json_body: dict):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_body
    return m


def _seed_open_position(tmp_path):
    """Create a ScanStorage with one OPEN shadow position that has full contract data."""
    import json
    import sqlite3

    db = tmp_path / "rh.db"
    storage = ScanStorage(db)
    con = sqlite3.connect(str(db))
    con.execute("""
        INSERT INTO options_shadow_journal
        (timestamp, scan_id, ticker, direction, score, pattern, status,
         setup_inputs_json, provider_snapshot_json, selected_contract_json, outcome_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "2026-06-16T10:00:00+00:00", 1, "AAPL", "LONG", 80, "N/A", "OPEN",
        "{}", "{}",
        json.dumps({
            "strike": 205.0,
            "expiry": "2026-07-18",
            "option_type": "CALL",
            "stop_premium": 1.25,
            "target_premium": 5.00,
        }),
        "{}",
    ))
    con.commit()
    shadow_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.close()
    return ScanStorage(db), shadow_id


# ── RHClient config ──────────────────────────────────────────────────────────


def test_rh_client_not_configured_when_empty_token():
    client = RHClient("")
    assert not client.configured


def test_rh_client_configured_when_token_set():
    client = RHClient("tok_abc123")
    assert client.configured


def test_rh_client_status_includes_config_flags():
    client = RHClient("tok_abc", "ref_xyz")
    s = client.status()
    assert s["configured"] is True
    assert s["refresh_token_set"] is True
    assert s["last_error"] is None


# ── Token refresh ─────────────────────────────────────────────────────────────


def test_rh_client_refreshes_on_401(tmp_path):
    client = RHClient("expired_token", "valid_refresh")

    refresh_response = _mock_response(200, {
        "access_token": "new_bearer",
        "refresh_token": "new_refresh",
    })
    real_response = _mock_response(200, {"results": [{"mark_price": "3.50"}]})

    call_count = {"n": 0}

    def fake_get(url, headers=None, params=None, timeout=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _mock_response(401, {})
        return real_response

    def fake_post(url, data=None, timeout=None):
        return refresh_response

    with patch("alert_ranker.rh_client.httpx.get", side_effect=fake_get), \
         patch("alert_ranker.rh_client.httpx.post", side_effect=fake_post):
        result = client._get("https://api.robinhood.com/marketdata/options/")

    assert result == {"results": [{"mark_price": "3.50"}]}
    assert client._bearer == "new_bearer"
    assert client._refresh_token == "new_refresh"


def test_rh_client_returns_none_when_refresh_fails():
    client = RHClient("expired_token", "bad_refresh")

    with patch("alert_ranker.rh_client.httpx.get", return_value=_mock_response(401, {})), \
         patch("alert_ranker.rh_client.httpx.post", return_value=_mock_response(401, {})):
        result = client._get("https://api.robinhood.com/anything/")

    assert result is None
    assert client.last_error is not None


def test_rh_client_no_refresh_attempt_without_refresh_token():
    client = RHClient("expired_token")  # no refresh token

    posted = []
    with patch("alert_ranker.rh_client.httpx.get", return_value=_mock_response(401, {})), \
         patch("alert_ranker.rh_client.httpx.post", side_effect=lambda *a, **k: posted.append(1)):
        client._get("https://api.robinhood.com/anything/")

    assert posted == [], "should not attempt refresh without refresh token"


# ── Instrument URL lookup ─────────────────────────────────────────────────────


def test_get_option_instrument_url_returns_url():
    client = RHClient("tok")
    fake = _mock_response(200, {"results": [{"url": "https://api.robinhood.com/options/instruments/abc123/"}]})

    with patch("alert_ranker.rh_client.httpx.get", return_value=fake):
        url = client.get_option_instrument_url("AAPL", 205.0, "2026-07-18", "call")

    assert url == "https://api.robinhood.com/options/instruments/abc123/"


def test_get_option_instrument_url_returns_none_on_empty_results():
    client = RHClient("tok")
    fake = _mock_response(200, {"results": []})

    with patch("alert_ranker.rh_client.httpx.get", return_value=fake):
        url = client.get_option_instrument_url("AAPL", 205.0, "2026-07-18", "call")

    assert url is None


# ── Mark fetching ─────────────────────────────────────────────────────────────


def test_get_option_mark_returns_float():
    client = RHClient("tok")
    fake = _mock_response(200, {"results": [{"mark_price": "3.45"}]})

    with patch("alert_ranker.rh_client.httpx.get", return_value=fake):
        mark = client.get_option_mark("https://api.robinhood.com/options/instruments/abc/")

    assert mark == pytest.approx(3.45)


def test_get_option_mark_returns_none_on_empty():
    client = RHClient("tok")
    fake = _mock_response(200, {"results": []})

    with patch("alert_ranker.rh_client.httpx.get", return_value=fake):
        mark = client.get_option_mark("https://api.robinhood.com/options/instruments/abc/")

    assert mark is None


def test_fetch_marks_for_positions_returns_marks(tmp_path):
    storage, shadow_id = _seed_open_position(tmp_path)
    positions = storage.latest_shadow_setups(status="OPEN", limit=10)
    assert positions

    client = RHClient("tok")
    instrument_url = "https://api.robinhood.com/options/instruments/abc123/"

    def fake_get(url, headers=None, params=None, timeout=None):
        if "instruments" in url and "marketdata" not in url:
            return _mock_response(200, {"results": [{"url": instrument_url}]})
        return _mock_response(200, {"results": [{"mark_price": "4.20"}]})

    with patch("alert_ranker.rh_client.httpx.get", side_effect=fake_get):
        marks = client.fetch_marks_for_positions(positions)

    assert str(shadow_id) in marks
    assert marks[str(shadow_id)] == pytest.approx(4.20)


def test_fetch_marks_skips_positions_missing_strike(tmp_path):
    import json, sqlite3
    db = tmp_path / "skip.db"
    storage = ScanStorage(db)
    # Insert a position without strike
    con = sqlite3.connect(str(db))
    con.execute("""
        INSERT INTO options_shadow_journal
        (timestamp, scan_id, ticker, direction, score, pattern, status,
         setup_inputs_json, provider_snapshot_json, selected_contract_json, outcome_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("2026-06-16T10:00:00+00:00", 1, "AAPL", "LONG", 80, "N/A", "OPEN",
          "{}", "{}", json.dumps({"expiry": "2026-07-18", "option_type": "CALL"}), "{}"))
    con.commit()
    con.close()

    client = RHClient("tok")
    positions = ScanStorage(db).latest_shadow_setups(status="OPEN")

    with patch("alert_ranker.rh_client.httpx.get"):
        marks = client.fetch_marks_for_positions(positions)

    assert marks == {}


def test_fetch_marks_uses_stored_instrument_url(tmp_path):
    """If rh_instrument_url is stored in selected_contract, skip the lookup call."""
    import json, sqlite3
    db = tmp_path / "stored.db"
    ScanStorage(db)
    instrument_url = "https://api.robinhood.com/options/instruments/stored123/"
    con = sqlite3.connect(str(db))
    con.execute("""
        INSERT INTO options_shadow_journal
        (timestamp, scan_id, ticker, direction, score, pattern, status,
         setup_inputs_json, provider_snapshot_json, selected_contract_json, outcome_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("2026-06-16T10:00:00+00:00", 1, "AAPL", "LONG", 80, "N/A", "OPEN",
          "{}", "{}",
          json.dumps({
              "strike": 205.0,
              "expiry": "2026-07-18",
              "option_type": "CALL",
              "rh_instrument_url": instrument_url,
          }), "{}"))
    con.commit()
    shadow_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.close()

    client = RHClient("tok")
    positions = ScanStorage(db).latest_shadow_setups(status="OPEN")

    lookup_calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        lookup_calls.append(url)
        return _mock_response(200, {"results": [{"mark_price": "5.00"}]})

    with patch("alert_ranker.rh_client.httpx.get", side_effect=fake_get):
        marks = client.fetch_marks_for_positions(positions)

    # Only ONE get call (the marketdata fetch) — not two (no instrument lookup)
    assert len(lookup_calls) == 1
    assert "marketdata" in lookup_calls[0]
    assert marks[str(shadow_id)] == pytest.approx(5.00)


# ── auto_check_positions ──────────────────────────────────────────────────────


def test_auto_check_positions_error_when_unconfigured(tmp_path):
    storage = ScanStorage(tmp_path / "ac.db")
    result = auto_check_positions(storage, "", None)
    assert result["error"] == "rh_not_configured"
    assert result["marks_fetched"] == 0


def test_auto_check_positions_empty_when_no_open(tmp_path):
    storage = ScanStorage(tmp_path / "ac2.db")
    client = RHClient("tok")
    result = auto_check_positions(storage, "", client)
    assert result["open_count"] == 0
    assert result["marks_fetched"] == 0


def test_auto_check_positions_fires_discord_on_hit(tmp_path):
    storage, shadow_id = _seed_open_position(tmp_path)
    pos = storage.latest_shadow_setups(status="OPEN")[0]
    # Mark above target_premium (5.00) → TARGET_HIT
    target = pos.selected_contract["target_premium"]
    mark_above = target + 0.50

    client = RHClient("tok")
    client.fetch_marks_for_positions = lambda positions: {str(shadow_id): mark_above}

    with patch("alert_ranker.rh_options._post_discord", return_value=True) as mock_discord:
        result = auto_check_positions(storage, "http://fake-discord", client)

    assert result["marks_fetched"] == 1
    assert len(result["hits"]) == 1
    assert result["hits"][0]["hit_type"] == "TARGET_HIT"
    mock_discord.assert_called_once()
