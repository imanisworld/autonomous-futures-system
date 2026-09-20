"""Advisory Discord alerting with duplicate suppression."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .config import ScannerConfig
from .paper_v1 import MAX_SANITY_DTE, POLICY_ID, dte_for
from .scorer import ScoreResult
from .session_calendar import us_equity_rth_state
from .signa_v2_display import render_signa_v2
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
        trade_proof_reason = _trade_proof_block_reason(result)
        if trade_proof_reason:
            # Paper evidence is allowed to continue collecting after the causal
            # setup bridge proves structure/targets/market alignment. User-facing
            # alerts are stricter: an explicit incomplete trade-proof marker can
            # never be overridden by scanner score, Signa, or contract quality.
            return AlertDecision(False, trade_proof_reason)
        if result.score < self.config.alert_threshold:
            # The scorer already knows why it could not score (missing feed
            # inputs, against_vwap, against_trend). Reporting
            # "score_below_threshold" for those cases makes a dead data feed
            # indistinguishable from a quiet market.
            return AlertDecision(False, result.reason or "score_below_threshold")
        sanity_reason = _contract_sanity_reason(result, now)
        if sanity_reason:
            return AlertDecision(False, sanity_reason)

        # Final user-facing alert boundary: every path (scheduled, webhook, or
        # manual) must still be inside the approved US-equity regular session.
        # Scheduled scans already stop outside RTH; this closes the bypass where
        # an externally triggered scan could otherwise post after close,
        # pre-market, on weekends/holidays, or after an early close.
        session_now = now or datetime.now(ZoneInfo(self.config.timezone))
        session_state = us_equity_rth_state(session_now)
        if not session_state.is_open:
            return AlertDecision(False, session_state.reason)

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
    side = "CALL" if result.direction == "LONG" else "PUT"
    state = _alert_state(result)
    color = 0x57F287 if result.direction == "LONG" else 0xED4245

    setup_status = "Watching"
    if state == "forming":
        setup_status = "Forming"
    elif state in {"confirmed", "golden"}:
        setup_status = "Triggered"
    title = "Options · SETUP " + setup_status.upper()

    fields: list[dict[str, Any]] = [
        {"name": "Status", "value": _status_text(result, side, state), "inline": False},
        {"name": "Setup", "value": _setup_card_text(result), "inline": True},
        {"name": "Context", "value": _context_card_text(result, session), "inline": True},
        {"name": "Contract", "value": _contract_card_text(result, side), "inline": True},
        {"name": "Levels", "value": _levels_card_text(result), "inline": True},
        {"name": "Liquidity / value", "value": _liquidity_card_text(result, volume_ratio, iv_rank, iv_label), "inline": True},
        {"name": "Signa Context", "value": _signa_text(result), "inline": True},
        {"name": "Why", "value": _why_text(result, session), "inline": False},
        {"name": "Risk", "value": _risk_text(result), "inline": False},
    ]
    if _mechanically_triggered(result):
        fields.insert(8, {"name": "Trade authority", "value": "Mechanical setup TRIGGERED · still requires contract/risk validation before action", "inline": False})
    else:
        fields.insert(8, {"name": "Trade authority", "value": "WAIT · observational only · no entry permission", "inline": False})

    fields = [field for field in fields if field["value"] != "N/A"]
    for field in fields:
        field["value"] = _discord_field_text(field["value"])
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": title,
                "description": f"{result.ticker} · {side} · {setup_status}",
                "color": color,
                "fields": fields,
                "footer": {
                    "text": "READ ONLY · Options advisory · No scanner/Signa-driven execution"
                },
            }
        ],
    }


def _discord_field_text(value: Any, limit: int = 900) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 34)] + "\n… Full detail in scanner evidence."


def _status_text(result: ScoreResult, side: str, state: str) -> str:
    if state in {"forming", "watching"}:
        label = "FORMING" if state == "forming" else "WATCHING"
        return f"**{label}** · no entry permission\nScanner score **{result.score}/10** · {result.ticker} {side}"
    label = "MECHANICAL SETUP TRIGGERED" if state == "golden" else "SETUP TRIGGERED"
    return f"**{label}**\nScanner score **{result.score}/10** · {result.ticker} {side}"


def _setup_card_text(result: ScoreResult) -> str:
    lines = []
    if result.pattern:
        lines.append(result.pattern)
    combo = _strat_combo_text(result)
    if combo != "N/A":
        lines.append(f"Strat {combo}")
    tf = _timeframe_text(result)
    if tf != "N/A":
        lines.append(f"Timeframe {tf}")
    ftfc = _ftfc_text(result)
    if ftfc != "N/A":
        lines.append(f"FTFC {ftfc}")
    return "\n".join(lines) if lines else "No setup metadata"


def _context_card_text(result: ScoreResult, session: str) -> str:
    raw = result.raw
    pieces = [f"Session {session}"]
    if raw.get("price") is not None:
        pieces.append(f"Spot {_money_text(raw.get('price'))}")
    pieces.append(f"VWAP {_pass_fail(result.components.get('vwap'))}")
    pieces.append(f"Trend {_pass_fail(result.components.get('trend'))}")
    return "\n".join(pieces)


def _contract_card_text(result: ScoreResult, side: str) -> str:
    return _contract_text(result, side)


def _levels_card_text(result: ScoreResult) -> str:
    raw = result.raw
    lines = []
    stop = _money_text(raw.get("stop") or raw.get("stop_level"))
    target_1 = _money_text(raw.get("target_1") or raw.get("target"))
    target_2 = _money_text(raw.get("target_2"))
    if stop != "N/A":
        lines.append(f"Stop {stop}")
    if target_1 != "N/A":
        lines.append(f"Target 1 {target_1}")
    if target_2 != "N/A":
        lines.append(f"Target 2 {target_2}")
    return "\n".join(lines) if lines else "No levels supplied"


def _liquidity_card_text(result: ScoreResult, volume_ratio: Any, iv_rank: Any, iv_label: str) -> str:
    lines = []
    volume = _ratio_text(volume_ratio)
    if volume != "N/A":
        lines.append(f"Volume {volume}")
    lines.append(f"IV {_iv_text(iv_rank, iv_label)}")
    premium = _premium_value_text(result)
    if premium != "N/A":
        lines.append(f"Premium {premium}")
    return "\n".join(lines) if lines else "No liquidity/value metadata"


def _trade_proof_block_reason(result: ScoreResult) -> str:
    raw = result.raw
    status = str(raw.get("trade_proof_status") or "").strip().upper()
    if not status or status == "VALID":
        return ""
    reason = str(raw.get("trade_proof_reason") or "unspecified").strip()
    return f"trade_proof_{status.lower()}:{reason}"


def _mechanically_triggered(result: ScoreResult) -> bool:
    return str(result.raw.get("setup_status") or "").upper() == "TRIGGERED"


def _alert_state(result: ScoreResult) -> str:
    raw_state = str(result.raw.get("status") or result.raw.get("alert_state") or "").lower()
    if not _mechanically_triggered(result):
        if raw_state in {"forming", "developing", "on_deck"}:
            return "forming"
        return "watching"
    if result.score >= 9:
        return "golden"
    return "confirmed"


def _alert_title(result: ScoreResult, side: str, state: str) -> str:
    prefix = "▲"
    if state == "forming":
        return f"{prefix} {result.ticker} {side} - SETUP FORMING"
    if state == "watching":
        return f"{prefix} {result.ticker} {side} - SETUP WATCHING"
    if state == "golden":
        return f"{prefix} {result.ticker} {side} - MECHANICAL SETUP TRIGGERED"
    return f"{prefix} {result.ticker} {side} - SETUP TRIGGERED"


def _alert_description(result: ScoreResult, side: str, state: str) -> str:
    raw = result.raw
    if state in {"forming", "watching"}:
        return (
            f"**{result.ticker} {side}** is observational only. "
            "Mechanical setup proof is not TRIGGERED; do not treat scanner score or Signa as entry permission."
        )
    thesis = raw.get("thesis") or raw.get("summary")
    if thesis:
        return str(thesis)
    return (
        f"**{result.ticker} is showing "
        f"{'bullish' if result.direction == 'LONG' else 'bearish'} structure.** "
        f"Mechanical setup status: TRIGGERED. Scanner score: {result.score}/10."
    )


def _contract_text(result: ScoreResult, side: str) -> str:
    raw = result.raw
    contract = raw.get("contract")
    dte = raw.get("dte")
    dte_suffix = (
        f" · {dte} DTE"
        if raw.get("paper_policy_id") == POLICY_ID and dte not in (None, "")
        else ""
    )
    if contract:
        return f"{contract}{dte_suffix}"
    strike = raw.get("strike")
    expiry = raw.get("expiry") or raw.get("expiration")
    if strike and expiry:
        return f"{result.ticker} ${strike} {side.title()} - {expiry}{dte_suffix}"
    if strike:
        return f"{result.ticker} ${strike} {side.title()}{dte_suffix}"
    return "N/A"


def _why_text(result: ScoreResult, session: str) -> str:
    raw = result.raw
    if _mechanically_triggered(result):
        why = raw.get("why") or raw.get("why_forming")
        if why:
            return str(why)
    reasons = []
    if _mechanically_triggered(result):
        reasons.append("mechanical setup TRIGGERED")
    elif result.pattern and result.pattern.upper() != "N/A":
        reasons.append(f"{result.pattern} observed")
    if result.components.get("vwap"):
        reasons.append("VWAP aligned")
    if result.components.get("trend"):
        reasons.append("20 EMA trend aligned")
    if result.components.get("volume"):
        reasons.append("volume expanding")
    if result.components.get("session"):
        reasons.append(f"{session} window")
    return "; ".join(reasons) if reasons else "Observational scanner context only."


def _edge_text(result: ScoreResult) -> str:
    raw = result.raw
    if _mechanically_triggered(result):
        edge = raw.get("edge") or raw.get("flow_note") or raw.get("gex_note")
        if edge:
            return str(edge)
    gates = sum(
        1 for name, value in result.components.items() if name != "signa" and value > 0
    )
    return f"{gates} positive scanner components. Signa is observational and not counted."


def _risk_text(result: ScoreResult) -> str:
    raw = result.raw
    if not _mechanically_triggered(result):
        return "No entry. Wait for mechanical TRIGGERED setup and canonical contract/risk proof."
    if raw.get("paper_policy_id") == POLICY_ID and raw.get("paper_policy_status") == "VALID":
        planned = _money_text(raw.get("planned_risk_dollars"))
        premium_stop = _money_text(raw.get("premium_stop"))
        projected = _money_text(raw.get("projected_aggregate_open_planned_risk"))
        warning = raw.get("paper_policy_warnings") or []
        warning_text = f" · {'/'.join(str(item) for item in warning)}" if warning else ""
        return (
            f"{POLICY_ID} · planned risk {planned} · premium stop {premium_stop} · "
            f"projected aggregate risk {projected}{warning_text}"
        )
    risk = raw.get("risk")
    if risk:
        return str(risk)
    return "Mechanical setup triggered; use canonical invalidation, targets, contract and risk validation before any trade."


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


def _signa_text(result: ScoreResult) -> str:
    """Legacy Signa line first, v2 observation on its own line when present.

    Both surfaces stay visible during the comparison period; v2 never
    replaces the legacy context it is being evaluated against.
    """
    raw = result.raw
    legacy = _legacy_signa_text(raw)
    v2 = render_signa_v2(raw)
    if v2 == "N/A":
        return legacy
    if legacy == "N/A":
        return v2
    return f"{legacy}\n{v2}"


def _legacy_signa_text(raw: dict) -> str:
    grade = raw.get("signa_grade")
    score = raw.get("signa_score")
    direction = raw.get("signa_daily_direction") or raw.get("signa_direction")
    symbol = raw.get("signa_symbol")
    action = raw.get("signa_action")
    error = raw.get("signa_error")
    if error:
        return f"Observational · unavailable ({error})"
    if not any((grade, score, direction, action)):
        return "N/A"
    parts = ["Observational"]
    if symbol:
        parts.append(str(symbol))
    if grade:
        parts.append(f"grade {grade}")
    if score is not None:
        try:
            parts.append(f"score {float(score):.0f}")
        except (TypeError, ValueError):
            parts.append(f"score {score}")
    if direction:
        parts.append(str(direction))
    if action and action != direction:
        parts.append(str(action))
    if raw.get("signa_stale") is True:
        parts.append("stale")
    if raw.get("signa_cached") is True:
        parts.append("cached")
    return " · ".join(parts)


def _contract_sanity_reason(result: ScoreResult, now: datetime | None) -> str:
    raw = result.raw
    expiry = raw.get("expiry") or raw.get("expiration")
    if not expiry:
        return ""
    stamp = now or datetime.now()
    dte = dte_for(expiry, stamp)
    if dte is None or dte < 0 or dte > MAX_SANITY_DTE:
        return "DATA_INVALID:expiration_out_of_range"
    return ""
