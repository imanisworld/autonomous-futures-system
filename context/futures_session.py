"""
context/futures_session.py

Single source of truth for "is the CME equity-index futures session active right
now?" — i.e. should a TradingView bar/webhook be arriving. Used by the backend
diagnostics (webhook/app.py), the feed watchdog (scripts/feed_watchdog.py), and
the ops monitor (adaptive/ops_monitor.py) so the dashboard, diagnostics, and
watchdog never disagree about feed health.

This is about FEED HEALTH, not strategy trade windows — the latter live in
risk_rules.yaml session_windows and are evaluated separately.

Pure datetime/zoneinfo; no heavy imports (the watchdog runs every ~5 min).
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def futures_session_active(now: datetime | None = None) -> bool:
    """True when CME equity-index futures are trading and bars should be arriving.

    Excludes:
      - the weekend close: Friday 17:00 ET → Sunday 18:00 ET, and
      - the daily maintenance halt: 17:00–18:00 ET on trading days,
    when no new bars print and a stale feed is expected idle, not a fault.

    Fails OPEN (returns True) if the timezone can't be resolved — better to warn
    than to silently hide a real outage. `now` may be naive (treated as UTC).
    """
    try:
        base = now or datetime.now(timezone.utc)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        et = base.astimezone(_ET)
    except Exception:
        return True
    wd = et.weekday()  # Mon=0 .. Sun=6
    t = et.time()
    if wd == 5:  # Saturday — closed all day
        return False
    if wd == 6 and t < time(18, 0):  # Sunday before the 18:00 reopen
        return False
    if wd == 4 and t >= time(17, 0):  # Friday after the 17:00 close
        return False
    if time(17, 0) <= t < time(18, 0):  # daily maintenance break
        return False
    return True


def feed_stale_after_minutes(expected_tf_minutes: int = 15) -> int:
    """Minutes of silence before a feed is considered stale: ~2 missed bars + 1m
    grace. Single definition shared by diagnostics, /status/today, the watchdog,
    and the dashboards so the threshold always tracks the configured timeframe.
    """
    tf = int(expected_tf_minutes or 15)
    return tf * 2 + 1
