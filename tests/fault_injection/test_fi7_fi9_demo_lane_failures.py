"""FI-7 / FI-8 / FI-9 — failure branches of the ARMED wide-stop demo lane
(#950 audit gaps 6 and 9).

FI-7 is coverage: each branch already fails closed (blocks new entries and
never double-submits) and had no test. FI-8 and FI-9 are known defects.

Recorded, not tested (operator D3, 2026-09-24): the demo lane's daily loss and
drawdown checks read ONE strategy's lane journal, so a 4HR loss does not count
toward 3-2-2's limit. Account-wide exposure is bounded only by the 3-slot cap
and the $450 per-trade planned-risk cap. Known design limit, not a defect here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

import context.wide_stop_demo_runtime as demo
from context import wide_stop_demo_state as demo_state
from context import wide_stop_forward_collector as collector
from execution.broker_interface import Position
from risk.risk_engine import DailyState
from tests.fault_injection._harness import FaultRecord
from tests.fault_injection._p2_harness import (
    DAY, FOUR_HR, FakeDemoBroker, demo_cfg, demo_env, demo_payload, demo_root, eod_broker,
    fixture_bars, patch_candidate, pending_record, require, run_fixture_bar,
    save_open_demo_position,
)

EOD = datetime(2026, 9, 8, 20, 2, tzinfo=timezone.utc)  # 16:02 ET
NEXT_BAR = "2026-09-08T14:10:00+00:00"


def _bar(tmp_path, broker, ts=None, for_date=DAY):
    payload = demo_payload(ts) if ts else demo_payload()
    return demo.process_demo_five_min_bar(
        payload=payload, cfg=demo_cfg(), bars_5m=[], log_dir=tmp_path,
        for_date=for_date, broker_factory=lambda: broker,
    )


def _eod(tmp_path, broker):
    return demo.run_demo_eod_fallback(
        cfg=demo_cfg(), log_dir=tmp_path, now=EOD, broker_factory=lambda: broker,
    )


def _state(tmp_path, day=DAY):
    return demo_state.load_state(demo_root(tmp_path), day)


def _save_pending(tmp_path, record=None):
    root = demo_root(tmp_path)
    state = demo_state.empty_state(DAY)
    record = record or pending_record()
    state["pending"] = record
    demo_state.reserve_slot(state, record["candidate_key"], FOUR_HR)
    demo_state.save_state(root, state)
    return root


def _long_position(**overrides) -> Position:
    fields = dict(
        instrument="MNQ", direction="LONG", entry_price=20_000.25,
        stop=19_950.0, target=20_070.0, quantity=1, open=True,
    )
    fields.update(overrides)
    return Position(**fields)


# ── FI-7a / 7b: EOD fallback cannot confirm the exit ──────────────────────────
def test_fi7a_eod_flatten_unconfirmed_keeps_position(tmp_path, monkeypatch):
    demo_env(monkeypatch)
    save_open_demo_position(tmp_path)
    broker = eod_broker(flatten_ok=False)
    result = _eod(tmp_path, broker)
    assert result["ok"] is False
    assert result["reason"] == "UNRESOLVED_EOD_FLATTEN"
    assert broker.flatten_calls == 1
    assert _state(tmp_path)["position"] is not None


def test_fi7b_eod_broker_mismatch_does_not_flatten(tmp_path, monkeypatch):
    demo_env(monkeypatch)
    save_open_demo_position(tmp_path)
    broker = eod_broker()
    broker.snapshot_position = _long_position(direction="SHORT")
    result = _eod(tmp_path, broker)
    assert result["ok"] is False
    assert result["reason"] == "UNRESOLVED_EOD_BROKER_MISMATCH"
    assert broker.flatten_calls == 0
    assert _state(tmp_path)["position"] is not None


# ── FI-7c..7f: an unresolved pending submission blocks every new entry ────────
class _RaisingSnapshotBroker(FakeDemoBroker):
    def get_position_snapshot(self):
        raise ConnectionError("position read failed")


def _assert_pending_blocks(tmp_path, broker):
    events = _bar(tmp_path, broker, NEXT_BAR)
    state = _state(tmp_path)
    assert broker.execute_calls == 0
    assert state["pending"] is not None
    assert state["position"] is None
    assert demo_state.reserved_slots(state) == 1
    assert not any(e.get("lane_result") == "RECOVERED_PENDING_POSITION" for e in events)


@pytest.mark.parametrize("kind", ["unconfirmed", "raises"])
def test_fi7c_pending_with_unreadable_snapshot_blocks_entries(tmp_path, monkeypatch, kind):
    demo_env(monkeypatch)
    patch_candidate(monkeypatch, FOUR_HR)
    _save_pending(tmp_path)
    broker = (FakeDemoBroker(snapshot_confirmed=False) if kind == "unconfirmed"
              else _RaisingSnapshotBroker())
    _assert_pending_blocks(tmp_path, broker)


@pytest.mark.parametrize("mismatch", [{"direction": "SHORT"}, {"quantity": 2}],
                         ids=["direction", "quantity"])
def test_fi7d_pending_with_mismatched_broker_position_blocks_entries(tmp_path, monkeypatch, mismatch):
    demo_env(monkeypatch)
    patch_candidate(monkeypatch, FOUR_HR)
    _save_pending(tmp_path)
    broker = FakeDemoBroker(snapshot_confirmed=True, snapshot_position=_long_position(**mismatch))
    _assert_pending_blocks(tmp_path, broker)


def test_fi7e_pending_with_flat_broker_keeps_the_block(tmp_path, monkeypatch):
    demo_env(monkeypatch)
    patch_candidate(monkeypatch, FOUR_HR)
    _save_pending(tmp_path)
    _assert_pending_blocks(tmp_path, FakeDemoBroker(snapshot_confirmed=True, snapshot_position=None))


class _RaisingSubmitBroker(FakeDemoBroker):
    def execute_bracket(self, order):
        self.execute_calls += 1
        raise TimeoutError("placeOSO read timed out")


def test_fi7f_execute_bracket_raising_after_pending_save_never_resubmits(tmp_path, monkeypatch):
    demo_env(monkeypatch)
    patch_candidate(monkeypatch, FOUR_HR)
    first = _RaisingSubmitBroker()
    with pytest.raises(TimeoutError):
        _bar(tmp_path, first)
    require(first.execute_calls == 1, "the submit was attempted once")
    state = _state(tmp_path)
    assert state["pending"] is not None
    assert demo_state.reserved_slots(state) == 1

    second = FakeDemoBroker(snapshot_confirmed=True, snapshot_position=None)
    _bar(tmp_path, second, NEXT_BAR)
    assert second.execute_calls == 0


# ── FI-7g: a day rollover with an open position refuses to trade ──────────────
def test_fi7g_rollover_with_open_position_raises_and_submits_nothing(tmp_path, monkeypatch):
    demo_env(monkeypatch)
    patch_candidate(monkeypatch, FOUR_HR)
    save_open_demo_position(tmp_path)
    broker = FakeDemoBroker()
    with pytest.raises(demo_state.DemoStateError):
        _bar(tmp_path, broker, "2026-09-09T14:05:00+00:00", for_date=DAY + timedelta(days=1))
    assert broker.execute_calls == 0


# ── FI-7h..7j: entry gates with real (unmocked) rejections ────────────────────
def _blocks(events) -> list:
    return [e.get("lane_failed_rule") for e in events if e.get("lane_result") == "BLOCKED_DEMO"]


def test_fi7h_open_demo_position_blocks_a_second_entry(tmp_path, monkeypatch):
    demo_env(monkeypatch)
    patch_candidate(monkeypatch, FOUR_HR)
    save_open_demo_position(tmp_path)
    broker = FakeDemoBroker()
    events = _bar(tmp_path, broker, NEXT_BAR)
    assert broker.execute_calls == 0
    assert "demo_same_instrument_position_open" in _blocks(events)


def test_fi7i_real_risk_engine_at_daily_loss_limit_blocks_entry(config, tmp_path, monkeypatch):
    """Unmocked RiskEngine on the fixture that otherwise submits (see the FI-5
    control): realized P&L at the loss limit must stop the order."""
    monkeypatch.setattr(
        collector, "_lane_daily_state",
        lambda *a, **k: DailyState(
            account_balance=5_000, account_peak_balance=5_000, realized_pnl_dollars=-100_000.0,
        ),
    )
    broker = FakeDemoBroker()
    events = run_fixture_bar(config, tmp_path, monkeypatch, fixture_bars(), broker)
    assert broker.execute_calls == 0
    assert "max_daily_loss" in _blocks(events)


def test_fi7j_planned_risk_over_450_blocks_entry(tmp_path, monkeypatch):
    demo_env(monkeypatch)
    patch_candidate(monkeypatch, FOUR_HR)
    from tests.test_wide_stop_demo_runtime import _setup

    wide = _setup(FOUR_HR)
    wide.stop = 19_700.0  # 300 points x $2 = $600 planned risk
    monkeypatch.setattr(collector, "_trade_setup", lambda state, out: wide)
    broker = FakeDemoBroker()
    events = _bar(tmp_path, broker)
    assert broker.execute_calls == 0
    assert "demo_combined_open_risk" in _blocks(events)


# ── FI-8: a position recovered after restart is never exited at EOD ───────────
def test_fi8_recovered_position_is_flattened_at_eod(tmp_path, monkeypatch):
    demo_env(monkeypatch)
    _save_pending(tmp_path)
    broker = eod_broker()  # broker holds the filled entry + working stop/target
    result = _eod(tmp_path, broker)
    require(result.get("recovery") is not None or result.get("reason") != "PENDING_SUBMISSION_UNRESOLVED",
            "the pending record was recovered into a position")
    rec = FaultRecord(
        case="FI-8 restart-recovered demo position at 16:02 ET",
        initial_journal="demo state: pending submission, broker_order_ids={}",
        initial_broker="MNQ LONG 1 open; stop/target orders Working",
        injected_failure="process restarted between submit and fill record",
        expected_safe_state="the recovered position is flattened at EOD",
        actual_state=(
            f"result={result.get('action')}/{result.get('reason')} "
            f"flatten_calls={broker.flatten_calls} "
            f"position_left={_state(tmp_path)['position'] is not None}"
        ),
    )
    assert broker.flatten_calls == 1, str(rec)
    assert _state(tmp_path)["position"] is None, str(rec)


def _recover(tmp_path, broker):
    """Next-bar recovery of the saved pending record (as after a restart)."""
    root = _save_pending(tmp_path)
    event = demo._pending_reconcile(
        cfg=demo_cfg(), log_dir=root, for_date=DAY, day=DAY,
        state=demo_state.load_state(root, DAY), broker_factory=lambda: broker,
    )
    require(event is not None and event["lane_result"] == "RECOVERED_PENDING_POSITION",
            "the pending record was recovered into a position")
    return _state(tmp_path)["position"]


def test_fi8_order_appearing_after_recovery_still_blocks_eod_flatten(tmp_path, monkeypatch):
    demo_env(monkeypatch)
    position = _recover(tmp_path, eod_broker())
    require(position.get("recovered_working_order_ids") == [12, 13], "snapshot taken")
    broker = eod_broker()
    broker.orders.append({"id": 99, "ordStatus": "Working"})  # not ours
    result = _eod(tmp_path, broker)
    assert result["reason"] == "UNRESOLVED_EOD_ACCOUNT_NOT_EXCLUSIVE"
    assert broker.flatten_calls == 0
    assert _state(tmp_path)["position"] is not None


def test_fi8_unreadable_orders_at_recovery_keeps_eod_blocked(tmp_path, monkeypatch):
    demo_env(monkeypatch)
    blind = eod_broker()
    blind.unreadable = True  # /order/list (and /position/list) raise; snapshot still confirms
    position = _recover(tmp_path, blind)
    assert "recovered_working_order_ids" not in position
    broker = eod_broker()
    result = _eod(tmp_path, broker)
    assert result["reason"] == "UNRESOLVED_EOD_ACCOUNT_NOT_EXCLUSIVE"
    assert broker.flatten_calls == 0


def test_fi8_recovered_position_without_working_orders_is_flattened(tmp_path, monkeypatch):
    demo_env(monkeypatch)
    _save_pending(tmp_path)
    broker = eod_broker()
    broker.orders = []  # protective children gone: the position is naked
    result = _eod(tmp_path, broker)
    assert broker.flatten_calls == 1
    assert result["action"] == "DEMO_POSITION_RESOLVED"
    assert _state(tmp_path)["position"] is None


# ── FI-9: the lane dies with only a log line ──────────────────────────────────
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="KNOWN DEFECT FI-9")
def test_fi9_dead_demo_lane_sends_an_operational_alert(tmp_path, monkeypatch, caplog):
    from context import five_min_feed

    demo_env(monkeypatch)
    save_open_demo_position(tmp_path)  # yesterday's (DAY) open position
    sent: list[str] = []
    monkeypatch.setattr(
        "notifications.discord_notifier.send_operational_alert",
        lambda cfg, msg, *a, **k: sent.append(msg),
    )
    monkeypatch.setattr(
        "notifications.system_notifier.notify_system",
        lambda msg, *a, **k: sent.append(msg) or type("R", (), {"sent": False, "reason": "test"})(),
    )
    next_day = DAY + timedelta(days=1)
    with caplog.at_level(logging.WARNING, logger=five_min_feed.logger.name):
        five_min_feed.record_five_min(
            demo_payload("2026-09-09T14:05:00+00:00"), str(tmp_path), for_date=next_day,
        )
    demo_failures = [
        r for r in caplog.records if "wide-stop demo lane failed closed" in r.getMessage()
    ]
    require(len(demo_failures) == 1, "the demo lane raised inside record_five_min")
    require(
        demo_failures[0].exc_info is not None
        and isinstance(demo_failures[0].exc_info[1], demo_state.DemoStateError),
        "the failure is the rollover DemoStateError",
    )
    rec = FaultRecord(
        case="FI-9 demo lane dead on rollover",
        initial_journal="demo state: prior-day open position",
        initial_broker="n/a",
        injected_failure="next-day MNQ 5m bar hits the rollover DemoStateError",
        expected_safe_state="an operational alert is sent",
        actual_state=f"alerts_sent={sent}",
    )
    assert sent, str(rec)
