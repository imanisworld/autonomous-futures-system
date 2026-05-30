"""Watchlist scan orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .config import ScannerConfig
from .discord import DiscordAlerter
from .scorer import ScoreResult, is_ny_open, score_setup
from .storage import ScanStorage
from .tastytrade_client import TastytradeClient


@dataclass(frozen=True)
class ScanOutcome:
    result: ScoreResult
    alert_sent: bool
    alert_suppression_reason: str
    storage_id: int


class OptionsScanner:
    def __init__(
        self,
        config: ScannerConfig,
        tastytrade: TastytradeClient,
        storage: ScanStorage,
        discord: DiscordAlerter,
    ):
        self.config = config
        self.tastytrade = tastytrade
        self.storage = storage
        self.discord = discord
        self.last_run_at: str | None = None
        self.last_skip_reason: str | None = None

    def is_market_hours(self, now: datetime | None = None) -> bool:
        local = (now or datetime.now(ZoneInfo(self.config.timezone))).astimezone(
            ZoneInfo(self.config.timezone)
        )
        if local.weekday() >= 5:
            return False
        return (local.hour > 9 or (local.hour == 9 and local.minute >= 30)) and local.hour < 16

    async def scan_watchlist(
        self,
        *,
        source: str = "scheduled",
        context: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> list[ScanOutcome]:
        now = now or datetime.now(ZoneInfo(self.config.timezone))
        if source == "scheduled" and not self.is_market_hours(now):
            self.last_skip_reason = "outside_market_hours"
            return []
        self.last_run_at = now.isoformat()
        self.last_skip_reason = None
        outcomes = []
        for ticker in self.config.watchlist:
            outcomes.append(await self.scan_ticker(ticker, source=source, context=context, now=now))
        return outcomes

    async def scan_ticker(
        self,
        ticker: str,
        *,
        source: str,
        context: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ScanOutcome:
        now = now or datetime.now(ZoneInfo(self.config.timezone))
        normalized = await self._build_normalized_data(ticker, context or {}, now)
        result = score_setup(normalized, now=now)
        decision = await self.discord.send_if_eligible(result, now=now)
        storage_id = self.storage.record_scan(
            result,
            source=source,
            alert_sent=decision.sent,
            alert_suppression_reason=decision.reason,
            timestamp=now,
        )
        return ScanOutcome(result, decision.sent, decision.reason, storage_id)

    async def _build_normalized_data(
        self, ticker: str, context: dict[str, Any], now: datetime
    ) -> dict[str, Any]:
        snapshot = await self.tastytrade.fetch_market_snapshot(ticker)
        data = {
            "ticker": ticker.upper(),
            "pattern": context.get("pattern") or context.get("strat_pattern") or "N/A",
            "price": context.get("price", snapshot.price),
            "vwap": context.get("vwap"),
            "ema20": context.get("ema20") or context.get("ema_20"),
            "volume": context.get("volume", snapshot.volume),
            "average_volume": context.get("average_volume") or context.get("avg_volume"),
            "volume_ratio": context.get("volume_ratio"),
            "iv_rank": context.get("iv_rank", snapshot.iv_rank),
            "ny_open": is_ny_open(now, self.config.timezone),
            "tastytrade_error": snapshot.error,
            "tastytrade_raw": snapshot.raw,
        }
        for key in (
            "alert_state",
            "status",
            "contract",
            "strike",
            "expiry",
            "expiration",
            "stop",
            "stop_level",
            "target",
            "target_1",
            "target_2",
            "why",
            "why_forming",
            "edge",
            "flow_note",
            "gex_note",
            "risk",
            "summary",
            "thesis",
            "strat_combo",
            "combo",
            "strat_sequence",
            "timeframe",
            "timeframes",
            "tf",
            "tf_stack",
            "ftfc",
            "ftfc_direction",
            "full_timeframe_continuity",
        ):
            if key in context:
                data[key] = context[key]
        if data["volume_ratio"] is None and data["volume"] and data["average_volume"]:
            try:
                data["volume_ratio"] = float(data["volume"]) / float(data["average_volume"])
            except (TypeError, ValueError, ZeroDivisionError):
                data["volume_ratio"] = None
        return data

    def status(self) -> dict[str, Any]:
        latest = [asdict(item) for item in self.storage.latest(limit=10)]
        return {
            "service": "options-scanner",
            "advisory_only": True,
            "watchlist": self.config.watchlist,
            "last_run_at": self.last_run_at,
            "last_skip_reason": self.last_skip_reason,
            "tastytrade_configured": self.config.tastytrade_configured,
            "latest": latest,
        }
