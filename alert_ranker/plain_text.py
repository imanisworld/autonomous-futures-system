"""Plain-English text helpers for options scanner Discord messages.

Presentation only (docs/discord-operator-message-style.md, "Plain English").
This is the options-scanner-local twin of ``notifications/plain_english.py``:
the scanner ships as its own curated release and ``alert_ranker`` has never
imported the ``notifications`` package, so the scanner keeps its own small,
stdlib-only copy instead of gaining a new cross-package dependency. Nothing
here reads or changes scoring, gating, storage or trading state.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# OCC/OSI option symbol, e.g. ``QQQ260923C00741000`` (spaces allowed after the root).
_OSI = re.compile(r"^(?P<root>[A-Z.]{1,6})\s*(?P<ymd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")

_STRAT_BARS = {"1": "inside", "2U": "up", "2D": "down", "2": "directional", "3": "outside", "3U": "outside up", "3D": "outside down"}


def today_et() -> date:
    return datetime.now(ET).date()


def as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(ET).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def day_label(day: date) -> str:
    """``Fri Sep 26``."""
    return f"{day.strftime('%a %b')} {day.day}"


def expires(expiry: Any = None, *, dte: Any = None, today: date | None = None) -> str:
    """``expires today`` / ``expires tomorrow, Thu Sep 24`` / ``expires Fri Sep 26 (3 days)``.

    Falls back to ``expires in 3 days`` when only a day count is known, and to
    ``expires <raw text>`` when the expiry cannot be parsed. Empty when nothing is known.
    """
    day = as_date(expiry)
    days: int | None = None
    if day is not None:
        days = (day - (today or today_et())).days
    else:
        try:
            days = int(dte) if dte not in (None, "") else None
        except (TypeError, ValueError):
            days = None
    if day is None:
        if days is None:
            raw = str(expiry or "").strip()
            return f"expires {raw}" if raw else ""
        if days == 0:
            return "expires today"
        if days == 1:
            return "expires tomorrow"
        return f"expires in {days} days"
    if days == 0:
        return "expires today"
    if days == 1:
        return f"expires tomorrow, {day_label(day)}"
    if days is not None and days > 1:
        return f"expires {day_label(day)} ({days} days)"
    return f"expired {day_label(day)}"


def option_kind(value: Any, *, explain: bool = False) -> str:
    """``call`` / ``put`` from CALL/PUT/C/P/LONG/SHORT; with explain adds what it bets on."""
    text = str(value or "").strip().upper()
    kind = {"CALL": "call", "C": "call", "LONG": "call", "PUT": "put", "P": "put", "SHORT": "put"}.get(text)
    if kind is None:
        return text.lower() or "option"
    if not explain:
        return kind
    return f"{kind} (bets the price goes {'up' if kind == 'call' else 'down'})"


def strike_text(value: Any) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value or "").strip()


def dollars(value: Any) -> str:
    """``$1,234.50``; empty when not a number."""
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return ""


def premium(value: Any) -> str:
    """Option price per share plus per contract: ``$1.20 a share ($120 per contract)``."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return ""
    return f"${amount:,.2f} a share (${amount * 100:,.0f} per contract)"


def parse_osi(symbol: Any) -> dict[str, Any] | None:
    """Split an OSI symbol into underlying / expiry / kind / strike; None if it isn't one."""
    match = _OSI.match(str(symbol or "").strip().upper())
    if not match:
        return None
    ymd = match.group("ymd")
    try:
        expiry = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:]))
    except ValueError:
        return None
    return {
        "underlying": match.group("root"),
        "expiry": expiry,
        "kind": "call" if match.group("cp") == "C" else "put",
        "strike": int(match.group("strike")) / 1000,
    }


def option_label(underlying: Any, strike: Any, kind: Any) -> str:
    """``QQQ 741 call``."""
    parts = [str(underlying or "").strip() or "?"]
    strike_part = strike_text(strike)
    if strike_part:
        parts.append(strike_part)
    parts.append(option_kind(kind))
    return " ".join(parts)


def timeframe(value: Any) -> str:
    """``15m`` → ``15-minute``; ``1D`` → ``daily``; ``1h`` → ``1-hour``."""
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d+)\s*([a-zA-Z]+)", text)
    if not match:
        return text
    count, unit = int(match.group(1)), match.group(2).lower()
    if unit in {"d", "day", "1d"}:
        return "daily" if count == 1 else f"{count}-day"
    if unit in {"w", "wk", "week"}:
        return "weekly" if count == 1 else f"{count}-week"
    if unit in {"h", "hr", "hour"}:
        return f"{count}-hour"
    if unit in {"m", "min", "minute"}:
        return f"{count}-minute"
    return text


def strat_sequence(combo: str) -> str:
    """``2U-1-2U`` → ``2U-1-2U (up, inside, up)``; unknown bars left as-is."""
    tokens = [tok for tok in re.split(r"[-\s]+", str(combo).strip()) if tok]
    words = [_STRAT_BARS.get(tok.upper()) for tok in tokens]
    if not tokens or not all(words):
        return str(combo)
    return f"{combo} ({', '.join(word for word in words if word)})"


def setup_name(pattern: Any) -> str:
    """``strat_222_reversal`` → ``2-2-2 reversal``; ``2-1-2`` → ``2-1-2 pattern``."""
    text = str(pattern or "").strip()
    if not text:
        return ""
    body = text[len("strat_"):] if text.lower().startswith("strat_") else text
    words = []
    for token in body.split("_"):
        if token.isdigit() and len(token) > 1:
            words.append("-".join(token))
        else:
            words.append(token.lower() if token.isupper() and len(token) > 3 else token)
    name = " ".join(words)
    if re.fullmatch(r"[\d\-UD]+", name):
        return f"{name} pattern"
    return name
