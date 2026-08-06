"""Watchlist scan orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .config import ScannerConfig
from .discord import DiscordAlerter
from .lifecycle import classify_candidate, open_candidate_fields, resolve_open_setup
from .market_data import MarketDataClient, build_provider_capabilities
from .rh_options import build_candidate_embed
from .scorer import ScoreResult, is_ny_open, score_setup
from .storage import ScanStorage
from sources.signa_client import SignaClient


@dataclass(frozen=True)
class ScanOutcome:
    result: ScoreResult
    alert_sent: bool
    alert_suppression_reason: str
    storage_id: int
    shadow_id: int
    shadow_reason: str = ""


class OptionsScanner:
    def __init__(
        self,
        config: ScannerConfig,
        market_data: MarketDataClient,
        storage: ScanStorage,
        discord: DiscordAlerter,
        signa_client: SignaClient | None = None,
    ):
        self.config = config
        self.market_data = market_data
        self.storage = storage
        self.discord = discord
        self.signa_client = signa_client
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
        # Only fully-formed candidates open a paper position in the shadow
        # journal; ordinary scans and provider failures record the scan row only.
        classification = classify_candidate({**result.raw, "direction": result.direction})
        shadow_id = 0
        shadow_reason = classification.reason or "candidate"
        if classification.is_open_eligible:
            duplicate = self.storage.find_open_duplicate(
                result.ticker, classification.contract_key
            )
            if duplicate is not None:
                shadow_reason = f"duplicate_open:{duplicate}"
            else:
                selected = _selected_contract(result.raw)
                selected.update(open_candidate_fields(result.raw, classification.contract_key))
                shadow_id = self.storage.record_shadow_setup(
                    result,
                    scan_id=storage_id,
                    setup_inputs=_shadow_setup_inputs(result.raw),
                    provider_snapshot=_provider_snapshot(result.raw),
                    selected_contract=selected,
                    timestamp=now,
                )
        elif not classification.reason.startswith("provider_error"):
            shadow_reason = "not_a_candidate:" + ",".join(classification.missing)
        # Fire enriched options candidate alert when Signa qualifies — never on
        # a failed or incomplete scan.
        if not normalized.get("market_data_error") or "price" in (context or {}):
            await self._maybe_send_candidate_alert(ticker, normalized, now)
        return ScanOutcome(
            result, decision.sent, decision.reason, storage_id, shadow_id, shadow_reason
        )

    async def resolve_open_candidates(self, now: datetime | None = None) -> dict[str, int]:
        """Resolve OPEN paper candidates against fresh underlying quotes.

        Provider failures leave rows OPEN — a candidate is never resolved on
        missing data (expiry is the only exception, which needs no quote).
        """
        now = now or datetime.now(ZoneInfo(self.config.timezone))
        counts = {"checked": 0, "resolved": 0}
        prices: dict[str, float | None] = {}
        last_id = 0
        while True:
            batch = self.storage.open_setups_after(last_id)
            if not batch:
                break
            for setup in batch:
                last_id = setup.id
                counts["checked"] += 1
                ticker = setup.ticker
                if ticker not in prices:
                    snapshot = await self.market_data.fetch_market_snapshot(ticker)
                    prices[ticker] = None if snapshot.error else snapshot.price
                resolution = resolve_open_setup(
                    direction=setup.direction,
                    contract=setup.selected_contract,
                    underlying_price=prices[ticker],
                    now=now,
                )
                if resolution is None:
                    continue
                status, outcome = resolution
                self.storage.update_shadow_outcome(setup.id, status=status, outcome=outcome)
                counts["resolved"] += 1
        return counts

    async def _maybe_send_candidate_alert(
        self, ticker: str, normalized: dict[str, Any], now: datetime
    ) -> None:
        """Send enriched options Discord when Signa score/grade qualify.

        Uses Signa pivots as proxy GEX walls (same approach as evaluate-auto).
        Fires in addition to (not instead of) the generic watchlist alert.
        Advisory only — no gates blocked, no orders placed.
        """
        discord_url = self.config.discord_webhook_url
        if not discord_url:
            return

        score = normalized.get("signa_score")
        grade = normalized.get("signa_grade")
        direction_raw = normalized.get("signa_daily_direction") or ""
        price_raw = normalized.get("price")

        if not score or not grade or not direction_raw or not price_raw:
            return
        try:
            score_f = float(score)
            price_f = float(price_raw)
        except (TypeError, ValueError):
            return

        if score_f < 70:
            return
        if str(grade).upper() not in ("A+", "A", "B"):
            return

        direction = "LONG" if str(direction_raw).upper() in ("BULLISH", "UP", "LONG") else "SHORT"

        # Earnings guard — suppress alert if earnings within 5 days
        earnings_note: str | None = None
        earnings_raw = normalized.get("earnings_date")
        if earnings_raw:
            try:
                from datetime import date
                earnings_dt = date.fromisoformat(str(earnings_raw))
                days_to_earnings = (earnings_dt - now.date()).days
                if 0 <= days_to_earnings <= 5:
                    return  # too close to earnings — don't ping
                if days_to_earnings <= 14:
                    earnings_note = f"Earnings in {days_to_earnings}d ({earnings_raw})"
            except ValueError:
                pass

        # GEX walls: use Signa pivots as proxy (S1 = put wall, R1 = call wall)
        pivot_s1 = normalized.get("signa_pivot_s1")
        pivot_r1 = normalized.get("signa_pivot_r1")
        call_wall: float | None = None
        put_wall: float | None = None
        regime = "TRANSITION"
        try:
            if pivot_s1 and pivot_r1:
                put_wall = float(pivot_s1)
                call_wall = float(pivot_r1)
                if price_f > call_wall:
                    regime = "BREAKOUT"
                elif price_f < put_wall:
                    regime = "BREAKDOWN"
                elif abs(price_f - call_wall) / price_f <= 0.01 or abs(price_f - put_wall) / price_f <= 0.01:
                    regime = "LOW_PINNING"
        except (TypeError, ValueError):
            pass

        embed = build_candidate_embed(
            ticker,
            direction,
            score_f,
            str(grade).upper(),
            price_f,
            call_wall=call_wall,
            put_wall=put_wall,
            regime=regime,
            note=earnings_note,
        )
        payload = {"embeds": [embed]}
        try:
            await asyncio.to_thread(
                httpx.post, discord_url, json=payload, timeout=5.0
            )
        except Exception:
            pass

    async def _build_normalized_data(
        self, ticker: str, context: dict[str, Any], now: datetime
    ) -> dict[str, Any]:
        snapshot = await self.market_data.fetch_market_snapshot(ticker)
        signa_context = await self._fetch_signa_context(ticker, context)
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
            "option_type": context.get("option_type") or context.get("right"),
            "option_mark": context.get("option_mark") or context.get("mark") or context.get("premium"),
            "theoretical_value": context.get("theoretical_value") or context.get("fair_value"),
            "underlying_price": context.get("underlying_price") or context.get("spot"),
            "dte": context.get("dte") or context.get("days_to_expiration"),
            "implied_volatility": context.get("implied_volatility") or context.get("iv"),
            "risk_free_rate": context.get("risk_free_rate"),
            "ny_open": is_ny_open(now, self.config.timezone),
            "source_timestamp": context.get("timestamp")
            or context.get("source_timestamp")
            or now.isoformat(),
            "market_data_provider": self.config.market_data_provider,
            "market_data_error": snapshot.error,
            "market_data_raw": snapshot.raw,
            # Backward-compatible aliases for existing status/tests.
            "tastytrade_error": snapshot.error if self.config.market_data_provider == "tastytrade" else None,
            "tastytrade_raw": snapshot.raw if self.config.market_data_provider == "tastytrade" else {},
            **signa_context,
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
            "invalidation",
            "target",
            "target_1",
            "target_2",
            "option_bid",
            "option_ask",
            "open_interest",
            "option_open_interest",
            "option_volume",
            "oi",
            "risk_cap",
            "max_loss",
            "risk_dollars",
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

    async def _fetch_signa_context(self, ticker: str, context: dict[str, Any]) -> dict[str, Any]:
        if not self.config.signa_api_enabled:
            return {}
        if context.get("signa_grade") or context.get("signa_score") or context.get("signa_daily_direction"):
            return {
                "signa_symbol": context.get("signa_symbol") or ticker.upper(),
                "signa_grade": context.get("signa_grade"),
                "signa_score": context.get("signa_score"),
                "signa_daily_direction": context.get("signa_daily_direction") or context.get("signa_direction"),
                "signa_weekly_direction": context.get("signa_weekly_direction"),
                "signa_action": context.get("signa_action"),
                "signa_risk_rating": context.get("signa_risk_rating"),
                "signa_pivot_s1": context.get("signa_pivot_s1"),
                "signa_pivot_r1": context.get("signa_pivot_r1"),
            }
        symbol = self._signa_symbol_for(ticker)
        client = self.signa_client or SignaClient(
            base_url=self.config.signa_base_url,
            timeout=self.config.signa_timeout_seconds,
        )
        signal = await asyncio.to_thread(client.fetch_signal, symbol)
        if not signal.ok:
            return {"signa_symbol": symbol, "signa_error": signal.error}
        return {
            "signa_symbol": symbol,
            "signa_grade": signal.grade,
            "signa_score": signal.score,
            "signa_daily_direction": signal.daily_direction,
            "signa_weekly_direction": signal.weekly_direction,
            "signa_action": signal.action,
            "signa_risk_rating": signal.risk_rating,
            "signa_pivot_s1": getattr(signal, "pivot_s1", None),
            "signa_pivot_r1": getattr(signal, "pivot_r1", None),
        }

    def _signa_symbol_for(self, ticker: str) -> str:
        root = (ticker or "").split(":")[-1].upper().strip()
        root = root.replace("1!", "").rstrip("!1234567890HMUZ")
        return self.config.signa_symbol_map.get(root, root)

    def status(self) -> dict[str, Any]:
        latest = [asdict(item) for item in self.storage.latest(limit=10)]
        provider_profile = build_provider_capabilities(
            self.config,
            last_error=getattr(self.market_data, "last_error", None),
        ).to_dict()
        scans = [
            {
                "symbol": s["ticker"],
                "time": s["timestamp"],
                "score": s["score"],
                "direction": s["direction"],
                "pattern": s["pattern"],
                "contract": s["raw"].get("contract") or s["raw"].get("strike"),
                "premium": s["raw"].get("option_mark"),
            }
            for s in latest
        ]
        signa = [
            {
                "symbol": s["ticker"],
                "grade": s["raw"].get("signa_grade"),
                "score": s["raw"].get("signa_score"),
                "action": s["raw"].get("signa_action"),
                "direction": s["raw"].get("signa_daily_direction") or s["raw"].get("signa_direction"),
                "alertSent": s["alert_sent"],
                "suppressedReason": s["alert_suppression_reason"] or None,
            }
            for s in latest
            if s["raw"].get("signa_grade") or s["raw"].get("signa_score")
        ]
        return {
            "service": "options-scanner",
            "advisory_only": True,
            "watchlist": self.config.watchlist,
            "market_data_provider": self.config.market_data_provider,
            "market_data_configured": self.config.market_data_configured,
            "provider_profile": provider_profile,
            "last_run_at": self.last_run_at,
            "last_skip_reason": self.last_skip_reason,
            "tastytrade_configured": self.config.tastytrade_configured,
            "signa_api_enabled": self.config.signa_api_enabled,
            "signa_api_key_configured": self.config.signa_api_key_configured,
            "latest": latest,
            "scans": scans,
            "signa": signa,
        }

    def terminal_state(self) -> dict[str, Any]:
        status = self.status()
        status["shadow_journal"] = [asdict(item) for item in self.storage.latest_shadow_setups(limit=10)]
        status["shadow_summary"] = asdict(self.storage.shadow_summary())
        status["scanner_config"] = {
            "port": self.config.port,
            "interval_minutes": self.config.interval_minutes,
            "alert_threshold": self.config.alert_threshold,
            "duplicate_window_minutes": self.config.duplicate_window_minutes,
        }
        return status


def _provider_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": raw.get("market_data_provider"),
        "error": raw.get("market_data_error"),
        "raw": raw.get("market_data_raw") or {},
    }


def _selected_contract(raw: dict[str, Any]) -> dict[str, Any]:
    keys = ("contract", "strike", "expiry", "expiration", "option_type", "option_mark", "dte")
    return {key: raw[key] for key in keys if raw.get(key) not in (None, "")}


def _shadow_setup_inputs(raw: dict[str, Any]) -> dict[str, Any]:
    omitted = {"market_data_raw", "tastytrade_raw"}
    return {key: value for key, value in raw.items() if key not in omitted}
