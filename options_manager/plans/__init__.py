"""Phase-1 advisory options thesis/plan manager.

Pure state reduction, proof reconciliation, and transport-neutral rendering only:
no network, broker, alert, order, execution, or live position mutation.
"""

from __future__ import annotations

from .base import (
    ContractPlanSnapshot,
    ConvictionBand,
    ConvictionProofs,
    PlanObservation,
    PlanPolicy,
    PlanStatus,
    PlanUpdate,
    RiskPlanSnapshot,
    SignaObservation,
    StructuralLevel,
    TradePlanSnapshot,
)
from .manager import update_trade_thesis
from .proof_adapter import CanonicalPlanProofResult, update_trade_thesis_from_authorities
from .renderer import PlanUpdateKind, RenderedPlanUpdate, render_plan_update

__all__ = [
    "CanonicalPlanProofResult",
    "ContractPlanSnapshot",
    "ConvictionBand",
    "ConvictionProofs",
    "PlanObservation",
    "PlanPolicy",
    "PlanStatus",
    "PlanUpdate",
    "PlanUpdateKind",
    "RenderedPlanUpdate",
    "RiskPlanSnapshot",
    "SignaObservation",
    "StructuralLevel",
    "TradePlanSnapshot",
    "render_plan_update",
    "update_trade_thesis",
    "update_trade_thesis_from_authorities",
]
