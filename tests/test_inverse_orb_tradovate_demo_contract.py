from __future__ import annotations

from dataclasses import dataclass

from context.mnq_orb_breakout_inverse_paper import (
    MARKETABLE_TICKS,
    evaluate,
    mirror_order,
    mode,
)
from execution.broker_interface import BracketOrder
from ops.project_check.runtime import _derived_lane_transforms


@dataclass
class _Cfg:
    mnq_orb_breakout_inverse_mode: str = "observe_only"
    mnq_orb_breakout_inverse_epoch_start: str = "2026-09-08T00:00:00+00:00"


def _source_order(*, post_fill_validation_required: bool) -> BracketOrder:
    return BracketOrder(
        instrument="MNQ",
        direction="LONG",
        entry=25000.0,
        stop=24988.0,
        target=25024.0,
        rr_ratio=2.0,
        strategy="orb_breakout",
        contracts=2,
        min_rr_ratio=2.0,
        max_stop_ticks=120.0,
        post_fill_validation_required=post_fill_validation_required,
    )


def test_tradovate_demo_is_an_explicit_inverse_mode():
    cfg = _Cfg(mnq_orb_breakout_inverse_mode="tradovate_demo")
    decision = evaluate(cfg)

    assert mode(cfg) == "tradovate_demo"
    assert decision.apply_override is True
    assert decision.force_paper_broker is False
    assert decision.marketable_ticks == 8.0
    assert decision.contracts == 1


def test_live_value_still_fails_back_to_observe_only():
    cfg = _Cfg(mnq_orb_breakout_inverse_mode="live")
    decision = evaluate(cfg)

    assert mode(cfg) == "observe_only"
    assert decision.apply_override is False
    assert decision.force_paper_broker is False


def test_inverse_transform_preserves_frozen_eight_tick_contract():
    inverse = mirror_order(_source_order(post_fill_validation_required=False))

    assert inverse.direction == "SHORT"
    assert inverse.entry == 25000.0
    assert inverse.stop == 25012.0
    assert inverse.target == 24976.0
    assert inverse.contracts == 1
    assert inverse.force_market_entry is False
    assert inverse.force_runner_exit is False
    assert inverse.execution_model == "ioc_limit_static"
    assert inverse.max_slippage_ticks == MARKETABLE_TICKS == 8.0


def test_external_post_fill_validation_is_not_disabled_by_inverse_transform():
    inverse = mirror_order(_source_order(post_fill_validation_required=True))
    assert inverse.post_fill_validation_required is True


def test_paper_post_fill_setting_remains_unchanged():
    inverse = mirror_order(_source_order(post_fill_validation_required=False))
    assert inverse.post_fill_validation_required is False


def test_project_check_reports_demo_as_active_inverse(monkeypatch):
    monkeypatch.setenv("MNQ_ORB_BREAKOUT_INVERSE_MODE", "tradovate_demo")
    row = _derived_lane_transforms()["orb_breakout"]

    assert row == {
        "transform": "inverse",
        "env": "MNQ_ORB_BREAKOUT_INVERSE_MODE",
        "mode": "tradovate_demo",
        "active": True,
    }
