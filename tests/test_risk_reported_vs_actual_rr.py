"""Reject R:R metadata that claims more reward than the actual bracket offers."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from risk.risk_engine import DailyState, RiskEngine, TradeSetup


def _engine(*, runner: bool = False) -> RiskEngine:
    engine = RiskEngine.__new__(RiskEngine)
    engine.config = SimpleNamespace(min_rr_ratio=2.0, runner_mode=runner)
    return engine


def _trade(direction: str, *, stop: float, target: float, claimed_rr: float) -> TradeSetup:
    return TradeSetup(
        direction=direction, entry=100.0, stop=stop, target=target,
        rr_ratio=claimed_rr, strategy="research", instrument="MNQ",
        session="new_york",
    )


@pytest.mark.parametrize(
    ("direction", "stop", "target"),
    [("LONG", 90.0, 110.0), ("SHORT", 110.0, 90.0)],
)
def test_reported_rr_cannot_override_inadequate_real_bracket(
    direction: str, stop: float, target: float
) -> None:
    result = _engine()._check_rr_ratio(
        _trade(direction, stop=stop, target=target, claimed_rr=5.0),
        DailyState(),
    )
    assert result is not None
    assert result.failed_rule == "rr_below_minimum"
    assert "actual bracket R:R 1.00" in result.reason


@pytest.mark.parametrize(
    ("direction", "stop", "target"),
    [("LONG", 95.0, 110.0), ("SHORT", 105.0, 90.0)],
)
def test_valid_actual_and_claimed_rr_continue(direction: str, stop: float, target: float) -> None:
    assert _engine()._check_rr_ratio(
        _trade(direction, stop=stop, target=target, claimed_rr=2.0),
        DailyState(),
    ) is None


def test_existing_rejection_on_low_claim_is_preserved() -> None:
    result = _engine()._check_rr_ratio(
        _trade("LONG", stop=95.0, target=115.0, claimed_rr=1.0),
        DailyState(),
    )
    assert result is not None and result.failed_rule == "rr_below_minimum"


@pytest.mark.parametrize("claimed_rr", [float("nan"), float("inf"), float("-inf"), None])
def test_nonfinite_or_missing_reported_rr_fails_closed(claimed_rr) -> None:
    result = _engine()._check_rr_ratio(
        _trade("LONG", stop=95.0, target=110.0, claimed_rr=claimed_rr),
        DailyState(),
    )
    assert result is not None and result.failed_rule == "rr_below_minimum"


def test_wrong_way_target_cannot_pass_from_claimed_rr() -> None:
    result = _engine()._check_rr_ratio(
        _trade("LONG", stop=95.0, target=97.0, claimed_rr=3.0),
        DailyState(),
    )
    assert result is not None and result.failed_rule == "rr_below_minimum"


@pytest.mark.parametrize(
    ("instrument", "entry", "stop", "target"),
    [
        ("MCL", 72.37, 72.07, 72.97),
        ("MGC", 1850.0, 1849.0, 1852.0),
        ("M2K", 2300.0, 2299.0, 2302.0),
    ],
)
def test_exact_two_to_one_brackets_pass_despite_float_noise(
    instrument: str, entry: float, stop: float, target: float
) -> None:
    actual_rr = (target - entry) / (entry - stop)
    assert actual_rr >= 1.99
    setup = TradeSetup(
        direction="LONG",
        entry=entry,
        stop=stop,
        target=target,
        rr_ratio=actual_rr,
        strategy="research",
        instrument=instrument,
        session="new_york",
    )
    assert _engine()._check_rr_ratio(setup, DailyState()) is None


def test_one_point_nine_nine_rr_still_rejected() -> None:
    setup = TradeSetup(
        direction="LONG",
        entry=100.0,
        stop=90.0,
        target=119.9,
        rr_ratio=1.99,
        strategy="research",
        instrument="MNQ",
        session="new_york",
    )
    result = _engine()._check_rr_ratio(setup, DailyState())
    assert result is not None and result.failed_rule == "rr_below_minimum"


def test_runner_existing_rr_floor_exemption_remains() -> None:
    # Runner targets are not the exit, so the original R:R gate remains
    # intentionally bypassed. This change does not modify runner policy.
    assert _engine(runner=True)._check_rr_ratio(
        _trade("LONG", stop=95.0, target=97.0, claimed_rr=0.4),
        DailyState(),
    ) is None
