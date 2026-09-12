"""Read-only client for the current documented Signa Action Card endpoint.

This client is deliberately isolated from the existing runtime client. It is not
wired into webhook, scanner, strategy, risk, broker, or execution paths. The
capability probe must prove the account contract before any runtime caller may be
migrated to this client.

Successful Action Cards are cached per ``(symbol, timeframe)`` for
``OPTIONS_SIGNA_V2_CACHE_TTL_SECONDS`` (default 30 minutes). The provider's 1d
surface is a nightly batch (``data_as_of`` moves once a day), and the scanner
polls every 5 minutes, so an uncached observer would double the account's
daily Signa request volume for no new information. Cache hits keep the
original ``retrieved_at`` and are flagged ``cached=True``; failures are never
cached, so a transient error retries on the next scan.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

import httpx

from .signa_observation import SignaActionCardObservation, parse_action_card


DEFAULT_BASE_URL = "https://app.getsigna.ai"
DEFAULT_CACHE_TTL_SECONDS = 1800.0


def cache_ttl_seconds_from_env() -> float:
    raw = os.getenv("OPTIONS_SIGNA_V2_CACHE_TTL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_CACHE_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_CACHE_TTL_SECONDS


class SignaV2Client:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 5.0,
        client: httpx.Client | None = None,
        cache_ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("SIGNA_API_KEY", "")).strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout)
        self._client = client
        self.cache_ttl_seconds = (
            max(0.0, float(cache_ttl_seconds))
            if cache_ttl_seconds is not None
            else cache_ttl_seconds_from_env()
        )
        self._clock = clock
        self._cache: dict[tuple[str, str], tuple[float, SignaActionCardObservation]] = {}

    def _cached(self, symbol: str, timeframe: str) -> SignaActionCardObservation | None:
        if self.cache_ttl_seconds <= 0:
            return None
        entry = self._cache.get((symbol, timeframe))
        if entry is None:
            return None
        stored_at, observation = entry
        if self._clock() - stored_at >= self.cache_ttl_seconds:
            self._cache.pop((symbol, timeframe), None)
            return None
        return replace(observation, cached=True)

    def _store(self, symbol: str, timeframe: str, observation: SignaActionCardObservation) -> None:
        if self.cache_ttl_seconds <= 0 or not observation.ok:
            return
        self._cache[(symbol, timeframe)] = (self._clock(), observation)

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def fetch_action_card(self, symbol: str, timeframe: str = "1d") -> SignaActionCardObservation:
        symbol = (symbol or "").strip().upper()
        timeframe = (timeframe or "1d").strip() or "1d"
        retrieved_at = datetime.now(timezone.utc).isoformat()

        if not symbol:
            return SignaActionCardObservation(
                ok=False,
                error="missing_symbol",
                retrieved_at=retrieved_at,
            )
        if not self.configured:
            return SignaActionCardObservation(
                ok=False,
                symbol=symbol,
                timeframe=timeframe,
                error="missing_api_key",
                retrieved_at=retrieved_at,
            )

        cached = self._cached(symbol, timeframe)
        if cached is not None:
            return cached

        close_client = self._client is None
        http = self._client or httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            follow_redirects=True,
        )
        try:
            response = http.get(
                f"/api/v1/signals/{symbol}",
                params={"timeframe": timeframe},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
            observation = parse_action_card(payload, retrieved_at=retrieved_at)
            if observation.symbol is None or observation.timeframe is None:
                observation = replace(
                    observation,
                    symbol=observation.symbol or symbol,
                    timeframe=observation.timeframe or timeframe,
                )
            self._store(symbol, timeframe, observation)
            return observation
        except httpx.HTTPStatusError as exc:
            return SignaActionCardObservation(
                ok=False,
                symbol=symbol,
                timeframe=timeframe,
                error=f"http_{exc.response.status_code}",
                retrieved_at=retrieved_at,
            )
        except (httpx.HTTPError, ValueError) as exc:
            return SignaActionCardObservation(
                ok=False,
                symbol=symbol,
                timeframe=timeframe,
                error=type(exc).__name__,
                retrieved_at=retrieved_at,
            )
        finally:
            if close_client:
                http.close()
