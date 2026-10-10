"""Integration proofs for strict reference fills + recomputed risk R:R.

No broker I/O; no strategy-policy changes. The PR is temporary QA-only.
"""
from __future__ import annotations

from types import SimpleNamespace

from execution.broker_interface import BracketOrder
from execution.research_reference_paper import ResearchReferencePaperBroker
from risk.risk_engine import DailyState, RiskEngine, TradeSetup


def _risk_engine():
    engine = RiskEngine.__new__(RiskEngine)
    engine.config = SimpleNamespace(min_rr_ratio=2.0, runner_mode=False)
    return engine


def test_signal_claim_cannot_overrule_actual_pre_entry_geometry():
    setup = TradeSetup(
        direction="LONG", entry=100.0, stop=90.0, target=112.0,
        rr_ratio=3.0, strategy="isolated_qa", instrument="MNQ",
        session="new_york",
    )
    result = _risk_engine()._check_rr_ratio(setup, DailyState())
    assert result is not None and result.failed_rule == "rr_below_minimum"


def test_causal_reference_that_breaks_actual_rr_is_rejected_after_fill():
    # The proposed bracket initially has 5.0 R. Waiting for a real
    # executable price makes it <1 R. The strict broker must reject.
    order = BracketOrder(
        instrument="MNQ", direction="LONG", entry=100.0,
        stop=90.0, target=150.0, rr_ratio=5.0,
        strategy="isolated_qa", contracts=1,
        min_rr_ratio=2.0,
    )
    broker = ResearchReferencePaperBroker(
        entry_fill_model="market_at_reference", slippage_ticks=1.0
    )
    fill = broker.execute_bracket(order, market_price=140.0)
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "POST_FILL_VALIDATION_FAILED"
    assert broker.get_position() is None


def test_valid_reference_and_actual_rr_open_without_external_route(monkeypatch):
    from execution import paper_mirror_hook

    def must_not_call(*_args, **_kwargs):
        raise AssertionError("external routing forbidden")
    monkeypatch.setattr(paper_mirror_hook, "after_entry", must_not_call)
    order = BracketOrder(
        instrument="MNQ", direction="LONG", entry=100.0,
        stop=90.0, target=150.0, rr_ratio=5.0,
        strategy="isolated_qa", contracts=1,
        min_rr_ratio=2.0,
    )
    broker = ResearchReferencePaperBroker(
        entry_fill_model="market_at_reference", slippage_ticks=1.0
    )
    fill = broker.execute_bracket(order, market_price=104.0)
    assert fill.result == "OPEN"
    assert broker.get_position() is not None
    assert fill.entry_price == 104.25



def test_short_reference_after_adverse_move_cannot_pass_bracket_rr():
    # Prior signal metadata says 3R, but after a worse short fill the
    # target may sit close enough that actual reward:risk is <2.
    order = BracketOrder(
        instrument="MNQ", direction="SHORT", entry=200.0,
        stop=210.0, target=170.0, rr_ratio=3.0,
        strategy="isolated_qa", contracts=1,
        min_rr_ratio=2.0,
    )
    broker = ResearchReferencePaperBroker(entry_fill_model="market_at_reference")
    fill = broker.execute_bracket(order, market_price=190.0)
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "POST_FILL_VALIDATION_FAILED"
    assert broker.get_position() is None



def test_natural_1m_4hr_touch_cannot_place_demo_order_even_when_route_armed(
    monkeypatch, tmp_path, config
):
    """1m observer and the guarded 5m IOC DEMO entry are distinct paths.

    Existing per-lane tests do not by themselves prove the observer remains
    broker-inert while the other lane's DEMO selector is explicitly armed.
    An unapproved 1m->DEMO shortcut must make this test fail.
    """
    from datetime import datetime
    from context import wide_stop_demo_runtime
    from context import wide_stop_execution
    from tests.test_one_min_trigger import (
        DAY, ET, _arm_observation, _enable_observer,
        _payload, _seed_completed_8am_hour, _without_executable_4hr,
    )
    from webhook.runner import process_alert

    _enable_observer(monkeypatch)
    _without_executable_4hr(config)
    log_dir = str(tmp_path)
    # Set up a real canonical read-only observation, before arming the
    # separate DEMO selector; setup's 5m seeding must not trigger DEMO.
    _seed_completed_8am_hour(log_dir)
    _arm_observation(log_dir)

    monkeypatch.setenv(wide_stop_execution.ROUTE_ENV, wide_stop_execution.DEMO_ROUTE)
    monkeypatch.setenv(
        wide_stop_execution.ROUTE_PROOF_PIN_ENV, wide_stop_execution.DEMO_ROUTE
    )
    monkeypatch.setenv(wide_stop_execution.DEMO_EXECUTION_ENABLED_ENV, "true")
    monkeypatch.setenv(wide_stop_execution.DEMO_EXECUTION_PROOF_PIN_ENV, "true")
    monkeypatch.setenv("WIDE_STOP_LEDGER_MODE", "paper_sim")
    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")

    def forbidden_demo_entry(**kwargs):
        raise AssertionError("1m observation must not enter the 5m DEMO router")

    monkeypatch.setattr(
        wide_stop_demo_runtime, "process_demo_five_min_bar",
        forbidden_demo_entry,
    )
    result = process_alert(
        _payload(datetime(2026, 6, 2, 9, 31, tzinfo=ET)),
        config=config, log_dir=log_dir, for_date=DAY,
    )
    touch = result["one_min_trigger"]
    assert touch is not None and touch["event"] == "TRIGGER_TOUCH"
    assert result["decision"] == "ONE_MIN_CONTEXT"
    assert result["fill"] is None
    assert result["risk"] is None
    assert result["execution_reachable"] is False
    assert touch["mode"] == "paper_evidence_only"
    assert touch["trade_authorized"] is False
    assert touch["external_broker"] is False
