"""
notifications/discord_notifier.py

Optional Discord output for paper-trading webhook decisions.
This module is read-only: it never changes trading state and never places orders.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from config.settings import SystemConfig, load_config
from webhook.payload import AlertPayload


logger = logging.getLogger(__name__)

Transport = Callable[[str, bytes, dict[str, str]], None]


@dataclass(frozen=True)
class NotificationResult:
    sent: bool
    reason: str


def notify_discord(
    *,
    payload: AlertPayload,
    result: dict,
    config: SystemConfig,
    transport: Optional[Transport] = None,
) -> NotificationResult:
    """
    Send a Discord notification for a paper-engine decision when enabled.

    Failures are logged and returned as skipped/failed results so notification
    trouble cannot break TradingView ingestion or paper-risk enforcement.
    """
    if not config.discord_notifications_enabled:
        return NotificationResult(sent=False, reason="disabled")
    if not config.discord_webhook_url:
        return NotificationResult(sent=False, reason="missing_webhook_url")
    if not _should_notify(result, config.discord_notify_decisions):
        return NotificationResult(sent=False, reason="decision_filtered")

    body = json.dumps(_discord_payload(payload, result)).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    sender = transport or _post_json

    try:
        sender(config.discord_webhook_url, body, headers)
    except Exception as exc:  # pragma: no cover - exact urllib errors vary
        logger.warning("Discord notification failed: %s", exc)
        return NotificationResult(sent=False, reason="send_failed")

    return NotificationResult(sent=True, reason="sent")


def send_discord_alert(
    config: SystemConfig,
    content: str,
    transport: Optional[Transport] = None,
) -> NotificationResult:
    """Send a plain operational alert to Discord (e.g. the feed-down watchdog).

    Separate from notify_discord, which is for trade *decisions* and is filtered
    by decision type. Operational alerts are infrastructure-critical, so they
    send whenever a webhook URL is configured (not gated on the decision toggle).
    Fail-soft: never raises.
    """
    if not config.discord_webhook_url:
        return NotificationResult(sent=False, reason="missing_webhook_url")
    body = json.dumps({"content": content}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    sender = transport or _post_json
    try:
        sender(config.discord_webhook_url, body, headers)
    except Exception as exc:  # pragma: no cover - exact urllib errors vary
        logger.warning("Discord alert failed: %s", exc)
        return NotificationResult(sent=False, reason="send_failed")
    return NotificationResult(sent=True, reason="sent")


def send_operational_alert(
    config: SystemConfig,
    content: str,
    transport: Optional[Transport] = None,
) -> NotificationResult:
    """Send a FAILURE / SAFETY alert a human should notice.

    Destination policy (2026-09-16): these belong on the DiscordRouter
    ``error`` route, not the heartbeat/legacy webhook. If the ``error`` route
    is unset the message falls back to the legacy ``send_discord_alert`` path
    so an alert is never silently lost. Never raises; a failed Discord send is
    logged and returned as a result — it can never affect ingestion, trading,
    risk, or broker state. Failure and recovery notices from the same monitor
    must both use this function so they stay paired on one route.
    """
    if transport is None:
        try:
            from notifications.discord_router import DiscordRouter

            router = DiscordRouter()
            if router.is_enabled("error"):
                delivered = router.send("error", content)
                return NotificationResult(sent=bool(delivered), reason="sent" if delivered else "send_failed")
        except Exception as exc:  # noqa: BLE001 — router trouble must never propagate
            logger.warning("Discord error route unavailable, using legacy alert path: %s", exc)
    return send_discord_alert(config, content, transport=transport)


def _should_notify(result: dict, allowed_decisions: list[str]) -> bool:
    decision = result.get("decision")
    return decision in allowed_decisions


# ─── Strategy label lookup ────────────────────────────────────────────────────

_STRATEGY_LABELS: dict[str, str] = {
    "strat_212":                 "strat_212 (2-1-2 Continuation)",
    "strat_122":                 "strat_122 (1-2-2 Reversal)",
    "strat_inside_break":        "strat_inside_break (Inside Bar Breakout)",
    "strat_outside_continuation":"strat_outside_continuation (Outside Bar Follow-Through)",
    "strat_4hr_retrigger":       "strat_4hr_retrigger (4HR Re-Trigger)",
    "orb_reclaim":               "orb_reclaim (ORB High Reclaim)",
    "orb_rejection":             "orb_rejection (ORB High Rejection)",
    "vwap_reclaim":              "vwap_reclaim (VWAP Reclaim)",
    "vwap_hold":                 "vwap_hold (VWAP Resistance Hold)",
    "pdh_reclaim":               "pdh_reclaim (PDH Reclaim)",
    "pdl_reclaim":               "pdl_reclaim (PDL Reclaim)",
    "continuation_pullback":     "continuation_pullback (VWAP Pullback)",
}


def _strategy_label(strategy: str) -> str:
    return _STRATEGY_LABELS.get(strategy, strategy)


def _format_price(value) -> str:
    if value is None:
        return "?"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _format_bar_time(timestamp) -> str:
    if timestamp is None:
        return "unknown"
    raw = str(timestamp)
    try:
        if raw.isdigit():
            seconds = int(raw) / 1000 if len(raw) >= 13 else int(raw)
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        # 12-hour clock with AM/PM, e.g. "2026-06-03 11:14 PM ET".
        return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %I:%M %p ET")
    except (OSError, ValueError):
        return raw


def _bar_close_label(payload: AlertPayload, context: dict) -> str:
    """The TradingView bar close (the value the decision was made on)."""
    context_close = context.get("close")
    close = context_close if context_close is not None else payload.close
    return _format_price(close)


def _reference_price_line(live_quote: Optional[dict]) -> Optional[str]:
    """A plain, display-only reference price line.

    Reference price is an independent live index proxy — NOT the broker execution
    price. Returns None for instruments with no proxy so the caller can omit the
    line entirely.
    """
    if not live_quote:
        return None
    status = str(live_quote.get("status", "UNAVAILABLE")).lower()
    price = live_quote.get("price")
    age = live_quote.get("age_seconds")
    if price is None or status == "unavailable":
        return "Reference price: unavailable"
    # Show the age only when the quote isn't fresh — staleness is the useful signal.
    age_str = f" · {age}s ago" if isinstance(age, int) and status != "fresh" else ""
    return f"Reference price: {_format_price(price)} (live · {status}{age_str})"


def _risk_line(risk: dict) -> str:
    """`Risk: REJECTED — <why>` instead of a bare result.

    The risk dict already carries the human-readable `reason` (and `failed_rule`)
    from RiskEngine.validate; surface it so a rejection explains itself in
    Discord instead of just saying REJECTED. Approved trades carry no reason, so
    they stay `Risk: APPROVED`.
    """
    result = risk.get("result")
    reason = risk.get("reason") or risk.get("failed_rule")
    return f"Risk: {result} — {reason}" if reason else f"Risk: {result}"


def _candidate_line(candidate: dict) -> str:
    """One-line near-miss snapshot: the would-be trade that was rejected.

    Audit-only — ``candidate`` is a read of an already-rejected decision; nothing
    here can place, queue, or retry an order. Shown so a rejection answers
    "did it see that move, and what would the trade have been?" in Discord.
    """
    direction = candidate.get("direction") or "?"
    symbol = candidate.get("symbol") or "?"
    entry = candidate.get("entry")
    stop = candidate.get("stop")
    target = candidate.get("target")
    gate = candidate.get("blocking_gate") or candidate.get("reject_code")
    why = gate.replace("_", " ").lower() if gate else (candidate.get("reject_reason") or "rejected")
    icon = "🟢" if direction == "LONG" else "🔴"
    return (
        f"⚠️ Almost traded — {icon} {symbol} {direction} "
        f"{_format_price(entry)} / stop {_format_price(stop)} / target {_format_price(target)} "
        f"· skipped: {why}"
    )


def _decision_reason_line(result: dict) -> Optional[str]:
    reason = result.get("gate_reason") or result.get("reason")
    if not reason:
        failed = result.get("failed_gates") or []
        if isinstance(failed, str):
            failed = [failed]
        if failed:
            reason = ", ".join(str(item) for item in failed)
    return f"Why: {reason}" if reason else None



def _truncate(value: str, limit: int = 900) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 35)] + "\n… Full details in the journal/artifact."


def _line_join(lines: list[str], *, empty: str = "None") -> str:
    filtered = [line for line in lines if line]
    return "\n".join(filtered) if filtered else empty


def _discord_payload(payload: AlertPayload, result: dict) -> dict:
    """Paper-collection-style Discord card for paper decisions.

    Presentation only. It never changes the decision result, risk state, broker
    state, journal state, or execution path. The old plain text formatter remains
    available for CLI dry-runs and tests that inspect exact content.
    """
    decision = result.get("decision") or "UNKNOWN"
    context = result.get("context") or {}
    risk = result.get("risk") or {}
    fill = result.get("fill") or {}
    candidate = result.get("candidate") or {}
    confluence = result.get("confluence") or {}
    symbol = context.get("instrument") or payload.ticker
    session = context.get("session") or "unknown_session"
    session_label = "New York Open" if session == "new_york" else str(session).replace("_", " ").title()
    bar_time = _format_bar_time(payload.timestamp)
    ref_line = _reference_price_line(result.get("live_quote"))
    color = 0xED4245 if "REJECT" in str(decision) or decision.startswith("BLOCKED") else (0x57F287 if decision == "TRADE" else 0xF0B232)

    fields: list[dict] = [
        {"name": "Decision", "value": f"**{decision}**", "inline": True},
        {"name": "Instrument", "value": f"{symbol} · {session_label}", "inline": True},
    ]
    if ref_line:
        fields.append({"name": "Reference", "value": ref_line, "inline": False})

    if decision == "TRADE":
        direction = fill.get("direction") or "?"
        dir_icon = "🟢" if direction == "LONG" else "🔴"
        strategy = fill.get("strategy") or "?"
        score = confluence.get("score", "?")
        grade = confluence.get("grade", "?")
        setup_lines = [
            f"{dir_icon} {symbol} {direction}",
            f"{grade} setup · score {score}/10",
            _strategy_label(str(strategy)),
        ]
        fields.append({"name": "Setup", "value": _line_join(setup_lines)})
        fields.append({
            "name": "Levels",
            "value": (
                f"Entry **{_format_price(fill.get('entry'))}**\n"
                f"Stop **{_format_price(fill.get('stop'))}**\n"
                f"Target **{_format_price(fill.get('target'))}**"
            ),
            "inline": True,
        })
        size_bits = []
        contracts = fill.get("contracts")
        if contracts is not None:
            size_bits.append(f"{contracts} contract" + ("s" if contracts != 1 else ""))
        rr = fill.get("rr_ratio")
        size_bits.append(f"R:R {rr:.1f}" if rr is not None else "R:R ?")
        if risk:
            size_bits.append(_risk_line(risk))
        fields.append({"name": "Risk", "value": "\n".join(size_bits), "inline": True})
        factors = [f"✅ {item}" for item in (confluence.get("factors") or [])]
        penalties = [f"⚠ {item}" for item in (confluence.get("penalties") or [])]
        if factors or penalties:
            fields.append({"name": "Confluence", "value": _truncate("\n".join(factors + penalties))})
    else:
        reason = _decision_reason_line(result)
        if risk:
            fields.append({"name": "Risk", "value": _risk_line(risk)})
        if reason:
            fields.append({"name": "Why", "value": reason.replace("Why: ", "", 1)})
        if candidate:
            direction = str(candidate.get("direction") or "?")
            icon = "🟢" if direction == "LONG" else "🔴"
            skipped = candidate.get("blocking_gate") or candidate.get("reject_code") or candidate.get("reject_reason") or "rejected"
            fields.append({
                "name": "Candidate skipped",
                "value": (
                    f"{icon} {candidate.get('symbol') or symbol} {direction}\n"
                    f"Entry **{_format_price(candidate.get('entry'))}** · "
                    f"Stop **{_format_price(candidate.get('stop'))}** · "
                    f"Target **{_format_price(candidate.get('target'))}**\n"
                    f"Skipped: {str(skipped).replace('_', ' ').lower()}"
                ),
            })

    fields.append({
        "name": "Bar context",
        "value": f"Bar close **{_bar_close_label(payload, context)}**\nBar time {bar_time}",
    })
    resolution = result.get("resolution")
    if resolution:
        fields.append({"name": "Resolution", "value": str(resolution)})
    for field in fields:
        field["value"] = _truncate(field.get("value", ""))
    prefix = "DISCORD SMOKE TEST · NOT JOURNALED" if result.get("smoke_test") else ""
    description = f"{symbol} · {session_label}"
    if prefix:
        description = prefix + "\n" + description
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": "Futures · Paper decision",
            "description": description,
            "color": color,
            "fields": fields,
            "footer": {"text": "READ ONLY · Paper decision · No Discord-driven execution"},
        }],
    }

def _format_message(payload: AlertPayload, result: dict) -> str:
    decision = result.get("decision") or "UNKNOWN"
    prefix = []
    if result.get("smoke_test"):
        prefix.extend([
            "DISCORD SMOKE TEST - NOT A JOURNALED TRADE",
            "Synthetic notification preview only.",
            "",
        ])

    # ── Non-TRADE: keep the existing minimal format ───────────────────────────
    if decision != "TRADE":
        context = result.get("context") or {}
        risk = result.get("risk") or {}
        resolution = result.get("resolution")
        symbol = context.get("instrument") or payload.ticker
        session = context.get("session") or "unknown_session"

        lines = [
            f"Vantage Point paper decision: {decision}",
            f"{symbol} | {session}",
        ]
        ref_line = _reference_price_line(result.get("live_quote"))
        if ref_line:
            lines.append(ref_line)
        lines.append(f"Bar close: {_bar_close_label(payload, context)}")
        lines.append(f"Bar time: {_format_bar_time(payload.timestamp)}")
        if resolution:
            lines.append(f"Resolution: {resolution}")
        reason_line = _decision_reason_line(result)
        if reason_line:
            lines.append(reason_line)
        if risk:
            lines.append(_risk_line(risk))
        candidate = result.get("candidate")
        if candidate:
            lines.append(_candidate_line(candidate))
        return "\n".join(prefix + lines)

    # ── TRADE: rich format with confluence score ──────────────────────────────
    context = result.get("context") or {}
    fill = result.get("fill") or {}
    risk = result.get("risk") or {}
    confluence = result.get("confluence") or {}
    resolution = result.get("resolution")

    score = confluence.get("score", 0)
    grade = confluence.get("grade", "?")
    factors: list = confluence.get("factors") or []
    penalties: list = confluence.get("penalties") or []

    symbol = context.get("instrument") or payload.ticker
    session = context.get("session") or "unknown_session"
    session_label = (
        "New York Open" if session == "new_york"
        else session.replace("_", " ").title()
    )

    direction = fill.get("direction", "?")
    strategy = fill.get("strategy", "?")
    entry = fill.get("entry")
    stop = fill.get("stop")
    target = fill.get("target")
    rr = fill.get("rr_ratio")
    market_condition = context.get("market_condition") or "?"

    dir_icon = "🟢" if direction == "LONG" else "🔴"
    contracts = fill.get("contracts")

    stop_label = (
        f" (-{abs(entry - stop):.2f})" if entry is not None and stop is not None else ""
    )
    target_label = (
        f" (+{abs(target - entry):.2f})" if target is not None and entry is not None else ""
    )
    rr_str = f"{rr:.1f}" if rr is not None else "?"

    # Compact, options-lane-style layout: the same fields as before, grouped onto
    # `·`-separated lines instead of a divider-wrapped aligned block. Every value
    # the previous format carried is still here — this is presentation only.
    lines = [
        f"Vantage Point paper decision: {decision}",
        f"{dir_icon} Futures OPEN — {symbol} {direction}",
        f"{grade} SETUP · Score: {score}/10 · {_strategy_label(strategy)} · {session_label}",
        f"Entry {_format_price(entry)} · Stop {_format_price(stop)}{stop_label} · "
        f"Target {_format_price(target)}{target_label}",
    ]

    size_bits = []
    if contracts is not None:
        size_bits.append(f"{contracts} contract" + ("s" if contracts != 1 else ""))
    size_bits.append(f"R:R {rr_str}")
    if risk:
        size_bits.append(_risk_line(risk))
    lines.append(" · ".join(size_bits))

    for f in factors:
        lines.append(f"✅ {f}")
    for p in penalties:
        lines.append(f"⚠️  {p}")

    if resolution:
        lines.append(f"Resolution: {resolution}")

    lines.append(
        f"Market: {market_condition} | Bar close: {_bar_close_label(payload, context)} | "
        f"Bar time: {_format_bar_time(payload.timestamp)}"
    )
    ref_line = _reference_price_line(result.get("live_quote"))
    if ref_line:
        lines.append(ref_line)

    return "\n".join(prefix + lines)


def _post_json(url: str, body: bytes, headers: dict[str, str]) -> None:
    import httpx
    response = httpx.post(url, content=body, headers=headers, timeout=5)
    response.raise_for_status()


def smoke_test_payload(decision: str = "TRADE") -> tuple[AlertPayload, dict]:
    """Build a safe synthetic paper decision for notification smoke tests."""
    payload = AlertPayload(
        ticker="MNQ1!",
        timestamp="2026-05-23T14:30:00+00:00",
        open=19480.0,
        high=19510.0,
        low=19475.0,
        close=19505.25,
    )
    result = {
        "smoke_test": True,
        "decision": decision,
        "resolution": None,
        "risk": {"result": "APPROVED", "failed_rule": None, "reason": None},
        "fill": {
            "direction": "LONG",
            "entry": 19505.25,
            "stop": 19495.25,
            "target": 19525.25,
            "rr_ratio": 2.0,
            "strategy": "orb_reclaim",
            "contracts": 1,
        },
        "context": {
            "instrument": "MNQ",
            "session": "new_york",
            "close": 19505.25,
            "market_condition": "TRENDING",
        },
        "confluence": {
            "score": 9,
            "grade": "A+",
            "factors": [
                "VWAP aligned (+2)",
                "Trend UP MODERATE (+2)",
                "Strat strat_212 confirmed (+3)",
                "Volume 1.4x avg (+2)",
                "NY session (+1)",
                "ORB confirms direction (+1)",
            ],
            "penalties": [],
        },
    }
    return payload, result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send or preview a read-only Discord paper-decision smoke test."
    )
    parser.add_argument(
        "--decision",
        default="TRADE",
        help="Synthetic paper decision to format (default: TRADE).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the Discord message without sending it.",
    )
    args = parser.parse_args(argv)

    payload, result = smoke_test_payload(args.decision)
    if args.dry_run:
        print(_format_message(payload, result))
        return 0

    config = load_config()
    send_result = notify_discord(payload=payload, result=result, config=config)
    print(json.dumps(send_result.__dict__, sort_keys=True))
    return 0 if send_result.sent else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
