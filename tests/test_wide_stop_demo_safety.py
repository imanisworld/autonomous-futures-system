"""Safety regressions for shared 4HR/3-2-2 limits and Tradovate DEMO routing."""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from config.settings import load_config
from context import wide_stop_execution as execution
from context import wide_stop_forward_collector as collector
from context import wide_stop_ledger_paper as contract
from context import wide_stop_portfolio as portfolio
import context.wide_stop_demo_runtime as demo
from execution.broker_interface import Fill, Position
from risk.risk_engine import DailyState, RiskResult, TradeSetup

DAY = date(2026, 9, 8)
EPOCH = "2026-09-08T00:00:00+00:00"
FOUR_HR = collector.FOUR_HR
THREE_TWO_TWO = collector.THREE_TWO_TWO


def _demo_env(monkeypatch):
    values = {
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
    for key, value in values.items():
        monkeypatch.setenv(key, value)


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
        "direction": "LONG",
        "entry": 20_000.0,
        "stop": 19_950.0,
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


class _FakeDemoBroker:
    def __init__(
        self, *, fill=None, live=False, positions=None, orders=None,
        snapshot_confirmed=True, snapshot_position=None, unreadable=False,
    ):
        self.is_live = live
        self.fill = fill
        self.positions = [] if positions is None else positions
        self.orders = [] if orders is None else orders
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
            execution_audit={
                "post_fill_validation": {
                    "accepted": True, "actual_entry": order.entry + 0.25,
                }
            },
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

    def fake_eval(*, strategy: str, **kwargs):
        if strategy != target:
            return None, None, None
        return decision, object(), _candidate(strategy)

    target = strategy
    monkeypatch.setattr(collector, "_evaluate_canonical_candidate", fake_eval)
    monkeypatch.setattr(collector, "_trade_setup", lambda state, out: _setup(target))
    monkeypatch.setattr(
        collector,
        "_lane_daily_state",
        lambda *args, **kwargs: DailyState(account_balance=5_000, account_peak_balance=5_000),
    )
    monkeypatch.setattr(demo, "RiskEngine", _AlwaysApproveRisk)


def test_demo_route_requires_exact_proof_pins(monkeypatch):
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


def test_shared_daily_slots_are_family_wide_and_crash_conservative(tmp_path):
    ok, _ = portfolio.reserve_daily_slot(tmp_path, DAY, "4hr-a", route="paper_sim")
    assert ok
    portfolio.confirm_daily_slot(tmp_path, DAY, "4hr-a")
    ok, _ = portfolio.reserve_daily_slot(tmp_path, DAY, "322-a", route=execution.DEMO_ROUTE)
    assert ok
    # Leave 322-a RESERVED to model a crash/ambiguous submission.
    ok, _ = portfolio.reserve_daily_slot(tmp_path, DAY, "4hr-b", route="paper_sim")
    assert ok
    portfolio.confirm_daily_slot(tmp_path, DAY, "4hr-b")
    ok, reason = portfolio.reserve_daily_slot(tmp_path, DAY, "fourth", route="paper_sim")
    assert not ok
    assert reason == "portfolio_max_trades_per_day"
    snap = portfolio.admission_snapshot(tmp_path, DAY)
    assert snap["confirmed_fills_today"] == 2
    assert snap["reserved_slots_today"] == 1
    assert snap["daily_slots_used"] == 3


def test_combined_day_strategy_risk_never_exceeds_existing_150_plus_300_caps(tmp_path):
    ledger = contract.LEDGERS["wide_stop_4k"]
    path = contract.journal_dir(tmp_path, ledger) / "forward_collector_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "position": {
            "instrument": "MNQ", "entry": 20_000.0, "stop": 19_925.0,
            "contracts": 1,
        }
    }))
    assert portfolio.combined_open_risk_dollars(tmp_path) == 150.0
    ok, combined = portfolio.proposed_risk_allowed(tmp_path, 300.0)
    assert ok and combined == 450.0
    ok, combined = portfolio.proposed_risk_allowed(tmp_path, 300.50)
    assert not ok and combined == 450.50


