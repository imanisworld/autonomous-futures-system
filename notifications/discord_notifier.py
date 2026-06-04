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

    body = json.dumps({"content": _format_message(payload, result)}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    sender = transport or _post_json

    try:
        sender(config.discord_webhook_url, body, headers)
    except Exception as exc:  # pragma: no cover - exact urllib errors vary
        logger.warning("Discord notification failed: %s", exc)
        return NotificationResult(sent=False, reason="send_failed")

    return NotificationResult(sent=True, reason="sent")


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

_DIVIDER = "━" * 23


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
        return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
    except (OSError, ValueError):
        return raw


def _close_label(payload: AlertPayload, context: dict, live_quote: Optional[dict] = None) -> str:
    context_close = context.get("close")
    close = context_close if context_close is not None else payload.close
    # Prefer an independent live index quote when available; show the bar close
    # alongside so the source is unambiguous (the bar close can be stale/replayed).
    if live_quote and live_quote.get("price") is not None:
        sym = live_quote.get("symbol", "live")
        return f"{_format_price(live_quote['price'])} (live {sym}) · bar {_format_price(close)}"
    source = "TV payload" if context_close == payload.close else "decision context"
    return f"{_format_price(close)} ({source})"


def _format_message(payload: AlertPayload, result: dict) -> str:
    decision = result.get("decision") or "UNKNOWN"

    # ── Non-TRADE: keep the existing minimal format ───────────────────────────
    if decision != "TRADE":
        context = result.get("context") or {}
        risk = result.get("risk") or {}
        resolution = result.get("resolution")
        symbol = context.get("instrument") or payload.ticker
        session = context.get("session") or "unknown_session"

        lines = [
            f"RiskSentinel paper decision: {decision}",
            f"{symbol} | {session} | close={_close_label(payload, context, result.get('live_quote'))}",
            f"bar={_format_bar_time(payload.timestamp)}",
        ]
        if resolution:
            lines.append(f"Resolution: {resolution}")
        if risk:
            lines.append(f"Risk: {risk.get('result')}")
        return "\n".join(lines)

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

    stop_label = (
        f"  (-{abs(entry - stop):.2f} pts)" if entry is not None and stop is not None else ""
    )
    target_label = (
        f"  (+{abs(target - entry):.2f} pts)" if target is not None and entry is not None else ""
    )
    rr_str = f"{rr:.1f}" if rr is not None else "?"

    lines = [
        f"RiskSentinel paper decision: {decision}",
        f"{dir_icon} {grade} SETUP — {symbol} | Score: {score}/10",
        _DIVIDER,
        f"Direction : {direction}",
        f"Strategy  : {_strategy_label(strategy)}",
        f"Session   : {session_label}",
        "",
        f"Entry     : {entry}",
        f"Stop      : {stop}{stop_label}",
        f"Target    : {target}{target_label}",
        f"R:R       : {rr_str}",
        "",
    ]
    for f in factors:
        lines.append(f"✅ {f}")
    for p in penalties:
        lines.append(f"⚠️  {p}")

    if resolution:
        lines.append(f"Resolution: {resolution}")
    if risk:
        lines.append(f"Risk: {risk.get('result')}")

    lines.append(_DIVIDER)
    lines.append(
        f"Market: {market_condition} | Close: {_close_label(payload, context, result.get('live_quote'))} | "
        f"Bar: {_format_bar_time(payload.timestamp)}"
    )

    return "\n".join(lines)


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
