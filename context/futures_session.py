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


# ── Product-aware calendars ──────────────────────────────────────────────────
# `futures_session_active()` above is the legacy EQUITY-INDEX calendar and is
# deliberately unchanged for its existing callers. The observation transport
# and per-instrument feed health use `product_session_active(root, now)` so
# the equity calendar is never applied to a product it does not describe.
#
# Sources (CME, checked 2026-09-16):
#   equity index  (MNQ/MES/M2K/ES/NQ): Sun 18:00 ET → Fri 17:00 ET, daily
#       17:00–18:00 ET maintenance, plus a 16:15–16:30 ET trading halt.
#   metals/energy (MGC/MCL): Sun 18:00 ET → Fri 17:00 ET, daily 17:00–18:00 ET.
#   crypto        (MBT): 24/7 since 2026; maintenance Mon–Fri 16:00–16:02 CT
#       (17:00–17:02 ET) and Saturday 02:00–04:00 CT (03:00–05:00 ET).
#       CME notice 2026-09-14 extends Sat 2026-09-19 maintenance to
#       02:00–08:00 CT (03:00–09:00 ET) for 24/7 markets.
PRODUCT_EQUITY_INDEX = "equity_index"
PRODUCT_METALS_ENERGY = "metals_energy"
PRODUCT_CRYPTO = "crypto"

_PRODUCT_BY_ROOT = {
    "MNQ": PRODUCT_EQUITY_INDEX, "MES": PRODUCT_EQUITY_INDEX, "M2K": PRODUCT_EQUITY_INDEX,
    "ES": PRODUCT_EQUITY_INDEX, "NQ": PRODUCT_EQUITY_INDEX,
    "MGC": PRODUCT_METALS_ENERGY, "MCL": PRODUCT_METALS_ENERGY,
    "MBT": PRODUCT_CRYPTO,
}


def product_of(root: str | None) -> str | None:
    """CME product family for a canonical root, or None when unknown (fail closed)."""
    return _PRODUCT_BY_ROOT.get(str(root or "").upper())


def _et(now: datetime | None) -> datetime | None:
    try:
        base = now or datetime.now(timezone.utc)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        return base.astimezone(_ET)
    except Exception:
        return None


def _globex_weekly_active(et: datetime, *, equity_halt: bool) -> bool:
    wd, t = et.weekday(), et.time()
    if wd == 5:
        return False
    if wd == 6 and t < time(18, 0):
        return False
    if wd == 4 and t >= time(17, 0):
        return False
    if time(17, 0) <= t < time(18, 0):
        return False
    if equity_halt and time(16, 15) <= t < time(16, 30):
        return False
    return True


def _crypto_active(et: datetime) -> bool:
    wd, t = et.weekday(), et.time()
    # One-off CME 24/7 maintenance extension for Saturday 2026-09-19:
    # 02:00–08:00 CT = 03:00–09:00 ET. Keep this date-scoped so the normal
    # recurring 02:00–04:00 CT window resumes automatically afterward.
    if (et.year, et.month, et.day) == (2026, 9, 19) and time(3, 0) <= t < time(9, 0):
        return False
    if wd <= 4 and time(17, 0) <= t < time(17, 2):  # Mon–Fri 16:00–16:02 CT
        return False
    if wd == 5 and time(3, 0) <= t < time(5, 0):     # Sat 02:00–04:00 CT
        return False
    return True


def product_session_active(root: str | None, now: datetime | None = None) -> bool | None:
    """True/False when bars should be arriving for this product; None when the
    root has no known calendar (callers must treat None as "unknown", never as
    "expected idle")."""
    product = product_of(root)
    if product is None:
        return None
    et = _et(now)
    if et is None:
        return True  # fail OPEN, like the legacy helper: warn rather than hide an outage
    if product == PRODUCT_CRYPTO:
        return _crypto_active(et)
    return _globex_weekly_active(et, equity_halt=(product == PRODUCT_EQUITY_INDEX))


def feed_stale_after_minutes(expected_tf_minutes: int = 15) -> int:
    """Minutes of silence before a feed is considered stale: ~2 missed bars + 1m
    grace. Single definition shared by diagnostics, /status/today, the watchdog,
    and the dashboards so the threshold always tracks the configured timeframe.
    """
    tf = int(expected_tf_minutes or 15)
    return tf * 2 + 1
