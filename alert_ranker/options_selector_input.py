"""Pure bridge from read-only option-chain snapshots to canonical selector bytes.

No provider fetch, network I/O, file write, broker call, order construction,
or runtime activation occurs here. Callers supply already-fetched OptionChain
snapshots and decision-time facts; this bridge only normalizes their structural
representation into the frozen options-manager selector input contract.

Only lossless structural normalization is allowed:
- integer-valued provider count fields such as 500.0 become 500;
- DTE is derived mechanically from chain expiration and decision date;
- chain/row order is canonicalized for byte-stable evidence.

Missing, fractional, non-finite, stale/future, or otherwise invalid market
facts are not repaired here. The canonical selector must fail closed on them.
"""

from __future__ import annotations

from datetime import date, datetime
import math
from typing import Sequence

from alert_ranker.market_data import OptionChain, OptionContractQuote
from options_manager.contracts import (
    ContractSelectionInput,
    OptionChainRow,
    selection_input_json,
)


def _parse_decision_ts(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("decision_ts must be a non-empty timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("decision_ts must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("decision_ts must be timezone-aware")
    return parsed


def _parse_expiration(value: object) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("option chain expiration is required")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("option chain expiration must be canonical YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError("option chain expiration must be canonical YYYY-MM-DD")
    return parsed


def _provider_count(value: object) -> object:
    """Normalize only exact integer-valued provider counts; preserve invalids."""

    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def _row_from_quote(
    quote: OptionContractQuote,
    *,
    expiration: str,
    dte: int,
) -> OptionChainRow:
    return OptionChainRow(
        contract_id=quote.symbol,
        expiration=expiration,
        strike=quote.strike,
        right=quote.option_type,
        bid=quote.bid,
        ask=quote.ask,
        quote_ts=quote.quote_timestamp,
        volume=_provider_count(quote.volume),
        open_interest=_provider_count(quote.open_interest),
        delta=quote.delta,
        dte=dte,
    )


def _row_sort_key(row: OptionChainRow) -> tuple[object, ...]:
    def number(value: object) -> tuple[int, float | str]:
        if (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
        ):
            return (0, float(value))
        return (1, repr(value))

    return (
        str(row.expiration),
        str(row.right),
        number(row.strike),
        str(row.contract_id),
        number(row.bid),
        number(row.ask),
        str(row.quote_ts),
    )


def serialized_selector_input_from_option_chains(
    chains: Sequence[OptionChain],
    *,
    rule_sha256: str,
    decision_ts: str,
    underlying_price: float,
    direction: str,
) -> bytes:

    """Return canonical selector-input bytes from supplied chain snapshots.

    All chains must belong to one underlying and be error-free. Multiple
    expirations are supported so the canonical selector, rather than an
    upstream pre-selector, owns the frozen expiration choice.
    """

    if not chains:
        raise ValueError("at least one option chain snapshot is required")

    decision_dt = _parse_decision_ts(decision_ts)
    underlyings = {str(chain.underlying or "").strip().upper() for chain in chains}
    if "" in underlyings or len(underlyings) != 1:
        raise ValueError("option chain snapshots must have one non-empty underlying")

    rows: list[OptionChainRow] = []
    seen_expirations: set[str] = set()
    for chain in chains:
        if chain.error:
            raise ValueError(f"option chain snapshot is blocked: {chain.error}")
        expiration_date = _parse_expiration(chain.expiration)
        expiration = expiration_date.isoformat()
        if expiration in seen_expirations:
            raise ValueError(f"duplicate option chain expiration: {expiration}")
        seen_expirations.add(expiration)
        dte = (expiration_date - decision_dt.date()).days


        for quote in (*chain.calls, *chain.puts):
            rows.append(_row_from_quote(quote, expiration=expiration, dte=dte))

    value = ContractSelectionInput(
        rule_sha256=rule_sha256,
        decision_ts=decision_ts,
        underlying_price=underlying_price,
        direction=direction,
        chain=tuple(sorted(rows, key=_row_sort_key)),
    )
    return selection_input_json(value).encode("utf-8")
