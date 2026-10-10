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
