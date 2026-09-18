"""Safety tests for the observation-only 1m MNQ 3-2-2 First Live observer."""
from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from context.five_min_feed import record_five_min
from context.one_min_322_observer import (
    advance_322_observer_from_five_min,
    one_min_322_observer_enabled,
    read_322_observer_state,
)
from webhook.payload import AlertPayload
from webhook.runner import process_alert

DAY = date(2026, 6, 2)
ET = timezone(timedelta(hours=-4))


def _payload(
    ts,
    *,
    ticker="MNQ1!",
    tf="5m",
    o=20000.0,
    h=20001.0,
    l=19999.0,
    c=20000.0,
):
    return AlertPayload(
        ticker=ticker,
        timestamp=ts.isoformat(),
        open=o,
        high=h,
        low=l,
        close=c,
        timeframe=tf,
        session="new_york",
    )


def _reference_payloads(*, direction="LONG", outside=True):
    rows = []
    start = datetime(2026, 6, 2, 7, 0, tzinfo=ET)

    # 7AM reference: H=110, L=100.
    for i in range(12):
        rows.append(
            _payload(
                start + timedelta(minutes=5 * i),
                o=20105,
                h=20110 if i == 0 else 20108,
                l=20100 if i == 0 else 20102,
                c=20105,
            )
        )

    # 8AM outside bar when requested: H=115, L=95.
    eight = start + timedelta(hours=1)
    for i in range(12):
        rows.append(
            _payload(
                eight + timedelta(minutes=5 * i),
                o=20105,
                h=(20115 if i == 0 else 20109) if outside else 20109,
                l=(20095 if i == 1 else 20101) if outside else 20101,
                c=20105,
            )
        )

    # 9AM directional bar. LONG = 2D, SHORT = 2U.
    nine = start + timedelta(hours=2)
    for i in range(12):
        if direction == "LONG":
            rows.append(
                _payload(
                    nine + timedelta(minutes=5 * i),
                    o=20100,
                    h=20104,
                    l=20090 if i == 0 else 20096,
                    c=20100,
                )
            )
        else:
            rows.append(
                _payload(
                    nine + timedelta(minutes=5 * i),
                    o=20105,
                    h=20120 if i == 0 else 20114,
                    l=20096,
                    c=20105,
                )
            )
    return rows


def _enable(monkeypatch):
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    monkeypatch.setenv("ONE_MIN_TRIGGER_ENABLED", "true")
    monkeypatch.setenv("ONE_MIN_322_OBSERVER_ENABLED", "true")
    monkeypatch.setenv("WIDE_STOP_LEDGER_MODE", "observe_only")


def _seed_to_0955(
    monkeypatch,
    tmp_path,
    config,
    *,
    direction="LONG",
    outside=True,
    omit_index=None,
):
    _enable(monkeypatch)
    rows = _reference_payloads(direction=direction, outside=outside)
    log_dir = str(tmp_path)
    for i, row in enumerate(rows[:-1]):
        if i == omit_index:
            continue
        record_five_min(row, log_dir, for_date=DAY)
    last_index = len(rows) - 1
    if last_index == omit_index:
        raise AssertionError("test helper cannot omit the 09:55 boundary bar")
    result = process_alert(
        rows[-1],
        config=config,
        log_dir=log_dir,
        for_date=DAY,
    )
    return result


def _arm_long(monkeypatch, tmp_path, config):
    result = _seed_to_0955(monkeypatch, tmp_path, config, direction="LONG")
    assert result["one_min_322_observer"]["event"] == "ARMED"
    state = read_322_observer_state(str(tmp_path), DAY)
    assert state["status"] == "ARMED"
    assert state["direction"] == "LONG"
    assert state["trigger"] == 20104.0
    assert state["stop"] == 20090.0
    assert state["target"] == 20115.0
    return state


def test_both_flags_are_required(monkeypatch, tmp_path, config):
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    monkeypatch.setenv("ONE_MIN_TRIGGER_ENABLED", "true")
    monkeypatch.delenv("ONE_MIN_322_OBSERVER_ENABLED", raising=False)
    assert not one_min_322_observer_enabled()
    rows = _reference_payloads()
    for row in rows[:-1]:
        record_five_min(row, str(tmp_path), for_date=DAY)
    result = process_alert(rows[-1], config=config, log_dir=str(tmp_path), for_date=DAY)
    assert result["decision"] == "FIVE_MIN_CONTEXT"
    assert result["one_min_322_observer"] is None
    assert read_322_observer_state(str(tmp_path), DAY) == {}

    monkeypatch.setenv("ONE_MIN_322_OBSERVER_ENABLED", "true")
    monkeypatch.delenv("ONE_MIN_TRIGGER_ENABLED", raising=False)
    assert not one_min_322_observer_enabled()


