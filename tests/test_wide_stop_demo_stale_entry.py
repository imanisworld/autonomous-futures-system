"""Log-only Track B stale-entry shadow. Synthetic prices; no market replay."""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from context import wide_stop_demo_state as demo_state
from context import wide_stop_execution as execution
from context import wide_stop_forward_collector as collector
from context.bar_history import BarHistory
from context.track_b_daily_heartbeat import (
    JOURNAL_NAME,
    bars_expected,
    record_bar,
    seal_session,
)
from context.wide_stop_demo_execution import (
    DEMO_STALE_ENTRY_SHADOW_X,
    stale_entry_check,
)
from context.wide_stop_demo_runtime import isolated_log_dir, process_demo_five_min_bar
from context.wide_stop_demo_runtime_core import _mask_broker_id
from execution.broker_interface import BracketOrder
from execution.contract_identity import MATCH, UNKNOWN, compare
from execution.post_fill_validation import validate_post_fill
from ops.forward_lanes_daily_proof import track_b_heartbeat_proof
from tests.test_mnq_orb_reclaim_proof import _tradovate_broker, _tradovate_order
from tests.test_wide_stop_demo_runtime import (
    _AlwaysApproveRisk,
    _FakeBroker,
    _cfg,
    _demo_env,
)
from tests.test_wide_stop_ledger_runtime import _observe

import context.wide_stop_demo_runtime as demo

ET = ZoneInfo("America/New_York")
FOUR_HR = collector.FOUR_HR
THREE_TWO_TWO = collector.THREE_TWO_TWO
DAY_22 = date(2026, 10, 22)
DAY_26 = date(2026, 10, 26)
DAY_28 = date(2026, 10, 28)
_SHADOW_ONLY = {
    "stale_entry_shadow",
    "submit_ts_utc",
    "submit_latency_ms",
}


def test_threshold_is_a_preregistered_constant(monkeypatch):
    monkeypatch.setenv("DEMO_STALE_ENTRY_SHADOW_X", "0.90")
    assert DEMO_STALE_ENTRY_SHADOW_X == 0.25


def _check(direction, entry, stop, ref, max_frac=DEMO_STALE_ENTRY_SHADOW_X):
    return stale_entry_check(direction, entry, stop, ref, max_frac)


def test_oct_22_long_would_block_and_does_not_submit_a_decision():
    blocked, reason, metrics = _check("LONG", 29546.5, 29495.5, 29527.5)
    assert blocked is True
    assert reason == "DEMO_ENTRY_STALE_LEVEL"
    assert round(metrics["frac"], 4) == 0.3725
    assert metrics["dist_ticks"] == -76
    assert metrics["side_of_level"] == "not_through"
    assert metrics["max_frac"] == 0.25


def test_oct_26_long_is_inside_the_threshold():
    blocked, reason, metrics = _check("LONG", 29296.5, 29253.25, 29294.25)
    assert blocked is False
    assert reason == "OK"
    assert round(metrics["frac"], 4) == 0.0520
    assert metrics["dist_ticks"] == -9
    assert metrics["side_of_level"] == "not_through"


def test_boundary_is_strictly_greater_than_the_threshold():
    at, at_reason, at_metrics = _check("LONG", 29546.5, 29495.5, 29533.75)
    over, over_reason, over_metrics = _check("LONG", 29546.5, 29495.5, 29533.5)
    adverse, adverse_reason, adverse_metrics = _check("LONG", 29546.5, 29495.5, 29566.0)
    assert at is False and at_reason == "OK"
    assert at_metrics["frac"] == pytest.approx(0.25)
    assert over is True and over_reason == "DEMO_ENTRY_STALE_LEVEL"
    assert adverse is True and adverse_reason == "DEMO_ENTRY_STALE_LEVEL"
    assert adverse_metrics["dist_ticks"] == 78
    assert adverse_metrics["side_of_level"] == "through"


def test_short_mirror():
    blocked, reason, metrics = _check("SHORT", 29890.75, 29947.75, 29905.75)
    passed, pass_reason, pass_metrics = _check("SHORT", 29890.75, 29947.75, 29895.0)
    assert blocked is True and reason == "DEMO_ENTRY_STALE_LEVEL"
    assert metrics["dist_ticks"] == -60
    assert metrics["side_of_level"] == "not_through"
    assert passed is False and pass_reason == "OK"
    assert pass_metrics["side_of_level"] == "not_through"
    assert pass_metrics["frac"] < 0.25


