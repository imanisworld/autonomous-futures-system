"""Read-only tastytrade client for auth and market metric lookups."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import ScannerConfig


READ_ONLY_PREFIXES = ("/sessions", "/market-metrics", "/market-data")
FORBIDDEN_PATH_PARTS = ("/orders", "/accounts", "/positions", "/balances", "/transactions")


@dataclass
class MarketSnapshot:
    ticker: str
    iv_rank: float | None = None
    price: float | None = None
    volume: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class TastytradeClient:
    provider_name = "tastytrade"

    def __init__(self, config: ScannerConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self._client = client
        self._owns_client = client is None
        self.session_token: str | None = None
        self.last_error: str | None = None

    async def __aenter__(self) -> "TastytradeClient":
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.config.tastytrade_base_url, timeout=10)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client and self._owns_client:
            await self._client.aclose()

    @property
    def available(self) -> bool:
        return self.config.tastytrade_configured and self.last_error is None

    async def authenticate(self) -> str | None:
        if not self.config.tastytrade_configured:
            self.last_error = "credentials_missing"
            return None
        payload = {
            "login": self.config.tastytrade_username,
            "password": self.config.tastytrade_password,
        }
        data = await self._request("POST", "/sessions", json=payload, auth_required=False)
        token = data.get("data", {}).get("session-token") if data else None
        if not token:
            self.last_error = "session_token_missing"
            return None
        self.session_token = str(token)
        self.last_error = None
        return self.session_token

    async def fetch_market_snapshot(self, ticker: str) -> MarketSnapshot:
        symbol = ticker.upper()
        if not self.config.tastytrade_configured:
            return MarketSnapshot(symbol, error="credentials_missing")
        if not self.session_token and not await self.authenticate():
            return MarketSnapshot(symbol, error=self.last_error or "auth_failed")

        metrics = await self.fetch_market_metrics(symbol)
        price_data = await self.fetch_current_market_data(symbol)
        if not metrics and not price_data and self.last_error is None:
            self.last_error = "unsupported_response_shape"
        raw = {"market_metrics": metrics, "market_data": price_data}
        return MarketSnapshot(
            ticker=symbol,
            iv_rank=parse_iv_rank(metrics),
            price=_first_float(price_data, ("last", "last-price", "mark", "price")),
            volume=_first_float(price_data, ("volume", "day-volume")),
            raw=raw,
            error=self.last_error,
        )

    async def fetch_market_metrics(self, ticker: str) -> dict[str, Any]:
        data = await self._request("GET", "/market-metrics", params={"symbols[]": ticker})
        return _first_item(data)

    async def fetch_current_market_data(self, ticker: str) -> dict[str, Any]:
        data = await self._request("GET", f"/market-data/{ticker}")
        return _first_item(data)

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        _assert_read_only_path(path)
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.config.tastytrade_base_url, timeout=10)
            self._owns_client = True

        auth_required = kwargs.pop("auth_required", True)
        headers = dict(kwargs.pop("headers", {}) or {})
        if auth_required and self.session_token:
            headers["Authorization"] = self.session_token
        try:
            response = await self._client.request(method, path, headers=headers, **kwargs)
            if response.status_code in {401, 403}:
                self.session_token = None
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


def _assert_read_only_path(path: str) -> None:
    if any(part in path for part in FORBIDDEN_PATH_PARTS):
        raise ValueError(f"forbidden tastytrade path: {path}")
    if not path.startswith(READ_ONLY_PREFIXES):
        raise ValueError(f"unsupported tastytrade path: {path}")


def _first_item(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    items = data.get("items") if isinstance(data, dict) else None
    if isinstance(items, list) and items:
        return items[0] if isinstance(items[0], dict) else {}
    return data if isinstance(data, dict) else {}


def _first_float(payload: dict[str, Any], names: tuple[str, ...]) -> float | None:
    for name in names:
        value = payload.get(name)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def parse_iv_rank(payload: dict[str, Any]) -> float | None:
    keys = (
        "implied-volatility-index-rank",
        "implied-volatility-rank",
        "iv-rank",
        "iv_rank",
    )
    return _first_float(payload, keys)
