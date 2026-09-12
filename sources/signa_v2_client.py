"""Read-only client for the current documented Signa Action Card endpoint.

This client is deliberately isolated from the existing runtime client. It is not
wired into webhook, scanner, strategy, risk, broker, or execution paths. The
capability probe must prove the account contract before any runtime caller may be
migrated to this client.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone

import httpx

from .signa_observation import SignaActionCardObservation, parse_action_card


DEFAULT_BASE_URL = "https://app.getsigna.ai"


class SignaV2Client:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("SIGNA_API_KEY", "")).strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout)
        self._client = client

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
                return replace(
                    observation,
                    symbol=observation.symbol or symbol,
                    timeframe=observation.timeframe or timeframe,
                )
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
