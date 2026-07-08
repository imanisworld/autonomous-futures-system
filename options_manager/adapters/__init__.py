"""options_manager/adapters — source-neutral adapter models and row builder.

Increment 13. Normalized, vendor-agnostic data models
(AdapterCandle, AdapterOptionQuote, AdapterUnderlyingSnapshot,
AdapterMarketContextSnapshot) and a pure row builder
(build_watchlist_row_from_adapter_data()) that translates already-
normalized, caller-supplied adapter data into a
options_manager.scanner.WatchlistRow. Nothing here calls
scan_watchlist_strat_212() or evaluate_strat_212(), and nothing here
imports execution, broker systems, webhook, alert_ranker,
options_companion, or risk/risk_engine.py.

One narrow, deliberate exception to "no network calls": polygon_historical.py
is a read-only STOCK-aggregates (candle) client only -- never an options
chain, never a quote, never a streaming connection, and never wired into
the scanner or any fixture auto-generation. It exists solely so a real
historical candle sequence can be pulled manually to reconstruct a
validation fixture. Every other module in this package remains pure
translation logic with no I/O of its own.
"""

from __future__ import annotations

from .base import (
    AdapterCandle,
    AdapterMarketContextSnapshot,
    AdapterOptionQuote,
    AdapterUnderlyingSnapshot,
)
from .polygon_historical import (
    PolygonHistoricalClient,
    PolygonHistoricalError,
    fetch_stock_aggregates,
)
from .row_builder import build_watchlist_row_from_adapter_data

__all__ = [
    "AdapterCandle",
    "AdapterMarketContextSnapshot",
    "AdapterOptionQuote",
    "AdapterUnderlyingSnapshot",
    "build_watchlist_row_from_adapter_data",
    "PolygonHistoricalClient",
    "PolygonHistoricalError",
    "fetch_stock_aggregates",
]
