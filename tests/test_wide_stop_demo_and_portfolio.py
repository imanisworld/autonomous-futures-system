"""Safety regressions for wide-stop shared limits + Tradovate demo routing."""
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
from context.wide_stop_demo_runtime import process_demo_five_min_bar
from context.wide_stop_forward_router import process_paper_five_min_bar
from execution.broker_interface import Fill
from risk.risk_engine import DailyState, RiskResult, TradeSetup

DAY = date(2026, 9, 8)
EPOCH = "2026-09-08T00:00:00+00:00"
FOUR_HR = collector.FOUR_HR
THREE_TWO_TWO = collector.THREE_TWO_TWO


def _cfg():
    cfg = copy.copy(load_config())
    cfg.wide_stop_ledger_mode = "paper_sim"
    cfg.wide_stop_ledger_epoch_start = EPOCH
    cfg.schedule_mode = "current"
    cfg.demo_execution_hold_sessions = []
    cfg.paper_eligible_sessions = ["new_york"]
    return cfg


def _demo_env(monkeypatch):
    values = {
        execution.ROUTE_ENV: execution.DEMO_ROUTE,
        execution.ROUTE_PROOF_PIN_ENV: execution.DEMO_ROUTE,
        "WIDE_STOP_LEDGER_MODE": "paper_sim",
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


def _payload():
    return SimpleNamespace(
        ticker="MNQ1!",
        timestamp="2026-09-08T14:05:00+00:00",
        timeframe="5m",
        open=20_000.0,
        high=20_010.0,
        low=19_998.0,
        close=20_004.0,
        volume=1000,
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
        direction="LONG",
        entry=20_000.0,
        stop=19_950.0,
        target=20_070.0,
        rr_ratio=1.4,
        strategy=strategy,
        instrument="MNQ",
        session="new_york",
        contracts=1,
        confluence_grade="B",
        entry_time=datetime.now(timezone.utc),
    )


class _AlwaysApproveRisk:
    def __init__(self, *args, **kwargs):
        pass

    def validate(self, setup, daily):
        return RiskResult(result="APPROVED")


class _FakeDemoBroker:
    def __init__(self, *, live=False, account_rows=None, order_rows=None, unreadable=False):
        self.is_live = live
        self.account_rows = [] if account_rows is None else account_rows
        self.order_rows = [] if order_rows is None else order_rows
        self.unreadable = unreadable
        self.execute_calls = 0
        self.last_order = None
        self._last_order_ids = None

    def _get(self, path):
        if self.unreadable:
            raise RuntimeError("read failed")
        if path == "/position/list":
            return list(self.account_rows)
        if path == "/order/list":
            return list(self.order_rows)
        return []

    def execute_bracket(self, order):
        self.execute_calls += 1
        self.last_order = order
        self._last_order_ids = {
            "instrument": "MNQ",
            "entry": 11,
            "target": 12,
            "stop": 13,
        }
        return Fill(
            instrument="MNQ",
            direction=order.direction,
            contracts=1,
            entry_price=order.entry + 0.25,
            exit_price=None,
            exit_reason=None,
            result="OPEN",
            pnl_ticks=None,
            pnl_dollars=None,
            execution_audit={"post_fill_validation": {"accepted": True, "actual_entry": order.entry + 0.25}},
        )

    def resolve_position(self):
        return None

    def get_position(self):
        return None

    def flatten_position(self):
        return {"flat_confirmed": False, "close_fill_price": None}


def _patch_candidate(monkeypatch, strategy=FOUR_HR):
    import context.wide_stop_demo_runtime as demo
    import context.wide_stop_forward_router as paper

    decision = SimpleNamespace(
        decision="TRADE",
        setup=SimpleNamespace(strategy=strategy),
        failed_gates=[],
        reason="ok",
    )

    def fake_eval(*, strategy: str, **kwargs):
        if strategy != strategy_target:
            return None, None, None
        return decision, object(), _candidate(strategy)

    strategy_target = strategy
    monkeypatch.setattr(collector, "_evaluate_canonical_candidate", fake_eval)
    monkeypatch.setattr(collector, "_trade_setup", lambda state, out: _setup(strategy_target))
    monkeypatch.setattr(
        collector,
        "_lane_daily_state",
        lambda *args, **kwargs: DailyState(account_balance=5_000, account_peak_balance=5_000),
    )
    monkeypatch.setattr(demo, "RiskEngine", _AlwaysApproveRisk)
    monkeypatch.setattr(paper, "RiskEngine", _AlwaysApproveRisk)


def test_demo_route_requires_exact_pins(monkeypatch):
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


def test_shared_fill_counter_is_family_wide(tmp_path):
    assert portfolio.filled_count(tmp_path, DAY) == 0
    assert portfolio.register_fill(tmp_path, DAY) == 1
    assert portfolio.register_fill(tmp_path, DAY) == 2
    assert portfolio.register_fill(tmp_path, DAY) == 3
    assert portfolio.admission_snapshot(tmp_path, DAY)["filled_trades_today"] == 3


def test_combined_open_risk_counts_both_paper_ledgers(tmp_path):
    positions = [
        (contract.LEDGERS["wide_stop_4k"], 20_000.0, 19_950.0),
        (contract.LEDGERS["wide_stop_6k"], 20_100.0, 19_950.0),
    ]
    for ledger, entry, stop in positions:
        path = contract.journal_dir(tmp_path, ledger) / "forward_collector_state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "position": {
                "instrument": "MNQ", "entry": entry, "stop": stop, "contracts": 1
            }
        }))
    snap = portfolio.admission_snapshot(tmp_path, DAY)
    assert snap["open_positions"] == 2
    assert snap["combined_open_risk_dollars"] == 400.0