def test_demo_order_carries_one_contract_and_actual_fill_guards(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _patch_candidate(monkeypatch, FOUR_HR)
    broker = _FakeDemoBroker()
    events = demo.process_demo_five_min_bar(
        payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        for_date=DAY, broker_factory=lambda: broker,
    )
    assert broker.execute_calls == 1
    assert broker.last_order.contracts == 1
    assert broker.last_order.max_stop_ticks == 300.0
    assert broker.last_order.max_dollar_risk == 150.0
    assert broker.last_order.max_slippage_ticks == 8.0
    assert broker.last_order.min_rr_ratio == 1.0
    assert broker.last_order.post_fill_validation_required is True
    assert portfolio.confirmed_fill_count(tmp_path, DAY) == 1
    assert portfolio.reserved_count(tmp_path, DAY) == 0
    assert any(row.get("fill_status") == "OPEN" for row in events)


def test_ambiguous_submit_keeps_pending_state_and_consumes_slot(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _patch_candidate(monkeypatch, FOUR_HR)
    ambiguous = Fill(
        instrument="MNQ", direction="LONG", contracts=1, entry_price=20_000.0,
        exit_price=None, exit_reason="TRADOVATE_NO_ORDER_ID", result="CANCELLED",
        pnl_ticks=None, pnl_dollars=None,
    )
    broker = _FakeDemoBroker(fill=ambiguous)
    demo.process_demo_five_min_bar(
        payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        for_date=DAY, broker_factory=lambda: broker,
    )
    ledger = contract.ledger_for("MNQ", FOUR_HR)
    state = demo._load_state(tmp_path, ledger)
    assert state["pending"] is not None
    assert portfolio.reserved_count(tmp_path, DAY, route=execution.DEMO_ROUTE) == 1
    assert portfolio.confirmed_fill_count(tmp_path, DAY) == 0

    # Next invocation cannot submit another order while outcome is unresolved.
    second = _FakeDemoBroker(snapshot_confirmed=True, snapshot_position=None)
    demo.process_demo_five_min_bar(
        payload=_payload("2026-09-08T14:10:00+00:00"), cfg=_cfg(), bars_5m=[],
        log_dir=tmp_path, for_date=DAY, broker_factory=lambda: second,
    )
    assert second.execute_calls == 0


def test_definitive_ioc_no_fill_releases_reserved_slot(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _patch_candidate(monkeypatch, FOUR_HR)
    no_fill = Fill(
        instrument="MNQ", direction="LONG", contracts=1, entry_price=20_000.0,
        exit_price=None, exit_reason="ENTRY_NOT_FILLED", result="CANCELLED",
        pnl_ticks=None, pnl_dollars=None,
    )
    broker = _FakeDemoBroker(fill=no_fill)
    demo.process_demo_five_min_bar(
        payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        for_date=DAY, broker_factory=lambda: broker,
    )
    assert portfolio.daily_slots_used(tmp_path, DAY) == 0
    ledger = contract.ledger_for("MNQ", FOUR_HR)
    assert demo._load_state(tmp_path, ledger)["pending"] is None


def test_pending_crash_state_can_recover_confirmed_mnq_position(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    ledger = contract.ledger_for("MNQ", FOUR_HR)
    key = "crash-candidate"
    pending = {
        "candidate_key": key, "strategy": FOUR_HR, "instrument": "MNQ",
        "session": "new_york", "direction": "LONG", "planned_entry": 20_000.0,
        "entry": 20_000.0, "stop": 19_950.0, "target": 20_070.0, "rr_ratio": 1.4,
        "contracts": 1, "entry_time": "2026-09-08T14:05:00+00:00",
        "client_order_id": "ws-crash", "broker_order_ids": {},
        "trading_date": DAY.isoformat(),
    }
    state = demo._empty_state()
    state["pending"] = pending
    demo._save_state(tmp_path, ledger, state)
    portfolio.reserve_daily_slot(tmp_path, DAY, key, route=execution.DEMO_ROUTE)
    recovered_position = Position(
        instrument="MNQ", direction="LONG", entry_price=20_000.25,
        stop=19_950.0, target=20_070.0, quantity=1, open=True,
    )
    broker = _FakeDemoBroker(snapshot_confirmed=True, snapshot_position=recovered_position)
    event = demo._pending_reconcile(
        cfg=_cfg(), ledger=ledger, log_dir=tmp_path, for_date=DAY,
        broker_factory=lambda: broker,
    )
    assert event is not None
    assert event["lane_result"] == "RECOVERED_PENDING_POSITION"
    restored = demo._load_state(tmp_path, ledger)
    assert restored["pending"] is None
    assert restored["position"]["entry"] == 20_000.25
    assert portfolio.confirmed_fill_count(tmp_path, DAY) == 1


def test_eod_flatten_gate_refuses_unexpected_account_orders():
    position = {
        "broker_order_ids": {"entry": 11, "target": 12, "stop": 13},
    }
    broker = _FakeDemoBroker(
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


def test_demo_refuses_live_broker(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _patch_candidate(monkeypatch, FOUR_HR)
    broker = _FakeDemoBroker(live=True)
    with pytest.raises(ValueError, match="refuses a live broker"):
        demo.process_demo_five_min_bar(
            payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
            for_date=DAY, broker_factory=lambda: broker,
        )


def test_daily_22_is_not_demo_eligible():
    assert set(collector._NATIVE) == {FOUR_HR, THREE_TWO_TWO}
    assert "daily_22_continuation" not in collector._NATIVE
