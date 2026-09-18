"""Pure base-vs-stress wrapper around the canonical options paper fill consumer.

No policy values are chosen here. Callers must provide an explicit base
slippage percentage, adverse stress percentage, and per-contract fee. Both
scenarios reuse paper_sim.simulate_round_trip so replay families cannot drift
into separate fill formulas.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from .config import OptionsManagerConfig
from .contract_quality import ContractMarketSnapshot, ContractQualityResult
from .models import OptionTradePacket
from .paper_sim import PaperSimResult, simulate_round_trip
from .risk_gate import RiskGateResult


@dataclass(frozen=True, kw_only=True)
class FillStressPolicy:
    base_slippage_percent: float
    stress_slippage_percent: float
    per_contract_fee: float


@dataclass(frozen=True, kw_only=True)
class FillStressResult:
    policy: FillStressPolicy
    base: PaperSimResult
    stress: PaperSimResult


def _finite_nonnegative(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite non-negative number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return parsed


def validate_fill_stress_policy(policy: FillStressPolicy) -> FillStressPolicy:
    base = _finite_nonnegative(policy.base_slippage_percent, "base_slippage_percent")
    stress = _finite_nonnegative(policy.stress_slippage_percent, "stress_slippage_percent")
    fee = _finite_nonnegative(policy.per_contract_fee, "per_contract_fee")
    if stress <= base:
        raise ValueError("stress_slippage_percent must be greater than base_slippage_percent")
    return FillStressPolicy(
        base_slippage_percent=base,
        stress_slippage_percent=stress,
        per_contract_fee=fee,
    )


def simulate_round_trip_slippage_stress(
    packet: OptionTradePacket,
    entry_snapshot: ContractMarketSnapshot,
    exit_snapshot: ContractMarketSnapshot,
    risk_result: RiskGateResult,
    quality_result: ContractQualityResult,
    config: OptionsManagerConfig,
    *,
    policy: FillStressPolicy,
) -> FillStressResult:
    """Run base and adverse stress through the exact same fill consumer."""
    frozen = validate_fill_stress_policy(policy)

    if str(config.paper_sim_entry_fill or "").strip().upper() != "ASK":
        raise ValueError("slippage stress qualification requires ASK entry fill")
    if str(config.paper_sim_exit_fill or "").strip().upper() != "BID":
        raise ValueError("slippage stress qualification requires BID exit fill")

    base_cfg = replace(
        config,
        paper_sim_slippage_percent=frozen.base_slippage_percent,
        paper_sim_per_contract_fee=frozen.per_contract_fee,
    )
    stress_cfg = replace(
        config,
        paper_sim_slippage_percent=frozen.stress_slippage_percent,
        paper_sim_per_contract_fee=frozen.per_contract_fee,
    )

    base_result = simulate_round_trip(
        packet,
        entry_snapshot,
        exit_snapshot,
        risk_result,
        quality_result,
        base_cfg,
    )
    stress_result = simulate_round_trip(
        packet,
        entry_snapshot,
        exit_snapshot,
        risk_result,
        quality_result,
        stress_cfg,
    )
    return FillStressResult(policy=frozen, base=base_result, stress=stress_result)
