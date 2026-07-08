"""options_manager/adapters — source-neutral adapter models and row builder.

Increment 13. Normalized, vendor-agnostic data models
(AdapterCandle, AdapterOptionQuote, AdapterUnderlyingSnapshot,
AdapterMarketContextSnapshot) and a pure row builder
(build_watchlist_row_from_adapter_data()) that translates already-
normalized, caller-supplied adapter data into a
options_manager.scanner.WatchlistRow.

No network calls, no HTTP, no login material, no live data fetching, no
option-chain fetching, no quote fetching are implemented anywhere in this
package yet -- this increment is data-model and translation logic only.
A real vendor client (e.g. Polygon) is explicitly out of scope and would
be a later, separate increment. Nothing here calls
scan_watchlist_strat_212() or evaluate_strat_212(), and nothing here
imports execution, broker systems, webhook, alert_ranker,
options_companion, or risk/risk_engine.py.
"""

from __future__ import annotations

from .base import (
    AdapterCandle,
    AdapterMarketContextSnapshot,
    AdapterOptionQuote,
    AdapterUnderlyingSnapshot,
)
from .row_builder import build_watchlist_row_from_adapter_data

__all__ = [
    "AdapterCandle",
    "AdapterMarketContextSnapshot",
    "AdapterOptionQuote",
    "AdapterUnderlyingSnapshot",
    "build_watchlist_row_from_adapter_data",
]
