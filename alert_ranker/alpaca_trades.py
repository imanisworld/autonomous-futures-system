"""Dependency-light Alpaca historical trade fetch for the setup-capture watcher.

Kept free of paper_v1, trigger_time, Signa, GEX, Discord, and scanner imports so
the oneshot collector's transitive import graph stays observation-only.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import httpx

TRADES_PATH_TEMPLATE = "/v2/stocks/{symbol}/trades"
_TS_RE = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d{1,9}))?Z$"
)


class TradeProviderError(RuntimeError):
    """Fail-closed trade-provider error (HTTP, payload, or window)."""


@dataclass(frozen=True, kw_only=True)
class CanonicalTrade:
    symbol: str
    timestamp: str
    timestamp_ns: int
    price: float
    size: int
    exchange: str
    conditions: tuple[str, ...]
    tape: str
    trade_id: str


def timestamp_ns(value: object) -> int:
    text = str(value or "")
    match = _TS_RE.fullmatch(text)
    if match is None:
        raise TradeProviderError(f"invalid trade timestamp: {text!r}")
    base = datetime.fromisoformat(match.group("base") + "+00:00")
    frac = (match.group("frac") or "").ljust(9, "0")
    return int(base.timestamp()) * 1_000_000_000 + int(frac or "0")


def datetime_ns(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    point = value.astimezone(timezone.utc)
    return int(point.timestamp()) * 1_000_000_000 + point.microsecond * 1_000


def parse_trade(symbol: str, payload: Mapping[str, Any]) -> CanonicalTrade:
    try:
        ts = str(payload["t"])
        price = float(payload["p"])
        size = int(payload["s"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TradeProviderError(f"malformed trade: {exc}") from exc
    if price <= 0 or size <= 0:
        raise TradeProviderError("trade price/size must be positive")
    conditions_raw = payload.get("c") or []
    if not isinstance(conditions_raw, list):
        raise TradeProviderError("trade conditions must be a list")
    return CanonicalTrade(
        symbol=symbol.upper(),
        timestamp=ts,
        timestamp_ns=timestamp_ns(ts),
        price=price,
        size=size,
        exchange=str(payload.get("x") or ""),
        conditions=tuple(sorted(str(item) for item in conditions_raw)),
        tape=str(payload.get("z") or "").upper(),
        trade_id=str(payload.get("i") or ""),
    )


def trades_in_window(
    trades: Sequence[CanonicalTrade],
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[CanonicalTrade]:
    start_ns = datetime_ns(window_start)
    end_ns = datetime_ns(window_end)
    if end_ns <= start_ns:
        raise ValueError("window_end must be after window_start")
    return [
        trade
        for trade in sorted(trades, key=lambda item: (item.timestamp_ns, item.trade_id))
        if start_ns <= trade.timestamp_ns < end_ns
    ]


@dataclass
class HistoricalTradeProvider:
    base_url: str
    api_key: str
    secret_key: str
    feed: str
    page_limit: int = 10000
    max_pages: int = 50
    timeout: float = 30.0

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
        }

    async def fetch_trades(
        self,
        *,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> list[CanonicalTrade]:
        if self.feed not in {"iex", "sip"}:
            raise ValueError("feed must be iex or sip")
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise TradeProviderError("invalid trade query window")

        params: dict[str, Any] = {
            "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "feed": self.feed,
            "limit": self.page_limit,
            "sort": "asc",
        }
        collected: list[CanonicalTrade] = []
        page_params = dict(params)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for _page in range(self.max_pages):
                response = None
                for attempt, delay in enumerate((0.0, 1.0, 2.0, 5.0, 10.0)):
                    if delay:
                        await asyncio.sleep(delay)
                    response = await client.get(
                        self.base_url.rstrip("/")
                        + TRADES_PATH_TEMPLATE.format(symbol=symbol.upper()),
                        params=page_params,
                        headers=self.headers,
                    )
                    if response.status_code != 429:
                        break
                assert response is not None
                if response.status_code != 200:
                    raise TradeProviderError(
                        f"{self.feed} trade provider HTTP {response.status_code}: "
                        f"{response.text[:200]}"
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise TradeProviderError(
                        f"{self.feed} trade provider returned invalid JSON"
                    ) from exc
                trades = payload.get("trades") or []
                if not isinstance(trades, list):
                    raise TradeProviderError(
                        f"{self.feed} trade provider payload is malformed"
                    )
                collected.extend(parse_trade(symbol, row) for row in trades)
                token = payload.get("next_page_token")
                if not token:
                    break
                page_params = dict(params)
                page_params["page_token"] = token
            else:
                raise TradeProviderError(
                    f"{self.feed} trade pagination truncated after {self.max_pages} pages"
                )

        return trades_in_window(
            collected,
            window_start=start,
            window_end=end,
        )
