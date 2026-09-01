"""Equity bar transport for the options advisory lane.

Transport only: this module fetches and parses, and never decides anything
about a setup. All bar reasoning lives in :mod:`alert_ranker.causal_bars`.

Three provider behaviours measured on the VPS (2026-09-01) are defended
against here, because each one fails silently rather than loudly:

* Omitting ``end`` returns HTTP 200 and quietly clamps to the entitlement
  boundary, so the caller receives data of undefined recency with no error.
  ``end`` is therefore mandatory.
* An unknown or misspelled symbol is omitted from the response with no error
  field, so a watchlist typo becomes silence instead of a failure.
* ``limit`` is shared across symbols and fills them one at a time: ``limit=10``
  across three symbols returned ten bars of the first and none of the other
  two. Pagination must run to token exhaustion, and truncation must raise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence

import httpx

from .causal_bars import Bar, Timeframe

__all__ = [
    "BarProviderError",
    "BarProvider",
    "AlpacaBarProvider",
    "CONSOLIDATED_FEED",
    "parse_bar",
]

CONSOLIDATED_FEED = "sip"

_ENTITLEMENT_MARKERS = (
    "subscription does not permit",
    "not permitted",
    "insufficient subscription",
)


class BarProviderError(RuntimeError):
    """A bar fetch that must fail closed, carrying a machine-readable reason."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


class BarProvider(Protocol):
    feed: str

    async def fetch_bars(
        self,
        symbols: Sequence[str],
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> dict[str, list[Bar]]:
        ...


def parse_bar(payload: dict[str, Any]) -> Bar:
    """Convert one provider bar object into a :class:`Bar`."""
    try:
        start = datetime.fromisoformat(str(payload["t"]).replace("Z", "+00:00"))
        return Bar(
            start=start.astimezone(timezone.utc),
            open=float(payload["o"]),
            high=float(payload["h"]),
            low=float(payload["l"]),
            close=float(payload["c"]),
            volume=float(payload["v"]),
            vwap=float(payload["vw"]) if payload.get("vw") is not None else None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BarProviderError("provider_malformed", str(exc)) from exc


@dataclass
class AlpacaBarProvider:
    """Consolidated-tape equity bars over the vendor's historical bars API."""

    base_url: str
    api_key: str
    secret_key: str
    client: httpx.AsyncClient | None = None
    feed: str = CONSOLIDATED_FEED
    adjustment: str = "raw"
    page_limit: int = 10000
    max_pages: int = 40
    timeout: float = 15.0

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
        }

    async def fetch_bars(
        self,
        symbols: Sequence[str],
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> dict[str, list[Bar]]:
        """Fetch every bar for every requested symbol in ``[start, end]``.

        Raises :class:`BarProviderError` rather than returning a partial view:
        a caller cannot distinguish "this symbol had no prints" from "this
        symbol was silently dropped", so the provider must make that call here.
        """
        if not symbols:
            raise BarProviderError("no_symbols_requested")
        if end is None:
            # Never reachable through typed callers, but an explicit guard is
            # cheaper than the silent clamp the provider would otherwise apply.
            raise BarProviderError("missing_information_cutoff")
        if start.tzinfo is None or end.tzinfo is None:
            raise BarProviderError("naive_timestamp")
        if end <= start:
            raise BarProviderError("invalid_window", f"end {end} not after start {start}")

        requested = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        params: dict[str, Any] = {
            "symbols": ",".join(requested),
            "timeframe": timeframe.name,
            "feed": self.feed,
            "adjustment": self.adjustment,
            "limit": self.page_limit,
            "start": _iso(start),
            "end": _iso(end),
        }

        collected: dict[str, list[Bar]] = {symbol: [] for symbol in requested}
        client = self.client
        owns = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
        try:
            page_params = dict(params)
            for _ in range(self.max_pages):
                payload = await self._get_page(client, page_params)
                bars = payload.get("bars") or {}
                if not isinstance(bars, dict):
                    raise BarProviderError("provider_malformed", "bars is not an object")
                for symbol, rows in bars.items():
                    key = str(symbol).upper()
                    if key not in collected:
                        # An unrequested symbol in the response means the
                        # request and the answer disagree; do not merge it in.
                        raise BarProviderError("provider_malformed", f"unexpected symbol {key}")
                    if not isinstance(rows, list):
                        raise BarProviderError("provider_malformed", f"{key}: bars not a list")
                    collected[key].extend(parse_bar(row) for row in rows)
                token = payload.get("next_page_token")
                if not token:
                    break
                page_params = dict(params)
                page_params["page_token"] = token
            else:
                raise BarProviderError(
                    "pagination_truncated",
                    f"more than {self.max_pages} pages for {params['symbols']}",
                )
        finally:
            if owns:
                await client.aclose()

        missing = sorted(symbol for symbol, rows in collected.items() if not rows)
        if missing:
            raise BarProviderError("missing_symbol", ",".join(missing))

        for rows in collected.values():
            rows.sort(key=lambda bar: bar.start_utc)
        return collected

    async def _get_page(
        self, client: httpx.AsyncClient, params: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            response = await client.get(
                f"{self.base_url.rstrip('/')}/v2/stocks/bars",
                params=params,
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise BarProviderError("provider_unavailable", str(exc)) from exc

        if response.status_code == 403:
            raise BarProviderError("provider_entitlement", _body_text(response))
        if response.status_code != 200:
            text = _body_text(response)
            if any(marker in text.lower() for marker in _ENTITLEMENT_MARKERS):
                raise BarProviderError("provider_entitlement", text)
            raise BarProviderError("provider_error", f"HTTP {response.status_code}: {text}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise BarProviderError("provider_malformed", str(exc)) from exc
        if not isinstance(payload, dict):
            raise BarProviderError("provider_malformed", "response is not an object")
        return payload


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _body_text(response: httpx.Response) -> str:
    try:
        return response.text[:300]
    except Exception:  # pragma: no cover - defensive only
        return ""
