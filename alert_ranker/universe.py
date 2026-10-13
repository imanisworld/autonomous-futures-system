"""Canonical options-scanner watchlist universe.

Single source of truth for the default `OPTIONS_SCANNER_WATCHLIST`. Tier order
is the scan order: CORE, then EXPANDED, then CONDITIONAL. Qualification,
liquidity, risk, setup, Signa, and alert rules are unchanged — membership here
only expands which underlyings are scanned.

CONDITIONAL symbols (today: KWEB) are scanned for underlying setups, but their
option contracts must still pass the existing V1 liquidity/quality gates before
any alert or plan is allowed. They are not treated as automatically liquid.
"""

from __future__ import annotations

# High-priority liquid underlyings — scanned first.
CORE: tuple[str, ...] = (
    "SPY",
    "QQQ",
    "IWM",
    "NVDA",
    "TSLA",
    "AAPL",
    "MSFT",
    "AMZN",
    "META",
    "GOOGL",
    "AMD",
    "AVGO",
    "PLTR",
    "NFLX",
    "JPM",
    "BAC",
    "COIN",
    "HOOD",
    "TLT",
    "GLD",
    "SLV",
    "IBIT",
    "SMH",
)

# Broader liquid optionable set — scanned after CORE.
EXPANDED: tuple[str, ...] = (
    "ORCL",
    "CRM",
    "MU",
    "QCOM",
    "ARM",
    "TSM",
    "SMCI",
    "INTC",
    "SOFI",
    "RIVN",
    "UBER",
    "ABNB",
    "DIS",
    "WMT",
    "COST",
    "HD",
    "NKE",
    "BA",
    "CAT",
    "GE",
    "F",
    "GM",
    "XOM",
    "CVX",
    "OXY",
    "COP",
    "C",
    "WFC",
    "GS",
    "MS",
    "SCHW",
    "LLY",
    "UNH",
    "PFE",
    "XLF",
    "XLK",
    "XLE",
    "XBI",
    "HYG",
    "EEM",
    "FXI",
    "DIA",
)

# Scanned last. Underlying setups allowed; option contracts still require the
# existing liquidity/quality gate (wide spreads / low OI / missing data reject).
CONDITIONAL: tuple[str, ...] = ("KWEB",)

_TIER_ORDER: tuple[tuple[str, ...], ...] = (CORE, EXPANDED, CONDITIONAL)


def _dedupe_preserve(symbols: tuple[str, ...] | list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in symbols:
        symbol = str(raw or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    return out


DEFAULT_WATCHLIST: tuple[str, ...] = tuple(
    _dedupe_preserve([symbol for tier in _TIER_ORDER for symbol in tier])
)

CORE_SET: frozenset[str] = frozenset(CORE)
EXPANDED_SET: frozenset[str] = frozenset(EXPANDED)
CONDITIONAL_SET: frozenset[str] = frozenset(CONDITIONAL)


def default_watchlist_csv() -> str:
    """Comma-separated default for env / .env.example consumers."""
    return ",".join(DEFAULT_WATCHLIST)


def resolve_watchlist(raw: str | None) -> list[str]:
    """Resolve the configured watchlist.

    Empty / unset -> canonical DEFAULT_WATCHLIST (CORE, EXPANDED, CONDITIONAL).
    Explicit CSV -> listed symbols, de-duplicated, uppercased, preserving order.
    """
    if raw is None or not str(raw).strip():
        return list(DEFAULT_WATCHLIST)
    return _dedupe_preserve(str(raw).split(","))


def watchlist_tier(symbol: str) -> str | None:
    """Return ``core`` / ``expanded`` / ``conditional``, or None if unknown."""
    ticker = str(symbol or "").strip().upper()
    if ticker in CORE_SET:
        return "core"
    if ticker in EXPANDED_SET:
        return "expanded"
    if ticker in CONDITIONAL_SET:
        return "conditional"
    return None
