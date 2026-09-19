from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json

from context.five_min_feed import record_five_min
from context.one_min_response_audit import append_observer_response_audit
from journal.journal_logger import JournalLogger
from scripts.forward_one_min_trigger_review import build_report
from webhook.payload import AlertPayload
from webhook.runner import process_alert

DAY = date(2026, 6, 2)
ET = timezone(timedelta(hours=-4))


def _payload(ts, *, tf="1m", o=20000.0, h=20001.0, l=19999.0, c=20000.0):
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


def _enable_observers(monkeypatch):
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    monkeypatch.setenv("ONE_MIN_TRIGGER_ENABLED", "true")
    monkeypatch.setenv("ONE_MIN_322_OBSERVER_ENABLED", "true")
    monkeypatch.setenv("WIDE_STOP_LEDGER_MODE", "observe_only")


def _seed_8am_hour(log_dir: str):
    start = datetime(2026, 6, 2, 8, 0, tzinfo=ET)
    for i in range(12):
        record_five_min(
            _payload(
                start + timedelta(minutes=5 * i),
                tf="5m",
                o=19950.0,
                h=20025.0 + i,
                l=19900.0,
                c=19975.0,
            ),
            log_dir,
            for_date=DAY,
        )


def _arm_4hr(log_dir: str):
    state = {
        "MNQ": {
            "trading_date": DAY.isoformat(),
            "status": "ARMED",
            "direction": "LONG",
            "trigger": 20000.0,
            "target": 20200.0,
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


def _make_4hr_touch(monkeypatch, tmp_path, config, *, audit=True, unsafe=False):
    _enable_observers(monkeypatch)
    if "strat_4hr_retrigger" not in config.enabled_concepts:
        config.enabled_concepts = [*config.enabled_concepts, "strat_4hr_retrigger"]
    log_dir = str(tmp_path)
    _seed_8am_hour(log_dir)
    _arm_4hr(log_dir)
    payload = _payload(
        datetime(2026, 6, 2, 9, 31, tzinfo=ET),
        o=19999.5, h=20000.25, l=19999.0, c=20000.0,
    )
    result = process_alert(payload, config=config, log_dir=log_dir, for_date=DAY)
    assert result["one_min_trigger"]["event"] == "TRIGGER_TOUCH"
    if audit:
        audited = dict(result)
        if unsafe:
            audited.update(
                decision="TRADE",
                fill={"result": "OPEN"},
                risk={"approved": True},
                execution_reachable=True,
            )
        append_observer_response_audit(log_dir, payload, audited)
    # Later completed 5m bar containing the 09:31 touch, for timing comparison.
    record_five_min(
        _payload(
            datetime(2026, 6, 2, 9, 30, tzinfo=ET),
            tf="5m", o=19999.0, h=20002.0, l=19998.0, c=20001.0,
        ),
        log_dir,
        for_date=DAY,
    )
    return result


def _reference_322_payloads():
    rows = []
    start = datetime(2026, 6, 2, 7, 0, tzinfo=ET)
    for i in range(12):
        rows.append(_payload(
            start + timedelta(minutes=5 * i), tf="5m",
            o=20105, h=20110 if i == 0 else 20108,
            l=20100 if i == 0 else 20102, c=20105,
        ))
    eight = start + timedelta(hours=1)
    for i in range(12):
        rows.append(_payload(
            eight + timedelta(minutes=5 * i), tf="5m",
            o=20105, h=20115 if i == 0 else 20109,
            l=20095 if i == 1 else 20101, c=20105,
        ))
    nine = start + timedelta(hours=2)
    for i in range(12):
        rows.append(_payload(
            nine + timedelta(minutes=5 * i), tf="5m",
            o=20100, h=20104, l=20090 if i == 0 else 20096, c=20100,
        ))
    return rows


def _make_322_touch(monkeypatch, tmp_path, config):
    _enable_observers(monkeypatch)
    log_dir = str(tmp_path)
    refs = _reference_322_payloads()
    for row in refs[:-1]:
        record_five_min(row, log_dir, for_date=DAY)
    arm_payload = refs[-1]
    arm_result = process_alert(
        arm_payload, config=config, log_dir=log_dir, for_date=DAY
    )
    assert arm_result["decision"] == "FIVE_MIN_CONTEXT"
    assert arm_result["one_min_322_observer"]["event"] == "ARMED"
    append_observer_response_audit(log_dir, arm_payload, arm_result)

    touch_payload = _payload(
        datetime(2026, 6, 2, 10, 1, tzinfo=ET),
        tf="1m", o=20103.5, h=20104.25, l=20103.0, c=20104.0,
    )
    touch_result = process_alert(
        touch_payload, config=config, log_dir=log_dir, for_date=DAY
    )
    assert touch_result["one_min_322_observer"]["event"] == "TRIGGER_TOUCH"
    append_observer_response_audit(log_dir, touch_payload, touch_result)

    record_five_min(
        _payload(
            datetime(2026, 6, 2, 10, 0, tzinfo=ET),
            tf="5m", o=20103.0, h=20106.0, l=20102.0, c=20105.0,
        ),
        log_dir,
        for_date=DAY,
    )
    return arm_result, touch_result


def test_empty_evidence_is_collect(tmp_path):
    report = build_report(tmp_path)
    assert report["overall"] == "COLLECT"
    assert report["four_hr"]["touches"] == 0
    assert report["strat_322_first_live"]["touches"] == 0
    assert report["four_hr"]["problems"] == []
    assert report["strat_322_first_live"]["problems"] == []


def test_clean_4hr_touch_passes_mechanism_checks(monkeypatch, tmp_path, config):
    _make_4hr_touch(monkeypatch, tmp_path, config)
    report = build_report(tmp_path)
    lane = report["four_hr"]
    assert lane["classification"] == "COLLECT"
    assert lane["touches"] == 1
    assert lane["problems"] == []
    assert lane["timing"]["n"] == 1
    assert lane["timing"]["median_adverse_detachment_ticks"] == 4.0
    assert lane["timing"]["median_timing_advantage_seconds"] == 180.0


def test_missing_response_proof_fails_closed(monkeypatch, tmp_path, config):
    _make_4hr_touch(monkeypatch, tmp_path, config, audit=False)
    report = build_report(tmp_path)
    assert report["overall"] == "HOLD / DEFECT FOUND"
    codes = {
        code
        for item in report["four_hr"]["problems"]
        for code in item["problems"]
    }
    assert "MISSING_DURABLE_RESPONSE_AUDIT" in codes


def test_unsafe_response_is_unsafe(monkeypatch, tmp_path, config):
    _make_4hr_touch(monkeypatch, tmp_path, config, unsafe=True)
    report = build_report(tmp_path)
    assert report["overall"] == "UNSAFE"
    codes = {
        code
        for item in report["four_hr"]["problems"]
        for code in item["problems"]
    }
    assert "RESPONSE_FILL_PRESENT" in codes
    assert "RESPONSE_RISK_PRESENT" in codes
    assert "RESPONSE_EXECUTION_REACHABLE" in codes


def test_clean_322_arm_and_touch_pass(monkeypatch, tmp_path, config):
    _make_322_touch(monkeypatch, tmp_path, config)
    report = build_report(tmp_path)
    lane = report["strat_322_first_live"]
    assert lane["classification"] == "COLLECT"
    assert lane["event_counts"]["ARMED"] == 1
    assert lane["event_counts"]["TRIGGER_TOUCH"] == 1
    assert lane["touches"] == 1
    assert lane["problems"] == []
    assert lane["timing"]["n"] == 1


def test_322_touch_without_persisted_arm_fails(monkeypatch, tmp_path, config):
    _make_322_touch(monkeypatch, tmp_path, config)
    path = (
        tmp_path / "tf1m" / "322_first_live" / f"evidence_{DAY.isoformat()}.jsonl"
    )
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows = [row for row in rows if row.get("event") != "ARMED"]
    path.write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n",
        encoding="utf-8",
    )
    report = build_report(tmp_path)
    codes = {
        code
        for item in report["strat_322_first_live"]["problems"]
        for code in item["problems"]
    }
    assert "322_TOUCH_WITHOUT_PRECEDING_ARM" in codes
    assert report["overall"] == "HOLD / DEFECT FOUND"
