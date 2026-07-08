"""options_manager/scanner — advisory-only 2-1-2 watchlist scanner.

Increment 9. scan_watchlist_strat_212() scans caller-supplied
WatchlistRow entries by calling
options_manager.strategies.evaluate_strat_212() directly, per row, and
reports each as TRIGGERED/WATCH/INVALID/NO_TRADE. Performs no I/O of any
kind: no quote fetch, no option-chain fetch, no market-data fetch, no
broker call, no order placement, no execution, no alert sending, no file
writes, no ranking/scoring. Does not import replay/replay_engine.py and
does not call options_manager.replay.replay_strat_212().
"""

from __future__ import annotations

from .base import ScanReport, ScanResult, ScanStatus, WatchlistRow
from .strat_212_scanner import scan_watchlist_strat_212

__all__ = [
    "ScanReport",
    "ScanResult",
    "ScanStatus",
    "WatchlistRow",
    "scan_watchlist_strat_212",
]
