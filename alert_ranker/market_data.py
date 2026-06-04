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


FORBIDDEN_PATH_PARTS = ("/orders", "/accounts", "/positions", "/balances", "/transactions")


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


class PublicMarketDataClient(_HttpProvider):
    """Read-only adapter placeholder for Public market-data APIs.

    Public's account/trading API choice is still undecided, so this adapter is
    intentionally narrow and configurable. It only calls a quote/metrics-style
    path and fails soft when the API shape differs.
    """

    def __init__(self, config: ScannerConfig, client: httpx.AsyncClient | None = None):
        super().__init__(
            config=config,
            client=client,
            provider_name="public",
            base_url=config.public_base_url,
            token_value=None,
            allowed_prefixes=("/market-data", "/data", "/quotes", "/options"),
        )

    async def fetch_market_snapshot(self, ticker: str) -> MarketSnapshot:
        symbol = ticker.upper()
        if not self.config.public_api_key_configured:
            return MarketSnapshot(symbol, error="credentials_missing")
        payload = await self._get(
            f"/market-data/options/{symbol}",
            params={"symbol": symbol},
            headers={"Authorization": f"Bearer {os.getenv('PUBLIC_API_KEY', '').strip()}"},
        )
        data = _first_payload(payload)
        if not data and self.last_error is None:
            self.last_error = "unsupported_response_shape"
        return MarketSnapshot(
            ticker=symbol,
            iv_rank=_first_float(data, ("iv_rank", "iv-rank", "implied_volatility_rank")),
            price=_first_float(data, ("price", "last", "last_price", "underlying_price")),
            volume=_first_float(data, ("volume", "day_volume")),
            raw={"provider": "public", "payload": payload},
            error=self.last_error,
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
            configured=config.public_api_key_configured,
            read_only=True,
            options_supported=True,
            paper_supported=False,
            order_supported=False,
            account_endpoints_forbidden=True,
            forbidden_path_parts=FORBIDDEN_PATH_PARTS,
            allowed_prefixes=("/market-data", "/data", "/quotes", "/options"),
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


def _midpoint(quote: dict[str, Any]) -> float | None:
    bid = _first_float(quote, ("bp", "bid", "bid_price"))
    ask = _first_float(quote, ("ap", "ask", "ask_price"))
    if bid is None or ask is None:
        return None
    return round((bid + ask) / 2.0, 4)