@pytest.mark.parametrize(
    "direction, entry, stop, ref",
    [
        ("LONG", 29546.5, 29495.5, None),
        ("LONG", 29546.5, 29495.5, math.nan),
        ("LONG", 29546.5, 29495.5, math.inf),
        ("LONG", 29546.5, 29546.5, 29546.5),
        (None, 29546.5, 29495.5, 29527.5),
        ("LONG", None, 29495.5, 29527.5),
        ("LONG", 29546.5, None, 29527.5),
        ("LONG", 29546.5, 29495.5, "not-a-price"),
    ],
)
def test_unavailable_inputs_would_block(direction, entry, stop, ref):
    blocked, reason, metrics = stale_entry_check(direction, entry, stop, ref, 0.25)
    assert blocked is True
    assert reason == "DEMO_ENTRY_REF_PRICE_UNAVAILABLE"
    assert "frac" in metrics and "dist_ticks" in metrics and "side_of_level" in metrics


def test_post_fill_reports_favorable_slippage_without_changing_acceptance():
    order = BracketOrder(
        instrument="MNQ", direction="LONG", entry=29603.5, stop=29583.5,
        target=29653.5, rr_ratio=2.5, strategy="orb_reclaim", contracts=1,
        min_rr_ratio=2.0, max_dollar_risk=56.0, max_stop_ticks=120,
        max_slippage_ticks=32, post_fill_validation_required=True,
    )
    better = validate_post_fill(order, 29603.0)
    worse = validate_post_fill(order, 29610.5)
    assert better.favorable_slippage_ticks == 2
    assert better.adverse_slippage_ticks == 0
    assert better.abs_slippage_frac_of_stop == pytest.approx(0.5 / 20.0)
    assert better.accepted is True
    assert worse.favorable_slippage_ticks == 0
    assert worse.accepted is False
    assert "actual_rr_minimum" in worse.failed_checks
    assert set(worse.checks) == {
        "actual_rr_minimum", "actual_dollar_risk", "actual_stop_distance",
        "slippage_limit", "target_direction", "stop_direction",
        "entry_tick", "stop_tick", "target_tick",
    }


def test_bar_history_counts_hint_presence_and_does_not_invent_one(tmp_path):
    history = BarHistory(log_dir=str(tmp_path))
    ts = datetime(2026, 10, 22, 14, 5, tzinfo=timezone.utc)
    missing = history.record(
        "MNQ", ts=ts, open=1, high=2, low=1, close=1.5, timeframe="5m",
    )
    present = history.record(
        "MNQ", ts=ts + timedelta(minutes=5), open=1, high=2, low=1, close=1.5,
        timeframe="5m", contract_hint="MNQZ2026",
    )
    assert missing["contract_hint_present"] is False
    assert "contract_hint" not in missing
    assert present["contract_hint_present"] is True
    assert present["contract_hint"] == "MNQZ2026"


def test_contract_compare_does_not_invent_a_hint():
    unknown = compare(None, "MNQZ6", context_date=date(2026, 10, 22))
    match = compare("MNQZ2026", "MNQZ6", context_date=date(2026, 10, 26))
    assert unknown.status == UNKNOWN
    assert unknown.hint is None
    assert match.status == MATCH


def _install(monkeypatch, prices):
    decision = SimpleNamespace(
        decision="TRADE", setup=SimpleNamespace(strategy=FOUR_HR),
        failed_gates=[], reason="ok",
    )
    candidate = {
        "direction": prices["direction"],
        "entry": prices["entry"],
        "stop": prices["stop"],
        "target": prices["target"],
        "entry_time": datetime(2026, 10, 22, 14, 5, tzinfo=timezone.utc),
        "strategy": FOUR_HR,
    }
    from risk.risk_engine import TradeSetup

    setup = TradeSetup(
        direction=prices["direction"], entry=prices["entry"], stop=prices["stop"],
        target=prices["target"], rr_ratio=1.4, strategy=FOUR_HR, instrument="MNQ",
        session="new_york", contracts=1, confluence_grade="B",
        entry_time=candidate["entry_time"],
    )

    def fake_eval(*, strategy: str, **kwargs):
        if strategy != FOUR_HR:
            return None, None, None, None
        return decision, object(), candidate, None

    monkeypatch.setattr(collector, "_evaluate_canonical_candidate", fake_eval)
    monkeypatch.setattr(collector, "_trade_setup", lambda state, out: setup)
    monkeypatch.setattr(
        collector, "_lane_daily_state",
        lambda *args, **kwargs: __import__(
            "risk.risk_engine", fromlist=["DailyState"]
        ).DailyState(account_balance=5_000, account_peak_balance=5_000),
    )
    monkeypatch.setattr(demo._core, "RiskEngine", _AlwaysApproveRisk)


