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
    side = "CALL" if result.direction == "LONG" else "PUT"
    state = _alert_state(result)
    color = 3066993 if result.direction == "LONG" else 15158332
    fields = [
        {"name": "Direction", "value": direction, "inline": True},
        {"name": "Confidence", "value": f"{result.score * 10}/100", "inline": True},
        {"name": "Pattern", "value": result.pattern or "N/A", "inline": True},
        {"name": "Strat Combo", "value": _strat_combo_text(result), "inline": True},
        {"name": "Timeframe", "value": _timeframe_text(result), "inline": True},
        {"name": "FTFC", "value": _ftfc_text(result), "inline": True},
        {"name": "Watch Contract", "value": _contract_text(result, side), "inline": True},
        {"name": "Spot Price", "value": _money_text(raw.get("price")), "inline": True},
        {"name": "Stop Level", "value": _money_text(raw.get("stop") or raw.get("stop_level")), "inline": True},
        {"name": "Target 1", "value": _money_text(raw.get("target_1") or raw.get("target")), "inline": True},
        {"name": "Target 2", "value": _money_text(raw.get("target_2")), "inline": True},
        {"name": "Volume", "value": _ratio_text(volume_ratio), "inline": True},
        {"name": "IV Rank", "value": _iv_text(iv_rank, iv_label), "inline": True},
        {"name": "Premium Value", "value": _premium_value_text(result), "inline": True},
        {"name": "VWAP", "value": _pass_fail(result.components.get("vwap")), "inline": True},
        {"name": "Trend", "value": _pass_fail(result.components.get("trend")), "inline": True},
        {"name": "Why", "value": _why_text(result, session), "inline": False},
        {"name": "Edge", "value": _edge_text(result), "inline": False},
        {"name": "Risk", "value": _risk_text(result), "inline": False},
    ]
    return {
        "embeds": [
            {
                "title": _alert_title(result, side, state),
                "description": _alert_description(result, side, state),
                "color": color,
                "fields": [field for field in fields if field["value"] != "N/A"],
                "footer": {
                    "text": (
                        "Signal Engine - "
                        f"{session} - Advisory only, independent research required"
                    )
                },
            }
        ]
    }


def _alert_state(result: ScoreResult) -> str:
    raw_state = str(result.raw.get("status") or result.raw.get("alert_state") or "").lower()
    if raw_state in {"forming", "developing", "on_deck"}:
        return "forming"
    if result.score >= 9:
        return "golden"
    return "confirmed"


def _alert_title(result: ScoreResult, side: str, state: str) -> str:
    prefix = "▲"
    if state == "forming":
        return f"{prefix} {result.ticker} {side} - SETUP FORMING ⭐"
    if state == "golden":
        return f"{prefix} {result.ticker} {side} - A+ CONFIRMED ⭐ GOLDEN SETUP"
    return f"{prefix} {result.ticker} {side} - A+ SETUP CONFIRMED"


def _alert_description(result: ScoreResult, side: str, state: str) -> str:
    raw = result.raw
    thesis = raw.get("thesis") or raw.get("summary")
    if thesis:
        return str(thesis)
    if state == "forming":
        return (
            f"**{result.ticker} {side}** structure is building. "
            "All gates not yet passed. Monitor closely - do not enter early."
        )
    return (
        f"**{result.ticker} is showing "
        f"{'bullish' if result.direction == 'LONG' else 'bearish'} structure.** "
        f"All gates passed. System confidence: {result.score * 10}/100."
    )


def _contract_text(result: ScoreResult, side: str) -> str:
    raw = result.raw
    contract = raw.get("contract")
    if contract:
        return str(contract)
    strike = raw.get("strike")
    expiry = raw.get("expiry") or raw.get("expiration")
    if strike and expiry:
        return f"{result.ticker} ${strike} {side.title()} - {expiry}"
    if strike:
        return f"{result.ticker} ${strike} {side.title()}"
    return "N/A"


def _why_text(result: ScoreResult, session: str) -> str:
    raw = result.raw
    why = raw.get("why") or raw.get("why_forming")
    if why:
        return str(why)
    reasons = []
    if result.pattern and result.pattern.upper() != "N/A":
        reasons.append(f"{result.pattern} confirmed")
    if result.components.get("vwap"):
        reasons.append("VWAP aligned")
    if result.components.get("trend"):
        reasons.append("20 EMA trend aligned")
    if result.components.get("volume"):
        reasons.append("volume expanding")
    if result.components.get("session"):
        reasons.append(f"{session} window")
    return "; ".join(reasons) if reasons else "Setup passed the scanner gates."


def _edge_text(result: ScoreResult) -> str:
    raw = result.raw
    edge = raw.get("edge") or raw.get("flow_note") or raw.get("gex_note")
    if edge:
        return str(edge)
    gates = sum(1 for value in result.components.values() if value > 0)
    return f"Multi-factor alignment confirmed. {gates} positive gates passed."


def _risk_text(result: ScoreResult) -> str:
    raw = result.raw
    risk = raw.get("risk")
    if risk:
        return str(risk)
    if _alert_state(result) == "forming":
        return "Setup is developing - NOT confirmed. Wait for the A+ signal before entering."
    return "Size for your account. Exit at stop - no exceptions. Targets 1 and 2 are the plan."


def _strat_combo_text(result: ScoreResult) -> str:
    raw = result.raw
    combo = raw.get("strat_combo") or raw.get("combo") or raw.get("strat_sequence")
    if isinstance(combo, (list, tuple)):
        return "-".join(str(item) for item in combo)
    if combo and str(combo).upper() != "N/A":
        return str(combo)
    return "N/A"


def _timeframe_text(result: ScoreResult) -> str:
    raw = result.raw
    timeframe = raw.get("timeframe") or raw.get("tf")
    if timeframe:
        return str(timeframe)
    timeframes = raw.get("timeframes") or raw.get("tf_stack")
    if isinstance(timeframes, (list, tuple)):
        return " / ".join(str(item) for item in timeframes)
    if timeframes:
        return str(timeframes)
    return "N/A"


def _ftfc_text(result: ScoreResult) -> str:
    raw = result.raw
    ftfc = raw.get("ftfc")
    if ftfc is None:
        ftfc = raw.get("full_timeframe_continuity")
    if isinstance(ftfc, bool):
        direction = raw.get("ftfc_direction") or result.direction
        return f"Yes ({direction})" if ftfc else "No"
    if ftfc:
        direction = raw.get("ftfc_direction")
        return f"{ftfc} ({direction})" if direction else str(ftfc)
    return "N/A"


def _pass_fail(value: Any) -> str:
    return "pass" if value else "fail"


def _money_text(value: Any) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "N/A"


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


def _premium_value_text(result: ScoreResult) -> str:
    raw = result.raw
    verdict = raw.get("option_value_verdict")
    if not verdict:
        return "N/A"
    edge = raw.get("option_edge_percent")
    fair = raw.get("option_theoretical_value")
    score = result.components.get("premium_value", 0)
    try:
        edge_text = f"{float(edge):+.1f}%"
    except (TypeError, ValueError):
        edge_text = "n/a"
    try:
        fair_text = f"${float(fair):.2f}"
    except (TypeError, ValueError):
        fair_text = "n/a"
    label = str(verdict).replace("_", " ").title()
    return f"{label} ({edge_text}, fair {fair_text}, score {score:+})"
