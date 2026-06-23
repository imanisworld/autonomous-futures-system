"""Provider-agnostic option-chain interface + a Public implementation.

The ``ChainProvider`` protocol is the only surface the companion lane talks to, so
selection/resolution logic is fully testable against a mock before a live Public key
exists. ``PublicChainProvider`` reuses ``alert_ranker.market_data._HttpProvider`` for
its auth header, forbidden-path guard, and soft-fail (``last_error``) machinery, and
keeps order/account endpoints forbidden — the lane is strictly data-only.

NOTE: Public's exact option-chain + per-contract quote endpoint shapes are unconfirmed
(see https://public.com/api/docs). The paths/parsers below are deliberately tolerant
(multiple key fallbacks, soft-fail on shape mismatch); confirm against the live API
before the first real read. No order is ever placed regardless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional, Protocol

import httpx

from alert_ranker.market_data import _HttpProvider


@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    delta: Optional[float] = None
    error: Optional[str] = None

    @property
    def mid(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        if self.bid <= 0 or self.ask <= 0:
            return None
        return round((self.bid + self.ask) / 2.0, 4)


@dataclass(frozen=True)
class ChainContract:
    symbol: str
    expiry: date
    strike: float
    contract_type: str  # CALL | PUT
    bid: Optional[float] = None
    ask: Optional[float] = None
    delta: Optional[float] = None

    @property
    def mid(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        if self.bid <= 0 or self.ask <= 0:
            return None
        return round((self.bid + self.ask) / 2.0, 4)


@dataclass(frozen=True)
class ChainSnapshot:
    underlying: str
    contracts: list[ChainContract] = field(default_factory=list)
    underlying_price: Optional[float] = None
    error: Optional[str] = None


class ChainProvider(Protocol):
    """Read-only option chain + quote source."""

    last_error: str | None

    async def __aenter__(self) -> "ChainProvider":
        ...

    async def __aexit__(self, *_exc: object) -> None:
        ...

    async def fetch_chain(self, underlying: str, *, max_dte: int) -> ChainSnapshot:
        ...

    async def fetch_quote(self, option_symbol: str) -> OptionQuote:
        ...


# Path prefixes the companion lane is allowed to read. Order/account paths remain
# forbidden via _HttpProvider's FORBIDDEN_PATH_PARTS guard (/orders /accounts ...).
_PUBLIC_ALLOWED_PREFIXES = (
    "/marketdata",
    "/market-data",
    "/options",
    "/option",
    "/quotes",
    "/data",
)


class PublicChainProvider(_HttpProvider):
    """Read-only Public option-chain adapter (data-only; no order/account paths)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        max_dte: int = 2,
        client: httpx.AsyncClient | None = None,
    ):
        # config is only used by _HttpProvider.capabilities(), which this lane never
        # calls; pass None and drive everything off base_url/allowed_prefixes.
        super().__init__(
            config=None,  # type: ignore[arg-type]
            client=client,
            provider_name="public",
            base_url=base_url,
            token_value=None,
            allowed_prefixes=_PUBLIC_ALLOWED_PREFIXES,
        )
        self.api_key = (api_key or "").strip()
        self.max_dte = max_dte

    def _auth_headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    async def fetch_chain(self, underlying: str, *, max_dte: int) -> ChainSnapshot:
        symbol = underlying.upper()
        if not self.api_key:
            return ChainSnapshot(symbol, error="credentials_missing")
        payload = await self._get(
            f"/marketdata/options/chain/{symbol}",
            params={"symbol": symbol, "maxDte": max_dte},
            headers=self._auth_headers(),
        )
        contracts = _parse_contracts(payload)
        if not contracts and self.last_error is None:
            self.last_error = "unsupported_response_shape"
        return ChainSnapshot(
            underlying=symbol,
            contracts=contracts,
            underlying_price=_first_float(
                _first_payload(payload),
                ("underlying_price", "underlyingPrice", "spot", "last", "price"),
            ),
            error=self.last_error,
        )

    async def fetch_quote(self, option_symbol: str) -> OptionQuote:
        if not self.api_key:
            return OptionQuote(option_symbol, error="credentials_missing")
        payload = await self._get(
            f"/marketdata/options/quote/{option_symbol}",
            params={"symbol": option_symbol},
            headers=self._auth_headers(),
        )
        data = _first_payload(payload)
        if not data and self.last_error is None:
            self.last_error = "unsupported_response_shape"
        return OptionQuote(
            symbol=option_symbol,
            bid=_first_float(data, ("bid", "bid_price", "bp")),
            ask=_first_float(data, ("ask", "ask_price", "ap")),
            delta=_first_float(data, ("delta", "greeks_delta")),
            error=self.last_error,
        )


# ─── tolerant payload parsing (mirrors alert_ranker.market_data helpers) ──────────


def _parse_contracts(payload: dict[str, Any]) -> list[ChainContract]:
    rows = _contract_rows(payload)
    contracts: list[ChainContract] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        expiry = _parse_date(
            _first_str(row, ("expiry", "expiration", "expiration_date", "expirationDate"))
        )
        strike = _first_float(row, ("strike", "strike_price", "strikePrice"))
        ctype = _normalize_type(
            _first_str(row, ("type", "contract_type", "contractType", "option_type", "side"))
        )
        symbol = _first_str(row, ("symbol", "option_symbol", "occ_symbol", "id"))
        if expiry is None or strike is None or ctype is None or not symbol:
            continue
        contracts.append(
            ChainContract(
                symbol=symbol,
                expiry=expiry,
                strike=strike,
                contract_type=ctype,
                bid=_first_float(row, ("bid", "bid_price", "bp")),
                ask=_first_float(row, ("ask", "ask_price", "ap")),
                delta=_first_float(row, ("delta", "greeks_delta")),
            )
        )
    return contracts


def _contract_rows(payload: dict[str, Any]) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    for key in ("contracts", "options", "chain", "data", "results", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _contract_rows(value)
            if nested:
                return nested
    return []


def _first_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key in ("data", "snapshot", "quote", "result", "option"):
        value = payload.get(key)
        if isinstance(value, dict):
            if len(value) == 1 and isinstance(next(iter(value.values())), dict):
                return next(iter(value.values()))
            return value
    return payload


def _normalize_type(value: str | None) -> str | None:
    text = (value or "").strip().upper()
    if text in {"CALL", "C"}:
        return "CALL"
    if text in {"PUT", "P"}:
        return "PUT"
    return None


def _parse_date(value: str | None) -> Optional[date]:
    text = (value or "").strip()
    if not text:
        return None
    text = text.split("T", 1)[0]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _first_float(payload: dict[str, Any], names: tuple[str, ...]) -> Optional[float]:
    for name in names:
        value = payload.get(name)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_str(payload: dict[str, Any], names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        value = payload.get(name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None
