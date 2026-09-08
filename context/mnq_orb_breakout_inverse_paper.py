"""Runtime contract for the frozen MNQ ORB Breakout inverse lane.

Research contract:
  - qualify and risk-check the existing ORB Breakout signal unchanged;
  - immediately before execution, flip direction and mirror the static
    stop/target distances around the unchanged planned entry;
  - submit exactly one MNQ contract through an eight-tick marketable IOC;
  - record dynamic sizing only as a diagnostic.

Modes:
  - observe_only: no inverse execution override;
  - paper_sim: frozen inverse contract through an isolated PaperBroker;
  - tradovate_demo: the SAME frozen inverse contract through the already-
    configured Tradovate DEMO broker. It never permits live-money routing.

Any invalid configuration fails back to observe_only here, while
config.settings must reject it at process startup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Optional

from execution.broker_interface import BracketOrder

VALID_MODES = ("observe_only", "paper_sim", "tradovate_demo")
DEFAULT_MODE = "observe_only"
MARKETABLE_TICKS = 8.0
CONTRACTS = 1


def mode(cfg=None) -> str:
    raw = getattr(cfg, "mnq_orb_breakout_inverse_mode", None)
    if raw is None:
        raw = os.getenv("MNQ_ORB_BREAKOUT_INVERSE_MODE", DEFAULT_MODE)
    value = str(raw or DEFAULT_MODE).strip().lower()
    return value if value in VALID_MODES else DEFAULT_MODE


def is_candidate(instrument: Optional[str], strategy: Optional[str]) -> bool:
    root = (instrument or "").upper().replace("1!", "")
    return root == "MNQ" and strategy == "orb_breakout"


def _mirror_prices(
    direction: str,
    entry: float,
    stop: float,
    target: float,
) -> dict:
    stop_distance = abs(float(entry) - float(stop))
    target_distance = abs(float(target) - float(entry))
    inverse_direction = "SHORT" if direction == "LONG" else "LONG"
    inverse_stop = (
        float(entry) + stop_distance
        if inverse_direction == "SHORT"
        else float(entry) - stop_distance
    )
    inverse_target = (
        float(entry) - target_distance
        if inverse_direction == "SHORT"
        else float(entry) + target_distance
    )
    if inverse_direction == "LONG" and not (
        inverse_stop < float(entry) < inverse_target
    ):
        raise ValueError("invalid inverse LONG bracket geometry")
    if inverse_direction == "SHORT" and not (
        inverse_target < float(entry) < inverse_stop
    ):
        raise ValueError("invalid inverse SHORT bracket geometry")
    return {
        "direction": inverse_direction,
        "entry": float(entry),
        "stop": inverse_stop,
        "target": inverse_target,
    }


def mirror_order(source: BracketOrder) -> BracketOrder:
    if not is_candidate(source.instrument, source.strategy):
        raise ValueError("inverse transform is MNQ orb_breakout only")
    mirrored = _mirror_prices(
        source.direction,
        source.entry,
        source.stop,
        source.target,
    )
    max_dollar_risk = (
        (
            abs(mirrored["entry"] - mirrored["stop"]) / 0.25
            + MARKETABLE_TICKS
        )
        * 0.50
        * CONTRACTS
    )
    # Do NOT override post_fill_validation_required here. The source order is
    # built after broker selection: PaperBroker orders carry False; external
    # Tradovate DEMO orders carry True. Preserving that value keeps the paper
    # evidence contract unchanged while retaining the existing external-broker
    # post-fill safety gate.
    return replace(
        source,
        direction=mirrored["direction"],
        entry=mirrored["entry"],
        stop=mirrored["stop"],
        target=mirrored["target"],
        contracts=CONTRACTS,
        force_market_entry=False,
        force_runner_exit=False,
        execution_model="ioc_limit_static",
        max_dollar_risk=max_dollar_risk,
        max_slippage_ticks=MARKETABLE_TICKS,
    )


@dataclass(frozen=True)
class PaperDecision:
    mode: str
    epoch_start: Optional[str]
    apply_override: bool
    force_paper_broker: bool
    marketable_ticks: float
    contracts: int
    reason: str

    def audit(
        self,
        *,
        source_direction: str,
        entry: float,
        stop: float,
        target: float,
    ) -> dict:
        mirrored = _mirror_prices(source_direction, entry, stop, target)
        return {
            "candidate": "mnq_orb_breakout_marketable_limit_inverse_v1",
            "paper_mode": self.mode,
            "accounting_epoch_start": self.epoch_start,
            "apply_override": self.apply_override,
            "force_paper_broker": self.force_paper_broker,
            "marketable_ticks": self.marketable_ticks,
            "contracts": self.contracts,
            "comparison_commission_round_trip": 1.48,
            "dynamic_sizing_diagnostic_only": True,
            "source_setup": {
                "direction": source_direction,
                "entry": float(entry),
                "stop": float(stop),
                "target": float(target),
            },
            "submitted_setup": {
                **mirrored,
                "contracts": self.contracts,
            },
            "reason": self.reason,
        }


def evaluate(cfg=None) -> PaperDecision:
    selected = mode(cfg)
    active = selected in {"paper_sim", "tradovate_demo"}
    epoch_start = getattr(cfg, "mnq_orb_breakout_inverse_epoch_start", None)
    return PaperDecision(
        mode=selected,
        epoch_start=str(epoch_start) if epoch_start else None,
        apply_override=active,
        force_paper_broker=selected == "paper_sim",
        marketable_ticks=MARKETABLE_TICKS,
        contracts=CONTRACTS,
        reason=(
            "paper_sim: inverse direction + mirrored static bracket + "
            "eight-tick marketable IOC + fixed one contract"
            if selected == "paper_sim"
            else (
                "tradovate_demo: same frozen inverse contract via configured "
                "Tradovate DEMO broker; no live-money mode"
                if selected == "tradovate_demo"
                else "observe_only: existing orb_breakout behavior unchanged"
            )
        ),
    )
