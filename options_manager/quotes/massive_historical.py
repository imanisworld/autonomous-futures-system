"""Pure normalization for Massive historical option quote evidence.

This module performs no network I/O, credential access, file writes, broker
calls, or runtime activation. It converts caller-supplied Massive quote rows
plus frozen contract identity into the canonical QuoteRetentionInput shape.

Massive historical quote rows do not prove the selector's historical
volume/open-interest/delta/IV fields. Those fields are intentionally left
missing so canonical retention fails closed until separate causal provenance
exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Mapping

from .retention import QuoteRetentionInput, QuoteSource


MASSIVE_HISTORICAL_QUOTE_SOURCE = QuoteSource.MASSIVE_OPTIONS_QUOTES.value


@dataclass(frozen=True, kw_only=True)
class HistoricalOptionIdentity:
    contract_id: str
    underlying: str
    expiration: str
    strike: float
    right: str


def sip_timestamp_ns_to_iso8601(value: object) -> str | None:
    """Convert one positive SIP nanosecond timestamp without losing digits."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("sip_timestamp must be a positive integer nanosecond timestamp")
    seconds, nanos = divmod(value, 1_000_000_000)
    dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}+00:00"


def _finite_positive(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{name} must be finite and > 0")
    return parsed


def _right(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in {"C", "CALL"}:
        return "CALL"
    if text in {"P", "PUT"}:
        return "PUT"
    raise ValueError("right must identify CALL or PUT")


def normalize_massive_historical_quote(
    *,
    identity: HistoricalOptionIdentity,
    quote_row: Mapping[str, object],
    decision_ts: str,
) -> QuoteRetentionInput:
    """Normalize only facts proven by one Massive historical quote row.

    The returned object is intentionally incomplete for selector replay:
    volume, open_interest, delta, and iv are always None. Supplying similarly
    named fields through the quote payload cannot upgrade them because this
    endpoint is not their causal source.
    """
    if not isinstance(quote_row, Mapping):
        raise ValueError("quote_row must be a mapping")

    contract_id = str(identity.contract_id or "").strip().upper()
    underlying = str(identity.underlying or "").strip().upper()
    expiration = str(identity.expiration or "").strip()
    if not contract_id or not underlying or not expiration:
        raise ValueError("contract identity fields are required")

    strike = _finite_positive(identity.strike, "strike")
    right = _right(identity.right)

    bid = quote_row.get("bid_price")
    ask = quote_row.get("ask_price")
    quote_ts = sip_timestamp_ns_to_iso8601(quote_row.get("sip_timestamp"))

    return QuoteRetentionInput(
        contract_id=contract_id,
        underlying=underlying,
        expiration=expiration,
        strike=strike,
        right=right,
        bid=bid if isinstance(bid, (int, float)) and not isinstance(bid, bool) else None,
        ask=ask if isinstance(ask, (int, float)) and not isinstance(ask, bool) else None,
        quote_ts=quote_ts,
        decision_ts=decision_ts,
        source=MASSIVE_HISTORICAL_QUOTE_SOURCE,
        volume=None,
        open_interest=None,
        delta=None,
        iv=None,
    )
