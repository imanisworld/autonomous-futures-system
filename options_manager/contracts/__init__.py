"""options_manager/contracts — advisory-only contract validation and shortlist layer.

All functions are pure and caller-supplied.  Nothing here fetches an option
chain, reads credentials/config, contacts a broker, sends an alert, or executes
an order.  The shortlist delegates contract constraints to the existing
validator and requires explicit selection policy rather than inventing trading
thresholds.
"""

from __future__ import annotations

from .base import (
    ContractConstraintsInputs,
    ContractConstraintsResult,
    ContractConstraintsStatus,
)
from .contract_validator import evaluate_contract_constraints
from .selector import (
    ContractCandidate,
    ContractSelectionPolicy,
    ContractSelectionRequest,
    ContractShortlistResult,
    EvaluatedContractCandidate,
    shortlist_contracts,
)

__all__ = [
    "ContractCandidate",
    "ContractConstraintsInputs",
    "ContractConstraintsResult",
    "ContractConstraintsStatus",
    "ContractSelectionPolicy",
    "ContractSelectionRequest",
    "ContractShortlistResult",
    "EvaluatedContractCandidate",
    "evaluate_contract_constraints",
    "shortlist_contracts",
]
