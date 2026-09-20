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

Two narrow, deliberate networked adapters exist:
- polygon_historical.py is a read-only STOCK-aggregates client used for
  historical validation fixtures.
- webull_sandbox.py is sandbox/paper-only and exposes read-only account
  inspection, option-contract discovery, and broker preview. It has no
  submission/cancel/replace/live-routing capability.

Neither adapter is wired into scanner decisions or automatic execution.
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
from .webull_sandbox import (
    WebullSandboxAccountSnapshot,
    WebullSandboxContractDiscovery,
    WebullSandboxOptionContract,
    WebullSandboxPreviewResult,
    discover_sandbox_option_contracts,
    preview_sandbox_option_order,
    read_sandbox_individual_cash_account,
)

__all__ = [
    "AdapterCandle",
    "AdapterMarketContextSnapshot",
    "AdapterOptionQuote",
    "AdapterUnderlyingSnapshot",
    "build_watchlist_row_from_adapter_data",
    "PolygonHistoricalClient",
    "PolygonHistoricalError",
    "fetch_stock_aggregates",
    "WebullSandboxAccountSnapshot",
    "WebullSandboxContractDiscovery",
    "WebullSandboxOptionContract",
    "WebullSandboxPreviewResult",
    "discover_sandbox_option_contracts",
    "preview_sandbox_option_order",
    "read_sandbox_individual_cash_account",
]
