"""Watchlist scan orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .bar_context import BarContextBuilder
from .config import ScannerConfig
from .discord import AlertDecision, DiscordAlerter
from .lifecycle import classify_candidate, open_candidate_fields, resolve_open_setup
from .market_data import MarketDataClient, build_provider_capabilities
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
        bar_context: BarContextBuilder | None = None,
    ):
        self.config = config
        self.market_data = market_data
        self.storage = storage
        self.discord = discord
        self.signa_client = signa_client
        # Optional by design: with no builder the scanner behaves exactly as
        # before, so enabling causal context is one explicit switch.
        self.bar_context = bar_context
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
        # The gate runs BEFORE the send, not as a relabel afterwards: an
        # unproven setup must not reach Discord at all, and a structural
        # failure is the more specific truth than "score below threshold" --
        # without it a blind lane and a quiet market are logged identically,
        # which is the exact defect this lane already had once.
        gate = self._structural_gate(normalized)
        if gate:
            decision = AlertDecision(False, gate)
        else:
            decision = await self.discord.send_if_eligible(result, now=now)
        suppression_reason = decision.reason
        storage_id = self.storage.record_scan(
            result,
            source=source,
            alert_sent=decision.sent,
            alert_suppression_reason=suppression_reason,
            timestamp=now,
        )
        # Only fully-formed candidates open a paper position in the shadow
        # journal; ordinary scans and provider failures record the scan row only.
        classification = classify_candidate({**result.raw, "direction": result.direction})
        shadow_id = 0
        shadow_reason = classification.reason or "candidate"
        if classification.is_open_eligible and gate:
            # No setup, no candidate: an unproven scan must not open a paper
            # position in the shadow journal either, or the campaign measures
            # its own blind spots as though they were setups.
            shadow_reason = f"suppressed:{gate}"
        elif classification.is_open_eligible:
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

        # Signa is observational telemetry only. There is intentionally no
        # second Signa-only Discord path here; all user-facing scanner alerts
        # flow through the same structural gate and DiscordAlerter above.
        return ScanOutcome(
            result, decision.sent, suppression_reason, storage_id, shadow_id, shadow_reason
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

    def _causal_lane_active(self) -> bool:
        """Whether this scanner is meant to be running on causal structure.

        True when the operator switched the lane on, even if the builder could
        not be constructed -- an enabled-but-broken configuration must not be
        indistinguishable from an intentional OFF.
        """
        return bool(
            getattr(self.config, "bar_context_enabled", False)
            or self.bar_context is not None
        )

    def _structural_gate(self, normalized: dict[str, Any]) -> str:
        """Reason this scan may not alert, or ``""`` when it may.

        With the lane off, the previous behaviour is preserved untouched. With
        the lane on, a generic alert requires a TRIGGERED verdict from the
        shared setup authority -- for every source, including a webhook that
        supplied its own VWAP, EMA20 and pattern. Caller-supplied values may
        still win precedence for scoring and display; they never stand in for
        mechanical proof. And telemetry that is simply absent is not
        permission: it fails closed as ``setup_proof_missing``.
        """
        if not self._causal_lane_active():
            return ""
        if "bar_context_available" not in normalized:
            return "setup_proof_missing"
        if not normalized.get("bar_context_available"):
            return str(normalized.get("bar_context_reason") or "bar_context_unavailable")
        status = normalized.get("setup_status")
        if not status:
            return "setup_proof_missing"
        if status != "TRIGGERED":
            return str(
                normalized.get("setup_suppression_reason") or f"no_setup:{str(status).lower()}"
            )
        return ""

    async def _build_normalized_data(
        self, ticker: str, context: dict[str, Any], now: datetime
    ) -> dict[str, Any]:
        snapshot = await self.market_data.fetch_market_snapshot(ticker)
        signa_context = await self._fetch_signa_context(ticker, context)
        bar_fields = await self._fetch_bar_context(ticker, now)
        caller_vwap = context.get("vwap")
        caller_ema20 = context.get("ema20")
        if caller_ema20 is None:
            caller_ema20 = context.get("ema_20")
        data = {
            "ticker": ticker.upper(),
            "pattern": (
                context.get("pattern")
                or context.get("strat_pattern")
                or bar_fields.get("pattern")
                or "N/A"
            ),
            "price": context.get("price", snapshot.price),
            "vwap": caller_vwap if caller_vwap is not None else bar_fields.get("vwap"),
            "ema20": caller_ema20 if caller_ema20 is not None else bar_fields.get("ema20"),
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
        # Caller-supplied context always wins; bar context fills the rest and
        # always contributes its telemetry, including on failure.
        for key, value in bar_fields.items():
            if key in {"vwap", "ema20", "pattern"}:
                continue
            data.setdefault(key, value)
        if data["volume_ratio"] is None and data["volume"] and data["average_volume"]:
            try:
                data["volume_ratio"] = float(data["volume"]) / float(data["average_volume"])
            except (TypeError, ValueError, ZeroDivisionError):
                data["volume_ratio"] = None
        return data

    async def _fetch_bar_context(self, ticker: str, now: datetime) -> dict[str, Any]:
        """Causal structural context, or telemetry stating why there is none.

        Skipped entirely when the lane is off, so a deployment that never
        switched it on behaves exactly as it did before this lane existed.
        With the lane on it is evaluated for every scan, including a webhook
        that supplied its own VWAP and EMA20: those values may take precedence
        for scoring, but they are not a setup verdict and must not suppress the
        one source of it. Switched on but not constructible is a third,
        explicitly reported case -- never a silent fourth.
        """
        if not self._causal_lane_active():
            return {}
        if self.bar_context is None:
            # Switched on but not constructible: missing or unreadable
            # credentials, most likely. Say so rather than behaving as though
            # the lane had been left off on purpose.
            return {
                "bar_context_available": False,
                "bar_context_reason": "bar_context_unconfigured",
            }
        try:
            market_context = await self.bar_context.build(ticker, now)
        except Exception as exc:  # noqa: BLE001 - a context failure must not kill the scan
            return {
                "bar_context_available": False,
                "bar_context_reason": f"bar_context_error:{type(exc).__name__}",
            }
        return market_context.to_scanner_fields()

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
                "signa_requested_timeframe": context.get("signa_requested_timeframe"),
                "signa_retrieved_at": context.get("signa_retrieved_at"),
                "signa_engine_run_at": context.get("signa_engine_run_at"),
                "signa_technicals_as_of": context.get("signa_technicals_as_of"),
                "signa_stale": context.get("signa_stale"),
                "signa_cached": context.get("signa_cached"),
                "signa_raw_engine_grade": context.get("signa_raw_engine_grade"),
                "signa_raw_engine_score": context.get("signa_raw_engine_score"),
                "signa_raw_engine_direction": context.get("signa_raw_engine_direction"),
                "signa_raw_data_direction": context.get("signa_raw_data_direction"),
                "signa_raw_payload": context.get("signa_raw_payload"),
            }
        symbol = self._signa_symbol_for(ticker)
        client = self.signa_client or SignaClient(
            base_url=self.config.signa_base_url,
            timeout=self.config.signa_timeout_seconds,
        )
        signal = await asyncio.to_thread(client.fetch_signal, symbol)
        provenance = signal.provenance_fields()
        if not signal.ok:
            return {
                "signa_symbol": symbol,
                "signa_error": signal.error,
                "signa_raw_payload": signal.raw,
                **provenance,
            }
        return {
            "signa_symbol": symbol,
            "signa_grade": signal.grade,
            "signa_score": signal.score,
            "signa_daily_direction": signal.daily_direction,
            "signa_weekly_direction": signal.weekly_direction,
            "signa_action": signal.action,
            "signa_risk_rating": signal.risk_rating,
            "signa_raw_payload": signal.raw,
            **provenance,
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
                "requestedTimeframe": s["raw"].get("signa_requested_timeframe"),
                "retrievedAt": s["raw"].get("signa_retrieved_at"),
                "engineRunAt": s["raw"].get("signa_engine_run_at"),
                "technicalsAsOf": s["raw"].get("signa_technicals_as_of"),
                "stale": s["raw"].get("signa_stale"),
                "cached": s["raw"].get("signa_cached"),
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
    # Keep compact Signa provenance in the shadow row, but avoid duplicating the
    # full raw API payload into every shadow candidate. The scan journal still
    # retains `signa_raw_payload` for reconstruction.
    omitted = {"market_data_raw", "tastytrade_raw", "signa_raw_payload"}
    return {key: value for key, value in raw.items() if key not in omitted}