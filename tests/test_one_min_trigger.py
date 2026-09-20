"""Tests for the isolated 1-minute armed 4HR trigger evidence lane."""
from datetime import date, datetime, timedelta, timezone

from context.five_min_feed import record_five_min
from context.one_min_trigger import (
    ONE_MIN_LANE,
    evaluate_armed_4hr_touch,
    is_one_min,
    recent_one_min,
)
from journal.journal_logger import JournalLogger
from webhook.payload import AlertPayload
from webhook.runner import process_alert

DAY = date(2026, 6, 2)
ET = timezone(timedelta(hours=-4))


def _payload(ts, *, tf="1m", o=19999.0, h=20001.0, l=19998.0, c=20000.5):
    return AlertPayload(
        ticker="MNQ1!",
        timestamp=ts.isoformat(),
        open=o,
        high=h,
        low=l,
        close=c,
        timeframe=tf,
        session="new_york",
    )
def _seed_completed_8am_hour(log_dir):
    start = datetime(2026, 6, 2, 8, 0, tzinfo=ET)
    for i in range(12):
        ts = start + timedelta(minutes=5 * i)
        bar = _payload(
            ts,
            tf="5m",
            o=19950.0,
            h=20025.0 + i,
            l=19900.0,
            c=19975.0,
        )
        record_five_min(bar, log_dir, for_date=DAY)


def _seed_4h_continuation_context(log_dir):
    rows = [
        (datetime(2026, 6, 1, 16, 0, tzinfo=ET), 19700.0, 19500.0),
        (datetime(2026, 6, 1, 20, 0, tzinfo=ET), 19800.0, 19600.0),
        (datetime(2026, 6, 2, 0, 0, tzinfo=ET), 19900.0, 19700.0),
        (datetime(2026, 6, 2, 4, 0, tzinfo=ET), 20000.0, 19800.0),
    ]
    for ts, high, low in rows:
        record_five_min(
            _payload(ts, tf="5m", o=(high + low) / 2, h=high, l=low, c=(high + low) / 2),
            log_dir,
            for_date=ts.date(),
        )


def _enable_4hr(config):
    if "strat_4hr_retrigger" not in config.enabled_concepts:
        config.enabled_concepts = [*config.enabled_concepts, "strat_4hr_retrigger"]


def _arm_4hr(log_dir, *, direction="LONG", trigger=20000.0, target=20200.0):
    state = {
        "MNQ": {
            "trading_date": DAY.isoformat(),
            "status": "ARMED",
            "direction": direction,
            "trigger": trigger,
            "target": target,
            "setup_bar_ts": "2026-06-02T09:10:00-04:00",
            "four_am_bar_ts": "2026-06-02T04:00:00-04:00",
        }
    }
    JournalLogger(log_dir=log_dir).log_decision(
        {
            "ts": "2026-06-02T09:30:00-04:00",
            "instrument": "MNQ",
            "decision": "NO_TRADE",
            "strategy_state": {"strat_4hr_retrigger": state},
        },
        None,
        for_date=DAY,
    )
def test_is_one_min_variants():
    assert is_one_min("1m")
    assert is_one_min("1")
    assert is_one_min("1min")
    assert not is_one_min("5m")


