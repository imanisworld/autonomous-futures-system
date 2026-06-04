"""
quotes/live_index.py

Lightweight live-price source for the Discord alert display line.

MES/MNQ track their parent CME index future 1:1, so we read the front-month
future quote (ES=F / NQ=F) from Yahoo Finance's public chart endpoint. This is
a DISPLAY price only — execution still anchors to the bar close / broker fill.
It is intentionally decoupled from the broker: Tradovate's REST API has no quote
endpoint (market data is WebSocket-only), so this gives an independent, correct
sanity price without the TradingView bar-close echo.

Read-only and fail-soft: every error returns None so notification/ingestion can
never break. Results are cached briefly to avoid hammering the upstream.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Instrument root → Yahoo front-month continuous future symbol.
_INDEX_SYMBOL: dict[str, str] = {
    "MES": "ES=F",
    "ES":  "ES=F",
    "MNQ": "NQ=F",
    "NQ":  "NQ=F",
    "MGC": "GC=F",
    "MCL": "CL=F",
}

_CACHE_TTL_SECONDS = 15.0
# symbol → (fetched_at_monotonic, price)
_cache: dict[str, tuple[float, float]] = {}

_YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_USER_AGENT = "Mozilla/5.0 (compatible; RiskSentinel/1.0)"


def _root(instrument: str) -> str:
    return instrument.replace("1!", "").replace("!", "").strip().upper()


def index_symbol_for(instrument: str) -> Optional[str]:
    """Return the Yahoo future symbol an instrument tracks, or None if unmapped."""
    root = _root(instrument)
    if root in _INDEX_SYMBOL:
        return _INDEX_SYMBOL[root]
    # Tolerate front-month suffixes like MESM6 → MES.
    for known, sym in _INDEX_SYMBOL.items():
        if root.startswith(known):
            return sym
    return None


def _fetch_price(symbol: str) -> Optional[float]:
    import httpx

    try:
        resp = httpx.get(
            _YAHOO_URL.format(symbol=symbol),
            params={"interval": "1m", "range": "1d"},
            headers={"User-Agent": _USER_AGENT},
            timeout=4.0,
        )
        resp.raise_for_status()
        meta = resp.json()["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        if price is None:
            return None
        return float(price)
    except Exception as exc:  # network, parse, schema — all non-fatal
        logger.warning("live_index quote fetch failed for %s: %s", symbol, exc)
        return None


def get_live_quote(instrument: str) -> Optional[dict]:
    """Return a fresh-ish live quote for the instrument's parent index future.

    Returns {"price": float, "symbol": str, "source": str} or None when the
    instrument is unmapped or the upstream is unavailable. Never raises.
    """
    symbol = index_symbol_for(instrument)
    if symbol is None:
        return None

    now = time.monotonic()
    cached = _cache.get(symbol)
    if cached is not None and (now - cached[0]) < _CACHE_TTL_SECONDS:
        price = cached[1]
    else:
        price = _fetch_price(symbol)
        if price is None:
            # Serve a slightly-stale cached value rather than nothing.
            if cached is not None:
                price = cached[1]
            else:
                return None
        else:
            _cache[symbol] = (now, price)

    return {"price": price, "symbol": symbol, "source": f"yahoo:{symbol}"}
