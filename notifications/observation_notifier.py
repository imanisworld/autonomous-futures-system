"""Discord messages for cross_instrument_observation_v1 — OBSERVATION ONLY.

Side effect of observation, never an input to it. Sends through the existing
``DiscordRouter`` on the optional ``observation`` route (env var
``DISCORD_ROUTE_OBSERVATION``, see config/notification_routes.yaml). When the
route is unset the router returns False and nothing else happens; when Discord
fails the router logs and returns False. This module never raises into the
observation pipeline and never reads or changes trading state.

Only meaningful state changes are announced: a new CANDIDATE / SIGNAL row and
a resolved structural OUTCOME. Bars that produce nothing are silent.

Watchdog stale alerts, transport failures, runtime errors, service failures,
recovery notices and safety blockers are NOT routed here — they keep their
existing routes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ROUTE_NAME = "observation"
LABEL = "OBSERVATION ONLY"

_ET = ZoneInfo("America/New_York")

# Plain names so a card reads without a glossary.
_MARKETS = {
    "MNQ": "Micro Nasdaq", "MES": "Micro S&P 500", "M2K": "Micro Russell",
    "MYM": "Micro Dow", "MGC": "Micro Gold", "MCL": "Micro Crude Oil",
    "MBT": "Micro Bitcoin",
}
_SETUPS = {
    "strat_22_continuation": "2-2 continuation",
    "strat_22_reversal": "2-2 reversal",
    "strat_212": "2-1-2 pattern",
    "strat_122": "1-2-2 pattern",
    "strat_122_pullback": "1-2-2 pullback",
    "strat_312": "3-1-2 pattern",
    "strat_322_reversal": "3-2-2 reversal",
    "strat_4hr_retrigger": "4-hour re-trigger",
    "ema_pullback_trend": "pullback in a trend",
    "impulse_first_pullback": "first pullback after a big move",
    "trend_consolidation_break": "breakout from a pause in a trend",
    "orb_false_break_fade": "fade of a failed opening-range break",
    "transition_failed_breakdown_reclaim": "failed breakdown, price back above",
    "vwap_hold": "holding above/below VWAP",
}
_EXITS = {
    "TARGET_HIT": "hit the profit target",
    "STOP_HIT": "hit the stop-loss",
    "STOP_HIT_ON_FILL_BAR": "hit the stop-loss right after entry",
    "STOP_GAP": "price jumped past the stop-loss",
    "EOD": "closed at end of day",
    "TIME_EXIT": "closed on time limit",
}
_FOOTER = "OBSERVATION ONLY · practice tracking, no real order was placed"


def _fmt_price(value) -> str:
    try:
        return f"{float(value):,.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "?"


def _money(value: float) -> str:
    sign = "-" if value < 0 else "+"
    return f"{sign}${abs(value):,.2f}"


def _when(ts: object) -> str:
    """'9:00 PM ET, Tue Sep 22' from an ISO UTC timestamp; raw text if unparseable."""
    text = str(ts or "")
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text or "?"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(_ET)
    return f"{local.strftime('%I:%M %p').lstrip('0')} ET, {local.strftime('%a %b')} {local.day}"


def _market(root: str) -> str:
    name = _MARKETS.get(root)
    return f"{root} ({name})" if name else root


def _setup(strategy: str) -> str:
    key = strategy[: -len("_observed")] if strategy.endswith("_observed") else strategy
    return _SETUPS.get(key) or key.replace("strat_", "").replace("_", " ")


def _side(direction: str) -> str:
    return {"LONG": "Buy", "SHORT": "Sell"}.get(direction, direction.title() or "?")


def _dollars_per_point(event: dict) -> Optional[float]:
    try:
        return float(event["tick_value_dollars"]) / float(event["tick_size"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


def _leg(event: dict, far_key: str, verb: str) -> str:
    """'7,832.98 (would lose about $2.79)' — the dollar part only when computable."""
    price = _fmt_price(event.get(far_key))
    per_point = _dollars_per_point(event)
    try:
        dollars = abs(float(event[far_key]) - float(event["entry"])) * per_point  # type: ignore[operator]
    except (KeyError, TypeError, ValueError):
        return price
    return f"{price} ({verb} about ${dollars:,.2f})"


def _outcome_dollars(event: dict) -> Optional[float]:
    gross = event.get("gross_pnl_dollars_1_contract")
    if isinstance(gross, (int, float)):
        return float(gross)
    per_point = _dollars_per_point(event)
    points = event.get("pnl_points")
    if per_point is not None and isinstance(points, (int, float)):
        return float(points) * per_point
    return None


def format_event(event: dict) -> Optional[str]:
    """One plain-English card per event; None for anything not announceable.

    Line 1 is the card title and always names the market; the last line is the
    OBSERVATION ONLY footer, so a reader can never mistake it for a real trade.
    """
    root = str(event.get("instrument") or "").strip()
    strategy = str(event.get("strategy") or "").strip()
    direction = str(event.get("direction") or "").strip().upper()
    kind = str(event.get("record_type") or "").strip().upper()
    if not root or not strategy or kind not in ("CANDIDATE", "SIGNAL", "OUTCOME"):
        return None
    side = _side(direction)
    if kind == "OUTCOME":
        result = str(event.get("result") or "").strip().upper()
        won = result == "WIN"
        mark = "🟢" if won else "🔴" if result == "LOSS" else "⚪"
        verdict = {"WIN": "won", "LOSS": "lost"}.get(result, result.lower() or "finished")
        reason = str(event.get("exit_reason") or "").strip().upper()
        lines = [
            f"{mark} {root} practice {side.lower()} {verdict}",
            f"Market: {_market(root)}",
            f"Setup: {_setup(strategy)}",
        ]
        dollars = _outcome_dollars(event)
        if dollars is not None:
            lines.append(f"Result: {_money(dollars)} (1 contract, before fees)")
        if reason:
            lines.append(f"How it ended: {_EXITS.get(reason, reason.replace('_', ' ').lower())}")
        if event.get("entry") is not None and event.get("exit_price") is not None:
            lines.append(f"Prices: in at {_fmt_price(event.get('entry'))}, out at {_fmt_price(event.get('exit_price'))}")
        lines.append(f"Closed: {_when(event.get('exit_timestamp') or event.get('resolved_at_bar_ts'))}")
        lines.append(_FOOTER)
        return "\n".join(lines)
    lines = [
        f"👀 {root} practice {side.lower()} setup spotted",
        f"Market: {_market(root)}",
        f"Setup: {_setup(strategy)}",
        f"Would {side.lower()} at: {_fmt_price(event.get('entry'))}",
        f"Stop-loss: {_leg(event, 'stop', 'would lose')}",
        f"Profit target: {_leg(event, 'target', 'would make')}",
        f"Seen: {_when(event.get('signal_timestamp'))}",
    ]
    if kind == "SIGNAL":
        lines.append("Note: stop-loss and target here are rough, from the chart alert")
    lines.append(_FOOTER)
    return "\n".join(lines)


def notify_observation(events: Iterable[dict], *, router=None) -> int:
    """Send one message per announceable event on the ``observation`` route.

    Returns the number of messages the router reported delivered. Never
    raises: a missing/disabled route, a missing routes file, or a Discord
    failure all end here with a log line and 0.
    """
    lines = [line for line in (format_event(e) for e in events if isinstance(e, dict)) if line]
    if not lines:
        return 0
    try:
        if router is None:
            from notifications.discord_router import DiscordRouter

            router = DiscordRouter()
        if not router.is_enabled(ROUTE_NAME):
            return 0
        sent = 0
        for line in lines:
            if router.send(ROUTE_NAME, line):
                sent += 1
        return sent
    except Exception:  # noqa: BLE001 — notification is a side effect; observation must continue
        logger.warning("observation Discord notification skipped", exc_info=True)
        return 0
