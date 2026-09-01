"""
context/range_signal.py

Range-behavior observation layer — classifies what price is doing relative to
nearby structural walls when the regime is RANGE_BOUND or CHOPPY.

JOURNAL-ONLY. No effect on trade decisions, risk gates, sizing, or execution.
Requires WallContext to be built first (context/wall_context.py).

Build entry points:
    build_range_state(wall_ctx, market_condition, orb_status) -> RangeState
    build_range_signal(range_state, wall_ctx) -> RangeSignal

RangeState answers: where is price inside the range?
RangeSignal answers: what is price doing at this location?

signal_type values:
    RANGE_BREAK_CLOSE       15m close outside a wall — executable (paper only)
    RANGE_BREAK_RETEST_SHADOW  price returned within 0.15% of broken level
    RANGE_REJECT            price within 0.25% of wall, closed back inside
    RANGE_BOUNCE            price within 0.25% of opposite wall, held away
    RANGE_MIDDLE_NO_TRADE   price between 35%-65% of range — no edge
    RANGE_NO_DATA           insufficient wall data to classify

wall_source precedence (highest quality first):
    ORB → PDH_PDL → SUPPLY_DEMAND → PWH_PWL → HOD_LOD → NONE
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from context.wall_context import WallContext, WallLevel

# ── Location labels ────────────────────────────────────────────────────────────
LOC_NEAR_HIGH = "NEAR_HIGH"
LOC_NEAR_LOW = "NEAR_LOW"
LOC_MIDDLE = "MIDDLE"
LOC_BREAKING_HIGH = "BREAKING_HIGH"
LOC_BREAKING_LOW = "BREAKING_LOW"
LOC_OUTSIDE_HIGH = "OUTSIDE_HIGH"
LOC_OUTSIDE_LOW = "OUTSIDE_LOW"

# ── Signal types ───────────────────────────────────────────────────────────────
SIG_BREAK_CLOSE = "RANGE_BREAK_CLOSE"
SIG_RETEST_SHADOW = "RANGE_BREAK_RETEST_SHADOW"
SIG_REJECT = "RANGE_REJECT"
SIG_BOUNCE = "RANGE_BOUNCE"
SIG_MIDDLE = "RANGE_MIDDLE_NO_TRADE"
SIG_NO_DATA = "RANGE_NO_DATA"

# ── Wall source names (for wall_source field) ──────────────────────────────────
_SOURCE_PRIORITY = ["ORB_HIGH", "ORB_LOW", "PDH", "PDL",
                    "SUPPLY_ZONE", "DEMAND_ZONE",
                    "PWH", "PWL", "HOD", "LOD"]

# ── Proximity thresholds ───────────────────────────────────────────────────────
_NEAR_WALL_PCT = 0.0025      # 0.25% — NEAR_HIGH / NEAR_LOW / REJECT / BOUNCE
_BREAK_CLOSE_PCT = 0.0015    # 0.15% — BREAKING (just closed outside by < 0.15%)
_RETEST_PCT = 0.0015         # 0.15% — RETEST: returned within this of broken level
_MIDDLE_LOW = 0.35           # location_pct below this → NEAR_LOW
_MIDDLE_HIGH = 0.65          # location_pct above this → NEAR_HIGH
_POLL_INTERVAL_S = 30        # system polls every 30 seconds


# ─── RangeState ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RangeState:
    """
    Price location within the current range.

    Derived from WallContext: the nearest resistance above is the range ceiling,
    the nearest support below is the range floor.
    """

    regime: str                      # RANGE_BOUND | CHOPPY
    range_high: Optional[float]      # ceiling level (nearest resistance)
    range_low: Optional[float]       # floor level (nearest support)
    range_midpoint: Optional[float]  # (high + low) / 2
    range_width: Optional[float]     # high - low in points
    price: float
    location: str                    # LOC_* constant
    location_pct: Optional[float]    # 0.0 = at low, 1.0 = at high; None if no range
    wall_source: str                 # name of the wall driving this range, or "NONE"
    wall_fresh: bool                 # True if the defining wall is fresh

    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "range_high": self.range_high,
            "range_low": self.range_low,
            "range_midpoint": self.range_midpoint,
            "range_width": self.range_width,
            "price": self.price,
            "location": self.location,
            "location_pct": round(self.location_pct, 4) if self.location_pct is not None else None,
            "wall_source": self.wall_source,
            "wall_fresh": self.wall_fresh,
        }


# ─── RangeSignal ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RangeSignal:
    """
    Behavioral classification of what price is doing at its current wall location.

    executable is True ONLY for RANGE_BREAK_CLOSE — all other signal types are
    observation-only. Even executable signals are paper-only; no live trading.
    """

    signal_type: str                     # SIG_* constant
    direction: str                       # LONG | SHORT | NONE
    entry_candidate: Optional[float]
    target_candidate: Optional[float]
    stop_candidate: Optional[float]
    executable: bool                     # True only for RANGE_BREAK_CLOSE
    retest_eligible: bool
    retest_bars_available: Optional[int]
    polling_risk: bool                   # True if retest window < 60s at 30s polling
    notes: Optional[str]

    def to_dict(self) -> dict:
        return {
            "signal_type": self.signal_type,
            "direction": self.direction,
            "entry_candidate": self.entry_candidate,
            "target_candidate": self.target_candidate,
            "stop_candidate": self.stop_candidate,
            "executable": self.executable,
            "retest_eligible": self.retest_eligible,
            "retest_bars_available": self.retest_bars_available,
            "polling_risk": self.polling_risk,
            "notes": self.notes,
        }


# ─── Build RangeState ─────────────────────────────────────────────────────────

def build_range_state(
    wall_ctx: "WallContext",
    market_condition: str,
    *,
    orb_status: Optional[str] = None,
) -> RangeState:
    """
    Build RangeState from WallContext.

    Args:
        wall_ctx:         current WallContext (from build_wall_context)
        market_condition: RANGE_BOUND | CHOPPY (only these regimes are meaningful)
        orb_status:       optional ORB status string from payload for source inference

    Returns a RangeState. Never raises — returns a stub on error.
    """
    try:
        return _build_range_state(wall_ctx, market_condition, orb_status=orb_status)
    except Exception as exc:
        return RangeState(
            regime=market_condition,
            range_high=None,
            range_low=None,
            range_midpoint=None,
            range_width=None,
            price=getattr(wall_ctx, "price", 0.0),
            location=LOC_MIDDLE,
            location_pct=None,
            wall_source="NONE",
            wall_fresh=True,
        )


def _build_range_state(
    wall_ctx: "WallContext",
    market_condition: str,
    *,
    orb_status: Optional[str],
) -> RangeState:
    price = wall_ctx.price
    res = wall_ctx.nearest_resistance
    sup = wall_ctx.nearest_support

    range_high = res.char_price() if res is not None else None
    range_low = sup.char_price() if sup is not None else None

    midpoint: Optional[float] = None
    width: Optional[float] = None
    if range_high is not None and range_low is not None:
        midpoint = (range_high + range_low) / 2.0
        width = range_high - range_low

    location, location_pct = _locate_price(price, range_high, range_low)

    # Best wall source name: prefer highest-priority named wall that is present
    wall_source = "NONE"
    wall_fresh = True
    for candidate_name in _SOURCE_PRIORITY:
        for w in (wall_ctx.walls_above + wall_ctx.walls_below):
            if w.name == candidate_name:
                wall_source = candidate_name
                wall_fresh = w.fresh
                break
        if wall_source != "NONE":
            break

    return RangeState(
        regime=market_condition,
        range_high=range_high,
        range_low=range_low,
        range_midpoint=midpoint,
        range_width=width,
        price=price,
        location=location,
        location_pct=location_pct,
        wall_source=wall_source,
        wall_fresh=wall_fresh,
    )


# ─── Build RangeSignal ────────────────────────────────────────────────────────

def build_range_signal(
    range_state: RangeState,
    wall_ctx: "WallContext",
    *,
    timeframe_minutes: int = 15,
    bars_since_break: Optional[int] = None,
) -> RangeSignal:
    """
    Classify what price is doing relative to range walls.

    Args:
        range_state:       from build_range_state
        wall_ctx:          current WallContext
        timeframe_minutes: bar timeframe (default 15m)
        bars_since_break:  bars elapsed since a confirmed break (for retest logic)

    Returns a RangeSignal. Never raises.
    """
    try:
        return _build_range_signal(range_state, wall_ctx,
                                   timeframe_minutes=timeframe_minutes,
                                   bars_since_break=bars_since_break)
    except Exception:
        return _no_data_signal("build_range_signal failed")


def _break_target(
    price: float,
    broken_cp: float,
    walls: list,
    *,
    direction: str,
) -> float:
    """
    Target for a RANGE_BREAK_CLOSE: the next structural wall in the trade's
    direction, strictly beyond price and of the matching kind.

    LONG  → next resistance (or supply zone) strictly ABOVE price.
    SHORT → next support (or demand zone) strictly BELOW price.

    ``walls`` is the already-sorted nearest-first list (walls_above for LONG,
    walls_below for SHORT). WallContext places a level that sits exactly at
    price into BOTH lists, and the previous ``walls[0]`` selection took that
    at-price level of any kind as the target — producing target == entry and
    LONG targets at supports. Falls back to a symmetric 1:1 projection off the
    broken wall when no qualifying wall exists; never returns the entry price.
    """
    from context.wall_context import KIND_RESISTANCE, KIND_SUPPORT, KIND_ZONE

    is_long = direction == "LONG"
    if is_long:
        kind_ok = lambda w: w.kind == KIND_RESISTANCE or (  # noqa: E731
            w.kind == KIND_ZONE and w.name == "SUPPLY_ZONE"
        )
        beyond = lambda cp: cp > price  # noqa: E731
    else:
        kind_ok = lambda w: w.kind == KIND_SUPPORT or (  # noqa: E731
            w.kind == KIND_ZONE and w.name == "DEMAND_ZONE"
        )
        beyond = lambda cp: cp < price  # noqa: E731

    for wall in walls:
        if not kind_ok(wall):
            continue
        wcp = wall.char_price()
        if wcp is None or wcp <= 0 or not beyond(wcp):
            continue
        target = round(wcp, 2)
        if beyond(target):
            return target

    # Symmetric fallback: project the broken-wall distance beyond price.
    if is_long:
        return round(price + (price - broken_cp), 2)
    return round(price - (broken_cp - price), 2)


def _build_range_signal(
    rs: RangeState,
    wall_ctx: "WallContext",
    *,
    timeframe_minutes: int,
    bars_since_break: Optional[int],
) -> RangeSignal:
    from context.wall_context import KIND_RESISTANCE, KIND_SUPPORT

    price = rs.price

    # ── RANGE_BREAK_CLOSE: a resistance wall is now below price ───────────────
    # When price closes above a resistance level, that level flips into
    # walls_below while retaining KIND_RESISTANCE.  Detect the break there.
    for wall in wall_ctx.walls_below:
        if wall.kind == KIND_RESISTANCE:
            cp = wall.char_price()
            if cp is None or cp <= 0:
                continue
            break_pct = (price - cp) / cp
            if break_pct > _BREAK_CLOSE_PCT:
                stop = round(cp * (1 - 0.001), 2)
                target = _break_target(
                    price, cp, wall_ctx.walls_above, direction="LONG"
                )
                return RangeSignal(
                    signal_type=SIG_BREAK_CLOSE,
                    direction="LONG",
                    entry_candidate=price,
                    stop_candidate=stop,
                    target_candidate=target,
                    executable=True,
                    retest_eligible=True,
                    retest_bars_available=_retest_bars(timeframe_minutes),
                    polling_risk=_has_polling_risk(timeframe_minutes),
                    notes=f"15m close above {wall.name} — break confirmed",
                )

    # ── RANGE_BREAK_CLOSE: a support wall is now above price ─────────────────
    for wall in wall_ctx.walls_above:
        if wall.kind == KIND_SUPPORT:
            cp = wall.char_price()
            if cp is None or cp <= 0:
                continue
            break_pct = (cp - price) / cp
            if break_pct > _BREAK_CLOSE_PCT:
                stop = round(cp * (1 + 0.001), 2)
                target = _break_target(
                    price, cp, wall_ctx.walls_below, direction="SHORT"
                )
                return RangeSignal(
                    signal_type=SIG_BREAK_CLOSE,
                    direction="SHORT",
                    entry_candidate=price,
                    stop_candidate=stop,
                    target_candidate=target,
                    executable=True,
                    retest_eligible=True,
                    retest_bars_available=_retest_bars(timeframe_minutes),
                    polling_risk=_has_polling_risk(timeframe_minutes),
                    notes=f"15m close below {wall.name} — break confirmed",
                )

    # ── No usable range for subsequent checks ─────────────────────────────────
    if rs.range_high is None or rs.range_low is None:
        return _no_data_signal("insufficient wall data")

    # ── RANGE_BREAK_RETEST_SHADOW: returned within 0.15% of broken level ──────
    # bars_since_break is provided by the caller which tracks multi-bar state.
    # Direction: res_dist small = short-break retest (support now above);
    #            sup_dist small = long-break retest (resistance now below).
    if bars_since_break is not None and bars_since_break > 0:
        res_dist_pct = wall_ctx.resistance_distance_pct or 0.0
        sup_dist_pct = wall_ctx.support_distance_pct or 0.0
        if res_dist_pct < _RETEST_PCT:
            polling_risk = bars_since_break < 2
            return RangeSignal(
                signal_type=SIG_RETEST_SHADOW,
                direction="SHORT",
                entry_candidate=None,
                stop_candidate=None,
                target_candidate=None,
                executable=False,
                retest_eligible=False,
                retest_bars_available=None,
                polling_risk=polling_risk,
                notes=f"Retest of broken support after {bars_since_break} bar(s)",
            )
        if sup_dist_pct < _RETEST_PCT:
            polling_risk = bars_since_break < 2
            return RangeSignal(
                signal_type=SIG_RETEST_SHADOW,
                direction="LONG",
                entry_candidate=None,
                stop_candidate=None,
                target_candidate=None,
                executable=False,
                retest_eligible=False,
                retest_bars_available=None,
                polling_risk=polling_risk,
                notes=f"Retest of broken resistance after {bars_since_break} bar(s)",
            )

    # ── RANGE_MIDDLE_NO_TRADE: price in the middle of the range ───────────────
    loc_pct = rs.location_pct
    if loc_pct is not None and _MIDDLE_LOW < loc_pct < _MIDDLE_HIGH:
        return RangeSignal(
            signal_type=SIG_MIDDLE,
            direction="NONE",
            entry_candidate=None,
            stop_candidate=None,
            target_candidate=None,
            executable=False,
            retest_eligible=False,
            retest_bars_available=None,
            polling_risk=False,
            notes="Price in middle of range — no edge",
        )

    # ── RANGE_REJECT: near wall, closed back inside ────────────────────────────
    res_pct = wall_ctx.resistance_distance_pct
    sup_pct = wall_ctx.support_distance_pct

    if res_pct is not None and res_pct < _NEAR_WALL_PCT and rs.location == LOC_NEAR_HIGH:
        return RangeSignal(
            signal_type=SIG_REJECT,
            direction="SHORT",
            entry_candidate=None,
            stop_candidate=None,
            target_candidate=None,
            executable=False,
            retest_eligible=False,
            retest_bars_available=None,
            polling_risk=False,
            notes="Price near resistance — monitoring for rejection close",
        )

    if sup_pct is not None and sup_pct < _NEAR_WALL_PCT and rs.location == LOC_NEAR_LOW:
        return RangeSignal(
            signal_type=SIG_REJECT,
            direction="LONG",
            entry_candidate=None,
            stop_candidate=None,
            target_candidate=None,
            executable=False,
            retest_eligible=False,
            retest_bars_available=None,
            polling_risk=False,
            notes="Price near support — monitoring for rejection/bounce close",
        )

    # ── RANGE_BOUNCE: away from wall, held ────────────────────────────────────
    if rs.location in (LOC_NEAR_HIGH, LOC_NEAR_LOW):
        direction = "SHORT" if rs.location == LOC_NEAR_HIGH else "LONG"
        return RangeSignal(
            signal_type=SIG_BOUNCE,
            direction=direction,
            entry_candidate=None,
            stop_candidate=None,
            target_candidate=None,
            executable=False,
            retest_eligible=False,
            retest_bars_available=None,
            polling_risk=False,
            notes="Price held away from opposite wall",
        )

    # ── Fallback ───────────────────────────────────────────────────────────────
    return _no_data_signal("location unclassified")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _locate_price(
    price: float,
    range_high: Optional[float],
    range_low: Optional[float],
) -> tuple[str, Optional[float]]:
    """Return (location_label, location_pct)."""
    if range_high is None or range_low is None:
        return LOC_MIDDLE, None

    width = range_high - range_low
    if width <= 0:
        return LOC_MIDDLE, 0.5

    if price > range_high:
        pct = (price - range_low) / width
        if (price - range_high) / range_high < _BREAK_CLOSE_PCT:
            return LOC_BREAKING_HIGH, min(pct, 1.0)
        return LOC_OUTSIDE_HIGH, pct

    if price < range_low:
        pct = (price - range_low) / width  # negative
        if (range_low - price) / range_low < _BREAK_CLOSE_PCT:
            return LOC_BREAKING_LOW, max(pct, 0.0)
        return LOC_OUTSIDE_LOW, pct

    pct = (price - range_low) / width  # 0.0–1.0

    if pct >= _MIDDLE_HIGH:
        return LOC_NEAR_HIGH, pct
    if pct <= _MIDDLE_LOW:
        return LOC_NEAR_LOW, pct
    return LOC_MIDDLE, pct


def _second_wall_above(wall_ctx: "WallContext") -> Optional["WallLevel"]:
    """Return the second-nearest wall above (first wall above nearest_resistance)."""
    walls = wall_ctx.walls_above
    return walls[1] if len(walls) >= 2 else None


def _second_wall_below(wall_ctx: "WallContext") -> Optional["WallLevel"]:
    """Return the second-nearest wall below (first wall below nearest_support)."""
    walls = wall_ctx.walls_below
    return walls[1] if len(walls) >= 2 else None


def _retest_bars(timeframe_minutes: int) -> int:
    """How many bars fit in a 30-minute retest window."""
    if timeframe_minutes <= 0:
        return 0
    return max(1, 30 // timeframe_minutes)


def _has_polling_risk(timeframe_minutes: int) -> bool:
    """True if the retest window (30m) is less than 2 poll cycles (60s at 30s polling)."""
    retest_window_seconds = 30 * 60
    return retest_window_seconds < (_POLL_INTERVAL_S * 2)


def _no_data_signal(notes: Optional[str] = None) -> RangeSignal:
    return RangeSignal(
        signal_type=SIG_NO_DATA,
        direction="NONE",
        entry_candidate=None,
        stop_candidate=None,
        target_candidate=None,
        executable=False,
        retest_eligible=False,
        retest_bars_available=None,
        polling_risk=False,
        notes=notes,
    )
