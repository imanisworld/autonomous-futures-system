"""Tests for the 5-minute entry feed (increment 1: ingest-only).

Verifies: timeframe parsing, the isolated 5M lane, and the process_alert
intercept — including that the default-OFF behaviour is byte-for-byte the old
15M timeframe rejection, and that an enabled 5M alert is stored as context
WITHOUT touching the 15M decision path or the 15M bar history.
"""

from datetime import datetime, timedelta, timezone

from context.bar_history import BarHistory
from context.five_min_feed import (
    FIVE_MIN_LANE,
    _root,
    arm_fifteen_min_setup,
    clear_armed_setup,
    five_min_status,
    is_five_min,
    normalize_minutes,
    read_armed_setup,
    recent_five_min,
    record_five_min,
    triggered_armed_setup,
)
from webhook.payload import AlertPayload
from webhook.runner import process_alert


# Fixed in-session timestamp (Tuesday 15:00 UTC = 11:00 ET = new_york) so the
# end-to-end process_alert tests pass the session gate regardless of when the
# suite runs. Session (and arm staleness) derive from the payload timestamp,
# never wall clock, so pinning keeps every check self-consistent.
_IN_SESSION_NOW = datetime(2026, 6, 2, 15, 0, tzinfo=timezone.utc)


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


def test_default_off_does_not_touch_arm_storage(monkeypatch, tmp_path):
    monkeypatch.delenv("FIVE_MIN_FEED_ENABLED", raising=False)

    def _must_not_clear(*args, **kwargs):
        raise AssertionError("default-off 15m path must not touch 5m arm storage")

    monkeypatch.setattr("webhook.runner.clear_armed_setup", _must_not_clear)
    result = process_alert(_payload("15m"), log_dir=str(tmp_path / "logs"))
    assert result["decision"] != "FIVE_MIN_CONTEXT"


def _arm(log_dir, *, direction="LONG", entry=5240.0, ts=None):
    ts = ts or datetime.now(timezone.utc)
    setup = {
        "direction": direction,
        "entry": entry,
        "stop": 5230.0 if direction == "LONG" else 5250.0,
        "target": 5260.0 if direction == "LONG" else 5220.0,
        "rr_ratio": 2.0,
        "strategy": "vwap_hold",
        "notes": "validated on 15m",
    }
    payload = _payload("15m").model_dump()
    payload["timestamp"] = ts.isoformat()
    payload["session"] = "new_york"
    return arm_fifteen_min_setup(
        "MES", log_dir, setup=setup, payload=payload, for_date=ts.date()
    )


def test_armed_setup_fails_closed_without_entry_retest(tmp_path):
    now = datetime.now(timezone.utc)
    log_dir = str(tmp_path)
    _arm(log_dir, entry=5240.0, ts=now)
    bar = _payload("5m")
    bar.timestamp = (now + timedelta(minutes=5)).isoformat()
    bar.low, bar.close = 5241.0, 5242.0
    assert triggered_armed_setup(bar, log_dir, now.date()) is None
    assert read_armed_setup("MES", log_dir, now.date()) is not None


def test_long_retest_triggers_original_unmodified_bracket_once(tmp_path):
    now = datetime.now(timezone.utc)
    log_dir = str(tmp_path)
    armed = _arm(log_dir, entry=5240.0, ts=now)
    bar = _payload("5m")
    bar.timestamp = (now + timedelta(minutes=5)).isoformat()
    bar.low, bar.close = 5239.75, 5240.25
    triggered = triggered_armed_setup(bar, log_dir, now.date())
    assert triggered["setup"] == armed["setup"]
    assert triggered["setup"]["entry"] == 5240.0
    # Matching the retest does not consume authority; process_alert does that
    # only after the broker confirms an OPEN position.
    assert read_armed_setup("MES", log_dir, now.date()) is not None


def test_short_retest_is_mirrored(tmp_path):
    now = datetime.now(timezone.utc)
    log_dir = str(tmp_path)
    _arm(log_dir, direction="SHORT", entry=5240.0, ts=now)
    bar = _payload("5m")
    bar.timestamp = (now + timedelta(minutes=5)).isoformat()
    bar.high, bar.close = 5240.25, 5239.75
    assert triggered_armed_setup(bar, log_dir, now.date()) is not None


