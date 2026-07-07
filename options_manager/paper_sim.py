"""Phase 4 — options paper simulation (backtest / replay).

Pure, deterministic simulation of a round-trip options trade using supplied
entry and exit market snapshots, plus the Phase 2 risk gate result and Phase 3
contract quality gate result for that packet. No broker calls, no order
calls, no HTTP, no Discord, no file writes, no provider fetching — this
module performs no I/O of any kind. It only reads a packet, two snapshots,
two upstream gate results, and a config object, and returns a result.

This module does NOT place orders, preview orders, or execute anything. It
only scores a hypothetical round trip using data the caller already supplied.
Fetching real options data and any live/dry-run order review are later
phases.

Independent of risk/risk_engine.py (futures) and risk/options_risk_engine.py
(reference only, not imported) — this is options_manager's own simulator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from .config import OptionsManagerConfig
from .contract_quality import ContractMarketSnapshot, ContractQualityResult
from .models import OptionTradePacket
from .risk_gate import RiskGateResult

ENTRY_FILL_MODES = ("ASK", "MID", "LAST")
EXIT_FILL_MODES = ("BID", "MID", "LAST")


@dataclass
class PaperSimResult:
    approved_for_sim: bool
    status: Literal["SIMULATED", "REJECTED", "DATA_BLOCKED"]
    failed_stage: Optional[str] = None
    reason: str = ""
    simulated_entry_price: Optional[float] = None
    simulated_exit_price: Optional[float] = None
    simulated_contracts: int = 0
    simulated_gross_pnl: Optional[float] = None
    simulated_fees: float = 0.0
    simulated_net_pnl: Optional[float] = None
    warnings: list[str] = field(default_factory=list)


def _simulated(
    *,
    entry_price: float,
    exit_price: float,
    contracts: int,
    gross_pnl: float,
    fees: float,
    net_pnl: float,
    warnings: list[str],
) -> PaperSimResult:
    return PaperSimResult(
        approved_for_sim=True,
        status="SIMULATED",
        failed_stage=None,
        reason="",
        simulated_entry_price=entry_price,
        simulated_exit_price=exit_price,
        simulated_contracts=contracts,
        simulated_gross_pnl=gross_pnl,
        simulated_fees=fees,
        simulated_net_pnl=net_pnl,
        warnings=warnings,
    )


def _rejected(failed_stage: str, reason: str) -> PaperSimResult:
    return PaperSimResult(
        approved_for_sim=False,
        status="REJECTED",
        failed_stage=failed_stage,
        reason=reason,
    )


def _data_blocked(failed_stage: str, reason: str) -> PaperSimResult:
    return PaperSimResult(
        approved_for_sim=False,
        status="DATA_BLOCKED",
        failed_stage=failed_stage,
        reason=reason,
    )


def simulate_round_trip(
    packet: OptionTradePacket,
    entry_snapshot: ContractMarketSnapshot,
    exit_snapshot: ContractMarketSnapshot,
    risk_result: RiskGateResult,
    quality_result: ContractQualityResult,
    config: OptionsManagerConfig,
) -> PaperSimResult:
    """Pure function of (packet, snapshots, gate results, config) -> PaperSimResult.

    config is required and must be passed explicitly by the caller — this
    function itself must never read env vars, .env files, or any other
    external mutable state, or it stops being deterministic. It never fetches
    data from a provider and never writes a journal entry; it only scores a
    hypothetical round trip using the entry/exit snapshots it's given.
    """
    cfg = config
    warnings: list[str] = []

    # 0. Only PENDING packets may be simulated — same defensive re-check
    # pattern used by risk_gate.py and contract_quality.py.
    if packet.status != "PENDING":
        return _rejected(
            "packet_status",
            f"packet status is '{packet.status}' (must be PENDING); "
            f"original rejection_reason={packet.rejection_reason!r}",
        )

    # 1. Risk gate precondition (skippable for isolated what-if simulation).
    if cfg.paper_sim_require_approved_risk:
        if risk_result.status == "REJECTED":
            return _rejected(
                "risk_gate",
                f"risk_gate rejected: failed_rule={risk_result.failed_rule!r}, "
                f"reason={risk_result.reason!r}",
            )
        if risk_result.status == "DATA_BLOCKED":
            return _data_blocked(
                "risk_gate",
                f"risk_gate data_blocked: failed_rule={risk_result.failed_rule!r}, "
                f"reason={risk_result.reason!r}",
            )

    # 2. Contract quality gate precondition (skippable for isolated what-if
    # simulation).
    if cfg.paper_sim_require_approved_quality:
        if quality_result.status == "REJECTED":
            return _rejected(
                "contract_quality",
                f"contract_quality rejected: failed_rule={quality_result.failed_rule!r}, "
                f"reason={quality_result.reason!r}",
            )
        if quality_result.status == "DATA_BLOCKED":
            return _data_blocked(
                "contract_quality",
                f"contract_quality data_blocked: failed_rule={quality_result.failed_rule!r}, "
                f"reason={quality_result.reason!r}",
            )

    # 3. Entry fill.
    entry_mode = (cfg.paper_sim_entry_fill or "").strip().upper()
    if entry_mode not in ENTRY_FILL_MODES:
        return _rejected(
            "fill_model",
            f"paper_sim_entry_fill {cfg.paper_sim_entry_fill!r} is an invalid fill "
            f"mode (must be one of {ENTRY_FILL_MODES})",
        )

    if entry_mode == "ASK":
        if entry_snapshot.ask is None:
            return _data_blocked(
                "entry_snapshot", "entry_snapshot.ask is missing (required for ASK fill)"
            )
        raw_entry_fill = entry_snapshot.ask
    elif entry_mode == "MID":
        if entry_snapshot.bid is None or entry_snapshot.ask is None:
            return _data_blocked(
                "entry_snapshot",
                "entry_snapshot bid/ask is missing (required for MID fill)",
            )
        raw_entry_fill = (entry_snapshot.bid + entry_snapshot.ask) / 2
    else:  # LAST
        if entry_snapshot.last is None:
            return _data_blocked(
                "entry_snapshot", "entry_snapshot.last is missing (required for LAST fill)"
            )
        raw_entry_fill = entry_snapshot.last

    entry_fill = raw_entry_fill * (1 + cfg.paper_sim_slippage_percent / 100)
    if entry_fill <= 0:
        return _rejected(
            "entry_snapshot",
            f"resolved entry fill {entry_fill} is invalid (must be > 0)",
        )

    # 4. Exit fill.
    exit_mode = (cfg.paper_sim_exit_fill or "").strip().upper()
    if exit_mode not in EXIT_FILL_MODES:
        return _rejected(
            "fill_model",
            f"paper_sim_exit_fill {cfg.paper_sim_exit_fill!r} is an invalid fill "
            f"mode (must be one of {EXIT_FILL_MODES})",
        )

    if exit_mode == "BID":
        if exit_snapshot.bid is None:
            return _data_blocked(
                "exit_snapshot", "exit_snapshot.bid is missing (required for BID fill)"
            )
        raw_exit_fill = exit_snapshot.bid
    elif exit_mode == "MID":
        if exit_snapshot.bid is None or exit_snapshot.ask is None:
            return _data_blocked(
                "exit_snapshot",
                "exit_snapshot bid/ask is missing (required for MID fill)",
            )
        raw_exit_fill = (exit_snapshot.bid + exit_snapshot.ask) / 2
    else:  # LAST
        if exit_snapshot.last is None:
            return _data_blocked(
                "exit_snapshot", "exit_snapshot.last is missing (required for LAST fill)"
            )
        raw_exit_fill = exit_snapshot.last

    # Adverse slippage reduces the exit fill; floor at 0 rather than reject —
    # a worthless expiration (bid == 0) is a valid, realistic outcome.
    exit_fill = max(0.0, raw_exit_fill * (1 - cfg.paper_sim_slippage_percent / 100))

    # 5. Contracts, per packet.
    contracts = packet.max_contracts

    # 6-8. P&L math.
    gross_pnl = (exit_fill - entry_fill) * cfg.paper_sim_contract_multiplier * contracts
    fees = cfg.paper_sim_per_contract_fee * contracts * 2
    net_pnl = gross_pnl - fees

    return _simulated(
        entry_price=entry_fill,
        exit_price=exit_fill,
        contracts=contracts,
        gross_pnl=gross_pnl,
        fees=fees,
        net_pnl=net_pnl,
        warnings=warnings,
    )
