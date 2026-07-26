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


# ─── The hole found on operator review: unscoped decision rows ───────────────
#
# claim_bar()'s scan matches ANY non-OUTCOME row with the same instrument+ts,
# not just BAR_CLAIM rows -- it also matches ordinary log_decision() rows.
# DecisionOutput.to_dict() (strategy/signal_engine.py) never carried a
# timeframe field, so a ordinary decision row logged right after a
# timeframe-tagged BAR_CLAIM would itself be untagged -- and claim_bar()
# correctly treats an untagged EXISTING entry as a match (its own backward-
# compatibility rule). A later, different-timeframe claim attempt would see
# that untagged decision row and be wrongly blocked, recreating the exact
# collision the BAR_CLAIM tagging alone was supposed to prevent. Fixed by
# tagging every journal_entry dict webhook/runner.py logs (decision.to_dict()
# results, plus the two early-rejection rows) with the same
# timeframe_minutes computed for claim_bar() itself.

def test_claim_bar_blocked_by_untagged_decision_row_recreates_the_hole(tmp_path):
    """Proves the hole existed: an untagged decision row logged between two
    claim_bar() calls still collides across timeframes. This models what
    log_decision() used to write before webhook/runner.py tagged every
    journal_entry with timeframe_minutes."""
    journal = JournalLogger(log_dir=str(tmp_path))
    assert journal.claim_bar(
        instrument="MNQ", bar_ts=_TS, for_date=_DAY, timeframe_minutes=5
    ) is True
    # An UNTAGGED decision row for the same instrument/ts (what a caller that
    # forgot to set timeframe_minutes on journal_entry would still write).
    journal.log_decision(
        {"ts": _TS, "instrument": "MNQ", "session": "new_york",
         "decision": "NO_TRADE", "reason": "test"},
        None, for_date=_DAY,
    )
    blocked = journal.claim_bar(
        instrument="MNQ", bar_ts=_TS, for_date=_DAY, timeframe_minutes=15
    )
    assert blocked is False  # the hole: an untagged row still collides


def test_claim_bar_tagged_decision_row_does_not_recreate_the_hole(tmp_path):
    """The fix: once the intervening decision row is ALSO tagged with the
    bar's own timeframe_minutes (what webhook/runner.py now does for every
    journal_entry it logs), a different-timeframe claim is no longer blocked
    by it. Requirement: claim MNQ/T as 5m -> log its (tagged) decision row ->
    attempt MNQ/T as 15m -> must succeed."""
    journal = JournalLogger(log_dir=str(tmp_path))
    assert journal.claim_bar(
        instrument="MNQ", bar_ts=_TS, for_date=_DAY, timeframe_minutes=5
    ) is True
    journal.log_decision(
        {"ts": _TS, "instrument": "MNQ", "session": "new_york",
         "decision": "NO_TRADE", "reason": "test", "timeframe_minutes": 5},
        None, for_date=_DAY,
    )
    assert journal.claim_bar(
        instrument="MNQ", bar_ts=_TS, for_date=_DAY, timeframe_minutes=15
    ) is True


def test_claim_bar_tagged_decision_row_reverse_order_15m_then_5m(tmp_path):
    """Same proof, reverse order: claim MNQ/T as 15m -> log its (tagged)
    decision row -> attempt MNQ/T as 5m -> must succeed."""
    journal = JournalLogger(log_dir=str(tmp_path))
    assert journal.claim_bar(
        instrument="MNQ", bar_ts=_TS, for_date=_DAY, timeframe_minutes=15
    ) is True
    journal.log_decision(
        {"ts": _TS, "instrument": "MNQ", "session": "new_york",
         "decision": "NO_TRADE", "reason": "test", "timeframe_minutes": 15},
        None, for_date=_DAY,
    )
    assert journal.claim_bar(
        instrument="MNQ", bar_ts=_TS, for_date=_DAY, timeframe_minutes=5
    ) is True


def test_claim_bar_legacy_decision_row_with_no_timeframe_field_still_blocks(tmp_path):
    """A truly legacy journal (containing only an old, unscoped decision row
    from before this fix existed) must still block -- same-timeframe-unknown
    entries stay conservative, exactly as before this whole fix."""
    journal = JournalLogger(log_dir=str(tmp_path))
    journal.log_decision(
        {"ts": _TS, "instrument": "MNQ", "session": "new_york",
         "decision": "NO_TRADE", "reason": "legacy, no timeframe_minutes field"},
        None, for_date=_DAY,
    )
    blocked_same = journal.claim_bar(
        instrument="MNQ", bar_ts=_TS, for_date=_DAY, timeframe_minutes=15
    )
    assert blocked_same is False


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
