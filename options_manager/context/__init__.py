"""options_manager/context — advisory-only market-context validation layer.

Increment 3. evaluate_market_context() is a pure function of its
explicit, caller-supplied inputs; it performs no I/O, no broker calls,
no market-data fetch, no config read, and no credential access. Nothing
here imports alert_ranker, options_companion, execution, webhook, broker
systems, risk/risk_engine.py, or the existing live context.market_context
loader (which pulls config and enriches data) — this package models
market context from caller-supplied fields only, and is additive: not
wired into options_manager/strategies/strat_212.py.
"""

from __future__ import annotations

from .base import MarketContextInputs, MarketContextResult, MarketContextStatus
from .market_validator import evaluate_market_context

__all__ = [
    "MarketContextInputs",
    "MarketContextResult",
    "MarketContextStatus",
    "evaluate_market_context",
]
