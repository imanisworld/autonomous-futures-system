"""Read-only Public.com quote adapter using the official publicdotcom-py SDK."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PublicQuote:
    symbol: str
    ok: bool
    last: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "ok": self.ok,
            "last": self.last,
            "bid": self.bid,
            "ask": self.ask,
            "volume": self.volume,
            "error": self.error,
        }


class PublicQuoteClient:
    """Narrow quote-only wrapper. It intentionally exposes no trading methods."""

    def __init__(
        self,
        api_secret_key: str | None = None,
        default_account_number: str | None = None,
        sdk_client: Any | None = None,
    ) -> None:
        self.api_secret_key = (
            api_secret_key
            if api_secret_key is not None
            else os.getenv("PUBLIC_API_SECRET_KEY", "")
        ).strip()
        self.default_account_number = (
            default_account_number
            if default_account_number is not None
            else os.getenv("PUBLIC_DEFAULT_ACCOUNT_NUMBER", "")
        ).strip()
        self._sdk_client = sdk_client

    @property
    def configured(self) -> bool:
        return bool(self.api_secret_key and self.default_account_number)

    def fetch_equity_quote(self, symbol: str) -> PublicQuote:
        requested = (symbol or "").strip().upper()
        if not requested:
            return PublicQuote(symbol=requested, ok=False, error="missing_symbol")
        if not self.configured:
            return PublicQuote(symbol=requested, ok=False, error="credentials_missing")
        try:
            sdk = self._sdk_client or self._make_sdk_client()
            from public_api_sdk import InstrumentType, OrderInstrument

            quotes = sdk.get_quotes([
                OrderInstrument(symbol=requested, type=InstrumentType.EQUITY)
            ])
            if not quotes:
                return PublicQuote(symbol=requested, ok=False, error="no_quote_returned")
            quote = quotes[0]
            return PublicQuote(
                symbol=requested,
                ok=True,
                last=_float_or_none(getattr(quote, "last", None)),
                bid=_float_or_none(getattr(quote, "bid", None)),
                ask=_float_or_none(getattr(quote, "ask", None)),
                volume=_float_or_none(getattr(quote, "volume", None)),
            )
        except ImportError:
            return PublicQuote(symbol=requested, ok=False, error="sdk_not_installed")
        except Exception as exc:
            return PublicQuote(symbol=requested, ok=False, error=exc.__class__.__name__)

    def _make_sdk_client(self) -> Any:
        from public_api_sdk import ApiKeyAuthConfig, PublicApiClient, PublicApiClientConfiguration

        return PublicApiClient(
            ApiKeyAuthConfig(api_secret_key=self.api_secret_key),
            config=PublicApiClientConfiguration(
                default_account_number=self.default_account_number,
            ),
        )


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
