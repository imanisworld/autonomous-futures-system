"""options_manager/levels — advisory-only level/target-finding layer.

Increment 2. find_targets() is a pure function that performs no I/O, no
broker calls, no execution, no order placement. Nothing here imports
alert_ranker, options_companion, execution, webhook, broker systems, or
risk/risk_engine.py, and nothing here imports options_manager's own
existing pipeline modules or the strategies package (Increment 1) — this
package is additive and standalone, not wired into strat_212.py.
"""

from __future__ import annotations

from .base import LevelFinderInputs, TargetFinderResult, TargetFinderStatus
from .target_finder import find_targets

__all__ = [
    "LevelFinderInputs",
    "TargetFinderResult",
    "TargetFinderStatus",
    "find_targets",
]
