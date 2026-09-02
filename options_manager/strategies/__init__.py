"""options_manager/strategies — advisory-only strategy layer.

Increment 1. Every validator here returns a StrategySignal (base.py) and
performs no I/O, no broker calls, no execution, no order placement. The
only import outside options_manager/ is strategy.strat_classifier (pure,
read-only candle/sequence classification, already tested, explicitly
documented as not placing trades) — nothing here imports alert_ranker,
options_companion, execution, webhook, or risk/risk_engine.py.
"""

from __future__ import annotations

from .base import (
    StrategyContractConstraints,
    StrategyMarketContext,
    StrategySignal,
    StrategyStatus,
)
from .mechanical import strat_212_mechanical_levels
from .strat_212 import STRATEGY_NAME as STRAT_212_STRATEGY_NAME
from .strat_212 import Strat212Bars, evaluate_strat_212

__all__ = [
    "StrategyContractConstraints",
    "StrategyMarketContext",
    "StrategySignal",
    "StrategyStatus",
    "Strat212Bars",
    "evaluate_strat_212",
    "strat_212_mechanical_levels",
    "STRAT_212_STRATEGY_NAME",
]