def test_retest_close_must_remain_within_one_tick_of_entry(tmp_path):
    now = datetime.now(timezone.utc)
    log_dir = str(tmp_path)
    _arm(log_dir, entry=5240.0, ts=now)
    bar = _payload("5m")
    bar.timestamp = (now + timedelta(minutes=5)).isoformat()
    bar.low, bar.close = 5239.75, 5241.0
    assert triggered_armed_setup(bar, log_dir, now.date()) is None
    assert read_armed_setup("MES", log_dir, now.date()) is not None


def test_stale_or_malformed_arm_fails_closed_and_is_cleared(tmp_path):
    now = datetime.now(timezone.utc)
    log_dir = str(tmp_path)
    _arm(log_dir, ts=now - timedelta(minutes=21))
    bar = _payload("5m")
    bar.timestamp = now.isoformat()
    assert triggered_armed_setup(bar, log_dir, now.date(), now=now) is None
    assert read_armed_setup("MES", log_dir, now.date()) is None

    _arm(log_dir, ts=now)
    path = tmp_path / FIVE_MIN_LANE / f"armed_MES_{now.date().isoformat()}.json"
    path.write_text("{broken")
    assert triggered_armed_setup(bar, log_dir, now.date(), now=now) is None


def test_clear_arm_is_idempotent(tmp_path):
    now = datetime.now(timezone.utc)
    _arm(str(tmp_path), ts=now)
    clear_armed_setup("MES", str(tmp_path), now.date())
    clear_armed_setup("MES", str(tmp_path), now.date())
    assert read_armed_setup("MES", str(tmp_path), now.date()) is None


def test_process_alert_executes_only_the_armed_15m_setup(
    monkeypatch, tmp_path, config
):
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    monkeypatch.setenv("BROKER", "paper")
    config.min_confluence_grade = ""
    config.allowed_sessions = [*config.allowed_sessions, "asian"]
    now = _IN_SESSION_NOW
    log_dir = str(tmp_path / "logs")
    armed = _arm(log_dir, entry=5240.0, ts=now)

    # Prove the 5M path cannot become a second decision engine.
    def _must_not_evaluate(*args, **kwargs):
        raise AssertionError("5m trigger must not evaluate a new strategy setup")

    monkeypatch.setattr(
        "webhook.runner.DecisionEngine.evaluate", _must_not_evaluate
    )
    bar = _payload("5m")
    bar.timestamp = (now + timedelta(minutes=5)).isoformat()
    bar.low, bar.close = 5239.75, 5240.25
    result = process_alert(bar, config=config, log_dir=log_dir, for_date=now.date())

    assert result["decision"] == "TRADE", result["risk"]
    assert result["fill"]["status"] == "OPEN"
    assert result["fill"]["entry"] == armed["setup"]["entry"]
    assert result["fill"]["stop"] == armed["setup"]["stop"]
    assert result["fill"]["target"] == armed["setup"]["target"]
    assert result["context"]["close"] == 5240.25
    assert result["context"]["timeframe"] == "5m"
    assert read_armed_setup("MES", log_dir, now.date()) is None


def test_failed_5m_execution_retains_arm_for_retry(monkeypatch, tmp_path, config):
    from execution.broker_interface import Fill
    from execution.paper_broker import PaperBroker

    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    config.min_confluence_grade = ""
    config.allowed_sessions = [*config.allowed_sessions, "asian"]
    now = _IN_SESSION_NOW
    log_dir = str(tmp_path / "logs")
    _arm(log_dir, entry=5240.0, ts=now)

    def _cancel(self, order):
        return Fill(
            instrument=order.instrument,
            direction=order.direction,
            contracts=order.contracts,
            entry_price=order.entry,
            exit_price=None,
            exit_reason="ENTRY_NOT_FILLED",
            result="CANCELLED",
            pnl_ticks=None,
            pnl_dollars=None,
        )

    monkeypatch.setattr(PaperBroker, "execute_bracket", _cancel)
    bar = _payload("5m")
    bar.timestamp = (now + timedelta(minutes=5)).isoformat()
    bar.low, bar.close = 5239.75, 5240.25

    result = process_alert(bar, config=config, log_dir=log_dir, for_date=now.date())

    assert result["decision"] == "BLOCKED_EXECUTION_FAILED"
    assert read_armed_setup("MES", log_dir, now.date()) is not None
