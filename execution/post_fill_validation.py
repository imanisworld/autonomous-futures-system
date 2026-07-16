"""Shared post-fill execution-quality validation for runtime and replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from execution.broker_interface import BracketOrder


TICK_SIZE = {
    "MNQ": 0.25, "MES": 0.25, "ES": 0.25, "NQ": 0.25, "MGC": 0.10, "MCL": 0.01,
}
TICK_VALUE = {
    "MNQ": 0.50, "MES": 1.25, "ES": 12.50, "NQ": 5.00, "MGC": 1.00, "MCL": 1.00,
}

# These strategies derive their brackets from market structure before broker
# submission. A worse fill must not move either protective level after the fact.
STRATEGY_EXECUTION_MODE = {
    "orb_reclaim": "anchored_structure",
    "orb_breakout": "anchored_structure",
    "pdh_reclaim": "anchored_structure",
    "pdl_reclaim": "anchored_structure",
    "vwap_hold": "anchored_structure",
    "vwap_reclaim": "anchored_structure",
}


def strategy_execution_model(strategy: str) -> str:
    return STRATEGY_EXECUTION_MODE.get(str(strategy or "").lower(), "anchored_structure")


def _on_tick(price: float, tick: float) -> bool:
    return tick > 0 and abs(float(price) / tick - round(float(price) / tick)) < 1e-7


@dataclass(frozen=True)
class PostFillValidation:
    accepted: bool
    execution_model: str
    requested_entry: float
    actual_entry: float
    stop: float
    target: float
    contracts: int
    tick_size: float
    tick_value: float
    planned_risk_points: float
    actual_risk_points: float
    planned_reward_points: float
    actual_reward_points: float
    planned_rr: float
    actual_rr: float
    actual_dollar_risk: float
    slippage_points: float
    slippage_ticks: float
    adverse_slippage_ticks: float
    max_dollar_risk: Optional[float]
    max_stop_ticks: Optional[float]
    max_slippage_ticks: Optional[float]
    min_rr_ratio: float
    checks: dict[str, bool]
    failed_checks: tuple[str, ...]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["failed_checks"] = list(self.failed_checks)
        return data


def validate_post_fill(order: BracketOrder, actual_entry: float) -> PostFillValidation:
    """Recalculate the approved bracket from the broker's actual fill price."""
    root = str(order.instrument or "").replace("1!", "").upper()
    tick = TICK_SIZE.get(root, 0.25)
    tick_value = TICK_VALUE.get(root, 1.25)
    requested = float(order.entry)
    actual = float(actual_entry)
    stop = float(order.stop)
    target = float(order.target)
    qty = max(1, int(order.contracts or 1))
    direction = str(order.direction or "").upper()

    if direction == "LONG":
        planned_risk = requested - stop
        actual_risk = actual - stop
        planned_reward = target - requested
        actual_reward = target - actual
        signed_slippage = actual - requested
        stop_direction_ok = stop < actual
        target_direction_ok = target > actual
    else:
        planned_risk = stop - requested
        actual_risk = stop - actual
        planned_reward = requested - target
        actual_reward = actual - target
        signed_slippage = requested - actual
        stop_direction_ok = stop > actual
        target_direction_ok = target < actual

    planned_rr = planned_reward / planned_risk if planned_risk > 0 else 0.0
    actual_rr = actual_reward / actual_risk if actual_risk > 0 else 0.0
    slippage_ticks = signed_slippage / tick if tick > 0 else 0.0
    adverse_slippage_ticks = max(0.0, slippage_ticks)
    actual_stop_ticks = actual_risk / tick if tick > 0 else float("inf")
    actual_dollar_risk = actual_stop_ticks * tick_value * qty

    minimum_rr = max(0.0, float(getattr(order, "min_rr_ratio", 2.0) or 0.0))
    max_dollar = getattr(order, "max_dollar_risk", None)
    max_stop = getattr(order, "max_stop_ticks", None)
    max_slippage = getattr(order, "max_slippage_ticks", None)
    checks = {
        "actual_rr_minimum": actual_rr >= minimum_rr,
        "actual_dollar_risk": max_dollar is None or actual_dollar_risk <= float(max_dollar) + 1e-9,
        "actual_stop_distance": max_stop is None or actual_stop_ticks <= float(max_stop) + 1e-9,
        "slippage_limit": max_slippage is None or adverse_slippage_ticks <= float(max_slippage) + 1e-9,
        "target_direction": target_direction_ok,
        "stop_direction": stop_direction_ok,
        "entry_tick": _on_tick(actual, tick),
        "stop_tick": _on_tick(stop, tick),
        "target_tick": _on_tick(target, tick),
    }
    failed = tuple(name for name, passed in checks.items() if not passed)
    return PostFillValidation(
        accepted=not failed,
        execution_model=strategy_execution_model(order.strategy),
        requested_entry=requested,
        actual_entry=actual,
        stop=stop,
        target=target,
        contracts=qty,
        tick_size=tick,
        tick_value=tick_value,
        planned_risk_points=planned_risk,
        actual_risk_points=actual_risk,
        planned_reward_points=planned_reward,
        actual_reward_points=actual_reward,
        planned_rr=planned_rr,
        actual_rr=actual_rr,
        actual_dollar_risk=actual_dollar_risk,
        slippage_points=signed_slippage,
        slippage_ticks=slippage_ticks,
        adverse_slippage_ticks=adverse_slippage_ticks,
        max_dollar_risk=float(max_dollar) if max_dollar is not None else None,
        max_stop_ticks=float(max_stop) if max_stop is not None else None,
        max_slippage_ticks=float(max_slippage) if max_slippage is not None else None,
        min_rr_ratio=minimum_rr,
        checks=checks,
        failed_checks=failed,
    )
