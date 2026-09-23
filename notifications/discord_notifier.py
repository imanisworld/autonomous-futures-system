"""
notifications/discord_notifier.py

Optional Discord output for paper-trading webhook decisions.
This module is read-only: it never changes trading state and never places orders.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from config.settings import SystemConfig, load_config
from notifications import plain_english as pe
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
    from notifications.discord_card import post_card_or_text

    headers = {"Content-Type": "application/json"}
    sender = transport or _post_json
    try:
        post_card_or_text(
            lambda body: sender(config.discord_webhook_url, json.dumps(body).encode("utf-8"), headers),
            content,
            source="operational alert",
        )
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


# ─── Plain-English presentation (docs/discord-operator-message-style.md) ─────
#
# Presentation only: every helper below reads an already-made decision and
# turns internal codes into short words for a phone reader. Nothing here can
# change the decision, the journal, risk state or the broker.

# Setup names the shared plain_english map doesn't cover (local, display only).
_STRATEGY_LABELS: dict[str, str] = {
    "strat_212_reversal":         "2-1-2 reversal",
    "strat_inside_break":         "inside-bar breakout",
    "strat_outside_continuation": "follow-through after an outside bar",
    "orb_rejection":              "turned away at the opening-range high",
    "vwap_reclaim":               "back above the day's average price",
    "pdh_reclaim":                "back above yesterday's high",
    "pdl_reclaim":                "back above yesterday's low",
    "continuation_pullback":      "pullback to the day's average price",
}

_DECISION_WORDS: dict[str, str] = {
    "TRADE": "Practice trade taken",
    "TRADE_INTENT": "Practice trade planned",
    "RISK_REJECTED": "Skipped — risk limits",
    "BLOCKED_MAX_TRADES": "Skipped — already hit today's trade limit",
    "BLOCKED_LOSS_LOCKOUT": "Skipped — paused after losses",
    "BLOCKED_OPEN_POSITION": "Skipped — already in a trade",
    "BLOCKED_DATA_QUALITY": "Skipped — bad price data",
    "BLOCKED_DUPLICATE_BAR": "Skipped — same price bar seen twice",
    "BLOCKED_EXECUTION_FAILED": "Order FAILED — not placed",
    "BLOCKED_ORDER_CONFIRMATION_MISSING": "Order not confirmed by the broker",
    "SHADOW_NO_ORDER": "Setup seen — practice only, no order sent",
    "ORDER_SUPPRESSED": "Setup seen — order held back",
    "NO_TRADE": "No trade",
    "IGNORED": "Ignored",
    "DUPLICATE_IGNORED": "Ignored — duplicate alert",
    "MAINTENANCE_MODE": "Paused — maintenance mode",
    "CONFIG_BLOCKED": "Skipped — settings don't allow it",
}

# risk.failed_rule codes (risk/risk_engine.py) → short words.
_RULE_WORDS: dict[str, str] = {
    "session_window": "outside trading hours",
    "session_cutoff": "too late in the session",
    "session_not_allowed": "not allowed in this session",
    "session_trade_limit": "hit the trade limit for this session",
    "daily_trade_limit": "hit today's trade limit",
    "daily_trade_limit_bonus_grade": "hit today's trade limit",
    "max_daily_loss": "hit today's loss limit",
    "max_drawdown": "account is down too far from its high",
    "consecutive_loss_limit": "too many losses in a row",
    "circuit_breaker": "safety stop is on",
    "early_session_loss_floor": "lost too much early in the session",
    "profit_protect_gate": "protecting today's profit",
    "rr_below_minimum": "target too small for the risk",
    "target_too_close": "target too close",
    "stop_too_wide": "stop-loss too far away",
    "stop_equals_target": "stop-loss and target are the same",
    "entry_equals_stop": "entry and stop-loss are the same",
    "entry_equals_target": "entry and target are the same",
    "incomplete_bracket": "missing stop-loss or target",
    "invalid_direction": "unknown buy/sell direction",
    "instrument_not_allowed": "market not allowed",
    "open_position_exists": "already in a trade",
    "max_contracts_exceeded": "too many contracts",
    "win_streak_contracts_exceeded": "too many contracts",
    "position_sizing_contracts": "couldn't size the trade",
    "position_sizing_instrument": "couldn't size the trade",
    "position_sizing_no_tier": "couldn't size the trade",
    "countertrend_risk_cap": "too much risk against the trend",
    "min_confluence_grade": "setup quality too low",
    "news_blackout": "news event nearby",
    "news_release_window": "news event nearby",
    "news_blackout_cutoff": "news event nearby",
    "news_blackout_trade_limit": "news event nearby",
    "stale_alert": "chart alert arrived too late",
    "alert_timestamp_missing": "chart alert had no time",
    "alert_timestamp_future": "chart alert time is in the future",
    "contract_metadata_missing": "unknown contract",
}

_GRADE_WORDS = {"A+": "very strong", "A": "strong", "B": "good", "C": "fair", "WEAK": "weak"}
_CONDITION_WORDS = {"TRENDING": "trending", "RANGE_BOUND": "moving sideways", "CHOPPY": "choppy"}
_LEVEL_WORDS = {
    "PDH": "yesterday's high", "PDL": "yesterday's low",
    "PWH": "last week's high", "PWL": "last week's low",
    "HOD": "today's high", "LOD": "today's low",
    "ORH": "opening-range high", "ORL": "opening-range low",
    "VWAP": "the day's average price",
}
_CODE = re.compile(r"^[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+$")
_POINTS_SUFFIX = re.compile(r"\s*\([+-]\d+\)\s*$")


def _words(code: object) -> str:
    """snake/UPPER codes → lower-case words; free text passes through."""
    text = str(code or "").strip()
    if _CODE.match(text):
        return text.replace("_", " ").lower()
    return text


def _strategy_label(strategy: str) -> str:
    key = str(strategy or "").strip()
    return _STRATEGY_LABELS.get(key) or pe.setup(key)


def _format_price(value) -> str:
    if value is None:
        return "?"
    text = pe.price(value)
    return str(value) if text == "?" else text


def _format_bar_time(timestamp) -> str:
    """``10:30 AM ET, Sat May 23``; raw text when unparseable."""
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
    except (OSError, ValueError, OverflowError):
        return raw
    return pe.et_time(dt)


def _bar_close_label(payload: AlertPayload, context: dict) -> str:
    """The TradingView bar close (the value the decision was made on)."""
    context_close = context.get("close")
    close = context_close if context_close is not None else payload.close
    return _format_price(close)


def _decision_words(decision: str) -> str:
    return _DECISION_WORDS.get(decision) or _words(decision).capitalize() or "Unknown"


def _decision_icon(decision: str) -> str:
    if decision == "BLOCKED_EXECUTION_FAILED":
        return "🚨"
    if decision == "TRADE":
        return "✅"
    if "REJECT" in decision or decision.startswith("BLOCKED"):
        return "⛔"
    return "👀" if decision in ("SHADOW_NO_ORDER", "ORDER_SUPPRESSED") else "⚪"


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
    if status == "fresh":
        note = "live"
    elif isinstance(age, int):
        note = f"{status}, {age} sec old"
    else:
        note = status
    return f"Reference price: {_format_price(price)} ({note}; index price, not the order price)"


def _rule_words(rule: object) -> str:
    key = str(rule or "").strip()
    return _RULE_WORDS.get(key) or _words(key)


def _risk_line(risk: dict) -> str:
    """`Risk check: blocked — <why>` instead of a bare result code.

    The risk dict already carries the human-readable `reason` (and `failed_rule`)
    from RiskEngine.validate; surface it so a rejection explains itself.
    Approved trades carry no reason, so they stay `Risk check: passed`.
    """
    result = str(risk.get("result") or "").upper()
    verdict = {"APPROVED": "passed", "REJECTED": "blocked"}.get(result, _words(result).lower() or "unknown")
    reason = risk.get("reason") or (_rule_words(risk.get("failed_rule")) if risk.get("failed_rule") else None)
    return f"Risk check: {verdict} — {reason}" if reason else f"Risk check: {verdict}"


def _candidate_line(candidate: dict) -> str:
    """One-line near-miss snapshot: the would-be trade that was rejected.

    Audit-only — ``candidate`` is a read of an already-rejected decision; nothing
    here can place, queue, or retry an order.
    """
    side = pe.side(candidate.get("direction"))
    symbol = candidate.get("symbol") or "?"
    gate = candidate.get("blocking_gate") or candidate.get("reject_code")
    why = _rule_words(gate) if gate else (candidate.get("reject_reason") or "rejected")
    return (
        f"Almost traded: {side} {symbol} at {_format_price(candidate.get('entry'))}, "
        f"stop-loss {_format_price(candidate.get('stop'))}, target {_format_price(candidate.get('target'))} "
        f"— skipped: {why}"
    )


def _gate_words(reason: str) -> str:
    text = str(reason or "").strip()
    if text.startswith("always_on_shadow"):
        return "practice-only mode — orders are never sent"
    head, sep, rest = text.partition(":")
    if sep and _CODE.match(head.strip()):
        return f"{_words(head)}:{rest}"
    return _words(text)


def _decision_reason_line(result: dict) -> Optional[str]:
    reason = result.get("gate_reason") or result.get("reason")
    if not reason:
        failed = result.get("failed_gates") or []
        if isinstance(failed, str):
            failed = [failed]
        if failed:
            reason = ", ".join(_rule_words(item) for item in failed)
    return f"Why: {_gate_words(reason)}" if reason else None


def _factor_words(text: str) -> str:
    """Confluence reason (strategy/confluence_scorer.py) → plain words, score dropped."""
    item = _POINTS_SUFFIX.sub("", str(text or "")).strip()
    m = re.match(r"^Trend (UP|DOWN) (\w+)$", item)
    if m:
        return f"trend is {m.group(1).lower()} ({m.group(2).lower()})"
    m = re.match(r"^Against trend (\w+)$", item)
    if m:
        return f"goes against the trend ({m.group(1).lower()})"
    m = re.match(r"^Strat (\S+) confirmed$", item)
    if m:
        name = _strategy_label(m.group(1))
        return f"{name} confirmed" if "pattern" in name else f"{name} pattern confirmed"
    if re.match(r"^Strat direction \w+ contradicts \w+$", item):
        return "the chart pattern points the other way"
    m = re.match(r"^Volume ([\d.]+)x avg$", item)
    if m:
        return f"trading volume {m.group(1)}× normal"
    m = re.match(r"^Low volume ([\d.]+)x$", item)
    if m:
        return f"low trading volume ({m.group(1)}× normal)"
    m = re.match(r"^Near (LOD|HOD) ([\d.,]+)", item)
    if m:
        where = "today's low" if m.group(1) == "LOD" else "today's high"
        return f"near {where} ({_format_price(m.group(2).replace(',', ''))})"
    m = re.match(r"^Target near (\S+)$", item)
    if m:
        levels = " / ".join(_LEVEL_WORDS.get(code, code) for code in m.group(1).split("/"))
        return f"target is near {levels}"
    fixed = {
        "VWAP aligned": "on the right side of the day's average price",
        "Strong trend bonus": "strong trend",
        "NY session": "New York session",
        "ORB confirms direction": "opening range agrees with the direction",
        "EMA 9/21 crossover aligned": "short-term trend lines agree",
        "EMA 9/21 crossover against direction": "short-term trend lines disagree",
        "EMA 55 bias aligned": "medium-term trend agrees",
        "EMA 200 macro bias aligned": "long-term trend agrees",
    }
    return fixed.get(item, item)


def _quality_words(confluence: dict) -> Optional[str]:
    score = confluence.get("score")
    grade = str(confluence.get("grade") or "")
    if score is None and not grade:
        return None
    words = _GRADE_WORDS.get(grade, grade.lower() or "unknown")
    return f"{words} ({score} of 10)" if score is not None else words


def _dollars(symbol: object, entry, other, contracts) -> Optional[float]:
    """Dollar distance entry→other for the contract count; None when unknown."""
    try:
        from config.futures_contracts import symbol_economics

        tick, value = symbol_economics(symbol)
        n = int(contracts) if contracts is not None else 1
        return abs(float(entry) - float(other)) * (value / tick) * max(n, 1)
    except Exception:  # noqa: BLE001 — display only; fall back to a ratio
        return None


def _risk_reward_words(symbol: object, fill: dict) -> str:
    entry, stop, target = fill.get("entry"), fill.get("stop"), fill.get("target")
    contracts = fill.get("contracts")
    risk = _dollars(symbol, entry, stop, contracts)
    reward = _dollars(symbol, entry, target, contracts)
    if risk is not None and reward is not None:
        return f"{pe.money(risk, signed=False)} to make {pe.money(reward, signed=False)}"
    rr = fill.get("rr_ratio")
    if isinstance(rr, (int, float)):
        return f"target is {rr:g}× the risk"
    return "not known"


def _root(symbol: str) -> str:
    text = str(symbol or "").strip()
    if text.endswith("1!"):
        text = text[:-2]
    return text.rstrip("!") or str(symbol)


def _plain_fields(payload: AlertPayload, result: dict) -> tuple[str, list[tuple[str, str]]]:
    """(title, [(label, value)]) — the one plain-English view both formats render."""
    decision = str(result.get("decision") or "UNKNOWN")
    context = result.get("context") or {}
    risk = result.get("risk") or {}
    fill = result.get("fill") or {}
    candidate = result.get("candidate") or {}
    confluence = result.get("confluence") or {}
    root = _root(context.get("instrument") or payload.ticker)
    session = context.get("session") or ""
    fields: list[tuple[str, str]] = [
        ("Market", f"{pe.market(root)} · {pe.session(session) if session else 'session unknown'}"),
    ]
    if decision == "TRADE":
        side = pe.side(fill.get("direction"))
        title = f"{_decision_icon(decision)} {root} practice {side.lower()} taken"
        fields.append(("Direction", f"{side} {pe.contracts(fill.get('contracts') or 1)}"))
        fields.append(("Setup", _strategy_label(str(fill.get("strategy") or "?"))))
        quality = _quality_words(confluence)
        if quality:
            fields.append(("Setup quality", quality))
        fields.append((
            "Prices",
            f"in at {_format_price(fill.get('entry'))}, stop-loss {_format_price(fill.get('stop'))}, "
            f"target {_format_price(fill.get('target'))}",
        ))
        fields.append(("Risk", _risk_reward_words(root, fill)))
        if risk:
            fields.append(("Risk check", _risk_line(risk).split(": ", 1)[1]))
        good = [f"✅ {_factor_words(item)}" for item in (confluence.get("factors") or [])]
        bad = [f"⚠️ {_factor_words(item)}" for item in (confluence.get("penalties") or [])]
        if good or bad:
            fields.append(("Why it looked good", "\n".join(good + bad)))
        condition = context.get("market_condition")
        if condition:
            fields.append(("Price action", _CONDITION_WORDS.get(str(condition), _words(condition))))
    else:
        title = f"{_decision_icon(decision)} {root}: {_decision_words(decision)}"
        reason = _decision_reason_line(result)
        if reason:
            fields.append(("Why", reason.replace("Why: ", "", 1)))
        if risk:
            fields.append(("Risk check", _risk_line(risk).split(": ", 1)[1]))
        if candidate:
            fields.append(("Almost traded", _candidate_line(candidate).split(": ", 1)[1]))

    fields.append((
        "Price when decided",
        f"{_bar_close_label(payload, context)} at {_format_bar_time(payload.timestamp)}",
    ))
    ref_line = _reference_price_line(result.get("live_quote"))
    if ref_line:
        fields.append(("Reference price", ref_line.split(": ", 1)[1]))
    resolution = result.get("resolution")
    if resolution:
        fields.append(("How it ended", pe.exit_reason(resolution)))
    return title, fields


_FOOTER = "READ ONLY · practice account decision · nothing is ever placed from Discord"
_SMOKE = "🧪 TEST MESSAGE — made-up example, not a real decision and not saved"


def _truncate(value: str, limit: int = 900) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 35)] + "\n… Full details in the journal/artifact."


def _discord_payload(payload: AlertPayload, result: dict) -> dict:
    """Paper-collection-style Discord card for paper decisions, in plain English.

    Presentation only. It never changes the decision result, risk state, broker
    state, journal state, or execution path.
    """
    decision = str(result.get("decision") or "UNKNOWN")
    context = result.get("context") or {}
    color = 0xED4245 if "REJECT" in decision or decision.startswith("BLOCKED") else (0x57F287 if decision == "TRADE" else 0xF0B232)
    title, pairs = _plain_fields(payload, result)
    fields = [
        {"name": name, "value": _truncate(value), "inline": len(value) <= 40 and "\n" not in value}
        for name, value in pairs
    ]
    description = f"{pe.market(_root(context.get('instrument') or payload.ticker))} · {_decision_words(decision)}"
    if result.get("smoke_test"):
        description = _SMOKE + "\n" + description
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "fields": fields,
            "footer": {"text": _FOOTER},
        }],
    }


def _format_message(payload: AlertPayload, result: dict) -> str:
    """Plain-text version of the same card (CLI dry-run / tests)."""
    title, pairs = _plain_fields(payload, result)
    lines: list[str] = []
    if result.get("smoke_test"):
        lines.extend([_SMOKE, ""])
    lines.append(title)
    for name, value in pairs:
        if "\n" in value:
            lines.append(f"{name}:")
            lines.extend(value.split("\n"))
        else:
            lines.append(f"{name}: {value}")
    lines.append(_FOOTER)
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
