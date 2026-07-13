"""MNQ structural-level 5-minute break/retest/reclaim/rejection — REJECTED, research-only.

STATUS: REJECTED (2026-07-13, docs/mnq-structural-level-5m-study-2026-07-13.md).
This is NOT a candidate awaiting activation. The replay study found the
fixed-target exit robustly negative (both walk-forward halves, every RR
bucket) and the runner-exit variant fails a 2-tick slippage stress test
(flips from +$0.46/trade to -$0.06/trade) -- too fragile to proceed under
the operator's own explicit gate ("only proceed to live integration if
replay survives both halves with realistic fills"). No shadow/live
integration was built, and none should be built from this module without a
NEW replay that clears that bar under a genuinely different setup design
(see the study doc's "what would need to change" section).

Deliberately kept OUT of `context/` (where the two live MNQ shadow lanes'
pure-decision modules live) and placed under `research/` instead, so its
directory location does not imply runtime-readiness. Nothing under
webhook/, strategy/, or execution/ imports this module, and nothing should.

Motivating gap this module was built to investigate: the deployed 15-minute
engine and the vwap_hold-only 5-minute early-signal lane
(context/mnq_vwap_hold_early.py, PR #267) both evaluate a SINGLE strategy
family (vwap_hold) reactively. Neither evaluates a plain structural-level
break/retest/reclaim/rejection sequence against the mapped levels already
carried on every alert — "the system received the context and mapped levels
but never scored the overnight move, the bounce, or the rejection against
them at all." The replay study answered this: scoring them this way does not
have a durable edge (see STATUS above).

MAPPED LEVELS — scoped strictly to what the payload schema actually carries,
per the explicit instruction not to invent new proprietary levels:

  present AND populated in both live alerts and the historical replay set:
    - previous_day_high / previous_day_low   (PDH / PDL)
    - orb_high / orb_low

  present in the schema (context/market_context.py GEXContext/SupplyDemandData)
  but NEVER populated in the historical Polygon-backfilled replay set
  (data/replay_polygon_5m/MNQ — checked empirically, 100% null across a
  16-day/3456-row sample: gex_flip, mid_upper, mid_lower, call_wall, put_wall,
  hvl, max_pain, supply_top/bottom, demand_top/bottom): these are wired in
  generically below (used live if a real alert ever carries a value) but are
  UNTESTED by the replay study — a live-only, backtest-blind gap, disclosed,
  not overclaimed.

  NOT present anywhere in the schema, and therefore NOT implemented (per
  "do not invent new proprietary levels"): "MID" (the payload's mid_upper/
  mid_lower are a GEX dealer-positioning band, not the requested chart "MID"
  concept), "HVI", "MP" (market profile), and overnight-session-specific
  high/low (the schema's hod/lod are a running INTRADAY high/low, a distinct
  concept, and are deliberately excluded from level detection to avoid scope
  drift — see docstring note in `mapped_levels()`).

This module is pure decision logic: no I/O, no journal, no risk/broker calls,
no mutation of any caller-owned state. The caller (replay study script, or
webhook/runner.py in shadow mode) owns persistence and evidence writing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional

VALID_MODES = ("off", "observe_only", "shadow")
DEFAULT_MODE = "off"
SCOPE_INSTRUMENT = "MNQ"
DEFAULT_DIRECTIONS = ("long", "short")
DEFAULT_ENTRY_MODES = ("momentum_close", "retest")
DEFAULT_SESSIONS = ("overnight", "premarket", "rth")
DEFAULT_MIN_RR = 1.5
DEFAULT_MAX_STOP_POINTS = 60.0
DEFAULT_LEVELS = (
    "previous_day_high",
    "previous_day_low",
    "orb_high",
    "orb_low",
    "gex_flip",
    "supply_top",
    "supply_bottom",
    "demand_top",
    "demand_bottom",
)
TICK = 0.25
DEFAULT_STOP_BUFFER_POINTS = 2.0
DEFAULT_RETEST_LOOKBACK_BARS = 12  # 12 x 5m = 60 minutes
DEFAULT_STRUCTURE_LOOKBACK_BARS = 36  # 36 x 5m = 3 hours, for swing-low/high search

# session vocabulary bridge: this codebase's canonical taxonomy
# (webhook/state_builder.py:detect_session -> asian/london/new_york/off_hours)
# mapped onto the operator-facing overnight/premarket/rth vocabulary used by
# STRUCTURAL_LEVEL_5M_SESSIONS. off_hours (the daily maintenance halt) is
# never eligible regardless of config -- it is not a real trading session.
_SESSION_MAP = {"asian": "overnight", "london": "premarket", "new_york": "rth"}

REJECTION_REASONS = (
    "NO_MAPPED_LEVEL",
    "LEVEL_TOO_FAR",
    "NO_CONFIRMED_CLOSE",
    "NO_RETEST",
    "CONTEXT_OPPOSED",
    "CHOPPY_OR_UNCLEAR",
    "INVALID_STOP",
    "STOP_TOO_WIDE",
    "TARGET_TOO_CLOSE",
    "RR_TOO_LOW",
    "DUPLICATE_SETUP",
    "SESSION_DISABLED",
    "STALE_ALERT",
    "WRONG_INSTRUMENT",
    "WRONG_TIMEFRAME",
)


def structural_level_5m_mode(cfg=None) -> str:
    if cfg is not None:
        value = getattr(cfg, "structural_level_5m_mode", None)
        if value:
            return str(value).strip().lower()
    return str(os.getenv("STRUCTURAL_LEVEL_5M_MODE", DEFAULT_MODE) or DEFAULT_MODE).strip().lower()


def structural_level_5m_instruments(cfg=None) -> frozenset:
    if cfg is not None:
        value = getattr(cfg, "structural_level_5m_instruments", None)
        if value:
            return frozenset(str(v).strip().upper() for v in value if str(v).strip())
    raw = os.getenv("STRUCTURAL_LEVEL_5M_INSTRUMENTS", SCOPE_INSTRUMENT)
    return frozenset(s.strip().upper() for s in raw.split(",") if s.strip()) or frozenset({SCOPE_INSTRUMENT})


def structural_level_5m_directions(cfg=None) -> frozenset:
    if cfg is not None:
        value = getattr(cfg, "structural_level_5m_directions", None)
        if value:
            return frozenset(str(v).strip().lower() for v in value if str(v).strip())
    raw = os.getenv("STRUCTURAL_LEVEL_5M_DIRECTIONS", ",".join(DEFAULT_DIRECTIONS))
    return frozenset(s.strip().lower() for s in raw.split(",") if s.strip()) or frozenset(DEFAULT_DIRECTIONS)


def structural_level_5m_entry_modes(cfg=None) -> frozenset:
    if cfg is not None:
        value = getattr(cfg, "structural_level_5m_entry_modes", None)
        if value:
            return frozenset(str(v).strip().lower() for v in value if str(v).strip())
    raw = os.getenv("STRUCTURAL_LEVEL_5M_ENTRY_MODES", ",".join(DEFAULT_ENTRY_MODES))
    return frozenset(s.strip().lower() for s in raw.split(",") if s.strip()) or frozenset(DEFAULT_ENTRY_MODES)


def structural_level_5m_sessions(cfg=None) -> frozenset:
    if cfg is not None:
        value = getattr(cfg, "structural_level_5m_sessions", None)
        if value:
            return frozenset(str(v).strip().lower() for v in value if str(v).strip())
    raw = os.getenv("STRUCTURAL_LEVEL_5M_SESSIONS", ",".join(DEFAULT_SESSIONS))
    return frozenset(s.strip().lower() for s in raw.split(",") if s.strip()) or frozenset(DEFAULT_SESSIONS)


def structural_level_5m_min_rr(cfg=None) -> float:
    if cfg is not None:
        value = getattr(cfg, "structural_level_5m_min_rr", None)
        if value is not None:
            return float(value)
    return float(os.getenv("STRUCTURAL_LEVEL_5M_MIN_RR", DEFAULT_MIN_RR) or DEFAULT_MIN_RR)


def structural_level_5m_max_stop_points(cfg=None) -> float:
    if cfg is not None:
        value = getattr(cfg, "structural_level_5m_max_stop_points", None)
        if value is not None:
            return float(value)
    return float(
        os.getenv("STRUCTURAL_LEVEL_5M_MAX_STOP_POINTS", DEFAULT_MAX_STOP_POINTS)
        or DEFAULT_MAX_STOP_POINTS
    )


def structural_level_5m_levels(cfg=None) -> frozenset:
    if cfg is not None:
        value = getattr(cfg, "structural_level_5m_levels", None)
        if value:
            return frozenset(str(v).strip().lower() for v in value if str(v).strip())
    raw = os.getenv("STRUCTURAL_LEVEL_5M_LEVELS", ",".join(DEFAULT_LEVELS))
    return frozenset(s.strip().lower() for s in raw.split(",") if s.strip()) or frozenset(DEFAULT_LEVELS)


def is_structural_level_5m_candidate(instrument: str, timeframe: str, cfg=None) -> bool:
    """Cheap, side-effect-free eligibility gate before any detection work runs."""
    if structural_level_5m_mode(cfg) == "off":
        return False
    from context.five_min_feed import is_five_min

    root = (instrument or "").upper().replace("1!", "").strip()
    if root not in structural_level_5m_instruments(cfg):
        return False
    return is_five_min(timeframe)


def session_bucket(session: str) -> Optional[str]:
    """Map the canonical asian/london/new_york/off_hours session onto the
    operator-facing overnight/premarket/rth vocabulary. off_hours -> None
    (never eligible, not a real trading session, not config-gateable)."""
    return _SESSION_MAP.get((session or "").strip().lower())


@dataclass(frozen=True)
class LevelBreak:
    level_name: str
    level_price: float
    direction: str  # "long" or "short"
    setup_type: str  # "reclaim" | "failed_breakdown" | "break_and_retest"
    entry_mode: str  # "momentum_close" | "retest"
    trigger_bar: dict
    stop_source_bar: dict


def mapped_levels(bar: dict) -> dict:
    """Extract every non-null mapped level present on a single bar/payload-shaped
    dict. `bar` may be a replay row (flat JSON with previous_day_high, orb_high,
    gex_flip, supply_top, ... keys) or an equivalent dict built from a live
    MarketState. Deliberately does NOT read hod/lod (running intraday high/
    low is a distinct concept from the requested "overnight high/low", which
    does not exist in this schema -- see module docstring) or any MID/HVI/MP
    field, since none exist.
    """
    out = {}
    for name in DEFAULT_LEVELS:
        value = bar.get(name)
        if value is not None:
            try:
                out[name] = float(value)
            except (TypeError, ValueError):
                continue
    return out


def _swing_low(bars: Iterable[dict]) -> Optional[float]:
    lows = [float(b["low"]) for b in bars if b.get("low") is not None]
    return min(lows) if lows else None


def _swing_high(bars: Iterable[dict]) -> Optional[float]:
    highs = [float(b["high"]) for b in bars if b.get("high") is not None]
    return max(highs) if highs else None


def classify_context(direction: str, *, trend_direction: Optional[str], trend_strength: Optional[str],
                      market_condition: Optional[str]) -> str:
    """aligned | neutral | opposed | unclear -- reuses the existing
    trend_direction/trend_strength/market_condition fields, invents nothing new."""
    trend_direction = (trend_direction or "").upper()
    trend_strength = (trend_strength or "").upper()
    market_condition = (market_condition or "").upper()
    want = "UP" if direction == "long" else "DOWN"
    oppose = "DOWN" if direction == "long" else "UP"

    if market_condition in ("CHOPPY", "RANGE_BOUND") or not trend_direction:
        return "unclear"
    if trend_direction == oppose and trend_strength == "STRONG":
        return "opposed"
    if trend_direction == want:
        return "aligned"
    return "neutral"


def _next_level_target(levels: dict, direction: str, entry: float) -> Optional[tuple]:
    """Nearest OTHER mapped level beyond entry in the trade direction. Returns
    (level_name, price) or None if no qualifying level exists."""
    candidates = []
    for name, price in levels.items():
        if direction == "long" and price > entry:
            candidates.append((name, price))
        elif direction == "short" and price < entry:
            candidates.append((name, price))
    if not candidates:
        return None
    if direction == "long":
        return min(candidates, key=lambda kv: kv[1])
    return max(candidates, key=lambda kv: kv[1])


def detect_candidates(
    *,
    window: list,
    current_bar: dict,
    session: str,
    trend_direction: Optional[str] = None,
    trend_strength: Optional[str] = None,
    market_condition: Optional[str] = None,
    cfg=None,
    stop_buffer: float = DEFAULT_STOP_BUFFER_POINTS,
    retest_lookback: int = DEFAULT_RETEST_LOOKBACK_BARS,
) -> list:
    """Pure, stateless, single-bar-close evaluation. `window` is the rolling
    history of PRIOR bars strictly before `current_bar` (chronological, most
    recent last) -- callers (replay + live) both build this from already-
    closed bars only, so there is no lookahead by construction. Returns a
    list of candidate dicts, one per (level, direction, setup_type,
    entry_mode) combination considered -- both ACCEPTED and REJECTED, so
    every evaluated event is auditable (per the required evidence contract).
    """
    out = []
    levels = mapped_levels(current_bar)
    allowed_levels = structural_level_5m_levels(cfg)
    allowed_directions = structural_level_5m_directions(cfg)
    allowed_entry_modes = structural_level_5m_entry_modes(cfg)
    min_rr = structural_level_5m_min_rr(cfg)
    max_stop = structural_level_5m_max_stop_points(cfg)

    bucket = session_bucket(session)
    if bucket is None or bucket not in structural_level_5m_sessions(cfg):
        return [{
            "direction": None, "setup_type": None, "entry_mode": None,
            "source_level_name": None, "decision": "REJECTED",
            "rejection_reason": "SESSION_DISABLED", "session": session,
        }]

    if not levels:
        return [{
            "direction": None, "setup_type": None, "entry_mode": None,
            "source_level_name": None, "decision": "REJECTED",
            "rejection_reason": "NO_MAPPED_LEVEL", "session": session,
        }]

    prev_bar = window[-1] if window else None
    lookback = window[-retest_lookback:] if window else []
    close = float(current_bar["close"])
    high = float(current_bar["high"])
    low = float(current_bar["low"])

    for level_name, level_price in levels.items():
        if level_name not in allowed_levels:
            continue

        for direction in ("long", "short"):
            if direction not in allowed_directions:
                continue
            ctx = classify_context(
                direction, trend_direction=trend_direction,
                trend_strength=trend_strength, market_condition=market_condition,
            )

            # --- reclaim / failed_breakdown (long)  |  rejection / failed_reclaim (short) ---
            if direction == "long":
                broke_below_intrabar = low <= level_price
                prior_closed_below = prev_bar is not None and float(prev_bar["close"]) <= level_price
                confirmed_close = close > level_price
                qualifies = confirmed_close and (broke_below_intrabar or prior_closed_below)
                setup_type = "reclaim" if prior_closed_below else "failed_breakdown"
            else:
                broke_above_intrabar = high >= level_price
                prior_closed_above = prev_bar is not None and float(prev_bar["close"]) >= level_price
                confirmed_close = close < level_price
                qualifies = confirmed_close and (broke_above_intrabar or prior_closed_above)
                setup_type = "rejection" if prior_closed_above else "failed_reclaim"

            for entry_mode in ("momentum_close",):
                if entry_mode not in allowed_entry_modes:
                    continue
                out.append(_build_candidate(
                    qualifies=qualifies, level_name=level_name, level_price=level_price,
                    direction=direction, setup_type=setup_type, entry_mode=entry_mode,
                    entry=close, stop_bars=(lookback + [current_bar]) if qualifies else [current_bar],
                    stop_buffer=stop_buffer, levels=levels, min_rr=min_rr, max_stop=max_stop,
                    ctx=ctx, session=session, current_bar=current_bar,
                    no_confirm_reason="NO_CONFIRMED_CLOSE",
                ))

            # --- break_and_retest: needs a break bar earlier in the lookback,
            # and the CURRENT bar as the confirmed retest-hold bar.
            if direction == "long":
                break_bar = next(
                    (b for b in reversed(lookback) if float(b["close"]) > level_price), None
                )
                retest_holds = (
                    break_bar is not None and low <= level_price + stop_buffer and close > level_price
                )
            else:
                break_bar = next(
                    (b for b in reversed(lookback) if float(b["close"]) < level_price), None
                )
                retest_holds = (
                    break_bar is not None and high >= level_price - stop_buffer and close < level_price
                )

            for entry_mode in ("retest",):
                if entry_mode not in allowed_entry_modes:
                    continue
                out.append(_build_candidate(
                    qualifies=bool(retest_holds), level_name=level_name, level_price=level_price,
                    direction=direction, setup_type="break_and_retest", entry_mode=entry_mode,
                    entry=close, stop_bars=[current_bar], stop_buffer=stop_buffer,
                    levels=levels, min_rr=min_rr, max_stop=max_stop, ctx=ctx, session=session,
                    current_bar=current_bar, no_confirm_reason="NO_RETEST",
                ))

    return out


def _build_candidate(
    *, qualifies: bool, level_name: str, level_price: float, direction: str, setup_type: str,
    entry_mode: str, entry: float, stop_bars: list, stop_buffer: float, levels: dict,
    min_rr: float, max_stop: float, ctx: str, session: str, current_bar: dict,
    no_confirm_reason: str,
) -> dict:
    base = {
        "instrument": SCOPE_INSTRUMENT,
        "timeframe": "5",
        "session": session,
        "direction": direction,
        "setup_type": setup_type,
        "entry_mode": entry_mode,
        "source_level_name": level_name,
        "source_level_price": level_price,
        "current_price": float(current_bar.get("close")),
        "trigger_bar": current_bar.get("timestamp") or current_bar.get("ts"),
        "trend_context": ctx,
    }
    if not qualifies:
        return {**base, "decision": "REJECTED", "rejection_reason": no_confirm_reason}

    if ctx == "opposed":
        return {**base, "decision": "REJECTED", "rejection_reason": "CONTEXT_OPPOSED"}

    if direction == "long":
        swing = _swing_low(stop_bars)
        stop = (min(swing, level_price) if swing is not None else level_price) - stop_buffer
        if stop >= entry:
            return {**base, "decision": "REJECTED", "rejection_reason": "INVALID_STOP"}
        risk = entry - stop
    else:
        swing = _swing_high(stop_bars)
        stop = (max(swing, level_price) if swing is not None else level_price) + stop_buffer
        if stop <= entry:
            return {**base, "decision": "REJECTED", "rejection_reason": "INVALID_STOP"}
        risk = stop - entry

    if risk <= 0:
        return {**base, "decision": "REJECTED", "rejection_reason": "INVALID_STOP"}
    if risk > max_stop:
        return {**base, "decision": "REJECTED", "rejection_reason": "STOP_TOO_WIDE", "stop": stop, "risk_points": risk}

    target_pick = _next_level_target(levels, direction, entry)
    if target_pick is None:
        return {**base, "decision": "REJECTED", "rejection_reason": "NO_MAPPED_LEVEL", "stop": stop, "risk_points": risk}
    target_name, target_price = target_pick
    reward = (target_price - entry) if direction == "long" else (entry - target_price)
    if reward <= 0:
        return {**base, "decision": "REJECTED", "rejection_reason": "TARGET_TOO_CLOSE", "stop": stop, "risk_points": risk}
    rr = reward / risk
    if rr < min_rr:
        return {
            **base, "decision": "REJECTED", "rejection_reason": "RR_TOO_LOW",
            "stop": stop, "target": target_price, "risk_points": risk, "reward_points": reward, "rr": rr,
        }

    return {
        **base, "decision": "ACCEPTED", "rejection_reason": None,
        "entry": entry, "stop": stop, "target": target_price, "next_mapped_level": target_name,
        "risk_points": risk, "reward_points": reward, "rr": rr,
    }
