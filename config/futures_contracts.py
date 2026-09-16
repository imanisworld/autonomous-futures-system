"""Outright futures price units for local simulation, never execution permission.

Sources (verified 2026-09-15):
https://www.cmegroup.com/articles/faqs/frequently-asked-questions-micro-e-mini-equity-index-futures.html
https://www.cmegroup.com/education/lessons/micro-gold-and-micro-silver-futures-product-overview
https://www.cmegroup.com/trading/energy/files/micro-wti-crude-oil-futures-fact-card.pdf
https://www.cmegroup.com/trading/files/micro-bitcoin-futures-fact-card-retail-us.pdf
ES/NQ retain their existing values. Commissions, calendars, feed subscriptions,
and strategy/broker eligibility are deliberately not inferred from price units.

This module is the ONLY place tick size / tick value / point value may be
defined. Every runtime, replay, observation and evidence consumer resolves
through it. Unknown roots never inherit another contract's units: economic
paths raise ``UnsupportedContractError`` (fail closed) and observation paths
use ``optional_tick_size`` to return an explicit "no metadata" result.
"""
from __future__ import annotations

import math
import re
from types import MappingProxyType
from typing import Optional

TICK_SIZE = MappingProxyType({
    "MNQ": 0.25, "MES": 0.25, "ES": 0.25, "NQ": 0.25,
    "MGC": 0.10, "MCL": 0.01, "M2K": 0.10, "MBT": 5.0,
})
TICK_VALUE = MappingProxyType({
    "MNQ": 0.50, "MES": 1.25, "ES": 12.50, "NQ": 5.0,
    "MGC": 1.0, "MCL": 1.0, "M2K": 0.50, "MBT": 0.50,
})

# Dollar value of a one-point move, derived mechanically (tick value / tick
# size). Never a second manually maintained table.
POINT_VALUE = MappingProxyType({
    root: round(TICK_VALUE[root] / TICK_SIZE[root], 10) for root in TICK_SIZE
})

SUPPORTED_ROOTS: tuple[str, ...] = tuple(TICK_SIZE)

# A real futures contract suffix: CME month code + 1-4 digit year (Z6, Z26, Z2026).
_CONTRACT_SUFFIX = re.compile(r"^[FGHJKMNQUVXZ]\d{1,4}$")


class UnsupportedContractError(ValueError):
    """Raised when contract economics are requested for an unknown root."""


def contract_root(symbol: object) -> Optional[str]:
    """Exact canonical root for a symbol, or ``None``.

    Accepts the canonical root itself, an exchange prefix, a continuous
    ``1!``/``!`` suffix, or a month+year contract suffix. Matching is exact
    against the supported roots (longest first) so ``M2K`` keeps its digit and
    ``MK2``/``ESTC``/``MNQX`` never acquire a root. This does NOT grant
    ingestion, strategy or broker eligibility — it only names the contract.
    """
    if symbol is None:
        return None
    sym = str(symbol).split(":")[-1].upper().strip()
    if not sym:
        return None
    if sym.endswith("1!"):
        sym = sym[:-2]
    sym = sym.rstrip("!")
    for root in sorted(SUPPORTED_ROOTS, key=len, reverse=True):
        if sym == root:
            return root
        if sym.startswith(root) and _CONTRACT_SUFFIX.match(sym[len(root):]):
            return root
    return None


def contract_economics(instrument: str) -> tuple[float, float]:
    """Require an exact canonical root; never guess another contract's units."""
    try:
        tick, value = TICK_SIZE[instrument], TICK_VALUE[instrument]
    except (KeyError, TypeError) as exc:
        raise UnsupportedContractError(f"Unsupported paper instrument: {instrument!r}") from exc
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (tick, value)):
        raise UnsupportedContractError(f"Invalid paper economics: {instrument!r}")
    return tick, value


def tick_size(instrument: str) -> float:
    """Tick size for an exact canonical root; raises on unknown roots."""
    return contract_economics(instrument)[0]


def tick_value(instrument: str) -> float:
    """Dollar value of one tick for an exact canonical root; raises on unknown roots."""
    return contract_economics(instrument)[1]


def point_value(instrument: str) -> float:
    """Dollars per one-point move, derived as tick value / tick size."""
    tick, value = contract_economics(instrument)
    return value / tick


def symbol_economics(symbol: object) -> tuple[float, float]:
    """``contract_economics`` for a dated/continuous/prefixed symbol; raises on unknown."""
    root = contract_root(symbol)
    if root is None:
        raise UnsupportedContractError(f"Unsupported contract symbol: {symbol!r}")
    return contract_economics(root)


def optional_tick_size(symbol: object) -> Optional[float]:
    """Tick size for a symbol, or ``None`` when no proven metadata exists.

    For observation-only paths that must not crash ingestion but also must not
    fabricate geometry. Callers must treat ``None`` as "unsupported".
    """
    root = contract_root(symbol)
    if root is None:
        return None
    return TICK_SIZE[root]


def round_to_tick(price: float, symbol: object) -> float:
    """Round a price to the contract's tick grid; raises on unknown roots."""
    tick, _ = symbol_economics(symbol)
    return round(round(float(price) / tick) * tick, 4)
