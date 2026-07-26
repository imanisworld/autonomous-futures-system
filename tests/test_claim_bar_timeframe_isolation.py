"""Regression coverage: JournalLogger.claim_bar() must distinguish bars by
timeframe, not just (instrument, timestamp).

Enabling strat_4hr_retrigger (a 5-minute-native strategy) means legitimate
5-minute MNQ bars now reach the same claim_bar() call as ordinary 15-minute
MNQ decision bars (webhook/runner.py:858-877). A 15-minute bar and a
5-minute bar can share the exact same wall-clock timestamp (every
15-minute boundary), so without a timeframe component in the dedup key,
whichever alert's webhook arrives first would incorrectly suppress the
other as BLOCKED_DUPLICATE_BAR -- silently dropping real decisions on
whichever timeframe lost the race. See docs/strategy-rules/ (n/a) --
finding surfaced during MNQ 4HR forward-demo activation preflight,
2026-07-26.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from journal.journal_logger import JournalLogger
from webhook.payload import AlertPayload
from webhook.runner import process_alert

_DAY = date(2026, 6, 2)
_TS = "2026-06-02T15:15:00+00:00"  # 15:15 UTC = 11:15 ET, in-session; also a
                                    # 15-minute boundary a 5m bar can share.


def _payload(*, timeframe: str, ts: str = _TS, ticker: str = "MNQ1!") -> AlertPayload:
    return AlertPayload(
        ticker=ticker,
        timestamp=ts,
        timeframe=timeframe,
        open=19480.0, high=19510.0, low=19475.0, close=19505.25,
        volume=4200, avg_volume=3800, vwap=19495.0,
        orb_high=19498.0, orb_low=19462.0, orb_status="reclaimed_high",
        market_condition="TRENDING", trend_direction="UP", trend_strength="MODERATE",
        previous_day_high=19520.0, previous_day_low=19440.0, previous_day_close=19475.0,
        current_bar_type="two_up", previous_bar_type="inside_bar", two_bars_back_type="two_up",
    )


# ─── Unit level: JournalLogger.claim_bar() ────────────────────────────────────

def test_claim_bar_same_timeframe_still_blocks_duplicate(tmp_path):
    """Requirement 1: identical instrument + timestamp + timeframe stays blocked."""
    journal = JournalLogger(log_dir=str(tmp_path))
    first = journal.claim_bar(
        instrument="MNQ", bar_ts=_TS, for_date=_DAY, timeframe_minutes=15
    )
    second = journal.claim_bar(
        instrument="MNQ", bar_ts=_TS, for_date=_DAY, timeframe_minutes=15
    )
    assert first is True
    assert second is False


def test_claim_bar_different_timeframe_both_succeed(tmp_path):
    """Requirement 2: same instrument + timestamp, different timeframe -> both claim."""
    journal = JournalLogger(log_dir=str(tmp_path))
    claim_15m = journal.claim_bar(
        instrument="MNQ", bar_ts=_TS, for_date=_DAY, timeframe_minutes=15
    )
    claim_5m = journal.claim_bar(
        instrument="MNQ", bar_ts=_TS, for_date=_DAY, timeframe_minutes=5
    )
    assert claim_15m is True
    assert claim_5m is True


def test_claim_bar_legacy_entry_without_timeframe_still_blocks(tmp_path):
    """Backward compatibility: an existing claim with no recorded timeframe
    (older journal rows, or a caller that omitted the parameter) is still
    treated as a match -- the fix only carves out an explicitly-DIFFERENT
    timeframe as non-colliding, it never loosens matching versus before."""
    journal = JournalLogger(log_dir=str(tmp_path))
    legacy = journal.claim_bar(instrument="MNQ", bar_ts=_TS, for_date=_DAY)
    assert legacy is True
    blocked = journal.claim_bar(
        instrument="MNQ", bar_ts=_TS, for_date=_DAY, timeframe_minutes=15
    )
    assert blocked is False


def test_claim_bar_without_timeframe_param_preserves_old_behavior(tmp_path):
    """A caller that never passes timeframe_minutes gets the exact pre-fix
    behavior: instrument + timestamp is the whole key, regardless of what
    timeframe (if any) an existing entry recorded."""
    journal = JournalLogger(log_dir=str(tmp_path))
    first = journal.claim_bar(
        instrument="MNQ", bar_ts=_TS, for_date=_DAY, timeframe_minutes=5
    )
    second = journal.claim_bar(instrument="MNQ", bar_ts=_TS, for_date=_DAY)
    assert first is True
    assert second is False


# ─── Integration level: process_alert() end-to-end ────────────────────────────

def test_5m_4hr_native_bar_does_not_suppress_15m_decision_bar(config, tmp_path, monkeypatch):
    """Requirement 3 (the actual production scenario): a 5-minute MNQ alert
    routed into the real decision pipeline by strat_4hr_retrigger's
    enablement must not BLOCKED_DUPLICATE_BAR the ordinary 15-minute MNQ
    decision alert sharing the same timestamp, and vice versa."""
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    cfg = replace(config, enabled_concepts=[*config.enabled_concepts, "strat_4hr_retrigger"])
    log_dir = str(tmp_path / "logs")

    five_min_result = process_alert(
        _payload(timeframe="5m"), config=cfg, log_dir=log_dir, for_date=_DAY
    )
    fifteen_min_result = process_alert(
        _payload(timeframe="15"), config=cfg, log_dir=log_dir, for_date=_DAY
    )

    assert five_min_result["decision"] != "BLOCKED_DUPLICATE_BAR"
    assert fifteen_min_result["decision"] != "BLOCKED_DUPLICATE_BAR"


def test_15m_decision_bar_does_not_suppress_later_5m_4hr_native_bar(config, tmp_path, monkeypatch):
    """Same collision, opposite arrival order -- whichever alert wins the race
    must not matter."""
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    cfg = replace(config, enabled_concepts=[*config.enabled_concepts, "strat_4hr_retrigger"])
    log_dir = str(tmp_path / "logs")

    fifteen_min_result = process_alert(
        _payload(timeframe="15"), config=cfg, log_dir=log_dir, for_date=_DAY
    )
    five_min_result = process_alert(
        _payload(timeframe="5m"), config=cfg, log_dir=log_dir, for_date=_DAY
    )

    assert fifteen_min_result["decision"] != "BLOCKED_DUPLICATE_BAR"
    assert five_min_result["decision"] != "BLOCKED_DUPLICATE_BAR"


def test_runner_duplicate_webhook_retry_still_blocked_with_4hr_enabled(config, tmp_path):
    """Requirement 4: existing duplicate-webhook protection remains intact --
    the SAME alert (same instrument, timestamp, AND timeframe) sent twice is
    still a duplicate, even with strat_4hr_retrigger enabled."""
    cfg = replace(config, enabled_concepts=[*config.enabled_concepts, "strat_4hr_retrigger"])
    log_dir = str(tmp_path / "logs")
    payload = _payload(timeframe="15")

    first = process_alert(payload, config=cfg, log_dir=log_dir, for_date=_DAY)
    second = process_alert(payload, config=cfg, log_dir=log_dir, for_date=_DAY)

    assert first["decision"] != "BLOCKED_DUPLICATE_BAR"
    assert second["decision"] == "BLOCKED_DUPLICATE_BAR"
