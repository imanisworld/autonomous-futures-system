"""Live higher-timeframe direction computed from price, not from lagged labels.

Why this exists (2026-07-02 incident): under HTF_DIRECTION_MODE=prioritize the
quality gates take their direction from the payload's daily_direction /
four_hour_direction fields. Those labels come from *completed* higher-timeframe
bars, so they turn AFTER the move they describe: on 07-02 the daily/4H labels
read UP through an all-afternoon FULL_SHORT selloff, pinning gate_direction
LONG and vetoing every short while the local tape was correctly bearish.
A daily label structurally cannot flip DOWN until the decline already happened.

This module computes direction from levels that are live on every 15m bar:

  Daily  — where price trades RIGHT NOW relative to yesterday's range:
           above PDH = UP, below PDL = DOWN, else a leaned read vs prior close
           (with a small buffer so a few ticks either side stays NEUTRAL).
  4H     — where price trades relative to the PRIOR completed 4h window built
           from the 15m bars we already ingest: break of its high = UP, break
           of its low = DOWN, else leaned vs its close.

Both turn the moment price takes the level — no waiting for a bar close days
or hours later. NEUTRAL means "computed, genuinely flat"; None means "inputs
unavailable" (missing levels / not enough bar history) and lets callers fall
back to other direction sources.

4h windows are UTC-anchored (00/04/08/12/16/20). That differs from
TradingView's session-anchored 4H bars; the break-of-prior-window logic only
needs a *consistent* partition, and UTC anchoring keeps replay and live
byte-deterministic across DST.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

# A leaned read (no range break) must clear this fraction of the reference
# price before it counts as UP/DOWN — a couple of MES points around prior
# close is noise, not direction.
LEAN_BUFFER_PCT = 0.0005

_FOUR_HOURS = 4 * 60 * 60


def _parse_ts(value) -> Optional[datetime]:
    """ISO or epoch → aware UTC datetime; None on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if s.isdigit():
        try:
            return datetime.fromtimestamp(int(s), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _lean(close: float, reference: float, buffer_pct: float) -> str:
    buffer = abs(reference) * buffer_pct
    if close > reference + buffer:
        return "UP"
    if close < reference - buffer:
        return "DOWN"
    return "NEUTRAL"


def daily_direction_live(
    close: Optional[float],
    prev_day_high: Optional[float],
    prev_day_low: Optional[float],
    prev_day_close: Optional[float],
    *,
    buffer_pct: float = LEAN_BUFFER_PCT,
) -> Optional[str]:
    """Direction of the FORMING daily bar, readable on every 15m close.

    Returns "UP" / "DOWN" / "NEUTRAL", or None when any input is missing
    (callers treat None as "source unavailable", not as flat).
    """
    if None in (close, prev_day_high, prev_day_low, prev_day_close):
        return None
    if close > prev_day_high:
        return "UP"
    if close < prev_day_low:
        return "DOWN"
    return _lean(close, prev_day_close, buffer_pct)


def four_hour_direction_live(
    bars: Iterable[dict],
    close: Optional[float],
    ts,
    *,
    buffer_pct: float = LEAN_BUFFER_PCT,
) -> Optional[str]:
    """Direction of the forming 4h window vs the prior completed one.

    ``bars`` is recent 15m history (BarHistory dicts or replay candles —
    anything with ts/timestamp, high, low, close). The prior completed window
    must contain at least one bar; otherwise returns None.
    """
    if close is None:
        return None
    now = _parse_ts(ts)
    if now is None:
        return None
    current_bucket = int(now.timestamp()) // _FOUR_HOURS

    # Prior window = the most recent bucket BEFORE the current one that has
    # bars. Overnight maintenance halts leave empty buckets; skipping to the
    # last populated one keeps the reference meaningful instead of vanishing.
    windows: dict[int, dict] = {}
    for bar in bars:
        bts = _parse_ts(bar.get("ts") or bar.get("timestamp"))
        if bts is None:
            continue
        bucket = int(bts.timestamp()) // _FOUR_HOURS
        if bucket >= current_bucket:
            continue
        high = bar.get("high")
        low = bar.get("low")
        bclose = bar.get("close")
        if None in (high, low, bclose):
            continue
        w = windows.setdefault(
            bucket, {"high": high, "low": low, "close": bclose, "last_ts": bts}
        )
        w["high"] = max(w["high"], high)
        w["low"] = min(w["low"], low)
        if bts >= w["last_ts"]:
            w["last_ts"] = bts
            w["close"] = bclose

    if not windows:
        return None
    prior = windows[max(windows)]

    if close > prior["high"]:
        return "UP"
    if close < prior["low"]:
        return "DOWN"
    return _lean(close, prior["close"], buffer_pct)


def apply_live_direction(state, bars: Iterable[dict]) -> None:
    """Overwrite state.htf daily/4H direction with live-computed values.

    Replaces the two direction fields outright (a None result stays None —
    never silently mixed with the payload's lagged label) and stamps
    direction_source="live" so journals show which source decided. Bar types,
    1H, and FTFC fields are payload-owned and untouched.
    """
    # Local import: market_context imports nothing from here, but keeping the
    # module import-light lets replay tooling use the pure functions alone.
    from context.market_context import HTFContext

    prev_day = getattr(state, "previous_day", None)
    close = state.ohlc.close if getattr(state, "ohlc", None) else None
    daily = daily_direction_live(
        close,
        getattr(prev_day, "high", None),
        getattr(prev_day, "low", None),
        getattr(prev_day, "close", None),
    )
    four_hour = four_hour_direction_live(bars, close, getattr(state, "timestamp", None))

    if state.htf is None:
        state.htf = HTFContext()
    state.htf.daily_direction = daily
    state.htf.four_hour_direction = four_hour
    state.htf.direction_source = "live"
