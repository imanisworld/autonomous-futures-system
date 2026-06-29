"""
context/wall_context.py

Normalized structural wall layer — a unified map of nearby support, resistance,
magnet, and zone levels regardless of source (price-derived or options-derived).

JOURNAL-ONLY observational layer. It has no effect on trade decisions, risk
gates, sizing, or execution. It exists to record wall state on every TRADE /
NO_TRADE / RISK_REJECTED journal event so the relationship between structural
levels and outcomes can be measured over time.

Build entry point:
    build_wall_context(state: MarketState, *, zone_state: str | None) -> WallContext

Wall sources
------------
Price-derived (independent of options data):
    ORB_HIGH / ORB_LOW          opening-range ceiling / floor
    PDH / PDL                   prior-day high / low
    PWH / PWL                   prior-week high / low
    HOD / LOD                   running high / low of day
    SUPPLY_ZONE                 supply zone (top / bottom / wavg from Pine)
    DEMAND_ZONE                 demand zone

Options-derived (require a vendor/feed to be populated):
    CALL_WALL                   gamma call ceiling
    PUT_WALL                    gamma put floor
    HVL                         high-volume level — price magnet
    MAX_PAIN                    options settlement / pinning reference
    GHOST                       unproven gamma level — journal only
    GEX_FLIP                    regime boundary (NOT support/resistance)

wall_alignment shadow tags
--------------------------
    CLEAR_PATH          price has breathing room from all walls (> 0.5%)
    INTO_RESISTANCE     nearest resistance is within 0.3% above price
    INTO_SUPPORT        nearest support is within 0.3% below price
    RECLAIMING_WALL     ORB status signals a reclaim in progress
    REJECTING_WALL      ORB status signals a rejection in progress
    PIN_RISK            price within 0.2% of a magnet (HVL / MAX_PAIN)
    NO_WALL_DATA        no levels available from any source

Note: BREAKING_WALL is reserved for the range-signal layer (requires multi-bar
state to distinguish a fresh break from normal price-above-level positioning).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from context.market_context import MarketState

# ── Kind / source constants ────────────────────────────────────────────────────

KIND_RESISTANCE = "resistance"
KIND_SUPPORT = "support"
KIND_MAGNET = "magnet"
KIND_ZONE = "zone"
KIND_REGIME = "regime_boundary"
KIND_UNKNOWN = "unknown"

SOURCE_PRICE = "price"
SOURCE_OPTIONS = "options"

# ── Proximity thresholds ───────────────────────────────────────────────────────
_INTO_WALL_PCT = 0.003   # 0.3% — INTO_RESISTANCE / INTO_SUPPORT trigger
_PIN_RISK_PCT = 0.002    # 0.2% — PIN_RISK trigger on a magnet
_CLEAR_PATH_PCT = 0.005  # 0.5% — below this is "not clear"

# ── ORB status strings that signal reclaim / rejection ────────────────────────
_RECLAIM_STATUSES = {"reclaiming", "reclaimed_high", "reclaimed_low", "reclaim"}
_REJECT_STATUSES = {"rejecting", "rejected_high", "rejected_low", "reject"}


# ─── WallLevel ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WallLevel:
    """A single structural price level or zone."""

    name: str
    kind: str               # resistance | support | magnet | zone | regime_boundary | unknown
    source: str             # price | options
    value: Optional[float]  # point level or zone midpoint; None if zone-only
    upper: Optional[float] = None   # zone upper edge
    lower: Optional[float] = None   # zone lower edge
    strength: str = "unknown"
    fresh: bool = True
    notes: Optional[str] = None

    # ── Geometry helpers ──────────────────────────────────────────────────────

    def char_price(self) -> Optional[float]:
        """Characteristic price used for above/below partitioning."""
        if self.value is not None:
            return self.value
        if self.upper is not None and self.lower is not None:
            return (self.upper + self.lower) / 2.0
        return self.upper or self.lower

    def distance_to(self, price: float) -> Optional[float]:
        """Absolute point distance from price to the nearest edge of this level."""
        if self.value is not None:
            return abs(price - self.value)
        # Zone: zero if price is inside; distance to nearest edge otherwise
        if self.upper is not None and self.lower is not None:
            if price > self.upper:
                return price - self.upper
            if price < self.lower:
                return self.lower - price
            return 0.0
        return None

    def pct_distance_to(self, price: float) -> Optional[float]:
        """Fractional distance from price to this level (distance / price)."""
        d = self.distance_to(price)
        if d is None or price == 0:
            return None
        return d / price

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        d = asdict(self)
        # Strip None values except required identity fields
        return {k: v for k, v in d.items()
                if v is not None or k in {"name", "kind", "source", "value"}}


# ─── WallContext ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WallContext:
    """
    Normalized map of structural walls around the current price.

    All levels are unified to the same schema — downstream code does not need
    to know whether a wall came from ORB, supply/demand, or a GEX feed.

    JOURNAL-ONLY. No gate or execution logic reads this object.
    """

    symbol: str
    price: float
    timestamp: datetime

    # ── Nearest walls (by characteristic price proximity to current price) ────
    nearest_resistance: Optional[WallLevel]
    nearest_support: Optional[WallLevel]
    nearest_magnet: Optional[WallLevel]

    # ── Point distances ───────────────────────────────────────────────────────
    resistance_distance_points: Optional[float]
    support_distance_points: Optional[float]
    magnet_distance_points: Optional[float]

    # ── Percentage distances ──────────────────────────────────────────────────
    resistance_distance_pct: Optional[float]
    support_distance_pct: Optional[float]
    magnet_distance_pct: Optional[float]

    # ── Full sorted wall lists ────────────────────────────────────────────────
    walls_above: list[WallLevel]   # nearest first (ascending by characteristic price)
    walls_below: list[WallLevel]   # nearest first (descending by characteristic price)

    # ── Shadow alignment tag ──────────────────────────────────────────────────
    wall_alignment: str

    # ── Validity ──────────────────────────────────────────────────────────────
    valid: bool
    invalid_reason: Optional[str] = None

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        def _lvl(w: Optional[WallLevel]) -> Optional[dict]:
            return w.to_dict() if w is not None else None

        def _rnd(v: Optional[float]) -> Optional[float]:
            return round(v, 6) if v is not None else None

        return {
            "symbol": self.symbol,
            "price": self.price,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "wall_alignment": self.wall_alignment,
            "nearest_resistance": _lvl(self.nearest_resistance),
            "nearest_support": _lvl(self.nearest_support),
            "nearest_magnet": _lvl(self.nearest_magnet),
            "resistance_distance_points": self.resistance_distance_points,
            "support_distance_points": self.support_distance_points,
            "magnet_distance_points": self.magnet_distance_points,
            "resistance_distance_pct": _rnd(self.resistance_distance_pct),
            "support_distance_pct": _rnd(self.support_distance_pct),
            "magnet_distance_pct": _rnd(self.magnet_distance_pct),
            "walls_above": [w.to_dict() for w in self.walls_above],
            "walls_below": [w.to_dict() for w in self.walls_below],
            "valid": self.valid,
            "invalid_reason": self.invalid_reason,
        }


# ─── Builder ──────────────────────────────────────────────────────────────────

def build_wall_context(
    state: "MarketState",
    *,
    zone_state: Optional[str] = None,
) -> WallContext:
    """
    Build a WallContext from the current MarketState.

    Args:
        state:      current MarketState (from build_market_state / webhook payload)
        zone_state: payload zone_state field ("fresh" | "used" | "stale") — not
                    stored in MarketState, so passed separately. None = unknown.

    Returns a valid WallContext or an invalid stub on error. Never raises.
    """
    try:
        return _build(state, zone_state=zone_state)
    except Exception as exc:
        try:
            _sym = str(state.instrument)  # type: ignore[attr-defined]
        except Exception:
            _sym = "UNKNOWN"
        try:
            _price = _safe_price(state)
        except Exception:
            _price = 0.0
        try:
            _ts = state.timestamp  # type: ignore[attr-defined]
        except Exception:
            _ts = datetime.now()
        return WallContext(
            symbol=_sym,
            price=_price,
            timestamp=_ts,
            nearest_resistance=None,
            nearest_support=None,
            nearest_magnet=None,
            resistance_distance_points=None,
            support_distance_points=None,
            magnet_distance_points=None,
            resistance_distance_pct=None,
            support_distance_pct=None,
            magnet_distance_pct=None,
            walls_above=[],
            walls_below=[],
            wall_alignment="NO_WALL_DATA",
            valid=False,
            invalid_reason=f"build_wall_context failed: {exc}",
        )


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _build(state: "MarketState", *, zone_state: Optional[str]) -> WallContext:
    price = _safe_price(state)
    symbol = getattr(state, "instrument", "UNKNOWN")
    ts = getattr(state, "timestamp", datetime.now())

    candidates: list[WallLevel] = []

    # ── 1. ORB levels ──────────────────────────────────────────────────────────
    orb = getattr(state, "orb", None)
    if orb is not None:
        if getattr(orb, "high", None) is not None:
            candidates.append(WallLevel(
                name="ORB_HIGH", kind=KIND_RESISTANCE, source=SOURCE_PRICE,
                value=orb.high, notes="intraday ceiling",
            ))
        if getattr(orb, "low", None) is not None:
            candidates.append(WallLevel(
                name="ORB_LOW", kind=KIND_SUPPORT, source=SOURCE_PRICE,
                value=orb.low, notes="intraday floor",
            ))
    orb_status = str(getattr(orb, "status", None) or "").lower()

    # ── 2. Prior-day levels ────────────────────────────────────────────────────
    prev = getattr(state, "previous_day", None)
    if prev is not None:
        if getattr(prev, "high", None) is not None:
            candidates.append(WallLevel(
                name="PDH", kind=KIND_RESISTANCE, source=SOURCE_PRICE,
                value=prev.high, notes="prior-day high",
            ))
        if getattr(prev, "low", None) is not None:
            candidates.append(WallLevel(
                name="PDL", kind=KIND_SUPPORT, source=SOURCE_PRICE,
                value=prev.low, notes="prior-day low",
            ))

    # ── 3. Key levels: HOD/LOD, prior-week ────────────────────────────────────
    kl = getattr(state, "key_levels", None)
    if kl is not None:
        if getattr(kl, "hod", None) is not None:
            candidates.append(WallLevel(
                name="HOD", kind=KIND_RESISTANCE, source=SOURCE_PRICE,
                value=kl.hod, notes="high of day",
            ))
        if getattr(kl, "lod", None) is not None:
            candidates.append(WallLevel(
                name="LOD", kind=KIND_SUPPORT, source=SOURCE_PRICE,
                value=kl.lod, notes="low of day",
            ))
        if getattr(kl, "prev_week_high", None) is not None:
            candidates.append(WallLevel(
                name="PWH", kind=KIND_RESISTANCE, source=SOURCE_PRICE,
                value=kl.prev_week_high, strength="htf", notes="prior-week high",
            ))
        if getattr(kl, "prev_week_low", None) is not None:
            candidates.append(WallLevel(
                name="PWL", kind=KIND_SUPPORT, source=SOURCE_PRICE,
                value=kl.prev_week_low, strength="htf", notes="prior-week low",
            ))

    # ── 4. Supply / demand zones ───────────────────────────────────────────────
    sd = getattr(state, "sd", None)
    zone_fresh = _zone_fresh(zone_state)

    if sd is not None:
        s_top = getattr(sd, "supply_top", None)
        s_bot = getattr(sd, "supply_bottom", None)
        s_avg = getattr(sd, "supply_wavg", None)
        if s_top is not None or s_bot is not None:
            candidates.append(WallLevel(
                name="SUPPLY_ZONE", kind=KIND_ZONE, source=SOURCE_PRICE,
                value=s_avg,
                upper=s_top,
                lower=s_bot,
                fresh=zone_fresh,
                notes="price-derived supply resistance",
            ))

        d_top = getattr(sd, "demand_top", None)
        d_bot = getattr(sd, "demand_bottom", None)
        d_avg = getattr(sd, "demand_wavg", None)
        if d_top is not None or d_bot is not None:
            candidates.append(WallLevel(
                name="DEMAND_ZONE", kind=KIND_ZONE, source=SOURCE_PRICE,
                value=d_avg,
                upper=d_top,
                lower=d_bot,
                fresh=zone_fresh,
                notes="price-derived demand support",
            ))

    # ── 5. Options-derived levels ──────────────────────────────────────────────
    gex = getattr(state, "gex", None)
    if gex is not None:
        if getattr(gex, "call_wall", None) is not None:
            candidates.append(WallLevel(
                name="CALL_WALL", kind=KIND_RESISTANCE, source=SOURCE_OPTIONS,
                value=gex.call_wall, notes="gamma call ceiling",
            ))
        if getattr(gex, "put_wall", None) is not None:
            candidates.append(WallLevel(
                name="PUT_WALL", kind=KIND_SUPPORT, source=SOURCE_OPTIONS,
                value=gex.put_wall, notes="gamma put floor",
            ))
        if getattr(gex, "hvl", None) is not None:
            candidates.append(WallLevel(
                name="HVL", kind=KIND_MAGNET, source=SOURCE_OPTIONS,
                value=gex.hvl, notes="high-volume magnet",
            ))
        if getattr(gex, "max_pain", None) is not None:
            candidates.append(WallLevel(
                name="MAX_PAIN", kind=KIND_MAGNET, source=SOURCE_OPTIONS,
                value=gex.max_pain, notes="options settlement reference",
            ))
        if getattr(gex, "ghost", None) is not None:
            candidates.append(WallLevel(
                name="GHOST", kind=KIND_UNKNOWN, source=SOURCE_OPTIONS,
                value=gex.ghost, notes="unproven gamma level — observe only",
            ))
        if getattr(gex, "gex_flip", None) is not None:
            candidates.append(WallLevel(
                name="GEX_FLIP", kind=KIND_REGIME, source=SOURCE_OPTIONS,
                value=gex.gex_flip,
                notes="regime boundary — not support/resistance",
            ))

    # ── 6. Partition walls: above vs below ────────────────────────────────────
    walls_above: list[WallLevel] = []
    walls_below: list[WallLevel] = []
    magnets: list[WallLevel] = []

    for lvl in candidates:
        if lvl.kind in (KIND_MAGNET, KIND_REGIME, KIND_UNKNOWN):
            magnets.append(lvl)
            continue
        cp = lvl.char_price()
        if cp is None:
            continue
        if cp > price:
            walls_above.append(lvl)
        elif cp < price:
            walls_below.append(lvl)
        # cp == price: price is exactly at this level — include in both
        else:
            walls_above.append(lvl)
            walls_below.append(lvl)

    # Sort: walls_above ascending (nearest ceiling first),
    #       walls_below descending (nearest floor first)
    walls_above.sort(key=lambda l: l.char_price() or float("inf"))
    walls_below.sort(key=lambda l: -(l.char_price() or 0.0))

    # ── 7. Nearest walls + distances ─────────────────────────────────────────
    nearest_res = walls_above[0] if walls_above else None
    nearest_sup = walls_below[0] if walls_below else None
    nearest_mag = (
        min(magnets, key=lambda m: m.pct_distance_to(price) or float("inf"))
        if magnets else None
    )

    res_pts = nearest_res.distance_to(price) if nearest_res else None
    sup_pts = nearest_sup.distance_to(price) if nearest_sup else None
    mag_pts = nearest_mag.distance_to(price) if nearest_mag else None

    res_pct = nearest_res.pct_distance_to(price) if nearest_res else None
    sup_pct = nearest_sup.pct_distance_to(price) if nearest_sup else None
    mag_pct = nearest_mag.pct_distance_to(price) if nearest_mag else None

    # ── 8. Wall alignment tag ─────────────────────────────────────────────────
    alignment = _compute_alignment(
        walls_above=walls_above,
        walls_below=walls_below,
        nearest_res=nearest_res,
        nearest_sup=nearest_sup,
        nearest_mag=nearest_mag,
        res_pct=res_pct,
        sup_pct=sup_pct,
        mag_pct=mag_pct,
        orb_status=orb_status,
    )

    return WallContext(
        symbol=symbol,
        price=price,
        timestamp=ts,
        nearest_resistance=nearest_res,
        nearest_support=nearest_sup,
        nearest_magnet=nearest_mag,
        resistance_distance_points=res_pts,
        support_distance_points=sup_pts,
        magnet_distance_points=mag_pts,
        resistance_distance_pct=res_pct,
        support_distance_pct=sup_pct,
        magnet_distance_pct=mag_pct,
        walls_above=walls_above,
        walls_below=walls_below,
        wall_alignment=alignment,
        valid=True,
    )


def _compute_alignment(
    *,
    walls_above: list[WallLevel],
    walls_below: list[WallLevel],
    nearest_res: Optional[WallLevel],
    nearest_sup: Optional[WallLevel],
    nearest_mag: Optional[WallLevel],
    res_pct: Optional[float],
    sup_pct: Optional[float],
    mag_pct: Optional[float],
    orb_status: str,
) -> str:
    has_walls = bool(walls_above or walls_below or nearest_mag)
    if not has_walls:
        return "NO_WALL_DATA"

    # ORB status is the highest-confidence source for reclaim / reject
    if orb_status in _RECLAIM_STATUSES:
        return "RECLAIMING_WALL"
    if orb_status in _REJECT_STATUSES:
        return "REJECTING_WALL"

    # Magnet PIN_RISK overrides directional proximity tags
    if mag_pct is not None and mag_pct < _PIN_RISK_PCT:
        return "PIN_RISK"

    into_res = res_pct is not None and res_pct < _INTO_WALL_PCT
    into_sup = sup_pct is not None and sup_pct < _INTO_WALL_PCT

    # Sandwiched between resistance and support both within threshold → PIN_RISK
    if into_res and into_sup:
        return "PIN_RISK"
    if into_res:
        return "INTO_RESISTANCE"
    if into_sup:
        return "INTO_SUPPORT"

    clear_above = res_pct is None or res_pct >= _CLEAR_PATH_PCT
    clear_below = sup_pct is None or sup_pct >= _CLEAR_PATH_PCT
    if clear_above and clear_below:
        return "CLEAR_PATH"

    # Has walls but none within the strict thresholds
    return "CLEAR_PATH"


def _safe_price(state: "MarketState") -> float:
    try:
        return float(state.price.last)
    except Exception:
        try:
            return float(state.ohlc.close)
        except Exception:
            return 0.0


def _zone_fresh(zone_state: Optional[str]) -> bool:
    if zone_state is None:
        return True   # assume fresh when unknown
    return str(zone_state).lower() == "fresh"
