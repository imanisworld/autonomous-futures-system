"""
tests/test_wall_context.py

Tests for the WallContext journal-only layer.

Covers:
- WallLevel geometry (distance, pct_distance, char_price)
- build_wall_context: wall partitioning, nearest walls, distances
- wall_alignment shadow tags for all implemented values
- Zone freshness from zone_state
- Missing / None fields do not crash
- to_dict() is valid JSON
- All walls_above are above price; all walls_below are below price
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pytest

from context.wall_context import (
    WallLevel,
    WallContext,
    build_wall_context,
    KIND_RESISTANCE, KIND_SUPPORT, KIND_MAGNET, KIND_ZONE,
    SOURCE_PRICE, SOURCE_OPTIONS,
)


# ─── Minimal MarketState stub ─────────────────────────────────────────────────
# We don't import the real MarketState to avoid a heavy dependency chain.
# build_wall_context uses getattr with defaults, so a minimal stub is enough.

@dataclass
class _ORB:
    high: Optional[float] = None
    low: Optional[float] = None
    status: Optional[str] = None


@dataclass
class _PrevDay:
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None


@dataclass
class _Price:
    last: float = 0.0
    bid: float = 0.0
    ask: float = 0.0


@dataclass
class _OHLC:
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    timeframe: str = "15m"


@dataclass
class _KeyLevels:
    hod: Optional[float] = None
    lod: Optional[float] = None
    prev_week_high: Optional[float] = None
    prev_week_low: Optional[float] = None


@dataclass
class _VWAP:
    value: Optional[float] = None
    price_vs_vwap: Optional[str] = None


@dataclass
class _SD:
    supply_top: Optional[float] = None
    supply_bottom: Optional[float] = None
    supply_wavg: Optional[float] = None
    demand_top: Optional[float] = None
    demand_bottom: Optional[float] = None
    demand_wavg: Optional[float] = None


@dataclass
class _GEX:
    call_wall: Optional[float] = None
    put_wall: Optional[float] = None
    hvl: Optional[float] = None
    max_pain: Optional[float] = None
    ghost: Optional[float] = None
    gex_flip: Optional[float] = None
    updated_at: Optional[str] = None


@dataclass
class _State:
    instrument: str = "MNQ"
    timestamp: datetime = field(default_factory=lambda: datetime(2026, 6, 24, 14, 0, tzinfo=timezone.utc))
    price: _Price = field(default_factory=_Price)
    ohlc: _OHLC = field(default_factory=_OHLC)
    orb: _ORB = field(default_factory=_ORB)
    vwap: Optional[_VWAP] = None
    previous_day: _PrevDay = field(default_factory=_PrevDay)
    key_levels: Optional[_KeyLevels] = None
    sd: Optional[_SD] = None
    gex: Optional[_GEX] = None
    raw: dict = field(default_factory=dict)


def _state(price: float = 20000.0, **kwargs) -> _State:
    """Convenience: build a state with given price and keyword overrides."""
    s = _State()
    s.price.last = price
    s.ohlc.close = price
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


# ─── WallLevel geometry ───────────────────────────────────────────────────────

class TestWallLevelGeometry:

    def test_char_price_point_level(self):
        w = WallLevel(name="PDH", kind=KIND_RESISTANCE, source=SOURCE_PRICE, value=20100.0)
        assert w.char_price() == 20100.0

    def test_char_price_zone_uses_midpoint(self):
        w = WallLevel(name="SUPPLY_ZONE", kind=KIND_ZONE, source=SOURCE_PRICE,
                      value=None, upper=20150.0, lower=20050.0)
        assert w.char_price() == 20100.0

    def test_char_price_zone_prefers_wavg_value(self):
        w = WallLevel(name="SUPPLY_ZONE", kind=KIND_ZONE, source=SOURCE_PRICE,
                      value=20090.0, upper=20150.0, lower=20050.0)
        assert w.char_price() == 20090.0

    def test_char_price_no_data_returns_none(self):
        w = WallLevel(name="GHOST", kind="unknown", source=SOURCE_OPTIONS, value=None)
        assert w.char_price() is None

    def test_distance_to_point_level_above(self):
        w = WallLevel(name="ORB_HIGH", kind=KIND_RESISTANCE, source=SOURCE_PRICE, value=20100.0)
        assert w.distance_to(20000.0) == pytest.approx(100.0)

    def test_distance_to_point_level_below(self):
        w = WallLevel(name="ORB_LOW", kind=KIND_SUPPORT, source=SOURCE_PRICE, value=19900.0)
        assert w.distance_to(20000.0) == pytest.approx(100.0)

    def test_distance_to_zone_above(self):
        # price 20000, zone bottom 20050
        w = WallLevel(name="SUPPLY_ZONE", kind=KIND_ZONE, source=SOURCE_PRICE,
                      value=None, upper=20150.0, lower=20050.0)
        assert w.distance_to(20000.0) == pytest.approx(50.0)

    def test_distance_to_zone_below(self):
        # price 20000, zone top 19950
        w = WallLevel(name="DEMAND_ZONE", kind=KIND_ZONE, source=SOURCE_PRICE,
                      value=None, upper=19950.0, lower=19850.0)
        assert w.distance_to(20000.0) == pytest.approx(50.0)

    def test_distance_to_zone_inside_is_zero(self):
        w = WallLevel(name="SUPPLY_ZONE", kind=KIND_ZONE, source=SOURCE_PRICE,
                      value=None, upper=20050.0, lower=19950.0)
        assert w.distance_to(20000.0) == 0.0

    def test_pct_distance(self):
        w = WallLevel(name="PDH", kind=KIND_RESISTANCE, source=SOURCE_PRICE, value=20060.0)
        pct = w.pct_distance_to(20000.0)
        assert pct == pytest.approx(60.0 / 20000.0)

    def test_pct_distance_none_when_no_value(self):
        w = WallLevel(name="GHOST", kind="unknown", source=SOURCE_OPTIONS, value=None)
        assert w.pct_distance_to(20000.0) is None

    def test_to_dict_excludes_none_fields(self):
        w = WallLevel(name="PDH", kind=KIND_RESISTANCE, source=SOURCE_PRICE, value=20100.0)
        d = w.to_dict()
        assert "upper" not in d
        assert "lower" not in d
        assert d["name"] == "PDH"


# ─── build_wall_context: partitioning ────────────────────────────────────────

class TestWallPartitioning:

    def test_walls_above_are_above_price(self):
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=20100.0, low=19900.0)
        ctx = build_wall_context(s)
        for w in ctx.walls_above:
            cp = w.char_price()
            assert cp is None or cp > price, f"{w.name} char_price {cp} not above {price}"

    def test_walls_below_are_below_price(self):
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=20100.0, low=19900.0)
        ctx = build_wall_context(s)
        for w in ctx.walls_below:
            cp = w.char_price()
            assert cp is None or cp < price, f"{w.name} char_price {cp} not below {price}"

    def test_orb_high_in_walls_above(self):
        s = _state(20000.0)
        s.orb = _ORB(high=20100.0, low=19900.0)
        ctx = build_wall_context(s)
        names_above = [w.name for w in ctx.walls_above]
        assert "ORB_HIGH" in names_above

    def test_orb_low_in_walls_below(self):
        s = _state(20000.0)
        s.orb = _ORB(high=20100.0, low=19900.0)
        ctx = build_wall_context(s)
        names_below = [w.name for w in ctx.walls_below]
        assert "ORB_LOW" in names_below

    def test_nearest_resistance_is_closest_above(self):
        # ORB_HIGH=20050, PDH=20200 — ORB_HIGH is closer ceiling
        s = _state(20000.0)
        s.orb = _ORB(high=20050.0, low=19900.0)
        s.previous_day = _PrevDay(high=20200.0, low=19700.0)
        ctx = build_wall_context(s)
        assert ctx.nearest_resistance is not None
        assert ctx.nearest_resistance.name == "ORB_HIGH"

    def test_nearest_support_is_closest_below(self):
        # ORB_LOW=19950, PDL=19700 — ORB_LOW is closer floor
        s = _state(20000.0)
        s.orb = _ORB(high=20100.0, low=19950.0)
        s.previous_day = _PrevDay(high=20200.0, low=19700.0)
        ctx = build_wall_context(s)
        assert ctx.nearest_support is not None
        assert ctx.nearest_support.name == "ORB_LOW"

    def test_hvl_is_nearest_magnet_not_in_walls(self):
        s = _state(20000.0)
        s.gex = _GEX(hvl=20010.0)
        ctx = build_wall_context(s)
        assert ctx.nearest_magnet is not None
        assert ctx.nearest_magnet.name == "HVL"
        # magnets go to nearest_magnet, not walls_above/walls_below
        magnet_names_above = [w.name for w in ctx.walls_above]
        assert "HVL" not in magnet_names_above

    def test_distance_points_calculated(self):
        s = _state(20000.0)
        s.orb = _ORB(high=20100.0, low=19900.0)
        ctx = build_wall_context(s)
        assert ctx.resistance_distance_points == pytest.approx(100.0)
        assert ctx.support_distance_points == pytest.approx(100.0)

    def test_distance_pct_calculated(self):
        s = _state(20000.0)
        s.orb = _ORB(high=20100.0, low=19900.0)
        ctx = build_wall_context(s)
        assert ctx.resistance_distance_pct == pytest.approx(100.0 / 20000.0)
        assert ctx.support_distance_pct == pytest.approx(100.0 / 20000.0)

    def test_walls_above_sorted_nearest_first(self):
        s = _state(20000.0)
        s.orb = _ORB(high=20050.0)
        s.previous_day = _PrevDay(high=20200.0)
        ctx = build_wall_context(s)
        prices = [w.char_price() for w in ctx.walls_above if w.char_price() is not None]
        assert prices == sorted(prices)


# ─── wall_alignment shadow tags ───────────────────────────────────────────────

class TestWallAlignment:

    def test_no_wall_data_when_no_levels(self):
        s = _state(20000.0)
        # No orb, no previous_day, no key_levels, no sd, no gex
        ctx = build_wall_context(s)
        assert ctx.wall_alignment == "NO_WALL_DATA"

    def test_clear_path_when_walls_far_away(self):
        # ORB_HIGH 1% above, ORB_LOW 1% below — well beyond 0.5% threshold
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=price * 1.01, low=price * 0.99)
        ctx = build_wall_context(s)
        assert ctx.wall_alignment == "CLEAR_PATH"

    def test_into_resistance_when_close_to_ceiling(self):
        # ORB_HIGH 0.2% above — within 0.3% threshold
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=price * 1.002, low=price * 0.98)
        ctx = build_wall_context(s)
        assert ctx.wall_alignment == "INTO_RESISTANCE"

    def test_into_support_when_close_to_floor(self):
        # ORB_LOW 0.2% below — within 0.3% threshold
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=price * 1.02, low=price * 0.998)
        ctx = build_wall_context(s)
        assert ctx.wall_alignment == "INTO_SUPPORT"

    def test_pin_risk_when_close_to_hvl(self):
        # HVL within 0.1% — within 0.2% magnet threshold
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=price * 1.02, low=price * 0.98)
        s.gex = _GEX(hvl=price * 1.001)
        ctx = build_wall_context(s)
        assert ctx.wall_alignment == "PIN_RISK"

    def test_pin_risk_when_sandwiched_between_walls(self):
        # Both resistance and support within 0.3%
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=price * 1.002, low=price * 0.998)
        ctx = build_wall_context(s)
        assert ctx.wall_alignment == "PIN_RISK"

    def test_breaking_wall_when_resistance_is_below_price(self):
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=price * 0.998, low=price * 0.99, status="above")
        ctx = build_wall_context(s)
        assert ctx.wall_alignment == "BREAKING_WALL"

    def test_reclaiming_wall_from_orb_status(self):
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=price * 1.01, low=price * 0.99, status="reclaiming")
        ctx = build_wall_context(s)
        assert ctx.wall_alignment == "RECLAIMING_WALL"

    def test_reclaiming_wall_reclaimed_high(self):
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=price * 1.01, low=price * 0.99, status="reclaimed_high")
        ctx = build_wall_context(s)
        assert ctx.wall_alignment == "RECLAIMING_WALL"

    def test_rejecting_wall_from_orb_status(self):
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=price * 1.01, low=price * 0.99, status="rejecting")
        ctx = build_wall_context(s)
        assert ctx.wall_alignment == "REJECTING_WALL"

    def test_rejecting_wall_rejected_high(self):
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=price * 1.01, low=price * 0.99, status="rejected_high")
        ctx = build_wall_context(s)
        assert ctx.wall_alignment == "REJECTING_WALL"

    def test_orb_status_beats_proximity(self):
        # reclaim status overrides INTO_RESISTANCE even if close to wall
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=price * 1.001, low=price * 0.99, status="reclaiming")
        ctx = build_wall_context(s)
        assert ctx.wall_alignment == "RECLAIMING_WALL"


# ─── Zone freshness ───────────────────────────────────────────────────────────

class TestZoneFreshness:

    def test_zone_fresh_when_zone_state_fresh(self):
        s = _state(20000.0)
        s.sd = _SD(supply_top=20100.0, supply_bottom=20050.0)
        ctx = build_wall_context(s, zone_state="fresh")
        supply = next((w for w in ctx.walls_above if w.name == "SUPPLY_ZONE"), None)
        assert supply is not None
        assert supply.fresh is True

    def test_zone_not_fresh_when_zone_state_stale(self):
        s = _state(20000.0)
        s.sd = _SD(supply_top=20100.0, supply_bottom=20050.0)
        ctx = build_wall_context(s, zone_state="stale")
        supply = next((w for w in ctx.walls_above if w.name == "SUPPLY_ZONE"), None)
        assert supply is not None
        assert supply.fresh is False

    def test_zone_not_fresh_when_zone_state_used(self):
        s = _state(20000.0)
        s.sd = _SD(demand_top=19950.0, demand_bottom=19900.0)
        ctx = build_wall_context(s, zone_state="used")
        demand = next((w for w in ctx.walls_below if w.name == "DEMAND_ZONE"), None)
        assert demand is not None
        assert demand.fresh is False

    def test_zone_assumed_fresh_when_zone_state_none(self):
        s = _state(20000.0)
        s.sd = _SD(supply_top=20100.0, supply_bottom=20050.0)
        ctx = build_wall_context(s, zone_state=None)
        supply = next((w for w in ctx.walls_above if w.name == "SUPPLY_ZONE"), None)
        assert supply is not None
        assert supply.fresh is True


# ─── VWAP / options proxy safety ──────────────────────────────────────────────

class TestWallProxySafety:

    def test_vwap_is_normalized_as_regime_boundary(self):
        s = _state(20000.0)
        s.vwap = _VWAP(value=19995.0, price_vs_vwap="above")
        ctx = build_wall_context(s)
        assert ctx.nearest_magnet is not None
        assert ctx.nearest_magnet.name == "VWAP"
        assert ctx.nearest_magnet.kind == "regime_boundary"

    def test_stale_options_timestamp_marks_options_levels_stale(self):
        s = _state(20000.0)
        s.gex = _GEX(call_wall=20250.0, updated_at="2026-06-24T12:00:00+00:00")
        ctx = build_wall_context(s)
        call_wall = next(w for w in ctx.walls_above if w.name == "CALL_WALL")
        assert call_wall.fresh is False
        assert call_wall.stale is True

    def test_raw_bar_timestamp_is_not_used_as_options_freshness(self):
        s = _state(20000.0)
        s.raw = {"timestamp": "2026-06-24T12:00:00+00:00"}
        s.gex = _GEX(call_wall=20250.0)
        ctx = build_wall_context(s)
        call_wall = next(w for w in ctx.walls_above if w.name == "CALL_WALL")
        assert call_wall.fresh is True
        assert call_wall.stale is False

    def test_futures_options_proxy_not_direct_price_equivalent(self):
        s = _state(20000.0)
        s.raw = {"underlying": "QQQ"}
        s.gex = _GEX(call_wall=20050.0)
        ctx = build_wall_context(s)
        call_wall = next(w for w in ctx.walls_above if w.name == "CALL_WALL")
        assert call_wall.direct_price_equivalent is False
        assert call_wall.distance_mode == "percent_context"
        assert call_wall.source_symbol == "QQQ"
        assert ctx.resistance_distance_points is None
        assert ctx.resistance_distance_pct is not None


# ─── Robustness: missing fields ───────────────────────────────────────────────

class TestMissingFields:

    def test_no_crash_when_no_sd(self):
        s = _state(20000.0)
        s.sd = None
        ctx = build_wall_context(s)
        assert ctx.valid

    def test_no_crash_when_no_gex(self):
        s = _state(20000.0)
        s.gex = None
        ctx = build_wall_context(s)
        assert ctx.valid

    def test_no_crash_when_no_key_levels(self):
        s = _state(20000.0)
        s.key_levels = None
        ctx = build_wall_context(s)
        assert ctx.valid

    def test_no_crash_when_orb_values_none(self):
        s = _state(20000.0)
        s.orb = _ORB(high=None, low=None)
        ctx = build_wall_context(s)
        assert ctx.valid

    def test_no_crash_when_previous_day_values_none(self):
        s = _state(20000.0)
        s.previous_day = _PrevDay(high=None, low=None)
        ctx = build_wall_context(s)
        assert ctx.valid

    def test_wall_source_defaults_to_none_when_no_levels(self):
        s = _state(20000.0)
        ctx = build_wall_context(s)
        assert ctx.nearest_resistance is None
        assert ctx.nearest_support is None
        assert ctx.nearest_magnet is None

    def test_invalid_context_returned_on_bad_state(self):
        # State whose properties raise — build_wall_context must not raise itself
        class _Broken:
            @property
            def instrument(self):
                raise RuntimeError("boom")
            @property
            def price(self):
                raise RuntimeError("boom")
            @property
            def ohlc(self):
                raise RuntimeError("boom")
            @property
            def orb(self):
                raise RuntimeError("boom")
            @property
            def previous_day(self):
                raise RuntimeError("boom")
            @property
            def timestamp(self):
                raise RuntimeError("boom")
        ctx = build_wall_context(_Broken())  # type: ignore[arg-type]
        # Should return an invalid stub rather than raising
        assert not ctx.valid
        assert ctx.wall_alignment == "NO_WALL_DATA"

    def test_partial_zone_data_does_not_crash(self):
        # Only supply_top set, not supply_bottom
        s = _state(20000.0)
        s.sd = _SD(supply_top=20100.0)
        ctx = build_wall_context(s)
        assert ctx.valid


# ─── All levels present (integration) ────────────────────────────────────────

class TestFullPayload:

    def _full_state(self) -> _State:
        price = 20000.0
        s = _state(price)
        s.orb = _ORB(high=20080.0, low=19920.0, status="above")
        s.previous_day = _PrevDay(high=20150.0, low=19850.0)
        s.key_levels = _KeyLevels(hod=20090.0, lod=19910.0,
                                   prev_week_high=20300.0, prev_week_low=19700.0)
        s.sd = _SD(supply_top=20200.0, supply_bottom=20160.0, supply_wavg=20180.0,
                   demand_top=19840.0, demand_bottom=19800.0, demand_wavg=19820.0)
        s.gex = _GEX(call_wall=20250.0, put_wall=19750.0,
                     hvl=20010.0, max_pain=20050.0, ghost=20400.0, gex_flip=20100.0)
        return s

    def test_all_price_derived_walls_present(self):
        ctx = build_wall_context(self._full_state(), zone_state="fresh")
        all_names = [w.name for w in ctx.walls_above + ctx.walls_below]
        for expected in ["ORB_HIGH", "ORB_LOW", "PDH", "PDL", "HOD", "LOD",
                          "PWH", "PWL", "SUPPLY_ZONE", "DEMAND_ZONE"]:
            assert expected in all_names, f"{expected} missing from walls"

    def test_options_walls_present(self):
        ctx = build_wall_context(self._full_state())
        all_names = ([w.name for w in ctx.walls_above + ctx.walls_below]
                     + ([ctx.nearest_magnet.name] if ctx.nearest_magnet else []))
        for expected in ["CALL_WALL", "PUT_WALL"]:
            assert expected in all_names, f"{expected} missing"

    def test_magnets_accessible_via_nearest_magnet(self):
        ctx = build_wall_context(self._full_state())
        # HVL is closest magnet to 20000 (10pt away), max_pain is 50pt away
        assert ctx.nearest_magnet is not None
        assert ctx.nearest_magnet.name == "HVL"

    def test_gex_flip_not_in_walls_above_or_below(self):
        # GEX_FLIP is regime_boundary kind — goes to magnets, not walls_above/below
        ctx = build_wall_context(self._full_state())
        all_wall_names = [w.name for w in ctx.walls_above + ctx.walls_below]
        assert "GEX_FLIP" not in all_wall_names

    def test_valid_is_true(self):
        ctx = build_wall_context(self._full_state())
        assert ctx.valid
        assert ctx.invalid_reason is None


# ─── JSON serialization ───────────────────────────────────────────────────────

class TestSerialization:

    def test_to_dict_is_valid_json(self):
        s = _state(20000.0)
        s.orb = _ORB(high=20100.0, low=19900.0)
        s.gex = _GEX(hvl=20010.0, call_wall=20250.0, put_wall=19750.0)
        ctx = build_wall_context(s)
        d = ctx.to_dict()
        serialized = json.dumps(d)   # must not raise
        roundtripped = json.loads(serialized)
        assert roundtripped["wall_alignment"] == ctx.wall_alignment

    def test_to_dict_contains_required_keys(self):
        s = _state(20000.0)
        ctx = build_wall_context(s)
        d = ctx.to_dict()
        for key in ("symbol", "price", "timestamp", "wall_alignment",
                    "walls_above", "walls_below", "valid"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_distance_pct_rounded(self):
        s = _state(20000.0)
        s.orb = _ORB(high=20100.0, low=19900.0)
        ctx = build_wall_context(s)
        d = ctx.to_dict()
        # Pct values should be floats, not excessive precision
        pct = d.get("resistance_distance_pct")
        if pct is not None:
            assert isinstance(pct, float)
            # 6dp max
            assert len(str(pct).split(".")[-1]) <= 7

    def test_empty_state_serializes_without_crash(self):
        # An object with no attrs builds a valid-but-empty WallContext (no walls to see)
        class _Empty:
            pass
        ctx = build_wall_context(_Empty())  # type: ignore[arg-type]
        d = ctx.to_dict()
        # Must serialize cleanly regardless of validity
        json.dumps(d)   # must not raise
        assert "wall_alignment" in d
