"""Derive the proposed broader V1 options universe without changing live scanning.

The historical 150-symbol coverage corpus is immutable research provenance.  V1
must not edit it in place.  This module derives a current equity/ETF candidate
universe from that corpus for preflight only:

* Block's stale historical symbol ``SQ`` is normalized to ``XYZ``.
* ``VIX`` remains in the historical corpus but is excluded from the V1 equity
  bar universe because it is a reference index, not an equity/ETF symbol for
  this scanner path.

Nothing in this module changes ``OPTIONS_SCANNER_WATCHLIST`` or activates a
symbol.  Promotion into V1 remains a separate, explicit step after capacity and
provider preflight.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "research" / "coverage" / "options_watchlist_150.csv"
EXPECTED_SOURCE_COUNT = 150
EXPECTED_CANDIDATE_COUNT = 149
SYMBOL_ALIASES = {"SQ": "XYZ"}
EXCLUDED_SYMBOLS = {"VIX": "reference_index_not_equity_etf_bar_universe"}
REQUIRED_CONTEXT = {"SPY", "QQQ"}
LEGACY_V1 = {"AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ"}


@dataclass(frozen=True)
class UniverseEntry:
    ticker: str
    sector: str
    notes: str
    source_ticker: str


def _normalize_symbol(symbol: str) -> str:
    raw = symbol.strip().upper()
    return SYMBOL_ALIASES.get(raw, raw)


def load_candidate_universe(path: Path = DEFAULT_SOURCE) -> tuple[UniverseEntry, ...]:
    """Return the validated proposed V1 universe; never writes ``path``."""
    rows: list[UniverseEntry] = []
    source_symbols: list[str] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["Ticker", "Sector", "Notes"]:
            raise ValueError(f"unexpected universe header: {reader.fieldnames!r}")
        for row in reader:
            source = (row.get("Ticker") or "").strip().upper()
            if not source:
                raise ValueError("blank ticker in source universe")
            source_symbols.append(source)
            if source in EXCLUDED_SYMBOLS:
                continue
            rows.append(
                UniverseEntry(
                    ticker=_normalize_symbol(source),
                    sector=(row.get("Sector") or "").strip(),
                    notes=(row.get("Notes") or "").strip(),
                    source_ticker=source,
                )
            )

    if len(source_symbols) != EXPECTED_SOURCE_COUNT:
        raise ValueError(
            f"source universe drift: expected {EXPECTED_SOURCE_COUNT}, got {len(source_symbols)}"
        )
    if len(source_symbols) != len(set(source_symbols)):
        raise ValueError("duplicate ticker in source universe")

    tickers = [row.ticker for row in rows]
    if len(tickers) != EXPECTED_CANDIDATE_COUNT:
        raise ValueError(
            f"candidate universe drift: expected {EXPECTED_CANDIDATE_COUNT}, got {len(tickers)}"
        )
    if len(tickers) != len(set(tickers)):
        raise ValueError("duplicate ticker after alias normalization")
    if REQUIRED_CONTEXT - set(tickers):
        raise ValueError(f"missing required context symbols: {sorted(REQUIRED_CONTEXT - set(tickers))}")
    if LEGACY_V1 - set(tickers):
        raise ValueError(f"missing legacy V1 symbols: {sorted(LEGACY_V1 - set(tickers))}")
    if set(SYMBOL_ALIASES) & set(tickers):
        raise ValueError("stale aliased symbol survived normalization")
    if set(EXCLUDED_SYMBOLS) & set(tickers):
        raise ValueError("excluded reference symbol survived normalization")
    return tuple(rows)


def ticker_list(entries: Iterable[UniverseEntry]) -> tuple[str, ...]:
    return tuple(row.ticker for row in entries)
