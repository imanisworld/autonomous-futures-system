"""Read-only client for the current documented Signa Action Card endpoint.

This client is deliberately isolated from trade authority. It has no strategy,
risk, broker, order, or execution imports.

Successful Action Cards are cached per symbol/timeframe for 30 minutes by
default. Failed requests use a shorter bounded cooldown so provider failures
cannot turn the 5-minute scanner cadence into a retry storm. The client also
participates in the shared in-process account-level 429 circuit and can reuse
the shared raw snapshot store populated by the context collector.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

import httpx

from .signa_observation import SignaActionCardObservation, parse_action_card
from .signa_request_budget import account_backoff_remaining, mark_account_rate_limited
from .signa_snapshot_store import SignaSnapshotStore


DEFAULT_BASE_URL = "https://app.getsigna.ai"
DEFAULT_CACHE_TTL_SECONDS = 1800.0
DEFAULT_FAILURE_BACKOFF_SECONDS = 900.0


def cache_ttl_seconds_from_env() -> float:
    raw = os.getenv("OPTIONS_SIGNA_V2_CACHE_TTL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_CACHE_TTL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_CACHE_TTL_SECONDS


def failure_backoff_seconds_from_env() -> float:
    raw = os.getenv("OPTIONS_SIGNA_V2_FAILURE_BACKOFF_SECONDS", "").strip()
    if not raw:
        return DEFAULT_FAILURE_BACKOFF_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_FAILURE_BACKOFF_SECONDS


class SignaV2Client:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 5.0,
        client: httpx.Client | None = None,
        cache_ttl_seconds: float | None = None,
        failure_backoff_seconds: float | None = None,
        snapshot_store: SignaSnapshotStore | None = None,
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
        self.failure_backoff_seconds = (
            max(0.0, float(failure_backoff_seconds))
            if failure_backoff_seconds is not None
            else failure_backoff_seconds_from_env()
        )
        self._snapshot_store = snapshot_store
        self._clock = clock
        self._cache: dict[tuple[str, str], tuple[float, SignaActionCardObservation]] = {}
        self._failure_cache: dict[tuple[str, str], tuple[float, SignaActionCardObservation]] = {}

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

    def _failed_cached(self, symbol: str, timeframe: str) -> SignaActionCardObservation | None:
        if self.failure_backoff_seconds <= 0:
            return None
        entry = self._failure_cache.get((symbol, timeframe))
        if entry is None:
            return None
        stored_at, observation = entry
        if self._clock() - stored_at >= self.failure_backoff_seconds:
            self._failure_cache.pop((symbol, timeframe), None)
            return None
        return replace(observation, cached=True)

    def _store(self, symbol: str, timeframe: str, observation: SignaActionCardObservation) -> None:
        if self.cache_ttl_seconds <= 0 or not observation.ok:
            return
        self._cache[(symbol, timeframe)] = (self._clock(), observation)
        self._failure_cache.pop((symbol, timeframe), None)

    def _store_failure(self, symbol: str, timeframe: str, observation: SignaActionCardObservation) -> None:
        if self.failure_backoff_seconds <= 0 or observation.ok:
            return
        self._failure_cache[(symbol, timeframe)] = (self._clock(), observation)

    def _shared_snapshot(self, symbol: str, timeframe: str) -> SignaActionCardObservation | None:
        if self._snapshot_store is None or self.cache_ttl_seconds <= 0:
            return None
        try:
            stored = self._snapshot_store.find_fresh(
                endpoint=f"/api/v1/signals/{symbol}",
                symbol=symbol,
                timeframe=timeframe,
                params={"symbol": symbol, "timeframe": timeframe},
                max_age_seconds=self.cache_ttl_seconds,
            )
        except Exception:
            return None
        if stored is None:
            return None
        observation = parse_action_card(stored.payload, retrieved_at=stored.retrieved_at)
        if not observation.ok:
            return None
        if observation.symbol is None or observation.timeframe is None:
            observation = replace(
                observation,
                symbol=observation.symbol or symbol,
                timeframe=observation.timeframe or timeframe,
            )
        observation = replace(observation, cached=True)
        self._store(symbol, timeframe, observation)
        return observation

    def _record_shared_snapshot(
        self,
        *,
        symbol: str,
        timeframe: str,
        payload: dict,
        retrieved_at: str,
        status_code: int | None,
    ) -> None:
        if self._snapshot_store is None:
            return
        try:
            self._snapshot_store.record_snapshot(
                endpoint=f"/api/v1/signals/{symbol}",
                symbol=symbol,
                timeframe=timeframe,
                params={"symbol": symbol, "timeframe": timeframe},
                retrieved_at=retrieved_at,
                data_as_of=payload.get("data_as_of"),
                payload=payload,
                status="OK",
                http_status=status_code,
            )
        except Exception:
            return

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

        failed_cached = self._failed_cached(symbol, timeframe)
        if failed_cached is not None:
            return failed_cached

        shared = self._shared_snapshot(symbol, timeframe)
        if shared is not None:
            return shared

        if account_backoff_remaining(
            self.base_url,
            self.api_key,
            clock=self._clock,
        ) > 0:
            return SignaActionCardObservation(
                ok=False,
                symbol=symbol,
                timeframe=timeframe,
                error="account_backoff_active",
                retrieved_at=retrieved_at,
            )

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
            if response.status_code == 429:
                mark_account_rate_limited(
                    self.base_url,
                    self.api_key,
                    retry_after=response.headers.get("Retry-After"),
                    clock=self._clock,
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
            if observation.ok:
                self._record_shared_snapshot(
                    symbol=symbol,
                    timeframe=timeframe,
                    payload=payload,
                    retrieved_at=retrieved_at,
                    status_code=response.status_code,
                )
                self._store(symbol, timeframe, observation)
            else:
                self._store_failure(symbol, timeframe, observation)
            return observation
        except httpx.HTTPStatusError as exc:
            observation = SignaActionCardObservation(
                ok=False,
                symbol=symbol,
                timeframe=timeframe,
                error=f"http_{exc.response.status_code}",
                retrieved_at=retrieved_at,
            )
            self._store_failure(symbol, timeframe, observation)
            return observation
        except (httpx.HTTPError, ValueError) as exc:
            observation = SignaActionCardObservation(
                ok=False,
                symbol=symbol,
                timeframe=timeframe,
                error=type(exc).__name__,
                retrieved_at=retrieved_at,
            )
            self._store_failure(symbol, timeframe, observation)
            return observation
        finally:
            if close_client:
                http.close()
