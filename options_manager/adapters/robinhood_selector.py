"""options_manager/adapters/robinhood_selector.py

Read-only Robinhood -> selector candidates. Extends the existing
``robinhood_readonly`` adapter (same package, same already-obtained-data
discipline) so that real Robinhood option instrument rows and option quote
rows become ``options_manager.contracts.ContractCandidate`` values the
fail-closed shortlist can evaluate. It is not a second provider path: it
reuses ``normalize_option_quote`` for the numeric fields and adds only
what the selector needs -- symbol, ticker, CALL/PUT, DTE, and provenance.

Boundary rules (every one test-asserted):
  * pure mapping over data the caller already fetched; no network, no
    credentials, no environment, no broker or order surface;
  * non-finite provider numbers ("nan", "inf", "") are rejected at this
    boundary, never coerced;
  * the quote's mark is preserved separately from any planned entry
    premium (which this module never invents);
  * spread is NOT computed here -- the selector owns the one shared
    spread definition;
  * earnings/event risk stay ``None`` unless the caller explicitly
    supplies resolved values, so the selector fails closed on them;
  * the provider's own ``updated_at`` is preserved and a quote stamped
    after ``retrieved_at`` is rejected (a source cannot report the future).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Mapping, Optional, Sequence

from ..contracts import ContractCandidate
from .robinhood_readonly import normalize_option_quote

PROVIDER = "robinhood-readonly"
Direction = Literal["CALL", "PUT"]
RiskLevel = Literal["NONE", "LOW", "HIGH"]
_NUMERIC_FIELDS = ("strike", "premium", "bid", "ask", "volume", "open_interest", "delta", "theta", "iv")


@dataclass(frozen=True)
class SelectorCandidateRecord:
    """One selector candidate plus the provenance the selector does not carry."""

    candidate: ContractCandidate
    mark: Optional[float]  # provider quote mark; NOT a planned entry premium
    planned_entry_premium: Optional[float] = None  # never set here
    provider: str = PROVIDER
    instrument_id: str = ""
    provider_updated_at: Optional[str] = None
    retrieved_at: str = ""
    raw_type: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RejectedRow:
    instrument_id: str
    symbol: str
    reason_code: str
    reason: str


@dataclass(frozen=True)
class SelectorCandidateBuild:
    ticker: str
    direction: Direction
    retrieved_at: str
    records: tuple[SelectorCandidateRecord, ...]
    rejected: tuple[RejectedRow, ...]

    @property
    def candidates(self) -> tuple[ContractCandidate, ...]:
        return tuple(r.candidate for r in self.records)


def _finite(value: Any) -> bool:
    return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and value not in (float("inf"), float("-inf")))


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _parse_date(value: Any) -> Optional[date]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _direction_of(raw_type: Any) -> Optional[Direction]:
    text = str(raw_type or "").strip().upper()
    if text in ("CALL", "C"):
        return "CALL"
    if text in ("PUT", "P"):
        return "PUT"
    return None


def contract_symbol(chain_symbol: str, expiration: str, direction: Direction, strike: float) -> str:
    strike_text = f"{strike:g}"
    return f"{chain_symbol.upper()} {expiration} {direction[0]} {strike_text}"


def build_selector_candidates(
    instruments: Sequence[Mapping[str, Any]],
    quotes: Sequence[Mapping[str, Any]],
    *,
    ticker: str,
    direction: Direction,
    retrieved_at: str,
    as_of: Optional[date] = None,
    earnings_risk: Optional[RiskLevel] = None,
    event_risk: Optional[RiskLevel] = None,
) -> SelectorCandidateBuild:
    """Join already-fetched instrument rows (``get_option_instruments``) with
    quote rows (``get_option_quotes``) on instrument id and map each pair
    into a ContractCandidate. Rows that do not map are reported in
    ``rejected`` with a reason code; nothing is guessed."""
    ticker_value = str(ticker or "").strip().upper()
    retrieved = _parse_ts(retrieved_at)
    if not ticker_value or direction not in ("CALL", "PUT") or retrieved is None:
        raise ValueError("ticker, direction CALL/PUT, and a timezone-aware retrieved_at are required")
    reference_day = as_of or retrieved.date()
    quotes_by_id: dict[str, Mapping[str, Any]] = {}
    for row in quotes:
        key = str(row.get("instrument_id") or row.get("id") or "").strip()
        if key:
            quotes_by_id.setdefault(key, row)

    records: list[SelectorCandidateRecord] = []
    rejected: list[RejectedRow] = []
    for row in instruments:
        instrument_id = str(row.get("id") or row.get("instrument_id") or "").strip()
        chain_symbol = str(row.get("chain_symbol") or row.get("underlying") or "").strip().upper()
        expiration = str(row.get("expiration_date") or row.get("expiration") or "").strip()[:10]
        raw_type = str(row.get("type") or row.get("contract_type") or "")
        row_direction = _direction_of(raw_type)
        symbol = f"{chain_symbol or '?'} {expiration or '?'} {raw_type or '?'} {row.get('strike_price') or row.get('strike') or '?'}"

        def reject(code: str, reason: str) -> None:
            rejected.append(RejectedRow(instrument_id=instrument_id, symbol=symbol, reason_code=code, reason=reason))

        if not instrument_id:
            reject("missing_instrument_id", "instrument row has no id")
            continue
        if chain_symbol != ticker_value:
            reject("ticker_mismatch", f"instrument chain_symbol {chain_symbol!r} != {ticker_value!r}")
            continue
        if row_direction is None:
            reject("unknown_type", f"instrument type {raw_type!r} is not call/put")
            continue
        if row_direction != direction:
            reject("direction_mismatch", f"instrument is {row_direction}, requested {direction}")
            continue
        if str(row.get("state") or "active").lower() != "active" or str(row.get("tradability") or "tradable").lower() != "tradable":
            reject("not_tradable", f"state={row.get('state')!r} tradability={row.get('tradability')!r}")
            continue
        expiry = _parse_date(expiration)
        if expiry is None:
            reject("missing_expiration", f"expiration {expiration!r} unparseable")
            continue
        quote = quotes_by_id.get(instrument_id)
        if quote is None:
            reject("missing_quote", "no quote row for instrument")
            continue
        merged: dict[str, Any] = dict(row)
        merged.update(quote)
        merged.setdefault("strike_price", row.get("strike_price") or row.get("strike"))
        try:
            mapped = normalize_option_quote(merged)
        except (TypeError, ValueError, OverflowError) as exc:
            reject("unmappable", f"quote does not map: {exc}")
            continue
        bad = next((name for name in _NUMERIC_FIELDS if not _finite(getattr(mapped, name))), None)
        if bad is not None:
            reject(f"non_finite_{bad}", f"provider {bad} is not a finite number")
            continue
        if mapped.strike is None or mapped.strike <= 0:
            reject("missing_strike", "strike missing or non-positive")
            continue
        updated_raw = quote.get("updated_at")
        updated = _parse_ts(updated_raw)
        if updated_raw is not None and updated is None:
            reject("bad_updated_at", f"provider updated_at {updated_raw!r} unparseable or naive")
            continue
        if updated is not None and updated > retrieved:
            reject("future_updated_at", f"provider updated_at {updated.isoformat()} is after retrieved_at")
            continue
        dte = (expiry - reference_day).days
        candidate = ContractCandidate(
            symbol=contract_symbol(chain_symbol, expiration, direction, mapped.strike),
            ticker=ticker_value,
            direction=direction,
            expiration=expiration,
            dte=dte,
            strike=mapped.strike,
            premium=mapped.premium,  # provider mark used only as current premium evidence, not a planned entry
            bid=mapped.bid,
            ask=mapped.ask,
            volume=mapped.volume,
            open_interest=mapped.open_interest,
            delta=mapped.delta,
            theta=mapped.theta,
            iv=mapped.iv,
            earnings_risk=earnings_risk,
            event_risk=event_risk,
        )
        records.append(
            SelectorCandidateRecord(
                candidate=candidate,
                mark=mapped.premium,
                instrument_id=instrument_id,
                provider_updated_at=updated.isoformat() if updated else None,
                retrieved_at=retrieved.isoformat(),
                raw_type=raw_type,
                provenance={
                    "provider": PROVIDER,
                    "instrument_id": instrument_id,
                    "chain_id": str(row.get("chain_id") or ""),
                    "quote_updated_at": updated.isoformat() if updated else None,
                    "retrieved_at": retrieved.isoformat(),
                    "as_of": reference_day.isoformat(),
                    "spread_definition": "selector (midpoint) -- not computed here",
                },
            )
        )
    return SelectorCandidateBuild(
        ticker=ticker_value, direction=direction, retrieved_at=retrieved.isoformat(), records=tuple(records), rejected=tuple(rejected)
    )
