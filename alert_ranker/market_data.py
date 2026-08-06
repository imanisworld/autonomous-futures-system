"""Read-only market data provider selection for the advisory options scanner."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
from typing import Any, Protocol

import httpx

from .config import ScannerConfig
from .tastytrade_client import MarketSnapshot, READ_ONLY_PREFIXES, TastytradeClient


class MarketDataClient(Protocol):
    provider_name: str
    last_error: str | None

    async def __aenter__(self) -> "MarketDataClient":
        ...

    async def __aexit__(self, *_exc: object) -> None:
        ...

    async def fetch_market_snapshot(self, ticker: str) -> MarketSnapshot:
        ...


FORBIDDEN_PATH_PARTS = (
    "/orders",
    "/accounts",
    "/positions",
    "/balances",
    "/transactions",
    "/trading",
)


@dataclass(frozen=True)
class ProviderCapabilities:
    name: str
    configured: bool
    read_only: bool
    options_supported: bool
    paper_supported: bool
    order_supported: bool
    account_endpoints_forbidden: bool
    forbidden_path_parts: tuple[str, ...]
    allowed_prefixes: tuple[str, ...]
    base_url: str
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _HttpProvider:
    config: ScannerConfig
    client: httpx.AsyncClient | None = None
    provider_name: str = ""
    base_url: str = ""
    token_header: str = "Authorization"
    token_value: str | None = None
    allowed_prefixes: tuple[str, ...] = ()
    _owns_client: bool = field(init=False, default=False)
    last_error: str | None = None

    async def __aenter__(self) -> "_HttpProvider":
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=self.base_url, timeout=10)
            self._owns_client = True
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self.client and self._owns_client:
            await self.client.aclose()

    async def _get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        _assert_read_only_path(path, self.allowed_prefixes)
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=self.base_url, timeout=10)
            self._owns_client = True
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.token_value:
            headers[self.token_header] = self.token_value
        try:
            response = await self.client.get(path, headers=headers, **kwargs)
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

    def capabilities(self) -> ProviderCapabilities:
        return build_provider_capabilities(
            self.config,
            provider_name=self.provider_name,
            last_error=self.last_error,
        )


PUBLIC_AUTH_TOKEN_PATH = "/userapiauthservice/personal/access-tokens"
PUBLIC_MARKETDATA_PREFIX = "/userapigateway/marketdata"
PUBLIC_ALLOWED_PREFIXES = (PUBLIC_MARKETDATA_PREFIX,)


@dataclass(frozen=True)
class OptionContractQuote:
    symbol: str
    option_type: str
    strike: float | None
    bid: float | None
    ask: float | None
    mid: float | None
    last: float | None
    volume: float | None
    open_interest: float | None
    delta: float | None
    implied_volatility: float | None


@dataclass(frozen=True)
class OptionChain:
    underlying: str
    expiration: str | None
    calls: tuple[OptionContractQuote, ...] = ()
    puts: tuple[OptionContractQuote, ...] = ()
    error: str | None = None


class PublicMarketDataClient:
    """Read-only client for Public's documented market-data API.

    Auth flow (per https://public.com/api/docs/quickstart): exchange the
    long-lived secret (PUBLIC_API_SECRET_KEY / PUBLIC_API_KEY) for a
    short-lived access token, then call
    POST /userapigateway/marketdata/{accountId}/quotes|option-expirations|option-chain.
    The account id comes exclusively from the PUBLIC_ACCOUNT_ID env pin — this
    client never calls account/trading endpoints to discover it.
    """

    provider_name = "public"

    def __init__(self, config: ScannerConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self.client = client
        self._owns_client = client is None
        self.last_error: str | None = None
        self._access_token: str | None = None
        self._token_deadline: float = 0.0

    async def __aenter__(self) -> "PublicMarketDataClient":
        self._ensure_client()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self.client and self._owns_client:
            await self.client.aclose()

    def _ensure_client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=self.config.public_base_url, timeout=10)
            self._owns_client = True
        return self.client

    @staticmethod
    def _secret() -> str:
        return (os.getenv("PUBLIC_API_SECRET_KEY", "") or os.getenv("PUBLIC_API_KEY", "")).strip()

    def _preflight_error(self) -> str | None:
        if not self.config.public_api_key_configured or not self._secret():
            return "credentials_missing"
        if not self.config.public_account_id:
            return "account_id_missing"
        return None

    async def _ensure_token(self) -> str | None:
        import time

        if self._access_token and time.monotonic() < self._token_deadline:
            return self._access_token
        client = self._ensure_client()
        try:
            response = await client.post(
                PUBLIC_AUTH_TOKEN_PATH,
                json={
                    "validityInMinutes": self.config.public_token_validity_minutes,
                    "secret": self._secret(),
                },
            )
        except httpx.TimeoutException:
            self.last_error = "timeout"
            return None
        except httpx.HTTPError:
            self.last_error = "network_error"
            return None
        if response.status_code in {401, 403}:
            self.last_error = "authentication_failed"
            return None
        if response.status_code == 429:
            self.last_error = "rate_limited"
            return None
        if not response.is_success:
            self.last_error = f"http_status_{response.status_code}"
            return None
        try:
            token = response.json().get("accessToken")
        except ValueError:
            self.last_error = "unsupported_response_shape"
            return None
        if not token:
            self.last_error = "unsupported_response_shape"
            return None
        self._access_token = str(token)
        # Refresh one minute before the advertised validity lapses.
        self._token_deadline = time.monotonic() + max(
            60.0, self.config.public_token_validity_minutes * 60.0 - 60.0
        )
        return self._access_token

    async def _post_marketdata(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        _assert_read_only_path(path, PUBLIC_ALLOWED_PREFIXES)
        token = await self._ensure_token()
        if token is None:
            return None
        client = self._ensure_client()
        try:
            response = await client.post(
                path,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.TimeoutException:
            self.last_error = "timeout"
            return None
        except httpx.HTTPError:
            self.last_error = "network_error"
            return None
        if response.status_code in {401, 403}:
            self._access_token = None
            self.last_error = "authentication_failed"
            return None
        if response.status_code == 429:
            self.last_error = "rate_limited"
            return None
        if not response.is_success:
            self.last_error = f"http_status_{response.status_code}"
            return None
        try:
            body = response.json()
        except ValueError:
            self.last_error = "unsupported_response_shape"
            return None
        if not isinstance(body, dict):
            self.last_error = "unsupported_response_shape"
            return None
        self.last_error = None
        return body

    def _marketdata_path(self, endpoint: str) -> str:
        return f"{PUBLIC_MARKETDATA_PREFIX}/{self.config.public_account_id}/{endpoint}"

    async def fetch_market_snapshot(self, ticker: str) -> MarketSnapshot:
        symbol = ticker.upper()
        preflight = self._preflight_error()
        if preflight:
            self.last_error = preflight
            return MarketSnapshot(symbol, error=preflight)
        body = await self._post_marketdata(
            self._marketdata_path("quotes"),
            {"instruments": [{"symbol": symbol, "type": "EQUITY"}]},
        )
        if body is None:
            return MarketSnapshot(symbol, error=self.last_error)
        quotes = body.get("quotes")
        if not isinstance(quotes, list) or not quotes or not isinstance(quotes[0], dict):
            self.last_error = "unsupported_response_shape"
            return MarketSnapshot(symbol, error=self.last_error)
        quote = quotes[0]
        if str(quote.get("outcome") or "").upper() not in {"SUCCESS", ""}:
            self.last_error = "quote_unavailable"
            return MarketSnapshot(symbol, error=self.last_error)
        price = _first_float(quote, ("last",))
        bid = _first_float(quote, ("bid",))
        ask = _first_float(quote, ("ask",))
        if price is None and bid is not None and ask is not None:
            price = round((bid + ask) / 2.0, 4)
        if price is None:
            self.last_error = "unsupported_response_shape"
            return MarketSnapshot(symbol, error=self.last_error)
        quote_ts = quote.get("lastTimestamp")
        stale = _quote_is_stale(quote_ts, self.config.public_stale_quote_seconds)
        if stale:
            self.last_error = "stale_quote"
        return MarketSnapshot(
            ticker=symbol,
            iv_rank=None,
            price=price,
            volume=_first_float(quote, ("volume",)),
            raw={
                "provider": "public",
                "endpoint": "quotes",
                "quote": _compact_public_quote(quote),
            },
            error=self.last_error,
            bid=bid,
            ask=ask,
            quote_timestamp=str(quote_ts) if quote_ts else None,
            stale=stale,
        )

    async def fetch_option_expirations(self, ticker: str) -> list[str]:
        symbol = ticker.upper()
        preflight = self._preflight_error()
        if preflight:
            self.last_error = preflight
            return []
        body = await self._post_marketdata(
            self._marketdata_path("option-expirations"),
            {"instrument": {"symbol": symbol, "type": "EQUITY"}},
        )
        if body is None:
            return []
        expirations = body.get("expirations")
        if not isinstance(expirations, list):
            self.last_error = "unsupported_response_shape"
            return []
        return [str(item) for item in expirations if item]

    async def fetch_option_chain(self, ticker: str, expiration: str | None = None) -> OptionChain:
        symbol = ticker.upper()
        preflight = self._preflight_error()
        if preflight:
            self.last_error = preflight
            return OptionChain(symbol, expiration, error=preflight)
        if expiration is None:
            expirations = await self.fetch_option_expirations(symbol)
            if not expirations:
                error = self.last_error or "empty_chain"
                self.last_error = error
                return OptionChain(symbol, None, error=error)
            expiration = expirations[0]
        body = await self._post_marketdata(
            self._marketdata_path("option-chain"),
            {
                "instrument": {"symbol": symbol, "type": "EQUITY"},
                "expirationDate": expiration,
            },
        )
        if body is None:
            return OptionChain(symbol, expiration, error=self.last_error)
        calls = _parse_public_contracts(body.get("calls"), "CALL")
        puts = _parse_public_contracts(body.get("puts"), "PUT")
        if not calls and not puts:
            self.last_error = "empty_chain"
            return OptionChain(symbol, expiration, error=self.last_error)
        return OptionChain(symbol, expiration, calls=calls, puts=puts, error=None)

    def capabilities(self) -> ProviderCapabilities:
        return build_provider_capabilities(
            self.config,
            provider_name=self.provider_name,
            last_error=self.last_error,
        )


class AlpacaMarketDataClient(_HttpProvider):
    """Read-only Alpaca market-data adapter.

    Uses market-data endpoints only. This class deliberately has no trading
    client, account client, or order methods.
    """

    def __init__(self, config: ScannerConfig, client: httpx.AsyncClient | None = None):
        super().__init__(
            config=config,
            client=client,
            provider_name="alpaca",
            base_url=config.alpaca_data_base_url,
            token_value=None,
            allowed_prefixes=("/v1beta1/options", "/v2/stocks", "/v2/options"),
        )

    async def fetch_market_snapshot(self, ticker: str) -> MarketSnapshot:
        symbol = ticker.upper()
        if not (self.config.alpaca_api_key_configured and self.config.alpaca_secret_key_configured):
            return MarketSnapshot(symbol, error="credentials_missing")
        payload = await self._get(
            f"/v1beta1/options/snapshots/{symbol}",
            headers={
                "APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", "").strip(),
                "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", "").strip(),
            },
        )
        data = _first_payload(payload)
        if not data and self.last_error is None:
            self.last_error = "unsupported_response_shape"
        quote = data.get("latestQuote") if isinstance(data.get("latestQuote"), dict) else {}
        trade = data.get("latestTrade") if isinstance(data.get("latestTrade"), dict) else {}
        greeks = data.get("greeks") if isinstance(data.get("greeks"), dict) else {}
        iv = data.get("impliedVolatility") or greeks.get("iv")
        return MarketSnapshot(
            ticker=symbol,
            iv_rank=_first_float(data, ("iv_rank", "ivRank")),
            price=_first_float(trade, ("p", "price")) or _midpoint(quote),
            volume=_first_float(data, ("volume", "day_volume")),
            raw={"provider": "alpaca", "payload": payload, "implied_volatility": iv},
            error=self.last_error,
        )


def create_market_data_client(
    config: ScannerConfig,
    client: httpx.AsyncClient | None = None,
) -> MarketDataClient:
    provider = config.market_data_provider.lower()
    if provider == "tastytrade":
        return TastytradeClient(config, client=client)
    if provider == "public":
        return PublicMarketDataClient(config, client=client)
    if provider == "alpaca":
        return AlpacaMarketDataClient(config, client=client)
    raise ValueError(f"Unsupported OPTIONS_MARKET_DATA_PROVIDER: {config.market_data_provider}")


def build_provider_capabilities(
    config: ScannerConfig,
    *,
    provider_name: str | None = None,
    last_error: str | None = None,
) -> ProviderCapabilities:
    provider = (provider_name or config.market_data_provider).lower()
    if provider == "public":
        return ProviderCapabilities(
            name="public",
            configured=config.public_api_key_configured and bool(config.public_account_id),
            read_only=True,
            options_supported=True,
            paper_supported=False,
            order_supported=False,
            account_endpoints_forbidden=True,
            forbidden_path_parts=FORBIDDEN_PATH_PARTS,
            allowed_prefixes=PUBLIC_ALLOWED_PREFIXES,
            base_url=config.public_base_url,
            last_error=last_error,
        )
    if provider == "alpaca":
        return ProviderCapabilities(
            name="alpaca",
            configured=config.alpaca_api_key_configured and config.alpaca_secret_key_configured,
            read_only=True,
            options_supported=True,
            paper_supported=config.alpaca_paper,
            order_supported=False,
            account_endpoints_forbidden=True,
            forbidden_path_parts=FORBIDDEN_PATH_PARTS,
            allowed_prefixes=("/v1beta1/options", "/v2/stocks", "/v2/options"),
            base_url=config.alpaca_data_base_url,
            last_error=last_error,
        )
    if provider == "tastytrade":
        return ProviderCapabilities(
            name="tastytrade",
            configured=config.tastytrade_configured,
            read_only=True,
            options_supported=True,
            paper_supported=False,
            order_supported=False,
            account_endpoints_forbidden=True,
            forbidden_path_parts=FORBIDDEN_PATH_PARTS,
            allowed_prefixes=READ_ONLY_PREFIXES,
            base_url=config.tastytrade_base_url,
            last_error=last_error,
        )
    return ProviderCapabilities(
        name=provider,
        configured=False,
        read_only=True,
        options_supported=False,
        paper_supported=False,
        order_supported=False,
        account_endpoints_forbidden=True,
        forbidden_path_parts=FORBIDDEN_PATH_PARTS,
        allowed_prefixes=(),
        base_url="",
        last_error=last_error or "unsupported_provider",
    )


def _assert_read_only_path(path: str, allowed_prefixes: tuple[str, ...]) -> None:
    if any(part in path for part in FORBIDDEN_PATH_PARTS):
        raise ValueError(f"forbidden market-data path: {path}")
    if allowed_prefixes and not path.startswith(allowed_prefixes):
        raise ValueError(f"unsupported market-data path: {path}")


def _first_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    for key in ("data", "snapshot", "snapshots", "quote", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            if len(value) == 1 and isinstance(next(iter(value.values())), dict):
                return next(iter(value.values()))
            return value
    return payload


def _first_float(payload: dict[str, Any], names: tuple[str, ...]) -> float | None:
    for name in names:
        value = payload.get(name)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _quote_is_stale(timestamp: Any, max_age_seconds: float) -> bool:
    if not timestamp or max_age_seconds <= 0:
        return False
    from datetime import datetime, timezone

    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds() > max_age_seconds


def _compact_public_quote(quote: dict[str, Any]) -> dict[str, Any]:
    keys = ("last", "lastTimestamp", "bid", "ask", "bidSize", "askSize", "volume", "outcome")
    return {key: quote[key] for key in keys if key in quote}


def _parse_public_contracts(items: Any, option_type: str) -> tuple[OptionContractQuote, ...]:
    if not isinstance(items, list):
        return ()
    contracts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        instrument = item.get("instrument") if isinstance(item.get("instrument"), dict) else {}
        details = item.get("optionDetails") if isinstance(item.get("optionDetails"), dict) else {}
        greeks = details.get("greeks") if isinstance(details.get("greeks"), dict) else {}
        bid = _first_float(item, ("bid",))
        ask = _first_float(item, ("ask",))
        mid = _first_float(details, ("midPrice",))
        if mid is None and bid is not None and ask is not None:
            mid = round((bid + ask) / 2.0, 4)
        contracts.append(
            OptionContractQuote(
                symbol=str(instrument.get("symbol") or ""),
                option_type=option_type,
                strike=_first_float(details, ("strikePrice",)),
                bid=bid,
                ask=ask,
                mid=mid,
                last=_first_float(item, ("last",)),
                volume=_first_float(item, ("volume",)),
                open_interest=_first_float(item, ("openInterest",)),
                delta=_first_float(greeks, ("delta",)),
                implied_volatility=_first_float(greeks, ("impliedVolatility",)),
            )
        )
    return tuple(contracts)


def _midpoint(quote: dict[str, Any]) -> float | None:
    bid = _first_float(quote, ("bp", "bid", "bid_price"))
    ask = _first_float(quote, ("ap", "ask", "ask_price"))
    if bid is None or ask is None:
        return None
    return round((bid + ask) / 2.0, 4)
