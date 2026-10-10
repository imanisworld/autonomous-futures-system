"""Wrong-side bracket geometry is invalid before the fixed-target R:R check.

Runner mode legitimately ignores a fixed target, but may not ignore an invalid
protective stop. This is a source-only tightening of an existing risk invariant.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from risk.risk_engine import DailyState, RiskEngine, TradeSetup


def _engine(runner: bool) -> RiskEngine:
    engine = RiskEngine.__new__(RiskEngine)
    engine.config = SimpleNamespace(runner_mode=runner, min_rr_ratio=2.0)
    return engine


def _setup(direction: str, *, stop: float, target: float) -> TradeSetup:
    return TradeSetup(
        direction=direction, entry=100.0,
        stop=stop, target=target, rr_ratio=3.0, strategy="research",
        instrument="MNQ", session="new_york",
    )


@pytest.mark.parametrize("runner", [True, False])
@pytest.mark.parametrize(("direction", "stop", "target"), [
    ("LONG", 105.0, 120.0),
    ("SHORT", 95.0, 80.0),
])
def test_wrong_side_stop_never_passes_runner_exemption(
    runner: bool, direction: str, stop: float, target: float
) -> None:
    verdict = _engine(runner)._check_bracket_direction(
        _setup(direction, stop=stop, target=target), DailyState()
    )
    assert verdict is not None and verdict.failed_rule == "stop_wrong_side"


@pytest.mark.parametrize(("direction", "stop", "target"), [
    ("LONG", 95.0, 90.0),
    ("SHORT", 105.0, 110.0),
])
def test_wrong_side_fixed_target_rejected(direction: str, stop: float, target: float) -> None:
    verdict = _engine(False)._check_bracket_direction(
        _setup(direction, stop=stop, target=target), DailyState()
    )
    assert verdict is not None and verdict.failed_rule == "target_wrong_side"


@pytest.mark.parametrize(("direction", "stop", "target"), [
    ("LONG", 95.0, 90.0),
    ("SHORT", 105.0, 110.0),
])
def test_runner_ignores_only_unused_fixed_target(
    direction: str, stop: float, target: float
) -> None:
    # Runner intentionally discards a fixed target, not its protective stop.
    assert _engine(True)._check_bracket_direction(
        _setup(direction, stop=stop, target=target), DailyState()
    ) is None


def test_full_risk_validation_rejects_wrong_side_runner_stop(
    config, valid_trade_setup, clean_daily_state
) -> None:
    cfg = replace(config, runner_mode=True)
    setup = replace(valid_trade_setup, stop=valid_trade_setup.entry + 5.0)
    result = RiskEngine(config=cfg).validate(setup, clean_daily_state)
    assert result.result == "REJECTED"
    assert result.failed_rule == "stop_wrong_side"