def _payload(day: date, close: float, hint=None):
    payload = SimpleNamespace(
        ticker="MNQ1!", timestamp=datetime(day.year, day.month, day.day, 14, 5, tzinfo=timezone.utc).isoformat(),
        timeframe="5m", open=close, high=close + 2, low=close - 2, close=close, volume=1000,
    )
    if hint is not None:
        payload.contract_hint = hint
    return payload


def _submit(tmp_path, monkeypatch, day, prices, close, hint=None):
    _demo_env(monkeypatch)
    _install(monkeypatch, prices)
    broker = _FakeBroker()
    events = process_demo_five_min_bar(
        payload=_payload(day, close, hint), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        broker_factory=lambda: broker,
    )
    return broker, events


_OCT_22 = {"direction": "LONG", "entry": 29546.5, "stop": 29495.5, "target": 30057.0}
_OCT_26 = {"direction": "LONG", "entry": 29296.5, "stop": 29253.25, "target": 29382.75}
# Synthetic third session. The prompt names 2026-10-28 for byte identity and
# does not give a price triple; these prices only prove the order is unchanged.
_OCT_28 = {"direction": "SHORT", "entry": 28800.25, "stop": 28840.25, "target": 28720.25}


def test_oct_22_shadow_blocks_on_the_journal_and_the_order_is_unchanged(tmp_path, monkeypatch):
    broker, events = _submit(tmp_path, monkeypatch, DAY_22, _OCT_22, 29527.5)
    assert broker.execute_calls == 1
    assert broker.last_order.entry == 29546.5
    assert broker.last_order.stop == 29495.5
    assert broker.last_order.target == 30057.0
    row = next(item for item in events if item.get("collector_event") == "CANDIDATE")
    assert row["stale_entry_shadow"]["would_block"] is True
    assert row["stale_entry_shadow"]["reason"] == "DEMO_ENTRY_STALE_LEVEL"
    assert row["planned_entry"] == 29546.5
    assert row["ref_price"] == 29527.5
    assert row["cap_ticks"] == execution.FROZEN_MNQ_IOC_TICKS


def test_oct_26_shadow_passes_and_the_bracket_is_unchanged(tmp_path, monkeypatch):
    broker, events = _submit(tmp_path, monkeypatch, DAY_26, _OCT_26, 29294.25)
    assert broker.execute_calls == 1
    assert broker.last_order.entry == 29296.5
    assert broker.last_order.stop == 29253.25
    assert broker.last_order.target == 29382.75
    row = next(item for item in events if item.get("collector_event") == "CANDIDATE")
    assert row["stale_entry_shadow"]["would_block"] is False
    assert row["stale_entry_shadow"]["reason"] == "OK"


def test_bar_without_hint_is_counted_and_the_order_is_unchanged(tmp_path, monkeypatch):
    broker, events = _submit(tmp_path, monkeypatch, DAY_22, _OCT_22, 29527.5)
    assert broker.execute_calls == 1
    assert broker.last_order.entry == 29546.5
    assert broker.last_order.contract_hint is None
    unknown = compare(None, "MNQZ6", context_date=DAY_22)
    assert unknown.status == UNKNOWN
    sealed = seal_session(
        tmp_path, now=datetime(2026, 10, 22, 20, 5, tzinfo=timezone.utc),
        fallback_action="NO_ACTION",
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / JOURNAL_NAME).read_text(encoding="utf-8").splitlines()
    ]
    four = next(row for row in rows if row.get("strategy") == FOUR_HR and row.get("lane") == "demo")
    assert four["hint_missing_bars"] == 1
    assert four["last_checkpoint"] == "SUBMITTED"
    assert events[0]["stale_entry_shadow"]["would_block"] is True
    assert sealed["schema"] == "track-b-daily-heartbeat-v2"


