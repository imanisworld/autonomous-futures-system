"""options_manager/adapters/base.py

Source-neutral, normalized adapter data models — Increment 13. These
dataclasses describe what a future market-data adapter (Polygon, or any
other source) would hand to the row builder; nothing in this module
fetches any of it. No adapter-specific (e.g. Polygon) shape leaks in
here -- these are the common, normalized shapes every future adapter is
expected to translate its own vendor response into.

Performs no I/O of any kind: no HTTP, no network, no login material, no
option-chain fetch, no quote fetch, no file access. Does not import
options_manager.scanner (the scanner must remain caller-supplied only
and must never depend on this package), execution, broker systems,
webhook, alert_ranker, options_companion, or risk/risk_engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Field values here intentionally use plain str (not the Literal type
# aliases defined inside options_manager.context.base /
# options_manager.contracts.base) -- those aliases are internal to their
# own packages (not part of either package's public __all__), and this
# module stays a self-contained, source-neutral shape rather than
# reaching into another package's internals. The downstream validators
# still enforce their own accepted values at evaluation time regardless
# of how this module types its fields.


@dataclass(frozen=True)
class AdapterCandle:
    """One source-neutral OHLC bar. A future adapter would populate this
    from historical/live bar data for whichever vendor it wraps; this
    module never fetches one itself."""

    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int] = None


@dataclass(frozen=True)
class AdapterOptionQuote:
    """One source-neutral option contract quote/snapshot. Every field is
    optional other than what the caller already has in hand -- this
    module never fetches an option chain or selects a contract."""

    expiration: Optional[str] = None
    dte: Optional[int] = None
    strike: Optional[float] = None
    premium: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    spread_percent: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    delta: Optional[float] = None
    theta: Optional[float] = None
    iv: Optional[float] = None
    earnings_risk: Optional[str] = None
    event_risk: Optional[str] = None


@dataclass(frozen=True)
class AdapterUnderlyingSnapshot:
    """Source-neutral snapshot of the underlying's own recent price
    action and any levels the caller has already identified. This module
    never detects levels itself -- resistance/support/gamma levels are
    whatever the caller already has in hand."""

    spot_price: Optional[float] = None
    resistance_levels: tuple[float, ...] = ()
    support_levels: tuple[float, ...] = ()
    gamma_resistance: Optional[float] = None
    gamma_support: Optional[float] = None


@dataclass(frozen=True)
class AdapterMarketContextSnapshot:
    """Source-neutral snapshot of broader market-context data (SPY/QQQ/
    GEX/Signa/HTF/event-risk) the caller has already gathered from one or
    more sources. This module never fetches any of it -- GEX/gamma
    context and Signa in particular are not natively supplied by any
    market-data vendor audited in Increment 12; a caller must supply
    them from wherever it separately obtains them."""

    spy_trend: Optional[str] = None
    qqq_trend: Optional[str] = None
    spy_above_flip: Optional[bool] = None
    qqq_above_flip: Optional[bool] = None
    gex_regime: Optional[str] = None
    price_above_gex_flip: Optional[bool] = None
    signa_direction: Optional[str] = None
    signa_grade: Optional[str] = None
    signa_score: Optional[float] = None
    higher_timeframe_alignment: Optional[str] = None
    gap_direction: Optional[str] = None
    distance_to_gamma_resistance: Optional[float] = None
    distance_to_gamma_support: Optional[float] = None
    event_risk: Optional[str] = None
