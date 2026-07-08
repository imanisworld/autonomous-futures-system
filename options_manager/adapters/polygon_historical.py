"""options_manager/adapters/polygon_historical.py

Read-only Polygon historical STOCK candle adapter. Pulls stock aggregate
bars only -- never an options chain, never a quote, never a streaming
connection of any kind -- and maps them into the existing AdapterCandle
shape. Used only to
manually reconstruct real historical setups for the validation layer
(options_manager/validation); nothing here is wired into the scanner,
nothing here auto-generates a fixture, and nothing here runs on any
schedule.

Fail-closed on a missing API key: PolygonHistoricalClient.configured is
False and fetch_stock_aggregates() raises PolygonHistoricalError rather
than returning empty or fabricated data. The API key is read only from
the POLYGON_API_KEY environment variable, is never included in any
exception message, log line, or repr, and is never written to a file.

This module performs no I/O beyond the one read-only HTTP GET a fetch
requires: no options-chain fetch, no quote fetch, no broker call, no
order placement, no execution, no file writes. Does not import
options_manager.scanner, options_manager.strategies, alert_ranker,
options_companion, execution, webhook, broker systems, or
risk/risk_engine.py.
"""

from __future__ import annotations

import os
import time as _time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .base import AdapterCandle

DEFAULT_BASE_URL = "https://api.polygon.io"

_ALLOWED_TIMESPANS = ("minute", "hour", "day", "week", "month", "quarter", "year")


class PolygonHistoricalError(RuntimeError):
    """Any failure talking to or interpreting the Polygon stock
    aggregates endpoint. Never includes the API key in its message."""


def _epoch_ms_to_iso(timestamp_ms: float) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc).isoformat()


def _map_bar(row: dict[str, Any]) -> Optional[AdapterCandle]:
    """Maps one Polygon stock-aggregate bar into an AdapterCandle.
    Returns None (the bar is skipped) rather than fabricating a value
    when any required OHLC field is missing or unparseable."""
    try:
        timestamp_ms = float(row["t"])
        open_ = float(row["o"])
        high = float(row["h"])
        low = float(row["l"])
        close = float(row["c"])
    except (KeyError, TypeError, ValueError):
        return None

    volume_raw = row.get("v")
    try:
        volume = int(volume_raw) if volume_raw is not None else None
    except (TypeError, ValueError):
        volume = None

    return AdapterCandle(
        timestamp=_epoch_ms_to_iso(timestamp_ms),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


class PolygonHistoricalClient:
    """Thin, read-only stock-aggregates client. Stocks only -- never
    calls an options-chain, quote, or streaming endpoint of any kind."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        client: Optional[httpx.Client] = None,
        max_retries: int = 5,
        retry_sleep_seconds: float = 15.0,
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else os.getenv("POLYGON_API_KEY", "")
        ).strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_sleep_seconds = retry_sleep_seconds
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def __repr__(self) -> str:
        # Deliberately never includes the API key itself.
        return f"PolygonHistoricalClient(configured={self.configured})"

    def _get(self, client: httpx.Client, url: str, params: dict) -> dict:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = client.get(url, params=params, headers=headers, timeout=self.timeout)
            except httpx.HTTPError as exc:
                last_err = exc
                if attempt < self.max_retries:
                    _time.sleep(self.retry_sleep_seconds)
                continue
            if resp.status_code == 429 and attempt < self.max_retries:
                try:
                    wait = float(resp.headers["Retry-After"])
                except (KeyError, TypeError, ValueError):
                    wait = self.retry_sleep_seconds * (attempt + 1)
                _time.sleep(max(1.0, wait))
                continue
            if resp.status_code != 200:
                raise PolygonHistoricalError(
                    f"HTTP {resp.status_code} from Polygon stock aggregates request"
                )
            try:
                return resp.json()
            except ValueError as exc:
                raise PolygonHistoricalError("non-JSON response from Polygon") from exc
        raise PolygonHistoricalError(
            f"request failed after {self.max_retries + 1} attempts: {last_err}"
        )

    def fetch_stock_aggregates(
        self,
        ticker: str,
        from_date: str,
        to_date: str,
        multiplier: int = 5,
        timespan: str = "minute",
        adjusted: bool = True,
        sort: str = "asc",
        limit: int = 5000,
    ) -> list[AdapterCandle]:
        """Stock aggregate bars for `ticker` in [from_date, to_date]
        (each an ISO "YYYY-MM-DD" string), oldest-to-newest by default.
        Never fetches an options chain, a quote, or anything beyond
        stock aggregates."""
        if not self.configured:
            raise PolygonHistoricalError("POLYGON_API_KEY not configured")
        if timespan not in _ALLOWED_TIMESPANS:
            raise PolygonHistoricalError(
                f"timespan {timespan!r} not one of {_ALLOWED_TIMESPANS}"
            )
        if multiplier < 1:
            raise PolygonHistoricalError(f"multiplier {multiplier} must be >= 1")

        url = (
            f"{self.base_url}/v2/aggs/ticker/{ticker.upper()}/range/"
            f"{multiplier}/{timespan}/{from_date}/{to_date}"
        )
        params = {
            "adjusted": "true" if adjusted else "false",
            "sort": sort,
            "limit": limit,
        }

        close_client = self._client is None
        client = self._client or httpx.Client()
        try:
            payload = self._get(client, url, params=params)
        finally:
            if close_client:
                client.close()

        candles: list[AdapterCandle] = []
        for row in payload.get("results") or []:
            candle = _map_bar(row)
            if candle is not None:
                candles.append(candle)
        return candles


def fetch_stock_aggregates(
    ticker: str,
    from_date: str,
    to_date: str,
    multiplier: int,
    timespan: str,
    *,
    adjusted: bool = True,
    sort: str = "asc",
    limit: int = 5000,
    client: Optional[PolygonHistoricalClient] = None,
) -> list[AdapterCandle]:
    """Module-level convenience entry point. Reads POLYGON_API_KEY from
    the environment via a fresh PolygonHistoricalClient unless an
    already-constructed `client` is supplied -- tests inject a client
    wired to a mock transport instead of touching the real network or
    the environment."""
    active_client = client or PolygonHistoricalClient()
    return active_client.fetch_stock_aggregates(
        ticker,
        from_date,
        to_date,
        multiplier=multiplier,
        timespan=timespan,
        adjusted=adjusted,
        sort=sort,
        limit=limit,
    )
