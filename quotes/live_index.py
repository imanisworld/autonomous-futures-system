"""
quotes/live_index.py

DISPLAY-ONLY reference price for the dashboard and Discord alerts.

MES/MNQ track their parent CME index future 1:1, so we read the front-month
future quote (ES=F / NQ=F) from a public HTTP endpoint (Yahoo Finance chart).

⚠️ This is a REFERENCE / DISPLAY price ONLY. It is NOT the broker execution
price and MUST NEVER be used for execution, signal validation, stop/target
calculation, risk checks, or trade decisions. Trading logic does not read it.
A Tradovate WebSocket feed (broker-exact) may be added later behind a flag.

Read-only and fail-soft: every error returns a status, never raises, so the
trading pipeline is unaffected when the upstream is unavailable.
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

# Human-facing source label (per product spec).
SOURCE_LABEL = "ES=F/NQ=F HTTP proxy"
KIND = "reference"

_CACHE_TTL_SECONDS = 15.0      # within this age → FRESH (no refetch)
_STALE_MAX_SECONDS = 600.0     # beyond this, a cached value is too old → UNAVAILABLE

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


def _quote(symbol: str, price: Optional[float], age_seconds: Optional[float], status: str) -> dict:
    return {
        "price": round(price, 2) if price is not None else None,
        "symbol": symbol,
        "source": SOURCE_LABEL,
        "age_seconds": int(age_seconds) if age_seconds is not None else None,
        "status": status,            # FRESH | STALE | UNAVAILABLE
        "kind": KIND,                # display-only reference, never execution
    }


def get_live_quote(instrument: str) -> Optional[dict]:
    """Return a display-only reference quote for the instrument's index future.

    Returns a dict with price/symbol/source/age_seconds/status, or None when the
    instrument has no index proxy. Status is one of FRESH / STALE / UNAVAILABLE.
    Never raises; trading logic must not depend on this value.
    """
    symbol = index_symbol_for(instrument)
    if symbol is None:
        return None

    now = time.monotonic()
    cached = _cache.get(symbol)

    # Fresh cache hit — serve without refetching.
    if cached is not None and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return _quote(symbol, cached[1], now - cached[0], "FRESH")

    # Try a live fetch.
    price = _fetch_price(symbol)
    if price is not None:
        _cache[symbol] = (now, price)
        return _quote(symbol, price, 0.0, "FRESH")

    # Upstream failed — serve cached value as STALE until it ages out entirely.
    if cached is not None:
        age = now - cached[0]
        if age <= _STALE_MAX_SECONDS:
            return _quote(symbol, cached[1], age, "STALE")

    # No usable value.
    return _quote(symbol, None, None, "UNAVAILABLE")
