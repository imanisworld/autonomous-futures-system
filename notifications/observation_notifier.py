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
from typing import Iterable, Optional

from notifications import plain_english as pe

logger = logging.getLogger(__name__)

ROUTE_NAME = "observation"
LABEL = "OBSERVATION ONLY"

_FOOTER = "OBSERVATION ONLY · practice tracking, no real order was placed"


_fmt_price = pe.price
_money = pe.money
_when = pe.et_time
_market = pe.market
_setup = pe.setup
_side = pe.side


def _dollars_per_point(event: dict) -> Optional[float]:
    return pe.dollars_per_point(event.get("tick_size"), event.get("tick_value_dollars"))


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


_BIG_PICTURE = {
    "aligned": "✅ month, week, day and hour all agree with this trade",
    "against": "⛔ month, week, day and hour all point the other way",
    "conflict": "⚠️ mixed: month, week, day and hour disagree",
    "unknown": "not enough price history yet",
}
# The MNQ 2-2 reversal "lined-up only" tracker (label view, no filtering).
_TRACKED = ("MNQ", "strat_22_reversal_observed")


def _big_picture_lines(event: dict) -> list[str]:
    label = event.get("strat_ftfc")
    alignment = label.get("alignment") if isinstance(label, dict) else None
    if alignment not in _BIG_PICTURE:
        return []
    lines = [f"Big-picture check: {_BIG_PICTURE[alignment]}"]
    if (str(event.get("instrument") or ""), str(event.get("strategy") or "")) == _TRACKED:
        lines.append("Lined-up-only tracker: " + ("counts this one" if alignment == "aligned" else "skips this one"))
    return lines


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
            lines.append(f"How it ended: {pe.exit_reason(reason)}")
        if event.get("entry") is not None and event.get("exit_price") is not None:
            lines.append(f"Prices: in at {_fmt_price(event.get('entry'))}, out at {_fmt_price(event.get('exit_price'))}")
        lines.append(f"Closed: {_when(event.get('exit_timestamp') or event.get('resolved_at_bar_ts'))}")
        lines.extend(_big_picture_lines(event))
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
    lines.extend(_big_picture_lines(event))
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
