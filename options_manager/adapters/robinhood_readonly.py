"""options_manager/adapters/robinhood_readonly.py

Read-only Robinhood data normalization. Every function here accepts data
a caller already obtained elsewhere and maps it into one of this
package's existing source-neutral shapes (AdapterOptionQuote,
AdapterUnderlyingSnapshot) or a small, local, account-number-free shape
for holdings data (RobinhoodAccountSummary, RobinhoodPosition). Nothing
here reaches a network, a broker, or any external service -- these are
pure mapping functions only, exactly like row_builder.py's translation
of already-fetched adapter data.

This module's public surface is limited to the five functions below and
nothing else. No broker instruction of any kind can be created, changed,
or removed through anything in this file, under any name. Missing fields
in the raw input are always left as None on the returned model rather
than guessed at, matching every other adapter/translation module in this
codebase.

Does not import alert_ranker (a separate system with its own login
boundary), options_companion, execution, webhook, or risk/risk_engine.py.
Never reads or mutates the live-options trading enablement flag. Never
logs, stores, or returns any login material -- there is no login
material anywhere in this module's inputs or outputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .base import AdapterOptionQuote, AdapterUnderlyingSnapshot


@dataclass(frozen=True)
class RobinhoodAccountSummary:
    """Account-level figures only -- no account number, no routing or
    identifying detail of any kind."""

    cash_available: Optional[float] = None
    cash: Optional[float] = None
    portfolio_value: Optional[float] = None
    equity: Optional[float] = None


@dataclass(frozen=True)
class RobinhoodPosition:
    """One held position's display figures only."""

    ticker: Optional[str] = None
    quantity: Optional[float] = None
    average_cost_basis: Optional[float] = None
    current_price: Optional[float] = None


def _first_present(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _map_option_quote(raw: dict[str, Any]) -> AdapterOptionQuote:
    expiration = _first_present(raw, "expiration_date", "expiration", "expiry")
    bid = _optional_float(_first_present(raw, "bid_price", "bid"))
    ask = _optional_float(_first_present(raw, "ask_price", "ask"))

    spread_percent = None
    if bid is not None and ask is not None and ask > 0:
        spread_percent = (ask - bid) / ask * 100.0

    return AdapterOptionQuote(
        expiration=str(expiration) if expiration is not None else None,
        dte=_optional_int(_first_present(raw, "dte", "days_to_expiration")),
        strike=_optional_float(_first_present(raw, "strike_price", "strike")),
        premium=_optional_float(_first_present(raw, "mark_price", "premium")),
        bid=bid,
        ask=ask,
        spread_percent=spread_percent,
        volume=_optional_int(_first_present(raw, "volume")),
        open_interest=_optional_int(_first_present(raw, "open_interest")),
        delta=_optional_float(_first_present(raw, "delta")),
        theta=_optional_float(_first_present(raw, "theta")),
        iv=_optional_float(_first_present(raw, "implied_volatility", "iv")),
        earnings_risk=None,
        event_risk=None,
    )


def normalize_option_quote(raw: dict[str, Any]) -> AdapterOptionQuote:
    """Maps one already-obtained raw contract quote dict into an
    AdapterOptionQuote. `earnings_risk`/`event_risk` are always left None
    here -- a quote payload does not natively carry either, and this
    function never invents a value for a field it wasn't given."""
    return _map_option_quote(raw)


def normalize_option_chain(raw: list[dict[str, Any]]) -> list[AdapterOptionQuote]:
    """Maps an already-obtained list of raw contract dicts into
    AdapterOptionQuote entries, skipping any row that fails to map
    rather than fabricating a value for it."""
    quotes: list[AdapterOptionQuote] = []
    for row in raw:
        try:
            quotes.append(_map_option_quote(row))
        except (TypeError, ValueError):
            continue
    return quotes


def normalize_underlying_quote(raw: dict[str, Any]) -> AdapterUnderlyingSnapshot:
    """Maps an already-obtained raw underlying quote dict into an
    AdapterUnderlyingSnapshot. This module never detects levels itself --
    resistance_levels/support_levels/gamma_resistance/gamma_support are
    always left at their defaults; only spot_price is populated here."""
    spot_price = _optional_float(_first_present(raw, "mark_price", "price"))
    return AdapterUnderlyingSnapshot(spot_price=spot_price)


def normalize_account_summary(raw: dict[str, Any]) -> RobinhoodAccountSummary:
    """Maps an already-obtained raw account dict into a
    RobinhoodAccountSummary. Deliberately carries no account number or
    other identifying detail -- only the four display figures below."""
    return RobinhoodAccountSummary(
        cash_available=_optional_float(_first_present(raw, "cash_available")),
        cash=_optional_float(_first_present(raw, "cash")),
        portfolio_value=_optional_float(_first_present(raw, "portfolio_value")),
        equity=_optional_float(_first_present(raw, "equity")),
    )


def normalize_portfolio_positions(raw: list[dict[str, Any]]) -> list[RobinhoodPosition]:
    """Maps an already-obtained list of raw position dicts into
    RobinhoodPosition entries, skipping any row that fails to map rather
    than fabricating a value for it."""
    positions: list[RobinhoodPosition] = []
    for row in raw:
        try:
            positions.append(
                RobinhoodPosition(
                    ticker=_first_present(row, "symbol", "ticker"),
                    quantity=_optional_float(_first_present(row, "quantity")),
                    average_cost_basis=_optional_float(
                        _first_present(row, "average_cost_basis")
                    ),
                    current_price=_optional_float(
                        _first_present(row, "current_price", "mark_price")
                    ),
                )
            )
        except (TypeError, ValueError):
            continue
    return positions
