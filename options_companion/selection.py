"""Expiry / strike / entry selection over a chain snapshot.

Pure function over a ``ChainSnapshot`` (no I/O) so it is fully unit-testable.

Rules:
- Expiry: prefer same-day (DTE 0) IF available AND now < 14:00 ET; else the nearest
  expiry with DTE <= max_dte; else reject ``no_valid_expiry``.
- Strike: if greeks present, pick the contract whose |delta| is closest into
  ``[0.30, 0.45]``; else the nearest slightly-OTM strike for the contract type.
- Entry: bid/ask midpoint. Reject ``market_data_unavailable`` if missing; reject
  ``spread_too_wide`` if (ask-bid)/mid exceeds the threshold.
- Paper bracket (2R premium model): stop = 0.50 x entry, target = 2.00 x entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as _time
from typing import Optional
from zoneinfo import ZoneInfo

from .chain_provider import ChainContract, ChainSnapshot

_ET = ZoneInfo("America/New_York")
_SAME_DAY_CUTOFF = _time(14, 0)  # after 14:00 ET, don't open a 0-DTE companion

_DELTA_LOW = 0.30
_DELTA_HIGH = 0.45
_DELTA_TARGET = (_DELTA_LOW + _DELTA_HIGH) / 2.0


@dataclass(frozen=True)
class CompanionSelection:
    option_symbol: str
    contract_type: str
    expiry: str  # ISO date
    strike: float
    dte: int
    entry_mark: float
    stop_mark: float
    target_mark: float
    bid: float
    ask: float
    delta: Optional[float] = None


@dataclass(frozen=True)
class SelectionRejected:
    failed_rule: str
    reason: str


def select_contract(
    snapshot: ChainSnapshot,
    contract_type: str,
    *,
    now: datetime,
    max_dte: int = 2,
    max_spread_ratio: float = 0.25,
) -> CompanionSelection | SelectionRejected:
    if snapshot.error:
        return SelectionRejected("market_data_unavailable", f"chain error: {snapshot.error}")

    ctype = contract_type.upper()
    now_et = now.astimezone(_ET)
    today_et = now_et.date()

    typed = [c for c in snapshot.contracts if c.contract_type.upper() == ctype]
    if not typed:
        return SelectionRejected("no_valid_expiry", f"no {ctype} contracts in chain")

    # ── Expiry selection ──────────────────────────────────────────────────────
    by_dte: dict[int, list[ChainContract]] = {}
    for c in typed:
        dte = (c.expiry - today_et).days
        if dte < 0:
            continue  # already expired
        by_dte.setdefault(dte, []).append(c)
    if not by_dte:
        return SelectionRejected("no_valid_expiry", "no non-expired contracts")

    chosen_dte: Optional[int] = None
    if 0 in by_dte and now_et.time() < _SAME_DAY_CUTOFF:
        chosen_dte = 0
    else:
        eligible = sorted(d for d in by_dte if 0 <= d <= max_dte and d != 0)
        # 0-DTE only allowed pre-cutoff (handled above); after cutoff fall to >=1.
        if not eligible:
            # allow 0-DTE as a last resort only if it's the sole option AND pre-cutoff
            eligible = sorted(d for d in by_dte if 0 <= d <= max_dte)
            eligible = [d for d in eligible if not (d == 0 and now_et.time() >= _SAME_DAY_CUTOFF)]
        if not eligible:
            return SelectionRejected(
                "no_valid_expiry", f"no expiry within DTE<= {max_dte} (after 14:00 ET cutoff)"
            )
        chosen_dte = eligible[0]

    expiry_contracts = by_dte[chosen_dte]

    # ── Strike selection ──────────────────────────────────────────────────────
    contract = _pick_strike(expiry_contracts, ctype, snapshot.underlying_price)
    if contract is None:
        return SelectionRejected("no_valid_expiry", "no strike candidate for chosen expiry")

    # ── Entry / spread ────────────────────────────────────────────────────────
    if contract.bid is None or contract.ask is None or contract.bid <= 0 or contract.ask <= 0:
        return SelectionRejected("market_data_unavailable", "missing bid/ask on selected contract")
    entry = contract.mid
    if entry is None or entry <= 0:
        return SelectionRejected("market_data_unavailable", "non-positive midpoint")
    spread_ratio = (contract.ask - contract.bid) / entry
    if spread_ratio > max_spread_ratio:
        return SelectionRejected(
            "spread_too_wide",
            f"spread {spread_ratio:.2%} exceeds max {max_spread_ratio:.2%}",
        )

    entry = round(entry, 4)
    return CompanionSelection(
        option_symbol=contract.symbol,
        contract_type=ctype,
        expiry=contract.expiry.isoformat(),
        strike=contract.strike,
        dte=chosen_dte,
        entry_mark=entry,
        stop_mark=round(entry * 0.50, 4),
        target_mark=round(entry * 2.00, 4),
        bid=contract.bid,
        ask=contract.ask,
        delta=contract.delta,
    )


def _pick_strike(
    contracts: list[ChainContract],
    contract_type: str,
    underlying_price: Optional[float],
) -> Optional[ChainContract]:
    if not contracts:
        return None

    # Prefer delta-based selection when greeks are present.
    with_delta = [c for c in contracts if c.delta is not None]
    if with_delta:
        in_band = [c for c in with_delta if _DELTA_LOW <= abs(c.delta) <= _DELTA_HIGH]
        pool = in_band or with_delta
        return min(pool, key=lambda c: abs(abs(c.delta) - _DELTA_TARGET))

    # Fallback: nearest slightly-OTM strike (needs the underlying price).
    if underlying_price is not None:
        if contract_type == "CALL":
            otm = [c for c in contracts if c.strike >= underlying_price]
        else:
            otm = [c for c in contracts if c.strike <= underlying_price]
        pool = otm or contracts
        return min(pool, key=lambda c: abs(c.strike - underlying_price))

    # No greeks and no spot: pick the median strike as a neutral default.
    ordered = sorted(contracts, key=lambda c: c.strike)
    return ordered[len(ordered) // 2]
