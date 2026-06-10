"""Read-only Polygon.io / Massive futures bar client.

Gives the system the price-history source it never had (the Tradovate broker
only exposes get_quote): historical OHLCV aggregate bars for CME futures from
the Polygon (now Massive) REST API.

Used ONLY for read-only historical data — gap backfill into BarHistory and
bulk downloads for replay/backtest. Never order routing, never live decisions.

Fail-soft contract: a client without an API key reports configured=False and
every fetch raises PolygonError — callers on optional paths catch it and
continue. Nothing in the live webhook pipeline imports this module.

API notes (verified against the live endpoint 2026-06-09):
  • GET {base}/futures/v1/aggs/{ticker}?resolution=15min&limit=50000
  • `window_start` is epoch NANOSECONDS (divide by 1e9 — treating it as ms or
    seconds is exactly the year-1781 class of bug fixed in bar_history).
  • Results come newest-first; pagination via `next_url` cursor.
  • Tickers are specific contracts (MESM6), not continuous (MES1!) — the
    front_contract/contract_schedule helpers map a continuous symbol + date
    range onto the quarterly contract chain.
"""

from __future__ import annotations

import calendar
import os
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

import httpx

DEFAULT_BASE_URL = "https://api.polygon.io"

# Quarterly CME equity-index cycle. Month code → month number.
_QUARTERLY_MONTHS = (3, 6, 9, 12)
_MONTH_CODES = {3: "H", 6: "M", 9: "U", 12: "Z"}

# Symbols this client knows how to roll (quarterly equity-index futures).
QUARTERLY_SYMBOLS = {"MES", "MNQ", "ES", "NQ", "M2K", "RTY", "MYM", "YM"}

# Days BEFORE expiry (3rd Friday) at which volume conventionally rolls to the
# next contract. 8 calendar days ≈ the Thursday before expiry week.
DEFAULT_ROLL_DAYS = 8


class PolygonError(RuntimeError):
    """Any failure talking to or interpreting the Polygon futures API."""


def _third_friday(year: int, month: int) -> date:
    cal = calendar.Calendar()
    fridays = [
        d for d in cal.itermonthdates(year, month)
        if d.month == month and d.weekday() == calendar.FRIDAY
    ]
    return fridays[2]


def front_contract(symbol: str, on_date: date, roll_days: int = DEFAULT_ROLL_DAYS) -> str:
    """Map a continuous symbol + date to the front quarterly contract ticker.

    E.g. ("MES", 2026-06-09) → "MESM6"; ("MES", 2024-09-03) → "MESU4".
    Rolls to the next quarter `roll_days` calendar days before the 3rd-Friday
    expiry, matching where volume (and TradingView's MES1!) migrates.
    """
    symbol = symbol.strip().upper()
    if symbol not in QUARTERLY_SYMBOLS:
        raise PolygonError(f"no roll schedule for symbol {symbol!r} (quarterly equity-index only)")
    year, month = on_date.year, on_date.month
    # Candidate = first quarterly month at/after this month.
    for _ in range(8):  # at most 2 years of scanning, always terminates earlier
        quarter_month = next((m for m in _QUARTERLY_MONTHS if m >= month), None)
        if quarter_month is None:
            year, month = year + 1, 1
            continue
        expiry = _third_friday(year, quarter_month)
        if on_date < expiry - timedelta(days=roll_days):
            return f"{symbol}{_MONTH_CODES[quarter_month]}{year % 10}"
        # Inside the roll window (or past expiry) — advance to next quarter.
        month = quarter_month + 1
        if month > 12:
            year, month = year + 1, 1
    raise PolygonError(f"could not resolve front contract for {symbol} on {on_date}")


def contract_schedule(
    symbol: str, start: date, end: date, roll_days: int = DEFAULT_ROLL_DAYS
) -> List[Tuple[str, date, date]]:
    """Split [start, end] into (ticker, seg_start, seg_end) segments, one per
    front contract, so a continuous series can be stitched across rolls."""
    if end < start:
        raise PolygonError(f"end {end} before start {start}")
    segments: List[Tuple[str, date, date]] = []
    cursor = start
    while cursor <= end:
        ticker = front_contract(symbol, cursor, roll_days)
        seg_end = cursor
        # Walk forward day by day until the front contract changes or range ends.
        # Cheap: front_contract is pure arithmetic, segments are ~90 days.
        while seg_end < end and front_contract(symbol, seg_end + timedelta(days=1), roll_days) == ticker:
            seg_end += timedelta(days=1)
        segments.append((ticker, cursor, seg_end))
        cursor = seg_end + timedelta(days=1)
    return segments


