"""Outright futures price units for local simulation, never execution permission.

Sources (verified 2026-09-15):
https://www.cmegroup.com/articles/faqs/frequently-asked-questions-micro-e-mini-equity-index-futures.html
https://www.cmegroup.com/education/lessons/micro-gold-and-micro-silver-futures-product-overview
https://www.cmegroup.com/trading/energy/files/micro-wti-crude-oil-futures-fact-card.pdf
https://www.cmegroup.com/trading/files/micro-bitcoin-futures-fact-card-retail-us.pdf
ES/NQ retain their existing values. Commissions, calendars, feed subscriptions,
and strategy/broker eligibility are deliberately not inferred from price units.
"""
from types import MappingProxyType

TICK_SIZE = MappingProxyType({
    "MNQ": 0.25, "MES": 0.25, "ES": 0.25, "NQ": 0.25,
    "MGC": 0.10, "MCL": 0.01, "M2K": 0.10, "MBT": 5.0,
})
TICK_VALUE = MappingProxyType({
    "MNQ": 0.50, "MES": 1.25, "ES": 12.50, "NQ": 5.0,
    "MGC": 1.0, "MCL": 1.0, "M2K": 0.50, "MBT": 0.50,
})


def contract_economics(instrument: str) -> tuple[float, float]:
    """Require an exact canonical root; never guess another contract's units."""
    import math

    try:
        tick, value = TICK_SIZE[instrument], TICK_VALUE[instrument]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Unsupported paper instrument: {instrument!r}") from exc
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (tick, value)):
        raise ValueError(f"Invalid paper economics: {instrument!r}")
    return tick, value