def test_portfolio_drawdown_can_be_viewed_at_1500_and_5000(tmp_path):
    portfolio.record_outcome(
        log_dir=tmp_path, for_date=DAY, instrument="MNQ", session="new_york",
        strategy=FOUR_HR, result="LOSS", entry_price=100, exit_price=95,
        exit_reason="STOP_HIT", pnl_ticks=-20, net_pnl_dollars=-100,
    )
    at_1500 = portfolio.performance(tmp_path, 1_500)
    at_5000 = portfolio.performance(tmp_path, 5_000)
    assert at_1500["max_drawdown"] == 100.0
    assert at_1500["max_drawdown_percent"] == pytest.approx(6.67, abs=0.01)
    assert at_5000["max_drawdown_percent"] == 2.0


def test_demo_submits_one_contract_with_8_tick_post_fill_guard(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _patch_candidate(monkeypatch, FOUR_HR)
    broker = _FakeDemoBroker()

    events = process_demo_five_min_bar(
        payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        for_date=DAY, broker_factory=lambda: broker,
    )

    assert broker.execute_calls == 1
    assert broker.last_order.contracts == 1
    assert broker.last_order.max_slippage_ticks == 8.0
    assert broker.last_order.post_fill_validation_required is True
    assert broker.last_order.client_order_id.startswith("ws-")
    assert portfolio.filled_count(tmp_path, DAY) == 1
    assert any(row.get("fill_status") == "OPEN" for row in events)


def test_second_mnq_demo_signal_is_blocked_while_first_is_open(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _patch_candidate(monkeypatch, FOUR_HR)
    first = _FakeDemoBroker()
    process_demo_five_min_bar(
        payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        for_date=DAY, broker_factory=lambda: first,
    )
    assert first.execute_calls == 1

    # Make the other strategy emit a different candidate on the next call. The
    # persisted MNQ demo position must block submission before a second broker order.
    _patch_candidate(monkeypatch, THREE_TWO_TWO)
    second = _FakeDemoBroker()
    events = process_demo_five_min_bar(
        payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        for_date=DAY, broker_factory=lambda: second,
    )
    assert second.execute_calls == 0
    assert any(
        row.get("lane_failed_rule") == "demo_same_instrument_position_open"
        for row in events
    )


def test_demo_account_read_failure_blocks_submission(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _patch_candidate(monkeypatch, FOUR_HR)
    broker = _FakeDemoBroker(unreadable=True)
    events = process_demo_five_min_bar(
        payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
        for_date=DAY, broker_factory=lambda: broker,
    )
    assert broker.execute_calls == 0
    assert any(row.get("lane_failed_rule") == "demo_account_exclusive_gate" for row in events)


def test_demo_refuses_live_broker_even_with_other_pins_valid(tmp_path, monkeypatch):
    _demo_env(monkeypatch)
    _patch_candidate(monkeypatch, FOUR_HR)
    broker = _FakeDemoBroker(live=True)
    with pytest.raises(ValueError, match="refuses a live broker"):
        process_demo_five_min_bar(
            payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path,
            for_date=DAY, broker_factory=lambda: broker,
        )


def test_paper_router_blocks_when_shared_three_fill_cap_is_reached(tmp_path, monkeypatch):
    monkeypatch.setenv(execution.ROUTE_ENV, "paper_sim")
    _patch_candidate(monkeypatch, FOUR_HR)
    for _ in range(3):
        portfolio.register_fill(tmp_path, DAY)

    events = process_paper_five_min_bar(
        payload=_payload(), cfg=_cfg(), bars_5m=[], log_dir=tmp_path, for_date=DAY
    )
    assert any(
        row.get("lane_failed_rule") == "portfolio_max_trades_per_day"
        for row in events
    )
