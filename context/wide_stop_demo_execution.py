"""Log-only stale-entry observation for the Track B demo route.

``DEMO_STALE_ENTRY_SHADOW_X`` is pre-registered. It is not an environment
setting and it does not change which orders are submitted, or at what price.
"""
from __future__ import annotations

import math

from config.futures_contracts import tick_size

# Pre-registered log-only threshold. Not env-overridable.
DEMO_STALE_ENTRY_SHADOW_X = 0.25

_UNAVAILABLE = "DEMO_ENTRY_REF_PRICE_UNAVAILABLE"
_STALE = "DEMO_ENTRY_STALE_LEVEL"
_OK = "OK"
_MNQ_TICK = tick_size("MNQ")


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _signed_ticks(direction: str, planned_entry: float, ref_price: float) -> float:
    ticks = (ref_price - planned_entry) / _MNQ_TICK
    if direction == "SHORT":
        ticks = -ticks
    nearest = round(ticks)
    if abs(ticks - nearest) < 1e-9:
        return int(nearest)
    return ticks


def stale_entry_check(direction, planned_entry, stop, ref_price, max_frac):
    """Return ``(would_block, reason, metrics)``. Never decides an order.

    ``dist_ticks`` is signed so positive means the reference has moved through
    the planned entry in the trade's direction. ``frac > max_frac`` would
    block; an equal fraction would not.
    """
    side = str(direction or "").strip().upper()
    entry_n = _finite(planned_entry)
    stop_n = _finite(stop)
    ref_n = _finite(ref_price)
    max_n = _finite(max_frac)
    metrics = {
        "ref_price": ref_n,
        "planned_entry": entry_n,
        "stop": stop_n,
        "dist_ticks": None,
        "frac": None,
        "max_frac": max_n,
        "side_of_level": None,
    }
    if (
        side not in {"LONG", "SHORT"}
        or entry_n is None
        or stop_n is None
        or ref_n is None
        or max_n is None
    ):
        return True, _UNAVAILABLE, metrics

    metrics["dist_ticks"] = _signed_ticks(side, entry_n, ref_n)
    metrics["side_of_level"] = "through" if metrics["dist_ticks"] > 0 else "not_through"
    stop_pts = abs(entry_n - stop_n)
    if stop_pts <= 0:
        return True, _UNAVAILABLE, metrics

    dist_pts = abs(ref_n - entry_n)
    frac = dist_pts / stop_pts
    metrics["frac"] = frac
    if frac > max_n:
        return True, _STALE, metrics
    return False, _OK, metrics


def stale_entry_shadow_record(direction, planned_entry, stop, ref_price, max_frac=DEMO_STALE_ENTRY_SHADOW_X):
    """Journal-shaped shadow. Any failure becomes ``{"status": "ERROR"}``."""
    try:
        would_block, reason, metrics = stale_entry_check(
            direction, planned_entry, stop, ref_price, max_frac,
        )
        return {
            "status": "OK",
            "would_block": bool(would_block),
            "reason": reason,
            **metrics,
        }
    except Exception:
        return {"status": "ERROR"}