def test_mnq_only(monkeypatch, tmp_path, config):
    _enable(monkeypatch)
    payload = _payload(
        datetime(2026, 6, 2, 9, 55, tzinfo=ET),
        ticker="MES1!", o=5000.0, h=5001.0, l=4999.0, c=5000.0,
    )
    event = advance_322_observer_from_five_min(payload, str(tmp_path), for_date=DAY)
    assert event is None
    assert read_322_observer_state(str(tmp_path), DAY) == {}


def test_one_min_cannot_arm_setup(monkeypatch, tmp_path, config):
    _enable(monkeypatch)
    result = process_alert(
        _payload(
            datetime(2026, 6, 2, 10, 0, tzinfo=ET),
            tf="1m",
            o=20103.0,
            h=20105.0,
            l=20102.0,
            c=20104.5,
        ),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    assert result["decision"] == "ONE_MIN_CONTEXT"
    assert result["fill"] is None
    assert result["risk"] is None
    assert result["execution_reachable"] is False
    assert result["one_min_322_observer"] is None
    assert read_322_observer_state(str(tmp_path), DAY) == {}


def test_0955_close_arms_at_exact_1000_boundary(monkeypatch, tmp_path, config):
    result = _seed_to_0955(monkeypatch, tmp_path, config)
    event = result["one_min_322_observer"]
    assert result["decision"] == "FIVE_MIN_CONTEXT"
    assert result["fill"] is None
    assert result["risk"] is None
    assert result["execution_reachable"] is False
    assert event["event"] == "ARMED"
    assert event["bar_ts"].startswith("2026-06-02T10:00:00")
    assert event["direction"] == "LONG"
    assert event["trigger"] == 20104.0
    state = read_322_observer_state(str(tmp_path), DAY)
    assert state["status"] == "ARMED"
    assert state["observer_boundary_ts"].startswith("2026-06-02T10:00:00")


def test_missing_reference_bar_fails_closed(monkeypatch, tmp_path, config):
    result = _seed_to_0955(
        monkeypatch, tmp_path, config, omit_index=18  # 08:30 ET
    )
    event = result["one_min_322_observer"]
    assert event["event"] == "ARM_BLOCKED"
    assert event["reason"] == "REFERENCE_DATA_INCOMPLETE"
    assert event["missing_reference_count"] == 1
    state = read_322_observer_state(str(tmp_path), DAY)
    assert state["status"] == "INVALIDATED"
    assert state["invalidation"] == "REFERENCE_DATA_INCOMPLETE"


def test_non_setup_day_does_not_arm(monkeypatch, tmp_path, config):
    result = _seed_to_0955(monkeypatch, tmp_path, config, outside=False)
    event = result["one_min_322_observer"]
    assert event["event"] == "SETUP_NOT_ARMED"
    assert event["reason"] == "EIGHT_AM_NOT_OUTSIDE_BAR"
    state = read_322_observer_state(str(tmp_path), DAY)
    assert state["status"] == "INVALIDATED"


def test_equality_at_trigger_is_not_first_live_break(monkeypatch, tmp_path, config):
    _arm_long(monkeypatch, tmp_path, config)
    result = process_alert(
        _payload(
            datetime(2026, 6, 2, 10, 0, tzinfo=ET),
            tf="1m",
            o=20103.5,
            h=20104.0,
            l=20103.0,
            c=20103.75,
        ),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    assert result["one_min_322_observer"] is None
    assert read_322_observer_state(str(tmp_path), DAY)["status"] == "ARMED"


def test_long_first_live_touch_is_observation_only(monkeypatch, tmp_path, config):
    _arm_long(monkeypatch, tmp_path, config)
    result = process_alert(
        _payload(
            datetime(2026, 6, 2, 10, 1, tzinfo=ET),
            tf="1m",
            o=20103.5,
            h=20104.25,
            l=20103.0,
            c=20104.0,
        ),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    event = result["one_min_322_observer"]
    assert event["event"] == "TRIGGER_TOUCH"
    assert event["mode"] == "observation_only"
    assert event["trade_authorized"] is False
    assert event["paper_fill_authorized"] is False
    assert event["external_broker"] is False
    assert event["direction"] == "LONG"
    assert event["trigger"] == 20104.0
    assert event["trigger_reference"] == 20104.0
    assert event["gap_through"] is False
    assert result["fill"] is None
    assert result["risk"] is None
    assert result["execution_reachable"] is False
    state = read_322_observer_state(str(tmp_path), DAY)
    assert state["status"] == "TRIGGERED"


def test_short_first_live_touch_is_observed(monkeypatch, tmp_path, config):
    result = _seed_to_0955(monkeypatch, tmp_path, config, direction="SHORT")
    assert result["one_min_322_observer"]["event"] == "ARMED"
    state = read_322_observer_state(str(tmp_path), DAY)
    assert state["direction"] == "SHORT"
    assert state["trigger"] == 20096.0
    assert state["stop"] == 20120.0
    assert state["target"] == 20095.0

    result = process_alert(
        _payload(
            datetime(2026, 6, 2, 10, 3, tzinfo=ET),
            tf="1m",
            o=20096.5,
            h=20097.0,
            l=20095.75,
            c=20096.0,
        ),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    event = result["one_min_322_observer"]
    assert event["event"] == "TRIGGER_TOUCH"
    assert event["direction"] == "SHORT"
    assert event["trigger_reference"] == 20096.0
    assert event["gap_through"] is False


def test_gap_through_is_recorded(monkeypatch, tmp_path, config):
    _arm_long(monkeypatch, tmp_path, config)
    result = process_alert(
        _payload(
            datetime(2026, 6, 2, 10, 2, tzinfo=ET),
            tf="1m",
            o=20105.0,
            h=20106.0,
            l=20104.5,
            c=20105.5,
        ),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    event = result["one_min_322_observer"]
    assert event["event"] == "TRIGGER_TOUCH"
    assert event["gap_through"] is True
    assert event["trigger_reference"] == 20105.0


def test_gap_beyond_target_fails_closed(monkeypatch, tmp_path, config):
    _arm_long(monkeypatch, tmp_path, config)
    result = process_alert(
        _payload(
            datetime(2026, 6, 2, 10, 2, tzinfo=ET),
            tf="1m",
            o=20116.0,
            h=20117.0,
            l=20115.5,
            c=20116.5,
        ),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    event = result["one_min_322_observer"]
    assert event["event"] == "TRIGGER_BLOCKED"
    assert event["reason"] == "ENTRY_BRACKET_INVALID_AT_TOUCH"
    assert event["trade_authorized"] is False
    state = read_322_observer_state(str(tmp_path), DAY)
    assert state["status"] == "INVALIDATED"
    assert state["invalidation"] == "ENTRY_BRACKET_INVALID_AT_TOUCH"


def test_duplicate_break_emits_no_second_evidence(monkeypatch, tmp_path, config):
    _arm_long(monkeypatch, tmp_path, config)
    first = process_alert(
        _payload(
            datetime(2026, 6, 2, 10, 1, tzinfo=ET),
            tf="1m", o=20103.5, h=20104.25, l=20103.0, c=20104.0,
        ),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    second = process_alert(
        _payload(
            datetime(2026, 6, 2, 10, 2, tzinfo=ET),
            tf="1m", o=20104.0, h=20105.0, l=20103.5, c=20104.5,
        ),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    assert first["one_min_322_observer"]["event"] == "TRIGGER_TOUCH"
    assert second["one_min_322_observer"] is None
    evidence = (
        tmp_path
        / "tf1m"
        / "322_first_live"
        / f"evidence_{DAY.isoformat()}.jsonl"
    ).read_text().strip().splitlines()
    # One ARM record + one trigger record. No duplicate trigger record.
    assert len(evidence) == 2


def test_touch_outside_window_is_ignored(monkeypatch, tmp_path, config):
    _arm_long(monkeypatch, tmp_path, config)
    result = process_alert(
        _payload(
            datetime(2026, 6, 2, 9, 59, tzinfo=ET),
            tf="1m", o=20103.5, h=20105.0, l=20103.0, c=20104.5,
        ),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    assert result["one_min_322_observer"] is None
    assert read_322_observer_state(str(tmp_path), DAY)["status"] == "ARMED"


def test_observer_state_survives_restart_style_reload(monkeypatch, tmp_path, config):
    expected = _arm_long(monkeypatch, tmp_path, config)
    reloaded = read_322_observer_state(str(tmp_path), DAY)
    assert reloaded == expected
    assert reloaded["observer_mode"] == "observation_only"


def test_1100_expiry_from_1055_close(monkeypatch, tmp_path, config):
    _arm_long(monkeypatch, tmp_path, config)
    result = process_alert(
        _payload(
            datetime(2026, 6, 2, 10, 55, tzinfo=ET),
            tf="5m", o=20103.0, h=20103.75, l=20102.5, c=20103.0,
        ),
        config=config,
        log_dir=str(tmp_path),
        for_date=DAY,
    )
    event = result["one_min_322_observer"]
    assert event["event"] == "EXPIRED"
    assert event["reason"] == "NO_BREAK_BY_11AM"
    assert read_322_observer_state(str(tmp_path), DAY)["status"] == "EXPIRED"


def test_new_flag_is_proof_critical():
    from ops.live_box_guard import PROOF_CRITICAL_RUNTIME_OVERRIDES

    assert "ONE_MIN_322_OBSERVER_ENABLED" in PROOF_CRITICAL_RUNTIME_OVERRIDES


def test_observer_module_has_no_execution_imports():
    path = Path(__file__).parents[1] / "context" / "one_min_322_observer.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")

    forbidden = (
        "strategy.signal_engine",
        "risk.risk_engine",
        "execution.paper_broker",
        "execution.tradovate",
        "execution.broker",
    )
    assert not any(
        module.startswith(prefix)
        for module in imported
        for prefix in forbidden
    )
