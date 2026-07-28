"""
tests/test_working_order_recheck.py

End-to-end tests for the final working-order recheck in webhook/runner.py,
immediately before broker.execute_bracket(order) (execution_safety.
working_order_recheck_enabled). These drive the real process_alert() pipeline
with a tradeable payload and a fake non-paper broker, proving the gate is
actually wired into the chokepoint — not just correct in isolation.

Uses a lightweight fake broker double (not a mocked TradovateBroker) so these
tests don't depend on Tradovate auth/HTTP plumbing — they only need to prove
"not a PaperBroker" routing and the order-list read/failure behavior.
"""
from __future__ import annotations

import dataclasses
import sys
from datetime import date

import pytest

from execution.broker_interface import BrokerCapabilities, Fill

sys.path.insert(0, "tests")
from test_e2e_scenarios import _base_payload  # noqa: E402


class _FakeLiveBroker:
    """Minimal BrokerInterface double that is NOT a PaperBroker instance —
    satisfies the runner's `not isinstance(broker, PaperBroker)` gate the same
    way a real TradovateBroker (demo or live) would."""

    def __init__(self):
        self.execute_bracket_called = False
        # A real TradovateBroker sets _last_order_ids before returning OPEN; mirror
        # that so the confirmed-execution gate (which fails closed on OPEN without
        # order ids) doesn't mask what THIS test isolates — the working-order recheck.
        self._last_order_ids = None

    @property
    def is_live(self) -> bool:
        return False  # demo-like; irrelevant — the gate no longer keys off this

    def execute_bracket(self, order) -> Fill:
        self.execute_bracket_called = True
        self._last_order_ids = {"entry": "E1", "stop": "S1", "target": "T1"}
        return Fill(
            instrument=order.instrument,
            direction=order.direction,
            contracts=order.contracts,
            entry_price=order.entry,
            exit_price=None,
            exit_reason=None,
            result="OPEN",
            pnl_ticks=None,
            pnl_dollars=None,
        )

    def get_position(self):
        return None

    def cancel_all(self) -> None:
        pass

    def get_account_balance(self):
        return 1500.0

    def get_broker_name(self) -> str:
        return "FakeLiveBroker"

    def get_capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            broker_name="FakeLiveBroker",
            asset_class="futures",
            account_mode="demo",
            starting_capital=1500.0,
            available_cash=1500.0,
            estimated_margin_required=None,
            max_dollars_risk_per_trade=None,
            supports_brackets=True,
            supports_options=False,
        )


def _run_with_fake_broker(monkeypatch, tmp_path, *, list_orders_impl, config_overrides=None):
    """Drive process_alert() with paper_mode=False so _make_broker's non-paper
    branch is taken, monkeypatched to return our fake broker instead of a real
    TradovateBroker (no network/auth needed to prove the gate's wiring)."""
    from tests.conftest import load_permissive_config
    from webhook import runner as runner_module

    fake_broker = _FakeLiveBroker()
    monkeypatch.setattr(runner_module, "_make_broker", lambda **kwargs: fake_broker)
    monkeypatch.setattr(
        "execution.live_preflight._list_orders", list_orders_impl
    )

    # Explicit permissive universe: this test exercises the live-broker
    # order-recheck gate, not the shipped isolated-lane config.
    cfg = load_permissive_config(
        max_staleness_seconds=10**9,
        paper_mode=False,
        **(config_overrides or {}),
    )
    payload = _base_payload(timestamp="2026-05-23T14:30:00+00:00")
    fd = date(2026, 5, 23)

    result = runner_module.process_alert(
        payload, config=cfg, log_dir=str(tmp_path / "logs"), for_date=fd
    )
    return result, fake_broker


def test_no_working_orders_proceeds_normally(monkeypatch, tmp_path):
    result, fake_broker = _run_with_fake_broker(
        monkeypatch, tmp_path, list_orders_impl=lambda broker: []
    )
    assert fake_broker.execute_bracket_called is True
    assert result["decision"] == "TRADE", result
    assert result.get("fill")


def test_working_order_conflict_prevents_execute_bracket(monkeypatch, tmp_path):
    result, fake_broker = _run_with_fake_broker(
        monkeypatch,
        tmp_path,
        list_orders_impl=lambda broker: [{"ordStatus": "Working", "accountId": None}],
    )
    assert fake_broker.execute_bracket_called is False
    assert result["decision"] == "ORDER_SUPPRESSED", result
    assert "working_order_conflict" in result.get("gate_reason", "")
    assert not result.get("fill")


def test_order_read_failure_prevents_execute_bracket(monkeypatch, tmp_path):
    def _boom(broker):
        raise RuntimeError("simulated /order/list failure")

    result, fake_broker = _run_with_fake_broker(
        monkeypatch, tmp_path, list_orders_impl=_boom
    )
    assert fake_broker.execute_bracket_called is False
    assert result["decision"] == "ORDER_SUPPRESSED", result
    assert "order_state_unreadable" in result.get("gate_reason", "")
    assert not result.get("fill")


def test_working_order_recheck_disabled_skips_the_gate(monkeypatch, tmp_path):
    """working_order_recheck_enabled=false must fully bypass the gate, even
    with a working order present — proves the toggle actually works."""
    result, fake_broker = _run_with_fake_broker(
        monkeypatch,
        tmp_path,
        list_orders_impl=lambda broker: [{"ordStatus": "Working", "accountId": None}],
        config_overrides={"working_order_recheck_enabled": False},
    )
    assert fake_broker.execute_bracket_called is True
    assert result["decision"] == "TRADE", result


def test_paper_broker_path_does_not_call_tradovate_order_list_logic(monkeypatch, tmp_path):
    """The default paper-mode path must never touch execution.live_preflight's
    order-list logic at all — PaperBroker structurally can't have this
    conflict and has no order book to read."""
    from tests.conftest import load_permissive_config
    from webhook.runner import process_alert

    def _trip_wire(broker):
        raise AssertionError(
            "PaperBroker path must never call execution.live_preflight._list_orders"
        )

    monkeypatch.setattr("execution.live_preflight._list_orders", _trip_wire)

    cfg = load_permissive_config(max_staleness_seconds=10**9)
    payload = _base_payload(timestamp="2026-05-23T14:30:00+00:00")
    fd = date(2026, 5, 23)

    result = process_alert(payload, config=cfg, log_dir=str(tmp_path / "logs"), for_date=fd)
    assert result["decision"] == "TRADE", result
    assert result.get("fill")
