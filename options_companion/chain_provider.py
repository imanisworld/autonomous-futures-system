"""Provider-agnostic option-chain interface + a Public.com implementation.

The ``ChainProvider`` protocol is the only surface the companion lane talks to, so
selection/resolution logic is fully testable against a mock before a live Public key
exists. ``PublicChainProvider`` is wired to Public's REAL documented HTTP contract
(confirmed against https://public.com/api/docs and the official ``publicdotcom-py``
SDK source), but stays strictly READ-ONLY: its allowed-path whitelist permits only
the auth-token mint and the ``/userapigateway/marketdata`` (+ ``/option-details``)
endpoints, so trading/order/account paths are structurally unreachable.

Public's market-data contract (account-scoped, POST-based):
- Auth:        POST /userapiauthservice/personal/access-tokens  {secret, validityInMinutes}
               -> {accessToken}; then ``Authorization: Bearer <token>`` (re-mint on expiry).
- Expirations: POST /userapigateway/marketdata/{accountId}/option-expirations
               {instrument:{symbol,type:EQUITY}} -> {expirations:[YYYY-MM-DD,...]}.
- Chain:       POST /userapigateway/marketdata/{accountId}/option-chain
               {instrument:{symbol,type:EQUITY}, expirationDate} -> {calls:[...], puts:[...]}
               each contract: instrument.symbol, optionDetails.strikePrice,
               optionDetails.greeks.delta, top-level bid/ask.

No order is ever placed. ``fetch_quote`` re-reads the chain for the stored expiry to
mark an open paper position (Public has no standalone single-contract quote path in
the read-only market-data scope used here).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional, Protocol

import httpx

from alert_ranker.market_data import _HttpProvider, _assert_read_only_path

PROD_BASE_URL = "https://api.public.com"


@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    delta: Optional[float] = None
    error: Optional[str] = None

    @property
    def mid(self) -> Optional[float]:
        if self.bid is None or self.ask is None or self.bid <= 0 or self.ask <= 0:
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
        if self.bid is None or self.ask is None or self.bid <= 0 or self.ask <= 0:
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

    async def fetch_quote(
        self,
        option_symbol: str,
        *,
        underlying: str | None = None,
        expiry: str | None = None,
    ) -> OptionQuote:
        ...


# Whitelist of path prefixes the companion lane may read. Auth-token mint + market
# data only — every trading/order/account path is structurally unreachable.
_PUBLIC_ALLOWED_PREFIXES = (
    "/userapiauthservice/personal/access-tokens",
    "/userapigateway/marketdata",
    "/userapigateway/option-details",
)


class PublicChainProvider(_HttpProvider):
    """Read-only Public.com option-chain adapter (data-only; orders unreachable)."""

    def __init__(
        self,
        *,
        base_url: str = PROD_BASE_URL,
        api_key: str | None = None,
        account_id: str | None = None,
        validity_minutes: int = 15,
        client: httpx.AsyncClient | None = None,
    ):
        # config is only used by _HttpProvider.capabilities(), which this lane never
        # calls; drive everything off base_url/allowed_prefixes instead.
        super().__init__(
            config=None,  # type: ignore[arg-type]
            client=client,
            provider_name="public",
            base_url=base_url or PROD_BASE_URL,
            token_value=None,
            allowed_prefixes=_PUBLIC_ALLOWED_PREFIXES,
        )
        self.api_key = (api_key or "").strip()
        self.account_id = (account_id or "").strip()
        self.validity_minutes = validity_minutes
        self._token: Optional[str] = None
        self._token_expires_at: Optional[float] = None

    # ── HTTP ──────────────────────────────────────────────────────────────────

    async def _post(
        self, path: str, json_data: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        _assert_read_only_path(path, self.allowed_prefixes)
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=self.base_url, timeout=10)
            self._owns_client = True
        try:
            response = await self.client.post(path, json=json_data, headers=headers or {})
            if response.status_code in {401, 403}:
                self.last_error = "authentication_failed"
                return {}
            if response.status_code == 429:
                self.last_error = "rate_limited"
                return {}
            response.raise_for_status()
            self.last_error = None
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            self.last_error = exc.__class__.__name__
            return {}

    async def _ensure_token(self) -> bool:
        if self._token and self._token_expires_at and time.time() < self._token_expires_at:
            return True
        if not self.api_key:
            self.last_error = "credentials_missing"
            return False
        data = await self._post(
            "/userapiauthservice/personal/access-tokens",
            {"secret": self.api_key, "validityInMinutes": self.validity_minutes},
        )
        token = data.get("accessToken") if isinstance(data, dict) else None
        if not token:
            if self.last_error is None:
                self.last_error = "auth_failed"
            return False
        self._token = token
        # Re-mint 5 min early (matches the SDK's safety buffer).
        self._token_expires_at = time.time() + max(60, (self.validity_minutes - 5) * 60)
        return True

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    # ── ChainProvider ─────────────────────────────────────────────────────────

    async def fetch_chain(self, underlying: str, *, max_dte: int) -> ChainSnapshot:
        symbol = underlying.upper()
        guard = self._preflight(symbol, ChainSnapshot)
        if guard is not None:
            return guard
        if not await self._ensure_token():
            return ChainSnapshot(symbol, error=self.last_error or "auth_failed")

        instrument = {"symbol": symbol, "type": "EQUITY"}
        headers = self._auth_headers()
        exp_payload = await self._post(
            f"/userapigateway/marketdata/{self.account_id}/option-expirations",
            {"instrument": instrument},
            headers,
        )
        expirations = exp_payload.get("expirations") if isinstance(exp_payload, dict) else None
        if not expirations:
            if self.last_error is None:
                self.last_error = "unsupported_response_shape"
            return ChainSnapshot(symbol, error=self.last_error)

        contracts: list[ChainContract] = []
        for expiry_iso in _expiries_within_dte(expirations, max_dte):
            chain_payload = await self._post(
                f"/userapigateway/marketdata/{self.account_id}/option-chain",
                {"instrument": instrument, "expirationDate": expiry_iso},
                headers,
            )
            contracts.extend(_parse_chain(chain_payload, expiry_iso))

        if not contracts and self.last_error is None:
            self.last_error = "unsupported_response_shape"
        return ChainSnapshot(symbol, contracts=contracts, error=self.last_error)

    async def fetch_quote(
        self,
        option_symbol: str,
        *,
        underlying: str | None = None,
        expiry: str | None = None,
    ) -> OptionQuote:
        guard = self._preflight(option_symbol, OptionQuote)
        if guard is not None:
            return guard
        if not (underlying and expiry):
            return OptionQuote(option_symbol, error="quote_context_missing")
        if not await self._ensure_token():
            return OptionQuote(option_symbol, error=self.last_error or "auth_failed")

        expiry_iso = expiry[:10]
        chain_payload = await self._post(
            f"/userapigateway/marketdata/{self.account_id}/option-chain",
            {"instrument": {"symbol": underlying.upper(), "type": "EQUITY"}, "expirationDate": expiry_iso},
            self._auth_headers(),
        )
        for contract in _parse_chain(chain_payload, expiry_iso):
            if contract.symbol == option_symbol:
                return OptionQuote(option_symbol, bid=contract.bid, ask=contract.ask, delta=contract.delta)
        if self.last_error is None:
            self.last_error = "contract_not_found"
        return OptionQuote(option_symbol, error=self.last_error)

    def _preflight(self, symbol: str, kind):
        """Shared credential/account guards. Returns an error result or None."""
        if not self.api_key:
            return kind(symbol, error="credentials_missing")
        if not self.account_id:
            return kind(symbol, error="account_id_missing")
        return None


# ─── tolerant payload parsing ────────────────────────────────────────────────


def _expiries_within_dte(expirations: list[Any], max_dte: int) -> list[str]:
    """Expirations (YYYY-MM-DD) with 0 <= DTE <= max_dte. Unparseable ones are
    kept (let selection decide) so a date-format surprise never silently drops all."""
    today = date.today()
    kept: list[str] = []
    for raw in expirations:
        iso = str(raw).strip()[:10]
        parsed = _parse_date(iso)
        if parsed is None:
            kept.append(iso)
            continue
        dte = (parsed - today).days
        if 0 <= dte <= max_dte:
            kept.append(iso)
    return kept


def _parse_chain(payload: dict[str, Any], expiry_iso: str) -> list[ChainContract]:
    if not isinstance(payload, dict):
        return []
    expiry = _parse_date(expiry_iso)
    if expiry is None:
        return []
    out: list[ChainContract] = []
    for array_key, ctype in (("calls", "CALL"), ("puts", "PUT")):
        for el in payload.get(array_key) or []:
            if not isinstance(el, dict):
                continue
            instrument = el.get("instrument") if isinstance(el.get("instrument"), dict) else {}
            details = el.get("optionDetails") if isinstance(el.get("optionDetails"), dict) else {}
            greeks = details.get("greeks") if isinstance(details.get("greeks"), dict) else {}
            symbol = _first_str(instrument, ("symbol", "osiSymbol")) or _first_str(
                el, ("symbol", "osiSymbol", "occ_symbol")
            )
            strike = _first_float(details, ("strikePrice", "strike")) or _first_float(
                el, ("strikePrice", "strike")
            )
            if not symbol or strike is None:
                continue
            out.append(
                ChainContract(
                    symbol=symbol,
                    expiry=expiry,
                    strike=strike,
                    contract_type=ctype,
                    bid=_first_float(el, ("bid", "bidPrice", "bp")),
                    ask=_first_float(el, ("ask", "askPrice", "ap")),
                    delta=_first_float(greeks, ("delta",)),
                )
            )
    return out


def _parse_date(value: str | None) -> Optional[date]:
    text = (value or "").strip().split("T", 1)[0]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _first_float(payload: dict[str, Any], names: tuple[str, ...]) -> Optional[float]:
    for name in names:
        value = payload.get(name)
        try:
            return float(value)  # Public returns greeks/prices as strings — float() handles both
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
