"""options_manager/replay — advisory-only 2-1-2 replay layer.

Increment 5. replay_strat_212() replays caller-supplied historical rows
through options_manager.strategies.evaluate_strat_212() and reports, per
row and in aggregate, what the strategy said and (when future price data
is supplied) whether the resulting setup would have hit its target or
its stop. Performs no I/O, no candle/option-chain/market-data fetch, no
broker calls, no order placement, no execution, no symbol scanning, no
option-premium simulation. Does not import replay/replay_engine.py or
replay/candle_loader.py, and does not modify
options_manager/strategies/strat_212.py.
"""

from __future__ import annotations

from .base import (
    ReplayOutcomeStatus,
    Strat212ReplayReport,
    Strat212ReplayResult,
    Strat212ReplayRow,
)
from .strat_212_replay import replay_strat_212

__all__ = [
    "ReplayOutcomeStatus",
    "Strat212ReplayReport",
    "Strat212ReplayResult",
    "Strat212ReplayRow",
    "replay_strat_212",
]