def test_disabled_one_min_preserves_timeframe_mismatch(monkeypatch, tmp_path, config):
    monkeypatch.delenv("ONE_MIN_TRIGGER_ENABLED", raising=False)
    result = process_alert(
        _payload(datetime(2026, 6, 2, 9, 31, tzinfo=ET)),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    assert result["decision"] == "CONFIG_BLOCKED"
    assert result["config_block"] == "TIMEFRAME_MISMATCH"
    assert recent_one_min("MNQ", str(tmp_path), for_date=DAY) == []


def test_enabled_one_min_without_arm_is_context_only(monkeypatch, tmp_path, config):
    monkeypatch.setenv("ONE_MIN_TRIGGER_ENABLED", "true")
    _enable_4hr(config)
    result = process_alert(
        _payload(datetime(2026, 6, 2, 9, 31, tzinfo=ET)),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    assert result["decision"] == "ONE_MIN_CONTEXT"
    assert result["fill"] is None
    assert result["risk"] is None
    assert result["execution_reachable"] is False
    assert result["one_min_trigger"] is None
    assert len(recent_one_min("MNQ", str(tmp_path), for_date=DAY)) == 1
def test_armed_touch_uses_prior_completed_one_hour(monkeypatch, tmp_path, config):
    monkeypatch.setenv("ONE_MIN_TRIGGER_ENABLED", "true")
    monkeypatch.setenv("WIDE_STOP_LEDGER_MODE", "observe_only")
    _enable_4hr(config)
    log_dir = str(tmp_path)
    _seed_completed_8am_hour(log_dir)
    _arm_4hr(log_dir)

    result = process_alert(
        _payload(datetime(2026, 6, 2, 9, 31, tzinfo=ET)),
        config=config,
        log_dir=log_dir,
        for_date=DAY,
    )
    event = result["one_min_trigger"]
    assert result["decision"] == "ONE_MIN_CONTEXT"
    assert event["event"] == "TRIGGER_TOUCH"
    assert event["mode"] == "paper_evidence_only"
    assert event["trade_authorized"] is False
    assert event["external_broker"] is False
    assert event["trigger"] == 20000.0
    assert event["stop"] == 19900.0
    assert event["stop_bar_ts"].startswith("2026-06-02T08:00:00")
    assert event["target"] == 20200.0
    assert event["paper_entry_1tick"] == 20000.25
    assert event["four_hour_treatment"]["treatment_eligible"] is None


def test_touch_records_causal_4h_continuation_treatment(monkeypatch, tmp_path, config):
    monkeypatch.setenv("ONE_MIN_TRIGGER_ENABLED", "true")
    monkeypatch.setenv("WIDE_STOP_LEDGER_MODE", "observe_only")
    _enable_4hr(config)
    log_dir = str(tmp_path)
    _seed_4h_continuation_context(log_dir)
    _seed_completed_8am_hour(log_dir)
    _arm_4hr(log_dir)

    result = process_alert(
        _payload(datetime(2026, 6, 2, 9, 31, tzinfo=ET)),
        config=config,
        log_dir=log_dir,
        for_date=DAY,
    )
    treatment = result["one_min_trigger"]["four_hour_treatment"]
    assert treatment["status"] == "OK"
    assert treatment["sequence"] == "strat_22_continuation"
    assert treatment["treatment_eligible"] is True
    assert treatment["definition"] == "completed_et_wall_clock_4h_sequence_v1"
    assert result["fill"] is None
    assert result["execution_reachable"] is False


def test_touch_is_deduped_per_armed_setup(monkeypatch, tmp_path, config):
    monkeypatch.setenv("ONE_MIN_TRIGGER_ENABLED", "true")
    monkeypatch.setenv("WIDE_STOP_LEDGER_MODE", "observe_only")
    _enable_4hr(config)
    log_dir = str(tmp_path)
    _seed_completed_8am_hour(log_dir)
    _arm_4hr(log_dir)

    first = process_alert(
        _payload(datetime(2026, 6, 2, 9, 31, tzinfo=ET)),
        config=config,
        log_dir=log_dir,
        for_date=DAY,
    )
    second = process_alert(
        _payload(datetime(2026, 6, 2, 9, 32, tzinfo=ET), h=20002.0),
        config=config,
        log_dir=log_dir,
        for_date=DAY,
    )
    assert first["one_min_trigger"]["event"] == "TRIGGER_TOUCH"
    assert second["one_min_trigger"]["event"] == "TRIGGER_DUPLICATE"

    evidence = (
        tmp_path / ONE_MIN_LANE / f"4hr_trigger_evidence_{DAY.isoformat()}.jsonl"
    ).read_text().strip().splitlines()
    assert len(evidence) == 1


def test_no_touch_cannot_create_candidate(monkeypatch, tmp_path, config):
    monkeypatch.setenv("ONE_MIN_TRIGGER_ENABLED", "true")
    monkeypatch.setenv("WIDE_STOP_LEDGER_MODE", "observe_only")
    _enable_4hr(config)
    log_dir = str(tmp_path)
    _seed_completed_8am_hour(log_dir)
    _arm_4hr(log_dir)

    result = process_alert(
        _payload(
            datetime(2026, 6, 2, 9, 31, tzinfo=ET),
            o=19990.0, h=19999.75, l=19985.0, c=19998.0,
        ),
        config=config,
        log_dir=log_dir,
        for_date=DAY,
    )
    assert result["decision"] == "ONE_MIN_CONTEXT"
    assert result["one_min_trigger"] is None
    assert result["fill"] is None


def test_one_min_enable_flag_is_proof_critical():
    from ops.live_box_guard import PROOF_CRITICAL_RUNTIME_OVERRIDES

    assert "ONE_MIN_TRIGGER_ENABLED" in PROOF_CRITICAL_RUNTIME_OVERRIDES
