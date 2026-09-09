"""Regression tests for the isolated Tradovate DEMO safety envelope."""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from config.settings import load_config
from context import wide_stop_demo_state as demo_state
from context import wide_stop_execution as execution
from context import wide_stop_forward_collector as collector
from context import wide_stop_ledger_paper as contract
import context.wide_stop_demo_runtime as demo
from execution.broker_interface import Fill, Position
from risk.risk_engine import DailyState, RiskResult, TradeSetup

DAY = date(2026, 9, 8)
EPOCH = "2026-09-08T00:00:00+00:00"
FOUR_HR = collector.FOUR_HR
THREE_TWO_TWO = collector.THREE_TWO_TWO


def _root(tmp_path):
    return demo.isolated_log_dir(tmp_path)


def _demo_env(monkeypatch):
    pins = {
        execution.ROUTE_ENV: execution.DEMO_ROUTE,
        execution.ROUTE_PROOF_PIN_ENV: execution.DEMO_ROUTE,
        "WIDE_STOP_LEDGER_MODE": "paper_sim",
        "WIDE_STOP_LEDGER_EPOCH_START": EPOCH,
        "BROKER": "tradovate",
        "TRADOVATE_ENV": "demo",
        "TRADOVATE_EXPECTED_ACCOUNT_ID": "12345",
        "TRADOVATE_ENTRY_EXECUTION_MODE": "ioc_limit",
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ": "8",
        "FIVE_MIN_FEED_ENABLED": "true",
        "LIVE_TRADING_ENABLED": "false",
    }
    for name, value in pins.items():
        monkeypatch.setenv(name, value)


def _cfg():
    cfg = copy.copy(load_config())
    cfg.wide_stop_ledger_mode = "paper_sim"
    cfg.wide_stop_ledger_epoch_start = EPOCH
    cfg.schedule_mode = "current"
    cfg.demo_execution_hold_sessions = []
    cfg.paper_eligible_sessions = ["new_york"]
    return cfg


def _payload(ts="2026-09-08T14:05:00+00:00"):
    return SimpleNamespace(
        ticker="MNQ1!", timestamp=ts, timeframe="5m",
        open=20_000.0, high=20_010.0, low=19_998.0, close=20_004.0, volume=1000,
    )


def _candidate(strategy=FOUR_HR):
    return {
        "direction": "LONG", "entry": 20_000.0, "stop": 19_950.0,
        "target": 20_070.0,
        "entry_time": datetime(2026, 9, 8, 14, 5, tzinfo=timezone.utc),
        "strategy": strategy,
    }


def _setup(strategy=FOUR_HR):
    return TradeSetup(
        direction="LONG", entry=20_000.0, stop=19_950.0, target=20_070.0,
        rr_ratio=1.4, strategy=strategy, instrument="MNQ", session="new_york",
        contracts=1, confluence_grade="B",
        entry_time=datetime(2026, 9, 8, 14, 5, tzinfo=timezone.utc),
    )


class _AlwaysApproveRisk:
    def __init__(self, *args, **kwargs):
        pass

    def validate(self, setup, daily):
        return RiskResult(result="APPROVED")


class _FakeBroker:
    def __init__(
        self, *, live=False, fill=None, positions=None, orders=None,
        snapshot_confirmed=True, snapshot_position=None, unreadable=False,
    ):
        self.is_live = live
        self.fill = fill
        self.positions = list(positions or [])
        self.orders = list(orders or [])
        self.snapshot_confirmed = snapshot_confirmed
        self.snapshot_position = snapshot_position
        self.unreadable = unreadable
        self.execute_calls = 0
        self.last_order = None
        self._last_order_ids = None
        self._last_position = None

    def _get(self, path):
        if self.unreadable:
            raise RuntimeError("read failed")
        if path == "/position/list":
            return list(self.positions)
        if path == "/order/list":
            return list(self.orders)
        return []

    def execute_bracket(self, order):
        self.execute_calls += 1
        self.last_order = order
        self._last_order_ids = {"instrument": "MNQ", "entry": 11, "target": 12, "stop": 13}
        if self.fill is not None:
            return self.fill
        return Fill(
            instrument="MNQ", direction=order.direction, contracts=1,
            entry_price=order.entry + 0.25, exit_price=None, exit_reason=None,
            result="OPEN", pnl_ticks=None, pnl_dollars=None,
            execution_audit={"post_fill_validation": {"accepted": True, "actual_entry": order.entry + 0.25}},
        )

    def get_position_snapshot(self):
        return self.snapshot_confirmed, self.snapshot_position

    def get_position(self):
        return self.snapshot_position

    def resolve_position(self):
        return None

    def flatten_position(self):
        return {"flat_confirmed": False, "close_fill_price": None}


