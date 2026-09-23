"""Advisory Discord alerting with duplicate suppression."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .config import ScannerConfig
from . import plain_text as pt
from .paper_v1 import MAX_SANITY_DTE, POLICY_ID, dte_for
from .scorer import ScoreResult
from .session_calendar import us_equity_rth_state
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

    async def send_if_eligible(
        self,
        result: ScoreResult,
        now: datetime | None = None,
        *,
        delivery_now: datetime | None = None,
    ) -> AlertDecision:
        trade_proof_reason = _trade_proof_block_reason(result)
        if trade_proof_reason:
            # An explicit NEGATIVE trade-proof verdict can never be overridden
            # by scanner score, Signa, or contract quality. INCOMPLETE is not a
            # verdict: it only records which optional checks (event risk, flip
            # context) have no implementation yet, and is surfaced in the alert
            # body instead (see _unchecked_text). Blocking on it made every
            # alert unreachable from #526 (2026-09-08) onward.
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

        # Re-check at the actual delivery boundary. ``now`` is the scan/decision
        # timestamp and must remain stable for scoring, contract sanity, dedupe,
        # and evidence. A scan that began inside RTH must not be allowed to post
        # after the exchange has closed while the scan was still running.
        actual_delivery = delivery_now or datetime.now(ZoneInfo(self.config.timezone))
        delivery_state = us_equity_rth_state(actual_delivery)
        if not delivery_state.is_open:
            return AlertDecision(False, delivery_state.reason)

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
    """Plain-English options card (docs/discord-operator-message-style.md).

    Presentation only: which fields appear and what they say about authority
    follow the setup state exactly as before — an untriggered setup always
    says "no entry permission", Signa is always "for info only".
    """
    raw = result.raw
    volume_ratio = raw.get("volume_ratio")
    iv_rank = raw.get("iv_rank")
    session = "New York open" if raw.get("ny_open") else "regular hours"
    iv_label = "unknown"
    if isinstance(iv_rank, (int, float)):
        iv_label = "cheap" if iv_rank < 30 else "neutral" if iv_rank <= 50 else "expensive"
    side = "CALL" if result.direction == "LONG" else "PUT"
    kind = pt.option_kind(side)
    state = _alert_state(result)
    color = 0x57F287 if result.direction == "LONG" else 0xED4245

    title = {
        "watching": f"👀 {result.ticker} {kind} idea — watching only",
        "forming": f"⏳ {result.ticker} {kind} idea — setup still forming",
        "confirmed": f"🔔 {result.ticker} {kind} — setup triggered",
        "golden": f"🔔 {result.ticker} {kind} — setup triggered (top score)",
    }[state]
    description = f"**{pt.option_kind(side, explain=True).capitalize()}** on {result.ticker}"

    fields: list[dict[str, Any]] = [
        {"name": "Status", "value": _status_text(result, side, state), "inline": False},
        {"name": "Setup", "value": _setup_card_text(result), "inline": True},
        {"name": "Market check", "value": _context_card_text(result, session), "inline": True},
        {"name": "Option", "value": _contract_card_text(result, side), "inline": True},
        {"name": "Stock price levels", "value": _levels_card_text(result), "inline": True},
        {"name": "Option price check", "value": _liquidity_card_text(result, volume_ratio, iv_rank, iv_label), "inline": True},
        {"name": "Signa (outside opinion)", "value": _signa_text(result), "inline": True},
        {"name": "Why", "value": _why_text(result, session), "inline": False},
        {"name": "Risk", "value": _risk_text(result), "inline": False},
        {"name": "Not checked", "value": _unchecked_text(result), "inline": False},
    ]
    if _mechanically_triggered(result):
        fields.insert(8, {"name": "Can I trade this?", "value": "Setup TRIGGERED · you still need to check the contract and risk before doing anything · nothing is placed automatically", "inline": False})
    else:
        fields.insert(8, {"name": "Can I trade this?", "value": "No — WAIT · watching only · no entry permission", "inline": False})

    fields = [field for field in fields if field["value"] != "N/A"]
    for field in fields:
        field["value"] = _discord_field_text(field["value"])
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "fields": fields,
                "footer": {"text": _footer_text(result)},
            }
        ],
    }


def _footer_text(result: ScoreResult) -> str:
    """Boundary first; debug ids (policy id, raw option symbol) trail it."""
    raw = result.raw
    bits = ["READ ONLY · advice only, paper · the scanner and Signa never place trades"]
    if raw.get("paper_policy_id"):
        bits.append(str(raw.get("paper_policy_id")))
    contract = raw.get("contract")
    if contract and pt.parse_osi(contract):
        bits.append(str(contract).strip())
    return " · ".join(bits)


def _discord_field_text(value: Any, limit: int = 900) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 34)] + "\n… Full detail in scanner evidence."


def _status_text(result: ScoreResult, side: str, state: str) -> str:
    score = f"Scanner score **{result.score} out of 10**"
    if state in {"forming", "watching"}:
        label = "Still forming" if state == "forming" else "Watching only"
        return f"**{label}** · no entry permission\n{score}"
    label = "Setup triggered (top score)" if state == "golden" else "Setup triggered"
    return f"**{label}**\n{score}"


def _setup_card_text(result: ScoreResult) -> str:
    lines = []
    if result.pattern:
        lines.append(pt.setup_name(result.pattern))
    combo = _strat_combo_text(result)
    if combo != "N/A":
        lines.append(f"Candles: {pt.strat_sequence(combo)}")
    tf = _timeframe_text(result)
    if tf != "N/A":
        lines.append(f"Chart: {tf}")
    ftfc = _ftfc_text(result)
    if ftfc != "N/A":
        lines.append(f"All timeframes agree: {ftfc}")
    return "\n".join(lines) if lines else "No setup details"


def _context_card_text(result: ScoreResult, session: str) -> str:
    raw = result.raw
    pieces = [f"Time of day: {session}"]
    if raw.get("price") is not None:
        pieces.append(f"Price: {_money_text(raw.get('price'))}")
    if "market_alignment" in result.components:
        # Daily setup: intraday VWAP/EMA20 are informational only (not scored).
        pieces.append(
            f"SPY/QQQ daily trend agrees: {_yes_no(result.components.get('market_alignment'))}"
        )
        pieces.append("Day's average price and short-term trend: not used for daily setups")
    else:
        pieces.append(f"Right side of the day's average price: {_yes_no(result.components.get('vwap'))}")
        pieces.append(f"Short-term trend agrees: {_yes_no(result.components.get('trend'))}")
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
        lines.append(f"Stop-loss: {stop}")
    if target_1 != "N/A":
        lines.append(f"First target: {target_1}")
    if target_2 != "N/A":
        lines.append(f"Second target: {target_2}")
    return "\n".join(lines) if lines else "N/A"


def _liquidity_card_text(result: ScoreResult, volume_ratio: Any, iv_rank: Any, iv_label: str) -> str:
    lines = []
    volume = _ratio_text(volume_ratio)
    if volume != "N/A":
        lines.append(f"Trading volume: {volume} normal")
    iv = _iv_text(iv_rank, iv_label)
    if iv != "N/A":
        lines.append(f"Option prices: {iv}")
    premium = _premium_value_text(result)
    if premium != "N/A":
        lines.append(f"Vs fair value: {premium}")
    return "\n".join(lines) if lines else "N/A"


# Trade-proof statuses that do NOT block a user-facing alert. VALID is a pass;
# INCOMPLETE only names optional checks that have no implementation yet
# (event_risk_unavailable; flip_context_unavailable) and is rendered as a
# caveat in the alert body. Anything else (FAILED, BLOCKED, INVALID, ...) is an
# explicit negative verdict and stays fail-closed.
_TRADE_PROOF_NON_BLOCKING = frozenset({"", "VALID", "INCOMPLETE"})


def _trade_proof_block_reason(result: ScoreResult) -> str:
    raw = result.raw
    status = str(raw.get("trade_proof_status") or "").strip().upper()
    if status in _TRADE_PROOF_NON_BLOCKING:
        return ""
    reason = str(raw.get("trade_proof_reason") or "unspecified").strip()
    return f"trade_proof_{status.lower()}:{reason}"


def _unchecked_text(result: ScoreResult) -> str:
    """Checks the trade proof could not run, shown so the reader knows what
    the alert did NOT verify. Empty (dropped) when the proof is VALID."""
    raw = result.raw
    status = str(raw.get("trade_proof_status") or "").strip().upper()
    if status != "INCOMPLETE":
        return "N/A"
    items = []
    for part in str(raw.get("trade_proof_reason") or "").split(";"):
        code = part.strip().replace("_unavailable", "")
        if code:
            items.append(_UNCHECKED_ENGLISH.get(code, code.replace("_", " ")))
    return "Not checked: " + ", ".join(items) if items else "Not checked: some safety checks did not run"


_UNCHECKED_ENGLISH = {
    "event_risk": "upcoming news or earnings",
    "flip_context": "big options-positioning price levels",
}


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
    expiry = raw.get("expiry") or raw.get("expiration")
    dte = raw.get("dte")
    osi = pt.parse_osi(contract) if contract else None
    if osi:
        # Raw option symbol (QQQ260923C00741000) is spelled out here and kept
        # only in the footer for debugging.
        label = pt.option_label(osi["underlying"], osi["strike"], osi["kind"])
        return f"{label}, {pt.expires(expiry or osi['expiry'], dte=dte)}"
    if contract:
        # Hand-written contract text from the alert source; the plain expiry is
        # added only when the paper policy chose it (as the DTE suffix was).
        if raw.get("paper_policy_id") == POLICY_ID and dte not in (None, ""):
            when = pt.expires(expiry, dte=dte) if pt.as_date(expiry) else pt.expires(dte=dte)
            return f"{contract} · {when}"
        return str(contract)
    strike = raw.get("strike")
    if strike:
        label = pt.option_label(result.ticker, strike, side)
        when = pt.expires(expiry, dte=dte) if (expiry or dte not in (None, "")) else ""
        return f"{label}, {when}" if when else label
    return "N/A"


def _why_text(result: ScoreResult, session: str) -> str:
    raw = result.raw
    if _mechanically_triggered(result):
        why = raw.get("why") or raw.get("why_forming")
        if why:
            return str(why)
    reasons = []
    if _mechanically_triggered(result):
        reasons.append("setup triggered")
    elif result.pattern and result.pattern.upper() != "N/A":
        reasons.append(f"{pt.setup_name(result.pattern)} seen")
    if result.components.get("market_alignment"):
        reasons.append("SPY/QQQ daily trend agrees")
    if result.components.get("vwap"):
        reasons.append("price is on the right side of the day's average price")
    if result.components.get("trend"):
        reasons.append("short-term trend agrees")
    if result.components.get("volume"):
        reasons.append("trading volume is picking up")
    if result.components.get("session"):
        reasons.append(f"{session} window")
    return "; ".join(reasons) if reasons else "Scanner background only — nothing confirmed."


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


_POLICY_WARNINGS = {
    "DTE_EXCEPTION": "expiry is outside the usual window",
    "GREEKS_PARTIAL": "some option data was missing",
}


def _risk_text(result: ScoreResult) -> str:
    raw = result.raw
    if not _mechanically_triggered(result):
        return "No entry. Wait for the setup to trigger and for the contract and risk check."
    if raw.get("paper_policy_id") == POLICY_ID and raw.get("paper_policy_status") == "VALID":
        planned = _money_text(raw.get("planned_risk_dollars"))
        premium_stop = _money_text(raw.get("premium_stop"))
        projected = _money_text(raw.get("projected_aggregate_open_planned_risk"))
        warning = raw.get("paper_policy_warnings") or []
        lines = [
            f"Most you'd lose: {planned} (1 contract, paper)",
            f"Get out if the option drops to {premium_stop} a share",
            f"Planned loss across all open paper trades: {projected}",
        ]
        if warning:
            lines.append(
                "Heads up: "
                + "; ".join(
                    _POLICY_WARNINGS.get(str(item), str(item).replace("_", " ").lower())
                    for item in warning
                )
            )
        return "\n".join(lines)
    risk = raw.get("risk")
    if risk:
        return str(risk)
    return "Setup triggered; check the stop-loss, targets, contract and risk before any trade."


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
        return pt.timeframe(timeframe)
    timeframes = raw.get("timeframes") or raw.get("tf_stack")
    if isinstance(timeframes, (list, tuple)):
        return " / ".join(pt.timeframe(item) for item in timeframes)
    if timeframes:
        return pt.timeframe(timeframes)
    return "N/A"


def _direction_word(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"UP", "LONG", "BULLISH", "BUY", "CALL"}:
        return "up"
    if text in {"DOWN", "SHORT", "BEARISH", "SELL", "PUT"}:
        return "down"
    if text in {"WAIT", "NEUTRAL", "HOLD", "FLAT"}:
        return "no clear direction"
    return text.lower()


def _ftfc_text(result: ScoreResult) -> str:
    raw = result.raw
    ftfc = raw.get("ftfc")
    if ftfc is None:
        ftfc = raw.get("full_timeframe_continuity")
    if isinstance(ftfc, bool):
        direction = raw.get("ftfc_direction") or result.direction
        return f"yes ({_direction_word(direction)})" if ftfc else "no"
    if ftfc:
        direction = raw.get("ftfc_direction")
        if direction:
            return f"{_direction_word(ftfc)} ({_direction_word(direction)})"
        return _direction_word(ftfc)
    return "N/A"


def _yes_no(value: Any) -> str:
    return "yes" if value else "no"


def _money_text(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _ratio_text(value: Any) -> str:
    try:
        return f"{float(value):.1f}×"
    except (TypeError, ValueError):
        return "N/A"


def _iv_text(value: Any, label: str) -> str:
    """Implied-volatility rank as words only (the number means nothing to the reader)."""
    try:
        float(value)
    except (TypeError, ValueError):
        return "N/A"
    return {
        "cheap": "cheaper than usual",
        "neutral": "about usual",
        "expensive": "more expensive than usual",
    }.get(label, "N/A")


def _premium_value_text(result: ScoreResult) -> str:
    raw = result.raw
    verdict = raw.get("option_value_verdict")
    if not verdict:
        return "N/A"
    edge = raw.get("option_edge_percent")
    fair = raw.get("option_theoretical_value")
    label = {
        "discount": "cheaper than fair value",
        "fair": "about fair value",
        "overpriced": "pricier than fair value",
    }.get(str(verdict).strip().lower(), str(verdict).replace("_", " ").lower())
    details = []
    try:
        edge_value = float(edge)
        details.append(f"{abs(edge_value):.0f}% {'below' if edge_value >= 0 else 'above'}")
    except (TypeError, ValueError):
        pass
    try:
        details.append(f"fair value ${float(fair):,.2f}")
    except (TypeError, ValueError):
        pass
    return f"{label} ({', '.join(details)})" if details else label


def _signa_text(result: ScoreResult) -> str:
    """Legacy Signa line first, v2 observation on its own line when present.

    Both surfaces stay visible during the comparison period; v2 never
    replaces the legacy context it is being evaluated against. Every line
    starts "For info only": Signa never adds to the score or grants entry.
    """
    raw = result.raw
    legacy = _legacy_signa_text(raw, result.ticker)
    v2 = _signa_v2_text(raw, result.ticker)
    if v2 == "N/A":
        return legacy
    if legacy == "N/A":
        return v2
    return f"{legacy}\n{v2}"


_SIGNA_STRENGTH = {"A": "strong", "B": "fairly strong", "C": "weak", "D": "very weak", "F": "very weak"}


def _signa_read(grade: Any, direction: Any) -> str:
    """``leaning up, strong (grade A)``."""
    bits = []
    if direction:
        lean = _direction_word(direction)
        bits.append(lean if lean == "no clear direction" else f"leaning {lean}")
    if grade:
        strength = _SIGNA_STRENGTH.get(str(grade).strip().upper()[:1])
        bits.append(f"{strength} (grade {grade})" if strength else f"grade {grade}")
    return ", ".join(bits)


def _legacy_signa_text(raw: dict, ticker: str = "") -> str:
    grade = raw.get("signa_grade")
    score = raw.get("signa_score")
    direction = raw.get("signa_daily_direction") or raw.get("signa_direction")
    symbol = raw.get("signa_symbol")
    action = raw.get("signa_action")
    error = raw.get("signa_error")
    if error:
        return "For info only · Signa not available right now"
    if not any((grade, score, direction, action)):
        return "N/A"
    read = _signa_read(grade, direction or action) or "no read"
    parts = [f"For info only · {read}"]
    if symbol and str(symbol) != str(ticker):
        parts.append(f"read on {symbol}")
    if raw.get("signa_stale") is True:
        parts.append("may be out of date")
    return " · ".join(parts)


def _signa_v2_text(raw: dict, ticker: str = "") -> str:
    """Plain twin of ``render_signa_v2`` for the Discord card (the dashboard keeps the original)."""
    if not isinstance(raw, dict):
        return "N/A"
    if raw.get("signa_v2_error"):
        return "For info only · Signa v2 (being tested) not available right now"
    if raw.get("signa_v2_ok") is not True:
        return "N/A"
    read = _signa_read(raw.get("signa_v2_grade"), raw.get("signa_v2_direction")) or "no read"
    parts = [f"For info only · Signa v2 (being tested): {read}"]
    confidence = raw.get("signa_v2_confidence")
    if confidence is not None:
        try:
            parts.append(f"{float(confidence):.0f}% confident")
        except (TypeError, ValueError):
            pass
    timeframe = raw.get("signa_v2_timeframe")
    if timeframe:
        parts.append(f"{pt.timeframe(timeframe)} view")
    symbol = raw.get("signa_v2_symbol")
    if symbol and str(symbol) != str(ticker):
        parts.append(f"read on {symbol}")
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
