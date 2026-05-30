"""Advisory Discord alerting with duplicate suppression."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from .config import ScannerConfig
from .scorer import ScoreResult
from .storage import ScanStorage


@dataclass(frozen=True)
class AlertDecision:
    sent: bool
    reason: str


class DiscordAlerter:
    def __init__(
        self,
        config: ScannerConfig,
        storage: ScanStorage,
        client: httpx.AsyncClient | None = None,
    ):
        self.config = config
        self.storage = storage
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "DiscordAlerter":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client and self._owns_client:
            await self._client.aclose()

    async def send_if_eligible(self, result: ScoreResult, now: datetime | None = None) -> AlertDecision:
        if result.score < self.config.alert_threshold:
            return AlertDecision(False, "score_below_threshold")
        if not self.config.discord_webhook_url:
            return AlertDecision(False, "discord_not_configured")
        if self.storage.recent_alert_exists(
            result.ticker,
            result.direction,
            result.pattern,
            window_minutes=self.config.duplicate_window_minutes,
            now=now,
        ):
            return AlertDecision(False, "duplicate_30m")

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10)
            self._owns_client = True
        try:
            response = await self._client.post(
                self.config.discord_webhook_url,
                json=build_discord_payload(result),
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return AlertDecision(False, "discord_error")
        return AlertDecision(True, "")


def build_discord_payload(result: ScoreResult) -> dict[str, Any]:
    raw = result.raw
    volume_ratio = raw.get("volume_ratio")
    iv_rank = raw.get("iv_rank")
    session = "NY Open" if raw.get("ny_open") else "Other"
    iv_label = "unknown"
    if isinstance(iv_rank, (int, float)):
        iv_label = "cheap" if iv_rank < 30 else "neutral" if iv_rank <= 50 else "expensive"
    direction = "LONG (calls)" if result.direction == "LONG" else "SHORT (puts)"
    return {
        "embeds": [
            {
                "title": f"\U0001f7e2 A+ SETUP \u2014 {result.ticker} | Score: {result.score}/10",
                "color": 3066993,
                "fields": [
                    {"name": "Direction", "value": direction, "inline": True},
                    {"name": "Pattern", "value": result.pattern or "N/A", "inline": True},
                    {"name": "VWAP", "value": "pass" if result.components.get("vwap") else "fail", "inline": True},
                    {"name": "Trend", "value": "pass" if result.components.get("trend") else "fail", "inline": True},
                    {"name": "Volume", "value": _ratio_text(volume_ratio), "inline": True},
                    {"name": "IV Rank", "value": _iv_text(iv_rank, iv_label), "inline": True},
                    {"name": "Session", "value": session, "inline": True},
                ],
                "footer": {"text": "Advisory only \u2014 not financial advice"},
            }
        ]
    }


def _ratio_text(value: Any) -> str:
    try:
        return f"{float(value):.2f}x"
    except (TypeError, ValueError):
        return "N/A"


def _iv_text(value: Any, label: str) -> str:
    try:
        return f"{float(value):.1f}% ({label})"
    except (TypeError, ValueError):
        return "N/A (unknown)"