def test_exception_inside_the_check_leaves_the_order_and_logs_error(tmp_path, monkeypatch, caplog):
    def _boom(*args, **kwargs):
        raise RuntimeError("shadow failed")

    monkeypatch.setattr(
        "context.wide_stop_demo_execution.stale_entry_check", _boom,
    )
    with caplog.at_level("ERROR"):
        broker, events = _submit(tmp_path, monkeypatch, DAY_22, _OCT_22, 29527.5)
    assert broker.execute_calls == 1
    assert broker.last_order.entry == 29546.5
    assert broker.last_order.stop == 29495.5
    assert broker.last_order.target == 30057.0
    row = next(item for item in events if item.get("collector_event") == "CANDIDATE")
    assert row["stale_entry_shadow"] == {"status": "ERROR"}
    assert 'stale_entry_shadow={"status":"ERROR"}' in caplog.text


def test_heartbeat_failure_does_not_change_the_order(tmp_path, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("heartbeat down")

    monkeypatch.setattr("context.track_b_daily_heartbeat.record_bar", _boom)
    broker, events = _submit(tmp_path, monkeypatch, DAY_22, _OCT_22, 29527.5)
    assert broker.execute_calls == 1
    assert broker.last_order.entry == 29546.5
    assert events[0]["collector_event"] == "CANDIDATE"


def _order_identity(order):
    return (
        order.direction, order.entry, order.stop, order.target, order.contracts,
        order.max_slippage_ticks, order.entry_slippage_tolerance_ticks_override,
        order.entry_execution_mode_override, order.contract_hint,
    )


def _tree(root: Path):
    found = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        text = path.read_text(encoding="utf-8")
        key = str(path.relative_to(root))
        if path.suffix == ".jsonl" or text.startswith("{") or text.startswith("["):
            rows = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    rows.append(line)
                    continue
                rows.append(_strip(parsed))
            found[key] = rows
        else:
            found[key] = text
    return found


def _strip(value):
    if isinstance(value, dict):
        return {key: _strip(item) for key, item in value.items() if key not in _SHADOW_ONLY}
    if isinstance(value, list):
        return [_strip(item) for item in value]
    return value


def test_byte_identity_of_oct_22_26_and_28(tmp_path, monkeypatch):
    cases = (
        (DAY_22, _OCT_22, 29527.5),
        (DAY_26, _OCT_26, 29294.25),
        (DAY_28, _OCT_28, 28802.0),
    )

    def _removed(*args, **kwargs):
        return False, "OK", {}

    for day, prices, close in cases:
        live_dir = tmp_path / f"live-{day.isoformat()}"
        quiet_dir = tmp_path / f"quiet-{day.isoformat()}"
        live_broker, live_events = _submit(live_dir, monkeypatch, day, prices, close)
        monkeypatch.setattr("context.wide_stop_demo_execution.stale_entry_check", _removed)
        quiet_broker, quiet_events = _submit(quiet_dir, monkeypatch, day, prices, close)
        monkeypatch.setattr(
            "context.wide_stop_demo_execution.stale_entry_check", stale_entry_check,
        )
        assert live_broker.execute_calls == quiet_broker.execute_calls == 1
        assert _order_identity(live_broker.last_order) == _order_identity(quiet_broker.last_order)
        assert _strip(live_events) == _strip(quiet_events)
        assert _tree(live_dir) == _tree(quiet_dir)


def test_placeoso_body_matches_itself_and_records_the_limit_already_sent(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENTRY_EXECUTION_MODE", "ioc_limit")
    order = _tradovate_order(
        entry_execution_mode_override="ioc_limit",
        entry_slippage_tolerance_ticks_override=8,
    )
    first, first_body = _tradovate_broker(monkeypatch, {"orderId": 10, "oso1Id": 20})
    second, second_body = _tradovate_broker(monkeypatch, {"orderId": 10, "oso1Id": 20})
    first.execute_bracket(order)
    second.execute_bracket(order)
    assert first_body["/order/placeOSO"] == second_body["/order/placeOSO"]
    assert first._last_entry_limit_price == first_body["/order/placeOSO"]["price"]
    assert "stale_entry_shadow" not in first_body["/order/placeOSO"]


def test_broker_ids_are_masked_to_the_last_three_digits_in_logs_only():
    assert _mask_broker_id("123456789") == "******789"
    assert _mask_broker_id(42) == "42"
    assert _mask_broker_id("MNQ-1234567") == "MNQ-****567"


def test_paper_rows_carry_the_same_shadow_field(tmp_path):
    audit = _observe(tmp_path)
    assert audit["stale_entry_shadow"]["status"] == "OK"
    assert audit["stale_entry_shadow"]["would_block"] is False
    shadow = collector._stale_entry_shadow(
        {"direction": "LONG", "entry": 29546.5, "stop": 29495.5}, 29527.5,
    )
    assert shadow["would_block"] is True
    assert shadow["reason"] == "DEMO_ENTRY_STALE_LEVEL"


def _session_stamps(strategy: str, day: date):
    start_clock = datetime.strptime("09:30" if strategy == FOUR_HR else "10:00", "%H:%M").time()
    cursor = datetime.combine(day, start_clock, tzinfo=ET)
    end = datetime.combine(day, datetime.strptime("15:55", "%H:%M").time(), tzinfo=ET)
    stamps = []
    while cursor <= end:
        stamps.append(cursor)
        cursor += timedelta(minutes=5)
    return stamps


def _seal_at(day: date):
    return datetime(day.year, day.month, day.day, 20, 5, tzinfo=timezone.utc)


def _rows(log_dir: Path):
    return [
        json.loads(line)
        for line in (log_dir / JOURNAL_NAME).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_full_window_with_no_signal_seals_no_setup(tmp_path):
    day = date(2026, 10, 20)
    for strategy in (FOUR_HR, THREE_TWO_TWO):
        stamps = _session_stamps(strategy, day)
        assert len(stamps) == bars_expected(strategy)
        for stamp in stamps:
            record_bar(
                tmp_path, current_ts=stamp, checked=True, signal=False,
                reason="NO_CANDIDATE", entry_recorded=False,
                strategy=strategy, lane="demo", hint_present=True,
            )
    seal_session(tmp_path, now=_seal_at(day), fallback_action="NO_ACTION")
    statuses = {row["strategy"]: row["status"] for row in _rows(tmp_path)}
    assert statuses[FOUR_HR] == "NO_SETUP"
    assert statuses[THREE_TWO_TWO] == "NO_SETUP"
    assert bars_expected(FOUR_HR) == 78
    assert bars_expected(THREE_TWO_TWO) == 72


def test_missing_bars_seal_incomplete_and_zero_checked_bars_seal_stopped(tmp_path):
    day = date(2026, 10, 20)
    incomplete = tmp_path / "incomplete"
    stopped = tmp_path / "stopped"
    for strategy in (FOUR_HR, THREE_TWO_TWO):
        record_bar(
            incomplete, current_ts=datetime(2026, 10, 20, 14, 5, tzinfo=timezone.utc),
            checked=True, signal=False, reason="NO_CANDIDATE", entry_recorded=False,
            strategy=strategy, lane="demo", hint_present=True,
        )
        record_bar(
            stopped, current_ts=datetime(2026, 10, 20, 14, 5, tzinfo=timezone.utc),
            checked=False, signal=False, reason="DEMO_CONFIG_BLOCKED", entry_recorded=False,
            strategy=strategy, lane="demo", hint_present=False,
        )
    seal_session(incomplete, now=_seal_at(day), fallback_action="NO_ACTION")
    seal_session(stopped, now=_seal_at(day), fallback_action="NO_ACTION")
    assert {row["status"] for row in _rows(incomplete)} == {"INCOMPLETE"}
    assert {row["status"] for row in _rows(stopped)} == {"STOPPED"}
    shared_incomplete = tmp_path / "shared-incomplete"
    shared_stopped = tmp_path / "shared-stopped"
    for name, source in (("shared-incomplete", incomplete), ("shared-stopped", stopped)):
        dest = tmp_path / name / "logs"
        dest.mkdir(parents=True)
        (dest / JOURNAL_NAME).write_text((source / JOURNAL_NAME).read_text(encoding="utf-8"), encoding="utf-8")
    now = datetime(2026, 10, 20, 21, 25, tzinfo=timezone.utc)
    assert track_b_heartbeat_proof(tmp_path / "shared-incomplete", now)["reason"] == "INCOMPLETE"
    assert track_b_heartbeat_proof(tmp_path / "shared-stopped", now)["reason"] == "STOPPED"


def test_three_two_two_row_is_present_and_a_missing_lane_fails_proof(tmp_path):
    day = date(2026, 10, 20)
    record_bar(
        tmp_path, current_ts=datetime(2026, 10, 20, 14, 5, tzinfo=timezone.utc),
        checked=True, signal=False, reason="NO_CANDIDATE", entry_recorded=False,
        strategy=THREE_TWO_TWO, lane="demo", hint_present=True,
    )
    seal_session(tmp_path, now=_seal_at(day), fallback_action="NO_ACTION")
    rows = _rows(tmp_path)
    assert any(row.get("strategy") == THREE_TWO_TWO and row.get("lane") == "demo" for row in rows)
    shared = tmp_path / "shared"
    (shared / "logs").mkdir(parents=True)
    (shared / "logs" / JOURNAL_NAME).write_text(
        (tmp_path / JOURNAL_NAME).read_text(encoding="utf-8"), encoding="utf-8",
    )
    report = track_b_heartbeat_proof(shared, datetime(2026, 10, 20, 21, 25, tzinfo=timezone.utc))
    assert report["passed"] is False
    assert report["reason"] == "MISSING_HEARTBEAT"


def test_duplicate_v2_rows_fail_the_daily_proof(tmp_path):
    day = date(2026, 10, 20)
    for strategy in (FOUR_HR, THREE_TWO_TWO):
        for stamp in _session_stamps(strategy, day):
            record_bar(
                tmp_path, current_ts=stamp, checked=True, signal=False,
                reason="NO_CANDIDATE", entry_recorded=False,
                strategy=strategy, lane="demo", hint_present=True,
            )
    seal_session(tmp_path, now=_seal_at(day), fallback_action="NO_ACTION")
    journal = tmp_path / JOURNAL_NAME
    first = journal.read_text(encoding="utf-8").splitlines()[0]
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(first + "\n")
    shared = tmp_path / "shared"
    (shared / "logs").mkdir(parents=True)
    (shared / "logs" / JOURNAL_NAME).write_text(journal.read_text(encoding="utf-8"), encoding="utf-8")
    report = track_b_heartbeat_proof(shared, datetime(2026, 10, 20, 21, 25, tzinfo=timezone.utc))
    assert report["passed"] is False
    assert report["reason"] == "DUPLICATE_HEARTBEAT"


def test_demo_config_block_is_checkpointed_and_still_raises(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    monkeypatch.delenv(execution.ROUTE_PROOF_PIN_ENV)
    with pytest.raises(ValueError, match="configuration blocked"):
        process_demo_five_min_bar(
            payload=_payload(date(2026, 10, 20), 20000.0),
            cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        )
    seal_session(
        tmp_path, now=datetime(2026, 10, 20, 20, 5, tzinfo=timezone.utc),
        fallback_action="NO_ACTION",
    )
    rows = _rows(tmp_path)
    assert {row["strategy"] for row in rows} == {FOUR_HR, THREE_TWO_TWO}
    assert {row["status"] for row in rows} == {"STOPPED"}
    assert {row["last_checkpoint"] for row in rows} == {"DEMO_CONFIG_BLOCKED"}


def test_pending_ambiguity_is_checkpointed_without_a_new_order(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _install(monkeypatch, _OCT_22)
    monkeypatch.setattr(demo._core, "_pending_reconcile", lambda **kwargs: None)
    monkeypatch.setattr(demo._core, "_resolve_position", lambda **kwargs: None)
    root = isolated_log_dir(tmp_path)
    state = demo_state.load_state(root, DAY_22)
    state["pending"] = {"candidate_key": "stuck", "strategy": FOUR_HR}
    demo_state.save_state(root, state)
    broker = _FakeBroker()
    events = process_demo_five_min_bar(
        payload=_payload(DAY_22, 29527.5), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        broker_factory=lambda: broker,
    )
    assert broker.execute_calls == 0
    assert events == []
    seal_session(
        tmp_path, now=datetime(2026, 10, 22, 20, 5, tzinfo=timezone.utc),
        fallback_action="NO_ACTION",
    )
    assert {row["last_checkpoint"] for row in _rows(tmp_path)} == {"DEMO_PENDING_AMBIGUITY"}
