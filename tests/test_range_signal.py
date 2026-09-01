"""
tests/test_range_signal.py

Unit tests for context/range_signal.py.

All tests are stateless; WallContext objects are constructed directly from
the frozen dataclasses so the test suite carries no heavy import chain.

Key invariants verified:
  - executable=True ONLY for RANGE_BREAK_CLOSE
  - build_* functions never raise (fail-soft / return stubs)
  - BREAK_CLOSE detected via KIND check on walls_below / walls_above
  - RETEST direction: res_dist small → SHORT, sup_dist small → LONG
  - wall_source picks highest-priority named wall
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from context.wall_context import (
    KIND_RESISTANCE,
    KIND_SUPPORT,
    KIND_MAGNET,
    KIND_ZONE,
    SOURCE_PRICE,
    SOURCE_OPTIONS,
    WallContext,
    WallLevel,
)
from context.range_signal import (
    LOC_BREAKING_HIGH,
    LOC_BREAKING_LOW,
    LOC_MIDDLE,
    LOC_NEAR_HIGH,
    LOC_NEAR_LOW,
    LOC_OUTSIDE_HIGH,
    LOC_OUTSIDE_LOW,
    SIG_BOUNCE,
    SIG_BREAK_CLOSE,
    SIG_MIDDLE,
    SIG_NO_DATA,
    SIG_REJECT,
    SIG_RETEST_SHADOW,
    RangeSignal,
    RangeState,
    build_range_signal,
    build_range_state,
    _locate_price,
    _retest_bars,
    _has_polling_risk,
)

_NOW = datetime(2026, 6, 24, 14, 30, 0, tzinfo=timezone.utc)


# ─── WallContext factory ───────────────────────────────────────────────────────

def _wl(name: str, kind: str, value: float, *, fresh: bool = True) -> WallLevel:
    return WallLevel(name=name, kind=kind, source=SOURCE_PRICE, value=value, fresh=fresh)


def _make_ctx(
    price: float,
    walls_above: list[WallLevel] | None = None,
    walls_below: list[WallLevel] | None = None,
    *,
    alignment: str = "CLEAR_PATH",
) -> WallContext:
    """
    Build a minimal WallContext for testing.

    Distances are computed from the given wall lists; nearest walls are
    the first entries in each sorted list.
    """
    wa = sorted(walls_above or [], key=lambda l: l.char_price() or float("inf"))
    wb = sorted(walls_below or [], key=lambda l: -(l.char_price() or 0.0))

    nearest_res = wa[0] if wa else None
    nearest_sup = wb[0] if wb else None

    res_pts = nearest_res.distance_to(price) if nearest_res else None
    sup_pts = nearest_sup.distance_to(price) if nearest_sup else None
    res_pct = nearest_res.pct_distance_to(price) if nearest_res else None
    sup_pct = nearest_sup.pct_distance_to(price) if nearest_sup else None

    return WallContext(
        symbol="MES",
        price=price,
        timestamp=_NOW,
        nearest_resistance=nearest_res,
        nearest_support=nearest_sup,
        nearest_magnet=None,
        resistance_distance_points=res_pts,
        support_distance_points=sup_pts,
        magnet_distance_points=None,
        resistance_distance_pct=res_pct,
        support_distance_pct=sup_pct,
        magnet_distance_pct=None,
        walls_above=wa,
        walls_below=wb,
        wall_alignment=alignment,
        valid=True,
    )


def _empty_ctx(price: float = 5900.0) -> WallContext:
    return _make_ctx(price, walls_above=[], walls_below=[])


# ─── RangeState tests ─────────────────────────────────────────────────────────


def test_range_state_no_walls_returns_stub():
    ctx = _empty_ctx(5900.0)
    rs = build_range_state(ctx, "RANGE_BOUND")
    assert rs.range_high is None
    assert rs.range_low is None
    assert rs.location_pct is None
    assert rs.wall_source == "NONE"
    assert rs.regime == "RANGE_BOUND"


def test_range_state_price_in_middle():
    # price = 5900 between 5880 (support) and 5920 (resistance)
    # location_pct = (5900-5880)/(5920-5880) = 0.50 → MIDDLE
    ctx = _make_ctx(
        5900.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5920.0)],
        walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    assert rs.range_high == 5920.0
    assert rs.range_low == 5880.0
    assert rs.range_width == pytest.approx(40.0)
    assert rs.range_midpoint == pytest.approx(5900.0)
    assert rs.location == LOC_MIDDLE
    assert rs.location_pct == pytest.approx(0.5)


def test_range_state_price_near_high():
    # price=5910 → pct = (5910-5880)/(5920-5880) = 0.75 → NEAR_HIGH
    ctx = _make_ctx(
        5910.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5920.0)],
        walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
    )
    rs = build_range_state(ctx, "CHOPPY")
    assert rs.location == LOC_NEAR_HIGH
    assert rs.location_pct == pytest.approx(0.75)


def test_range_state_price_near_low():
    # price=5885 → pct = (5885-5880)/(5920-5880) = 0.125 → NEAR_LOW
    ctx = _make_ctx(
        5885.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5920.0)],
        walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    assert rs.location == LOC_NEAR_LOW
    assert rs.location_pct == pytest.approx(0.125)


def test_range_state_wall_source_priority_orb_over_pdh():
    # ORB_HIGH and PDH both present; ORB_HIGH wins
    ctx = _make_ctx(
        5900.0,
        walls_above=[
            _wl("PDH", KIND_RESISTANCE, 5925.0),
            _wl("ORB_HIGH", KIND_RESISTANCE, 5920.0),
        ],
        walls_below=[_wl("ORB_LOW", KIND_SUPPORT, 5880.0)],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    assert rs.wall_source == "ORB_HIGH"


def test_range_state_wall_fresh_flag_propagates():
    ctx = _make_ctx(
        5900.0,
        walls_above=[_wl("ORB_HIGH", KIND_RESISTANCE, 5920.0, fresh=False)],
        walls_below=[_wl("ORB_LOW", KIND_SUPPORT, 5880.0)],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    assert rs.wall_fresh is False


def test_range_state_to_dict_keys():
    ctx = _make_ctx(
        5900.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5920.0)],
        walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    d = rs.to_dict()
    for key in ("regime", "range_high", "range_low", "range_midpoint",
                 "range_width", "price", "location", "location_pct",
                 "wall_source", "wall_fresh"):
        assert key in d, f"missing key: {key}"


def test_range_state_never_raises_on_empty_ctx():
    ctx = _empty_ctx()
    rs = build_range_state(ctx, "CHOPPY")  # must not raise
    assert isinstance(rs, RangeState)


def test_range_state_choppy_regime_preserved():
    ctx = _empty_ctx()
    rs = build_range_state(ctx, "CHOPPY")
    assert rs.regime == "CHOPPY"


# ─── _locate_price helper ─────────────────────────────────────────────────────


def test_locate_price_no_walls():
    loc, pct = _locate_price(5900.0, None, None)
    assert loc == LOC_MIDDLE
    assert pct is None


def test_locate_price_zero_width():
    loc, pct = _locate_price(5900.0, 5900.0, 5900.0)
    assert loc == LOC_MIDDLE
    assert pct == pytest.approx(0.5)


def test_locate_price_middle():
    loc, pct = _locate_price(5900.0, 5920.0, 5880.0)
    assert loc == LOC_MIDDLE
    assert pct == pytest.approx(0.5)


def test_locate_price_near_high():
    loc, pct = _locate_price(5910.0, 5920.0, 5880.0)
    assert loc == LOC_NEAR_HIGH
    assert pct == pytest.approx(0.75)


def test_locate_price_near_low():
    loc, pct = _locate_price(5885.0, 5920.0, 5880.0)
    assert loc == LOC_NEAR_LOW
    assert pct == pytest.approx(0.125)


def test_locate_price_breaking_high():
    # Just above range_high by less than 0.15%: BREAKING_HIGH
    # 5920 * 0.0015 = 8.88 pts; price = 5920 + 5 = 5925 (< 8.88 pts above)
    loc, pct = _locate_price(5925.0, 5920.0, 5880.0)
    assert loc == LOC_BREAKING_HIGH


def test_locate_price_outside_high():
    # price = 5930, range_high = 5920: (5930-5920)/5920 ≈ 0.0017 > 0.0015
    loc, pct = _locate_price(5930.0, 5920.0, 5880.0)
    assert loc == LOC_OUTSIDE_HIGH


def test_locate_price_outside_low():
    # price = 5870, range_low = 5880: (5880-5870)/5880 ≈ 0.0017 > 0.0015
    loc, pct = _locate_price(5870.0, 5920.0, 5880.0)
    assert loc == LOC_OUTSIDE_LOW


# ─── RangeSignal — RANGE_BREAK_CLOSE ─────────────────────────────────────────


def _ctx_break_long() -> WallContext:
    """
    Price 5930 closed above ORB_HIGH 5920 — ORB_HIGH (KIND_RESISTANCE) is now
    in walls_below.  PDH 5950 is the next resistance above.
    """
    return _make_ctx(
        5930.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5950.0)],
        walls_below=[
            _wl("ORB_HIGH", KIND_RESISTANCE, 5920.0),
            _wl("ORB_LOW", KIND_SUPPORT, 5880.0),
        ],
    )


def _ctx_break_short() -> WallContext:
    """
    Price 5870 closed below ORB_LOW 5880 — ORB_LOW (KIND_SUPPORT) is now
    in walls_above.  PWL 5850 is next support below.
    """
    return _make_ctx(
        5870.0,
        walls_above=[
            _wl("ORB_LOW", KIND_SUPPORT, 5880.0),
            _wl("PDH", KIND_RESISTANCE, 5920.0),
        ],
        walls_below=[_wl("PWL", KIND_SUPPORT, 5850.0)],
    )


def test_break_close_long_detected():
    ctx = _ctx_break_long()
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    assert sig.signal_type == SIG_BREAK_CLOSE
    assert sig.direction == "LONG"
    assert sig.executable is True


def test_break_close_long_has_target_and_stop():
    ctx = _ctx_break_long()
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    assert sig.stop_candidate is not None
    assert sig.target_candidate is not None
    # Stop below the broken level (5920)
    assert sig.stop_candidate < 5920.0
    # Target at next resistance (PDH=5950)
    assert sig.target_candidate == pytest.approx(5950.0, abs=1.0)


def test_break_close_short_detected():
    ctx = _ctx_break_short()
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    assert sig.signal_type == SIG_BREAK_CLOSE
    assert sig.direction == "SHORT"
    assert sig.executable is True


def test_break_close_short_has_target_and_stop():
    ctx = _ctx_break_short()
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    assert sig.stop_candidate is not None
    assert sig.target_candidate is not None
    # Stop above the broken level (5880)
    assert sig.stop_candidate > 5880.0
    # Target at next support (PWL=5850)
    assert sig.target_candidate == pytest.approx(5850.0, abs=1.0)


def test_break_close_retest_eligible():
    ctx = _ctx_break_long()
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    assert sig.retest_eligible is True
    assert sig.retest_bars_available is not None and sig.retest_bars_available >= 1


def test_break_close_not_fired_when_margin_too_small():
    # price = 5921 — only 1pt above ORB_HIGH 5920 ≈ 0.017% < 0.15% threshold
    ctx = _make_ctx(
        5921.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5950.0)],
        walls_below=[
            _wl("ORB_HIGH", KIND_RESISTANCE, 5920.0),
            _wl("ORB_LOW", KIND_SUPPORT, 5880.0),
        ],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    assert sig.signal_type != SIG_BREAK_CLOSE


# ─── RangeSignal — RANGE_BREAK_RETEST_SHADOW ─────────────────────────────────


def test_retest_long_when_near_broken_resistance_below():
    """
    After a LONG break: broken resistance (5920) is in walls_below.
    Price retests at 5921 — sup_dist_pct is tiny → RETEST LONG.
    """
    ctx = _make_ctx(
        5921.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5950.0)],
        walls_below=[
            _wl("ORB_HIGH", KIND_RESISTANCE, 5920.0),
            _wl("ORB_LOW", KIND_SUPPORT, 5880.0),
        ],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx, bars_since_break=3)
    assert sig.signal_type == SIG_RETEST_SHADOW
    assert sig.direction == "LONG"
    assert sig.executable is False


def test_retest_short_when_near_broken_support_above():
    """
    After a SHORT break: broken support (5880) is in walls_above.
    Price retests at 5879 — res_dist_pct is tiny → RETEST SHORT.
    """
    ctx = _make_ctx(
        5879.0,
        walls_above=[
            _wl("ORB_LOW", KIND_SUPPORT, 5880.0),
            _wl("PDH", KIND_RESISTANCE, 5920.0),
        ],
        walls_below=[_wl("PWL", KIND_SUPPORT, 5850.0)],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx, bars_since_break=2)
    assert sig.signal_type == SIG_RETEST_SHADOW
    assert sig.direction == "SHORT"
    assert sig.executable is False


def test_retest_polling_risk_when_bars_since_break_lt_2():
    ctx = _make_ctx(
        5879.0,
        walls_above=[
            _wl("ORB_LOW", KIND_SUPPORT, 5880.0),
            _wl("PDH", KIND_RESISTANCE, 5920.0),
        ],
        walls_below=[_wl("PWL", KIND_SUPPORT, 5850.0)],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx, bars_since_break=1)
    assert sig.signal_type == SIG_RETEST_SHADOW
    assert sig.polling_risk is True


def test_retest_not_fired_without_bars_since_break():
    ctx = _make_ctx(
        5879.0,
        walls_above=[
            _wl("ORB_LOW", KIND_SUPPORT, 5880.0),
            _wl("PDH", KIND_RESISTANCE, 5920.0),
        ],
        walls_below=[_wl("PWL", KIND_SUPPORT, 5850.0)],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    # bars_since_break=None → retest gate disabled
    sig = build_range_signal(rs, ctx, bars_since_break=None)
    assert sig.signal_type != SIG_RETEST_SHADOW


# ─── RangeSignal — RANGE_MIDDLE_NO_TRADE ─────────────────────────────────────


def test_middle_no_trade_at_50_pct():
    ctx = _make_ctx(
        5900.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5920.0)],
        walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    assert sig.signal_type == SIG_MIDDLE
    assert sig.direction == "NONE"
    assert sig.executable is False


def test_middle_no_trade_at_40_pct():
    # pct = (5896 - 5880) / 40 = 0.4 → between 0.35 and 0.65
    ctx = _make_ctx(
        5896.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5920.0)],
        walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
    )
    rs = build_range_state(ctx, "CHOPPY")
    sig = build_range_signal(rs, ctx)
    assert sig.signal_type == SIG_MIDDLE


# ─── RangeSignal — RANGE_REJECT ──────────────────────────────────────────────


def test_reject_short_near_resistance():
    # price=5910 → pct=0.75 (NEAR_HIGH), res_dist=(5920-5910)/5910≈0.0017 < 0.0025
    ctx = _make_ctx(
        5910.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5920.0)],
        walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    assert sig.signal_type == SIG_REJECT
    assert sig.direction == "SHORT"
    assert sig.executable is False


def test_reject_long_near_support():
    # price=5883 → pct=0.075 (NEAR_LOW), sup_dist=(5883-5880)/5883≈0.00051 < 0.0025
    ctx = _make_ctx(
        5883.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5920.0)],
        walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    assert sig.signal_type == SIG_REJECT
    assert sig.direction == "LONG"
    assert sig.executable is False


# ─── RangeSignal — RANGE_BOUNCE ──────────────────────────────────────────────


def test_bounce_at_near_high_far_from_wall():
    # price=5908 → pct=0.70 (NEAR_HIGH), res_dist=(5920-5908)/5908≈0.00203 < 0.003
    # but res_dist 0.00203 < 0.0025 (NEAR_WALL_PCT) → actually triggers REJECT
    # Test a case just outside the NEAR_WALL_PCT threshold
    # price=5906 → pct=0.65 = exactly MIDDLE_HIGH boundary
    # pct=0.66 → just inside NEAR_HIGH
    # res_dist = (5920-5906.5)/5920 ≈ 0.00228 > 0.0025? No, 13.5/5906.5 ≈ 0.00229 < 0.0025
    # Need a larger resistance: 5950 → res_dist far enough
    ctx = _make_ctx(
        5910.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5950.0)],  # 40pt away → clear
        walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
    )
    # range: 5880–5950, width=70; price=5910 pct=(5910-5880)/70=0.43 → MIDDLE
    # Need the price to be in NEAR_HIGH (>= 0.65): price >= 5880 + 0.65*70 = 5925.5
    # But 5925.5 with resistance at 5950: res_dist=(5950-5925.5)/5925.5≈0.00414>0.0025
    ctx2 = _make_ctx(
        5930.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5960.0)],  # far above
        walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
    )
    # range: 5880–5960, width=80; price=5930 pct=(5930-5880)/80=0.625 < 0.65 → MIDDLE
    # Try price=5940: pct=(5940-5880)/80=0.75 → NEAR_HIGH
    # res_dist=(5960-5940)/5940=20/5940≈0.0034 > 0.0025 → NOT REJECT → BOUNCE
    ctx3 = _make_ctx(
        5940.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5960.0)],
        walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
    )
    rs = build_range_state(ctx3, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx3)
    assert sig.signal_type == SIG_BOUNCE
    assert sig.direction == "SHORT"
    assert sig.executable is False


def test_bounce_at_near_low_far_from_wall():
    # range: 5880–5960, width=80; price=5895 pct=(5895-5880)/80=0.1875 → NEAR_LOW
    # sup_dist=(5895-5880)/5895=15/5895≈0.0025 — exactly at threshold
    # Use price=5896 for sup_dist slightly > 0.0025 → BOUNCE not REJECT
    ctx = _make_ctx(
        5896.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5960.0)],
        walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    # pct=(5896-5880)/80=0.2 → NEAR_LOW; sup_dist=16/5896≈0.0027 > 0.0025 → BOUNCE
    assert sig.signal_type == SIG_BOUNCE
    assert sig.direction == "LONG"
    assert sig.executable is False


# ─── RangeSignal — RANGE_NO_DATA ─────────────────────────────────────────────


def test_no_data_on_empty_ctx():
    ctx = _empty_ctx(5900.0)
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    assert sig.signal_type == SIG_NO_DATA
    assert sig.executable is False


def test_no_data_when_only_one_wall():
    # Only resistance above — no support below → range_low is None
    ctx = _make_ctx(
        5900.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5920.0)],
        walls_below=[],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    assert sig.signal_type == SIG_NO_DATA


# ─── Executable invariant ─────────────────────────────────────────────────────


def test_only_break_close_is_executable():
    """
    Build every non-break scenario and confirm executable=False.
    Also confirm BREAK_CLOSE has executable=True.
    """
    scenarios: list[tuple[WallContext, dict]] = [
        # MIDDLE
        (
            _make_ctx(
                5900.0,
                walls_above=[_wl("PDH", KIND_RESISTANCE, 5920.0)],
                walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
            ),
            {},
        ),
        # REJECT SHORT
        (
            _make_ctx(
                5910.0,
                walls_above=[_wl("PDH", KIND_RESISTANCE, 5920.0)],
                walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
            ),
            {},
        ),
        # NO_DATA
        (_empty_ctx(), {}),
        # RETEST LONG
        (
            _make_ctx(
                5921.0,
                walls_above=[_wl("PDH", KIND_RESISTANCE, 5950.0)],
                walls_below=[
                    _wl("ORB_HIGH", KIND_RESISTANCE, 5920.0),
                    _wl("ORB_LOW", KIND_SUPPORT, 5880.0),
                ],
            ),
            {"bars_since_break": 3},
        ),
    ]

    for ctx, kwargs in scenarios:
        rs = build_range_state(ctx, "RANGE_BOUND")
        sig = build_range_signal(rs, ctx, **kwargs)
        if sig.signal_type == SIG_BREAK_CLOSE:
            assert sig.executable is True, f"BREAK_CLOSE must be executable (signal={sig})"
        else:
            assert sig.executable is False, (
                f"signal_type={sig.signal_type} must not be executable"
            )

    # Explicitly verify BREAK_CLOSE is executable
    break_ctx = _ctx_break_long()
    rs = build_range_state(break_ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, break_ctx)
    assert sig.signal_type == SIG_BREAK_CLOSE
    assert sig.executable is True


# ─── to_dict ─────────────────────────────────────────────────────────────────


def test_range_signal_to_dict_keys():
    ctx = _ctx_break_long()
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    d = sig.to_dict()
    for key in ("signal_type", "direction", "entry_candidate", "target_candidate",
                 "stop_candidate", "executable", "retest_eligible",
                 "retest_bars_available", "polling_risk", "notes"):
        assert key in d, f"missing key: {key}"


# ─── Helpers ──────────────────────────────────────────────────────────────────


def test_retest_bars_15m():
    assert _retest_bars(15) == 2


def test_retest_bars_5m():
    assert _retest_bars(5) == 6


def test_retest_bars_0_does_not_crash():
    assert _retest_bars(0) == 0


def test_has_polling_risk_is_false_for_normal_timeframes():
    # 30-min retest window always > 2 poll cycles (60s); should be False
    assert _has_polling_risk(15) is False
    assert _has_polling_risk(5) is False
    assert _has_polling_risk(1) is False


# ─── Fail-soft / never raises ────────────────────────────────────────────────


def test_build_range_state_never_raises_on_none_fields():
    # Create a WallContext with None distances (simulates partial data)
    ctx = WallContext(
        symbol="MES",
        price=5900.0,
        timestamp=_NOW,
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
        invalid_reason="test stub",
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    assert isinstance(rs, RangeState)


def test_build_range_signal_never_raises_on_stub_state():
    ctx = _empty_ctx()
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    assert isinstance(sig, RangeSignal)


def test_to_dict_round_trips_no_crash():
    ctx = _make_ctx(
        5900.0,
        walls_above=[_wl("PDH", KIND_RESISTANCE, 5920.0)],
        walls_below=[_wl("PDL", KIND_SUPPORT, 5880.0)],
    )
    rs = build_range_state(ctx, "RANGE_BOUND")
    sig = build_range_signal(rs, ctx)
    d_rs = rs.to_dict()
    d_sig = sig.to_dict()
    assert isinstance(d_rs, dict)
    assert isinstance(d_sig, dict)


# ─── RANGE_BREAK_CLOSE — target selection (directional, matching kind) ────────
#
# WallContext places a level that sits exactly at price into BOTH walls_above
# and walls_below. The old target selection took walls_above[0] / walls_below[0]
# of any kind, which produced target == entry (16 resolved rows, Aug 2026) and
# LONG targets at supports (115 of 1,284 candidates).


def _wall_at_price_ctx_long() -> WallContext:
    """Price 5930 closed above ORB_HIGH 5920; HOD sits exactly at 5930."""
    hod = _wl("HOD", KIND_RESISTANCE, 5930.0)
    return _make_ctx(
        5930.0,
        walls_above=[hod, _wl("PDH", KIND_RESISTANCE, 5950.0)],
        walls_below=[hod, _wl("ORB_HIGH", KIND_RESISTANCE, 5920.0)],
    )


def _wall_at_price_ctx_short() -> WallContext:
    """Price 5870 closed below ORB_LOW 5880; LOD sits exactly at 5870."""
    lod = _wl("LOD", KIND_SUPPORT, 5870.0)
    return _make_ctx(
        5870.0,
        walls_above=[lod, _wl("ORB_LOW", KIND_SUPPORT, 5880.0)],
        walls_below=[lod, _wl("PWL", KIND_SUPPORT, 5850.0)],
    )


def test_break_close_long_target_never_equals_entry():
    ctx = _wall_at_price_ctx_long()
    sig = build_range_signal(build_range_state(ctx, "RANGE_BOUND"), ctx)
    assert sig.signal_type == SIG_BREAK_CLOSE and sig.direction == "LONG"
    assert sig.target_candidate != sig.entry_candidate
    assert sig.target_candidate == pytest.approx(5950.0, abs=0.01)


def test_break_close_short_target_never_equals_entry():
    ctx = _wall_at_price_ctx_short()
    sig = build_range_signal(build_range_state(ctx, "RANGE_BOUND"), ctx)
    assert sig.signal_type == SIG_BREAK_CLOSE and sig.direction == "SHORT"
    assert sig.target_candidate != sig.entry_candidate
    assert sig.target_candidate == pytest.approx(5850.0, abs=0.01)


def test_break_close_long_skips_support_above_price():
    # A support (LOD) above price is not a LONG target; PDH is.
    ctx = _make_ctx(
        5930.0,
        walls_above=[_wl("LOD", KIND_SUPPORT, 5935.0), _wl("PDH", KIND_RESISTANCE, 5950.0)],
        walls_below=[_wl("ORB_HIGH", KIND_RESISTANCE, 5920.0)],
    )
    sig = build_range_signal(build_range_state(ctx, "RANGE_BOUND"), ctx)
    assert sig.direction == "LONG"
    assert sig.target_candidate == pytest.approx(5950.0, abs=0.01)


def test_break_close_short_skips_resistance_below_price():
    ctx = _make_ctx(
        5870.0,
        walls_above=[_wl("ORB_LOW", KIND_SUPPORT, 5880.0)],
        walls_below=[_wl("HOD", KIND_RESISTANCE, 5865.0), _wl("PWL", KIND_SUPPORT, 5850.0)],
    )
    sig = build_range_signal(build_range_state(ctx, "RANGE_BOUND"), ctx)
    assert sig.direction == "SHORT"
    assert sig.target_candidate == pytest.approx(5850.0, abs=0.01)


def test_break_close_long_supply_zone_is_a_valid_target():
    ctx = _make_ctx(
        5930.0,
        walls_above=[_wl("SUPPLY_ZONE", KIND_ZONE, 5945.0), _wl("PDH", KIND_RESISTANCE, 5950.0)],
        walls_below=[_wl("ORB_HIGH", KIND_RESISTANCE, 5920.0)],
    )
    sig = build_range_signal(build_range_state(ctx, "RANGE_BOUND"), ctx)
    assert sig.target_candidate == pytest.approx(5945.0, abs=0.01)


def test_break_close_long_falls_back_symmetric_when_no_resistance_above():
    # Only a support sits above; fall back to a 1:1 projection off the broken wall.
    ctx = _make_ctx(
        5930.0,
        walls_above=[_wl("LOD", KIND_SUPPORT, 5935.0)],
        walls_below=[_wl("ORB_HIGH", KIND_RESISTANCE, 5920.0)],
    )
    sig = build_range_signal(build_range_state(ctx, "RANGE_BOUND"), ctx)
    assert sig.target_candidate == pytest.approx(5940.0, abs=0.01)
    assert sig.target_candidate > sig.entry_candidate


def test_break_close_stop_construction_unchanged():
    ctx = _wall_at_price_ctx_long()
    sig = build_range_signal(build_range_state(ctx, "RANGE_BOUND"), ctx)
    assert sig.stop_candidate == pytest.approx(round(5920.0 * 0.999, 2), abs=0.001)
