"""Tests for the 5-minute entry feed (increment 1: ingest-only).

Verifies: timeframe parsing, the isolated 5M lane, and the process_alert
intercept — including that the default-OFF behaviour is byte-for-byte the old
15M timeframe rejection, and that an enabled 5M alert is stored as context
WITHOUT touching the 15M decision path or the 15M bar history.
"""

from datetime import datetime, timezone

from context.bar_history import BarHistory
from context.five_min_feed import (
    FIVE_MIN_LANE,
    _root,
    five_min_status,
    is_five_min,
    normalize_minutes,
    recent_five_min,
    record_five_min,
)
from webhook.payload import AlertPayload
from webhook.runner import process_alert


def _payload(tf="5m", ticker="MES1!"):
    # Fresh timestamp: keeps the bar inside the staleness budget and aligns the
    # record/read date so recent() (which defaults to today) sees it.
    return AlertPayload(
        ticker=ticker,
        timestamp=datetime.now(timezone.utc).isoformat(),
        open=5240.0, high=5241.25, low=5239.5, close=5240.5,
        timeframe=tf,
    )


def test_normalize_minutes_variants():
    assert normalize_minutes("5") == 5
    assert normalize_minutes("5m") == 5
    assert normalize_minutes("5min") == 5
    assert normalize_minutes("15m") == 15
    assert normalize_minutes("1h") == 60
    assert normalize_minutes("") is None
    assert normalize_minutes(None) is None


def test_is_five_min():
    assert is_five_min("5m") and is_five_min("5") and is_five_min("5min")
    assert not is_five_min("15m")
    assert not is_five_min("1h")


def test_root_does_not_overstrip_month_letter_roots():
    # the old rstrip("!1234567890HMUZ") turned MYM -> 'MY'; the regex must not.
    assert _root("MYM1!") == "MYM"
    assert _root("MES1!") == "MES"
    assert _root("MNQ1!") == "MNQ"
    assert _root("MGC1!") == "MGC"
    assert _root("MES") == "MES"


def test_five_min_status_reports_counts(tmp_path):
    log_dir = str(tmp_path)
    before = five_min_status(log_dir, instruments=["MES1!"])
    assert before["instruments"]["MES"]["bars"] == 0
    assert before["instruments"]["MES"]["last_ts"] is None

    record_five_min(_payload("5m", ticker="MES1!"), log_dir)
    after = five_min_status(log_dir, instruments=["MES1!"])
    assert after["instruments"]["MES"]["bars"] == 1
    assert after["instruments"]["MES"]["last_ts"] is not None


def test_record_and_read_isolated_lane(tmp_path):
    record_five_min(_payload("5m"), str(tmp_path))
    bars = recent_five_min("MES1!", str(tmp_path), 10)
    assert len(bars) == 1
    assert bars[-1]["close"] == 5240.5
    # stored under the isolated 5M lane, keyed by contract ROOT
    assert (tmp_path / FIVE_MIN_LANE).exists()
    # and NOT in the 15M history (lane isolation)
    assert BarHistory(log_dir=str(tmp_path)).recent("MES", 10) == []


def test_record_dedupes_resends(tmp_path):
    p = _payload("5m")  # one bar, recorded twice (same ts) → no duplicate
    record_five_min(p, str(tmp_path))
    record_five_min(p, str(tmp_path))
    assert len(recent_five_min("MES1!", str(tmp_path), 10)) == 1


def test_process_alert_rejects_5m_when_feed_disabled(monkeypatch, tmp_path):
    """Default OFF: a 5M alert still hits the 15M timeframe guard (unchanged)."""
    monkeypatch.delenv("FIVE_MIN_FEED_ENABLED", raising=False)
    result = process_alert(_payload("5m"), log_dir=str(tmp_path / "logs"))
    assert result["decision"] == "CONFIG_BLOCKED"
    assert result["config_block"] == "TIMEFRAME_MISMATCH"
    # nothing stored in the 5M lane
    assert recent_five_min("MES1!", str(tmp_path / "logs"), 10) == []


def test_process_alert_stores_5m_as_context_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    log_dir = str(tmp_path / "logs")
    result = process_alert(_payload("5m"), log_dir=log_dir)
    # acknowledged as context, NOT a trade and NOT a config error
    assert result["decision"] == "FIVE_MIN_CONTEXT"
    assert result["fill"] is None
    # the bar landed on the 5M lane...
    bars = recent_five_min("MES1!", log_dir, 10)
    assert len(bars) == 1 and bars[-1]["close"] == 5240.5
    # ...and NOT on the 15M history that the decision engine reads
    assert BarHistory(log_dir=log_dir).recent("MES", 10) == []


def test_process_alert_15m_unaffected_when_feed_enabled(monkeypatch, tmp_path):
    """A 15M alert is never intercepted, even with the 5M feed on."""
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    result = process_alert(_payload("15m"), log_dir=str(tmp_path / "logs"))
    assert result["decision"] != "FIVE_MIN_CONTEXT"
    # 5M lane stays empty
    assert recent_five_min("MES1!", str(tmp_path / "logs"), 10) == []