def _ns_to_utc(window_start: Any) -> Optional[datetime]:
    """Epoch-nanoseconds → aware UTC datetime; None on garbage."""
    try:
        return datetime.fromtimestamp(float(window_start) / 1e9, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


@dataclass(frozen=True)
class PolygonBar:
    ts: datetime  # bar window START, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float
    ticker: str

    def to_dict(self) -> dict:
        return {
            "ts": self.ts.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "ticker": self.ticker,
        }


class PolygonFuturesClient:
    """Thin, read-only aggregates client with pagination and 429 backoff."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
        client: Optional[httpx.Client] = None,
        max_retries: int = 5,
        retry_sleep_seconds: float = 15.0,
        min_request_interval: float = 0.0,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("POLYGON_API_KEY", "")).strip()
        self.base_url = (base_url or os.getenv("POLYGON_BASE_URL", "") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_sleep_seconds = retry_sleep_seconds
        # Proactive pacing for the free tier (~5 req/min): sleep so consecutive
        # requests are at least this many seconds apart. Reactive 429 retries
        # alone are not enough — a multi-contract bulk download bursts straight
        # through the per-minute window. ~13s ≈ safely under 5/min.
        self.min_request_interval = min_request_interval
        self._last_request_at = 0.0
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    # ── http ────────────────────────────────────────────────────────────────
    def _get(self, client: httpx.Client, url: str, params: Optional[dict] = None) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            if self.min_request_interval > 0:
                wait = self.min_request_interval - (_time.monotonic() - self._last_request_at)
                if wait > 0:
                    _time.sleep(wait)
            self._last_request_at = _time.monotonic()
            try:
                resp = client.get(url, params=params, headers=headers, timeout=self.timeout)
            except httpx.HTTPError as exc:
                last_err = exc
                if attempt < self.max_retries:
                    _time.sleep(self.retry_sleep_seconds)
                continue
            if resp.status_code == 429 and attempt < self.max_retries:
                # Free tier rate limit — honor Retry-After when present,
                # otherwise back off harder each attempt (the per-minute
                # window needs real time to drain, not a fixed short nap).
                try:
                    wait = float(resp.headers["Retry-After"])
                except (KeyError, TypeError, ValueError):
                    wait = self.retry_sleep_seconds * (attempt + 1)
                _time.sleep(max(1.0, wait))
                continue
            if resp.status_code != 200:
                raise PolygonError(f"HTTP {resp.status_code} from {url}: {resp.text[:200]}")
            try:
                return resp.json()
            except ValueError as exc:
                raise PolygonError(f"non-JSON response from {url}") from exc
        raise PolygonError(f"request failed after {self.max_retries + 1} attempts: {last_err}")

    # ── fetch ───────────────────────────────────────────────────────────────
    def fetch_bars(
        self,
        ticker: str,
        start: date,
        end: date,
        timeframe_minutes: int = 15,
        limit: int = 50_000,
    ) -> List[PolygonBar]:
        """All bars for one specific contract in [start, end], oldest→newest."""
        if not self.configured:
            raise PolygonError("POLYGON_API_KEY not configured")
        if timeframe_minutes < 1 or timeframe_minutes > 59:
            raise PolygonError(f"timeframe {timeframe_minutes}min outside the 1-59min resolution range")
        url = f"{self.base_url}/futures/v1/aggs/{ticker}"
        params: Optional[dict] = {
            "resolution": f"{timeframe_minutes}min",
            "window_start.gte": start.isoformat(),
            # End is inclusive as a date: take windows starting before the next day.
            "window_start.lt": (end + timedelta(days=1)).isoformat(),
            "limit": limit,
        }
        bars: List[PolygonBar] = []
        close_client = self._client is None
        client = self._client or httpx.Client()
        try:
            while url:
                payload = self._get(client, url, params=params)
                params = None  # next_url carries the cursor
                for row in payload.get("results") or []:
                    ts = _ns_to_utc(row.get("window_start"))
                    if ts is None:
                        continue
                    try:
                        bars.append(
                            PolygonBar(
                                ts=ts,
                                open=float(row["open"]),
                                high=float(row["high"]),
                                low=float(row["low"]),
                                close=float(row["close"]),
                                volume=float(row.get("volume") or 0),
                                ticker=str(row.get("ticker") or ticker),
                            )
                        )
                    except (KeyError, TypeError, ValueError):
                        continue  # skip malformed rows, never fabricate
                url = payload.get("next_url") or ""
        finally:
            if close_client:
                client.close()
        bars.sort(key=lambda b: b.ts)
        return bars

    def fetch_continuous(
        self,
        symbol: str,
        start: date,
        end: date,
        timeframe_minutes: int = 15,
        roll_days: int = DEFAULT_ROLL_DAYS,
    ) -> List[PolygonBar]:
        """Continuous front-contract series for a symbol (e.g. "MES") across
        rolls, oldest→newest, deduped on bar timestamp at the roll seams."""
        out: List[PolygonBar] = []
        seen: set = set()
        for ticker, seg_start, seg_end in contract_schedule(symbol, start, end, roll_days):
            for bar in self.fetch_bars(ticker, seg_start, seg_end, timeframe_minutes):
                if bar.ts in seen:
                    continue
                seen.add(bar.ts)
                out.append(bar)
        out.sort(key=lambda b: b.ts)
        return out