def _patch_candidate(monkeypatch, strategy=FOUR_HR):
    decision = SimpleNamespace(
        decision="TRADE", setup=SimpleNamespace(strategy=strategy), failed_gates=[], reason="ok"
    )
    target = strategy

    def fake_eval(*, strategy: str, **kwargs):
        if strategy != target:
            return None, None, None
        return decision, object(), _candidate(strategy)

    monkeypatch.setattr(collector, "_evaluate_canonical_candidate", fake_eval)
    monkeypatch.setattr(collector, "_trade_setup", lambda state, out: _setup(target))
    monkeypatch.setattr(
        collector, "_lane_daily_state",
        lambda *args, **kwargs: DailyState(account_balance=5_000, account_peak_balance=5_000),
    )
    monkeypatch.setattr(demo._core, "RiskEngine", _AlwaysApproveRisk)


def test_demo_requires_all_safety_pins(monkeypatch):
    _demo_env(monkeypatch)
    assert execution.demo_config_errors(_cfg()) == []
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    assert "mnq_ioc_tolerance_not_8_ticks" in execution.demo_config_errors(_cfg())
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "8")
    monkeypatch.delenv(execution.ROUTE_PROOF_PIN_ENV)
    assert "wide_stop_execution_route_not_proof_pinned" in execution.demo_config_errors(_cfg())
    monkeypatch.setenv(execution.ROUTE_PROOF_PIN_ENV, execution.DEMO_ROUTE)
    monkeypatch.setenv("TRADOVATE_ENV", "live")
    assert "tradovate_env_not_demo" in execution.demo_config_errors(_cfg())


