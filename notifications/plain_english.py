"""
notifications/plain_english.py

Presentation-only helpers so every operator-facing Discord message speaks the
same plain English (docs/discord-operator-message-style.md, "Plain English"):

- times in US Eastern, e.g. ``9:00 PM ET, Tue Sep 22`` — never UTC ``Z`` stamps;
- money in dollars per contract — never ``R`` multiples;
- ``Buy`` / ``Sell`` — never ``LONG`` / ``SHORT``;
- spelled-out market and setup names — never raw ``strat_*`` ids;
- short words for internal status codes (``TARGET_HIT`` → "hit the profit target").

Stdlib only: the read-only watcher copies this file next to itself, the same
way it copies ``discord_card.py``. Nothing here reads or changes trading state.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

MARKETS = {
    "MNQ": "Micro Nasdaq",
    "MES": "Micro S&P 500",
    "M2K": "Micro Russell",
    "MYM": "Micro Dow",
    "MGC": "Micro Gold",
    "MCL": "Micro Crude Oil",
    "MBT": "Micro Bitcoin",
    "NQ": "Nasdaq",
    "ES": "S&P 500",
}

SETUPS = {
    "strat_22_continuation": "2-2 continuation",
    "strat_22_reversal": "2-2 reversal",
    "strat_212": "2-1-2 pattern",
    "strat_122": "1-2-2 pattern",
    "strat_122_pullback": "1-2-2 pullback",
    "strat_312": "3-1-2 pattern",
    "strat_322": "3-2-2 pattern",
    "strat_32": "3-2 pattern",
    "strat_322_reversal": "3-2-2 reversal",
    "strat_322_first_live": "hourly 3-2-2",
    "strat_4hr_retrigger": "4-hour re-trigger",
    "strat_12hr_miyagi": "12-hour Miyagi",
    "daily_22": "daily 2-2",
    "ema_pullback_trend": "pullback in a trend",
    "impulse_first_pullback": "first pullback after a big move",
    "trend_consolidation_break": "breakout from a pause in a trend",
    "orb_breakout": "opening-range breakout",
    "orb_reclaim": "back inside the opening range",
    "orb_false_break_fade": "fade of a failed opening-range break",
    "transition_failed_breakdown_reclaim": "failed breakdown, price back above",
    "vwap_hold": "holding the day's average price",
    "vwap_failed_reclaim": "failed to get back above the day's average price",
}

EXITS = {
    "TARGET_HIT": "hit the profit target",
    "STOP_HIT": "hit the stop-loss",
    "STOP_HIT_ON_FILL_BAR": "hit the stop-loss right after entry",
    "STOP_GAP": "price jumped past the stop-loss",
    "BREAKEVEN_STOP": "stopped out at break-even",
    "EOD": "closed at end of day",
    "TIME_EXIT": "closed on time limit",
    "FORCE_CLOSE_SESSION_TIMEOUT": "closed by the safety net (session ended)",
    "FORCE_CLOSE_PRICE_MISMATCH": "closed by the safety net (price didn't match)",
    "FORCE_CLOSE_UNMATCHED": "closed by the safety net (couldn't match the trade)",
    "SESSION_TIMEOUT": "session ended",
    "PRICE_MISMATCH": "price didn't match",
}

SESSIONS = {
    "asian": "Asia session",
    "london": "London session",
    "new_york": "New York session",
    "off_hours": "market closed",
}


def et_time(ts: object, *, with_day: bool = True) -> str:
    """``9:00 PM ET, Tue Sep 22`` (or ``9:00 PM ET``) from an ISO/datetime; raw text if unparseable."""
    if isinstance(ts, datetime):
        moment = ts
    else:
        text = str(ts or "").strip()
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text or "?"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(ET)
    clock = f"{local.strftime('%I:%M %p').lstrip('0')} ET"
    if not with_day:
        return clock
    return f"{clock}, {local.strftime('%a %b')} {local.day}"


def et_date(value: object) -> str:
    """``Tue Sep 22`` from a date, datetime or ISO date/time string; raw text if unparseable."""
    if isinstance(value, datetime):
        day = value.astimezone(ET).date() if value.tzinfo else value.date()
    elif isinstance(value, date):
        day = value
    else:
        text = str(value or "").strip()
        try:
            day = date.fromisoformat(text[:10])
        except ValueError:
            return text or "?"
    return f"{day.strftime('%a %b')} {day.day}"


def ago(minutes: object) -> str:
    """``3 min ago`` / ``2 hr 5 min ago`` from a minute count."""
    try:
        total = int(round(float(minutes)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    if total < 60:
        return f"{total} min ago"
    hours, mins = divmod(total, 60)
    return f"{hours} hr {mins} min ago" if mins else f"{hours} hr ago"


def duration(minutes: object) -> str:
    """``35 min`` / ``2 hr 5 min`` from a minute count."""
    text = ago(minutes)
    return text[: -len(" ago")] if text.endswith(" ago") else text


def side(direction: object) -> str:
    """``Buy`` / ``Sell`` for LONG / SHORT (case-insensitive); title-cased otherwise."""
    text = str(direction or "").strip().upper()
    return {"LONG": "Buy", "SHORT": "Sell", "BUY": "Buy", "SELL": "Sell"}.get(text, text.title() or "?")


def market(root: object) -> str:
    """``MNQ (Micro Nasdaq)``; the bare root when unknown."""
    text = str(root or "").strip().upper().rstrip("!").rstrip("1")
    name = MARKETS.get(text)
    return f"{text} ({name})" if name else (text or "?")


def setup(strategy: object) -> str:
    """Plain setup name for a strategy id; underscores turned to spaces when unknown."""
    key = str(strategy or "").strip()
    for suffix in ("_observed", "_observer"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
    return SETUPS.get(key) or key.replace("strat_", "").replace("_", " ") or "?"


def exit_reason(code: object) -> str:
    """Short words for an exit/close code; lower-cased words when unknown."""
    text = str(code or "").strip().upper()
    return EXITS.get(text) or text.replace("_", " ").lower() or "?"


def session(name: object) -> str:
    text = str(name or "").strip().lower()
    return SESSIONS.get(text) or text.replace("_", " ") or "?"


def money(value: object, *, signed: bool = True) -> str:
    """``+$25.00`` / ``-$10.50`` (or ``$25.00`` when not signed); ``?`` if not a number."""
    try:
        amount = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    if not signed:
        return f"${amount:,.2f}"
    sign = "-" if amount < 0 else "+"
    return f"{sign}${abs(amount):,.2f}"


def price(value: object) -> str:
    """``19,505.25`` — thousands separators, no trailing zeros."""
    try:
        text = f"{float(value):,.2f}"  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    return text.rstrip("0").rstrip(".")


def contracts(count: object) -> str:
    try:
        n = int(count)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "? contracts"
    return f"{n} contract" if n == 1 else f"{n} contracts"


def dollars_per_point(tick_size: object, tick_value: object) -> Optional[float]:
    try:
        return float(tick_value) / float(tick_size)  # type: ignore[arg-type]
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# ── Broker / execution safety wording (appended 2026-09-23) ──────────────────

EXITS.update({
    "TARGET": "hit the profit target",
    "STOP": "hit the stop-loss",
    "CLOSED": "closed",
    "FEED_GAP": "price feed gap",
})

ORDER_PROBLEMS = {
    "POST_FILL_INVALID_AUTO_FLATTENED": "the real fill price broke the risk rules, so the bot closed the position",
    "POST_FILL_INVALID_FLATTEN_UNCONFIRMED": (
        "the real fill price broke the risk rules; the bot tried to close the position "
        "but Tradovate hasn't confirmed it"
    ),
    "NAKED_BRACKET_AUTO_FLATTENED": "the stop-loss or profit target wasn't confirmed, so the bot closed the position",
    "NAKED_FLATTEN_UNCONFIRMED": (
        "the stop-loss or profit target wasn't confirmed; the bot tried to close the position "
        "but Tradovate hasn't confirmed it"
    ),
    "TRADOVATE_ORDER_ERROR": "Tradovate returned an error for the order",
    "REJECTED": "Tradovate rejected the order",
    "ERROR": "the order hit an error",
}

POST_FILL_CHECKS = {
    "actual_rr_minimum": "profit target too small for the risk at the real fill price",
    "actual_dollar_risk": "dollar risk too big at the real fill price",
    "actual_stop_distance": "stop-loss too far away at the real fill price",
    "slippage_limit": "filled at a worse price than allowed",
    "target_direction": "profit target on the wrong side of the fill price",
    "stop_direction": "stop-loss on the wrong side of the fill price",
    "entry_tick": "fill price not a valid price step",
    "stop_tick": "stop-loss not a valid price step",
    "target_tick": "profit target not a valid price step",
}

PROTECTION = {
    "STOP": "stop-loss",
    "TARGET": "profit target",
}

PREFLIGHT_CHECKS = {
    "tradovate_reliability_healthy": "the Tradovate connection isn't healthy",
    "heartbeat_fresh": "no recent check-in from Tradovate",
    "account_readable": "couldn't read the Tradovate account",
    "positions_readable": "couldn't read open positions",
    "orders_readable": "couldn't read waiting orders",
    "no_open_positions": "a position is already open",
    "no_working_orders": "orders are already waiting in Tradovate",
    "live_box_drift_guard": "the server's code or settings don't match what was approved",
    "unknown": "an unknown check",
}

RESULTS = {"WIN": "won", "LOSS": "lost", "BREAKEVEN": "broke even", "CANCELLED": "cancelled"}


def _words(code: object) -> str:
    return str(code or "").strip().replace("_", " ").lower() or "?"


def order_problem(code: object) -> str:
    """Short words for why an order did not stay open; lower-cased words when unknown."""
    text = str(code or "").strip()
    return ORDER_PROBLEMS.get(text.upper()) or _words(text)


def code_list(codes: object) -> str:
    """``a, b`` for a list of raw codes (for ``-# details`` footers); never raises."""
    if isinstance(codes, (list, tuple, set)):
        return ", ".join(str(c) for c in codes) or "none"
    return str(codes)


def post_fill_problems(codes: object) -> str:
    """``profit target too small …; filled at a worse price …`` from failed check names."""
    names = [str(c) for c in codes] if isinstance(codes, (list, tuple, set)) else []
    if not names:
        return "the fill didn't pass the risk rules"
    return "; ".join(POST_FILL_CHECKS.get(n, _words(n)) for n in names)


def protection(names: object) -> str:
    """``stop-loss and profit target`` from ``["STOP", "TARGET"]``."""
    items = (
        [PROTECTION.get(str(n).upper(), _words(n)) for n in names]
        if isinstance(names, (list, tuple))
        else []
    )
    return " and ".join(items) or "stop-loss or profit target"


def order_states(states: object) -> str:
    """``stop-loss order expired, profit target order expired`` from ``{"stop": "expired", …}``."""
    if not isinstance(states, dict) or not states:
        return "unknown"
    parts = []
    for key, value in states.items():
        if isinstance(value, int) and not isinstance(value, bool):
            parts.append(f"{value} {_words(key)}")  # census counts, e.g. {"Expired": 2}
        else:
            parts.append(f"{PROTECTION.get(str(key).upper(), _words(key))} order {_words(value)}")
    return ", ".join(parts)


def preflight_reason(reason: object) -> str:
    """Plain words for a live-trading on/off reason (``preflight_failed:heartbeat_fresh`` etc.)."""
    text = str(reason or "").strip()
    prefix = "preflight_failed:"
    if text.startswith(prefix):
        name = text[len(prefix):]
        return PREFLIGHT_CHECKS.get(name, _words(name))
    return _words(text)


def result_word(result: object) -> str:
    """``won`` / ``lost`` / ``broke even`` for WIN / LOSS / BREAKEVEN."""
    text = str(result or "").strip().upper()
    return RESULTS.get(text) or _words(text)


# ── Options (appended 2026-09-23 for the options-side messages) ─────────────
# The options scanner (alert_ranker/) keeps its own twin of these in
# alert_ranker/plain_text.py because it ships as a separate curated release.


def today_et() -> date:
    return datetime.now(ET).date()


def expires(expiry: object = None, *, dte: object = None, today: Optional[date] = None) -> str:
    """``expires today`` / ``expires tomorrow, Thu Sep 24`` / ``expires Fri Sep 26 (3 days)``.

    Uses the day count when only that is known (``expires in 3 days``); empty when
    nothing is known. Never ``DTE`` / ``0DTE`` / ISO dates.
    """
    day: Optional[date] = None
    if isinstance(expiry, datetime):
        day = expiry.date()
    elif isinstance(expiry, date):
        day = expiry
    elif expiry:
        try:
            day = date.fromisoformat(str(expiry).strip()[:10])
        except ValueError:
            day = None
    days: Optional[int] = None
    if day is not None:
        days = (day - (today or today_et())).days
    elif dte not in (None, ""):
        try:
            days = int(dte)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            days = None
    if day is None:
        if days is None:
            raw = str(expiry or "").strip()
            return f"expires {raw}" if raw else ""
        return "expires today" if days == 0 else "expires tomorrow" if days == 1 else f"expires in {days} days"
    label = f"{day.strftime('%a %b')} {day.day}"
    if days == 0:
        return "expires today"
    if days == 1:
        return f"expires tomorrow, {label}"
    if days is not None and days > 1:
        return f"expires {label} ({days} days)"
    return f"expired {label}"


def option_kind(value: object, *, explain: bool = False) -> str:
    """``call`` / ``put`` (from CALL/PUT/C/P); with explain: ``call (bets the price goes up)``."""
    text = str(value or "").strip().upper()
    kind = {"CALL": "call", "C": "call", "PUT": "put", "P": "put"}.get(text)
    if kind is None:
        return text.lower() or "option"
    if not explain:
        return kind
    return f"{kind} (bets the price goes {'up' if kind == 'call' else 'down'})"


def option_label(underlying: object, strike: object, kind: object) -> str:
    """``QQQ 741 call``."""
    parts = [str(underlying or "").strip() or "?"]
    try:
        parts.append(f"{float(strike):g}")  # type: ignore[arg-type]
    except (TypeError, ValueError):
        pass
    parts.append(option_kind(kind))
    return " ".join(parts)


def option_price(value: object) -> str:
    """Option price per share plus per contract: ``$1.20 a share ($120 per contract)``."""
    try:
        amount = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    return f"${amount:,.2f} a share (${amount * 100:,.0f} per contract)"
