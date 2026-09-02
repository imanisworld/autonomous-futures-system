"""Phase-1 advisory options thesis/plan manager.

Pure state reduction and proof reconciliation only: no network, broker, alert,
order, execution, or live position mutation.
"""

from __future__ import annotations

from .base import (
    ConvictionBand,
    ConvictionProofs,
    PlanObservation,
    PlanPolicy,
    PlanStatus,
    PlanUpdate,
    SignaObservation,
    StructuralLevel,
    TradePlanSnapshot,
)
from .manager import update_trade_thesis
from .proof_adapter import CanonicalPlanProofResult, update_trade_thesis_from_authorities

__all__ = [
    "CanonicalPlanProofResult",
    "ConvictionBand",
    "ConvictionProofs",
    "PlanObservation",
    "PlanPolicy",
    "PlanStatus",
    "PlanUpdate",
    "SignaObservation",
    "StructuralLevel",
    "TradePlanSnapshot",
    "update_trade_thesis",
    "update_trade_thesis_from_authorities",
]