def test_demo_submits_one_contract_with_strategy_caps_and_postfill_guard(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _patch_candidate(monkeypatch, FOUR_HR)
    broker = _FakeBroker()
    events = demo.process_demo_five_min_bar(
        payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        for_date=DAY, broker_factory=lambda: broker,
    )
    assert broker.execute_calls == 1
    assert broker.last_order.contracts == 1
    assert broker.last_order.max_stop_ticks == 300.0
    assert broker.last_order.max_dollar_risk == 150.0
    assert broker.last_order.max_slippage_ticks == 8.0
    assert broker.last_order.post_fill_validation_required is True
    state = demo_state.load_state(_root(tmp_path), DAY)
    assert demo_state.confirmed_fills(state) == 1
    assert state["position"] is not None
    assert state["pending"] is None
    assert any(row.get("fill_status") == "OPEN" for row in events)


def test_demo_storage_is_separate_from_paper_ledger(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _patch_candidate(monkeypatch, FOUR_HR)
    broker = _FakeBroker()
    demo.process_demo_five_min_bar(
        payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        for_date=DAY, broker_factory=lambda: broker,
    )
    ledger = contract.ledger_for("MNQ", FOUR_HR)
    assert demo_state.state_path(_root(tmp_path)).exists()
    assert not (contract.journal_dir(tmp_path, ledger) / "journal.jsonl").exists()
    isolated_ledger = contract.journal_dir(_root(tmp_path), ledger)
    assert isolated_ledger.exists()


def test_ambiguous_submit_consumes_slot_and_blocks_retry(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _patch_candidate(monkeypatch, FOUR_HR)
    ambiguous = Fill(
        instrument="MNQ", direction="LONG", contracts=1, entry_price=20_000.0,
        exit_price=None, exit_reason="TRADOVATE_NO_ORDER_ID", result="CANCELLED",
        pnl_ticks=None, pnl_dollars=None,
    )
    first = _FakeBroker(fill=ambiguous)
    demo.process_demo_five_min_bar(
        payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        for_date=DAY, broker_factory=lambda: first,
    )
    state = demo_state.load_state(_root(tmp_path), DAY)
    assert state["pending"] is not None
    assert demo_state.reserved_slots(state) == 1
    assert demo_state.confirmed_fills(state) == 0

    second = _FakeBroker(snapshot_confirmed=True, snapshot_position=None)
    demo.process_demo_five_min_bar(
        payload=_payload("2026-09-08T14:10:00+00:00"), cfg=_cfg(), bars_5m=[],
        log_dir=tmp_path, for_date=DAY, broker_factory=lambda: second,
    )
    assert second.execute_calls == 0
    assert demo_state.reserved_slots(demo_state.load_state(_root(tmp_path), DAY)) == 1


def test_definite_ioc_no_fill_releases_slot(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _patch_candidate(monkeypatch, FOUR_HR)
    no_fill = Fill(
        instrument="MNQ", direction="LONG", contracts=1, entry_price=20_000.0,
        exit_price=None, exit_reason="ENTRY_NOT_FILLED", result="CANCELLED",
        pnl_ticks=None, pnl_dollars=None,
    )
    broker = _FakeBroker(fill=no_fill)
    demo.process_demo_five_min_bar(
        payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        for_date=DAY, broker_factory=lambda: broker,
    )
    state = demo_state.load_state(_root(tmp_path), DAY)
    assert demo_state.slots_used(state) == 0
    assert state["pending"] is None


def test_three_slots_block_a_fourth_demo_trade():
    state = demo_state.empty_state(DAY)
    for i in range(3):
        ok, _ = demo_state.reserve_slot(state, f"candidate-{i}", FOUR_HR)
        assert ok
        demo_state.confirm_slot(state, f"candidate-{i}", FOUR_HR)
    ok, reason = demo_state.reserve_slot(state, "candidate-four", THREE_TWO_TWO)
    assert not ok
    assert reason == "demo_max_trades_per_day"


def test_pending_state_recovers_confirmed_mnq_position_after_restart(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    root = _root(tmp_path)
    state = demo_state.empty_state(DAY)
    key = "crash-candidate"
    state["pending"] = {
        "candidate_key": key, "strategy": FOUR_HR, "instrument": "MNQ",
        "session": "new_york", "direction": "LONG", "planned_entry": 20_000.0,
        "entry": 20_000.0, "stop": 19_950.0, "target": 20_070.0,
        "rr_ratio": 1.4, "contracts": 1,
        "entry_time": "2026-09-08T14:05:00+00:00",
        "client_order_id": "ws-crash", "broker_order_ids": {},
        "trading_date": DAY.isoformat(),
    }
    demo_state.reserve_slot(state, key, FOUR_HR)
    demo_state.save_state(root, state)
    broker_position = Position(
        instrument="MNQ", direction="LONG", entry_price=20_000.25,
        stop=19_950.0, target=20_070.0, quantity=1, open=True,
    )
    broker = _FakeBroker(snapshot_confirmed=True, snapshot_position=broker_position)
    event = demo._pending_reconcile(
        cfg=_cfg(), log_dir=root, for_date=DAY, day=DAY,
        state=state, broker_factory=lambda: broker,
    )
    assert event is not None
    assert event["lane_result"] == "RECOVERED_PENDING_POSITION"
    restored = demo_state.load_state(root, DAY)
    assert restored["pending"] is None
    assert restored["position"]["entry"] == 20_000.25
    assert demo_state.confirmed_fills(restored) == 1


def test_corrupt_existing_demo_state_fails_closed(tmp_path):
    root = _root(tmp_path)
    path = demo_state.state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{bad-json")
    with pytest.raises(demo_state.DemoStateError):
        demo_state.load_state(root, DAY)


def test_eod_broad_flatten_refuses_unexpected_working_order():
    position = {"broker_order_ids": {"entry": 11, "target": 12, "stop": 13}}
    broker = _FakeBroker(
        positions=[{"netPos": 1}],
        orders=[
            {"id": 12, "ordStatus": "Working"},
            {"id": 13, "ordStatus": "Working"},
            {"id": 99, "ordStatus": "Working"},
        ],
    )
    ok, reason = demo._eod_exclusive_gate(broker, position)
    assert not ok
    assert reason == "eod_unexpected_working_orders_present"


def test_live_broker_object_is_rejected_even_if_env_pins_say_demo(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _patch_candidate(monkeypatch, FOUR_HR)
    broker = _FakeBroker(live=True)
    with pytest.raises(ValueError, match="refuses a live broker"):
        demo.process_demo_five_min_bar(
            payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
            for_date=DAY, broker_factory=lambda: broker,
        )


def test_daily_22_has_no_demo_candidate_path():
    assert set(collector._NATIVE) == {FOUR_HR, THREE_TWO_TWO}
    assert "daily_22_continuation" not in collector._NATIVE
