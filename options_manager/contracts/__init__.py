"""options_manager/contracts — advisory-only contract-constraints validation layer.

Increment 4. evaluate_contract_constraints() is a pure function of its
explicit, caller-supplied inputs; it performs no I/O, no option-chain
fetch, no contract selection, no broker calls, no config read, and no
credential access. Nothing here imports alert_ranker, options_companion
(credentialed, different trust boundary), execution, webhook, broker
systems, or risk/risk_engine.py — this package evaluates a caller-
supplied contract's own data against caller-supplied risk limits only,
and is additive: not wired into options_manager/strategies/strat_212.py.
"""

from __future__ import annotations

from .base import (
    ContractConstraintsInputs,
    ContractConstraintsResult,
    ContractConstraintsStatus,
)
from .contract_validator import evaluate_contract_constraints

__all__ = [
    "ContractConstraintsInputs",
    "ContractConstraintsResult",
    "ContractConstraintsStatus",
    "evaluate_contract_constraints",
]
