"""
strategy/confluence_scorer.py

Scores a trade setup's confluence strength on a 0–10 scale.

Pure, stateless, no I/O — takes a MarketState and SetupDetail, returns a
frozen ConfluenceScore. Called by webhook/runner.py on the TRADE path only,
before the Discord alert fires.

Scoring is MNQ/MES directional (calls/long or short) — it considers:
  structural factors  (strat pattern, VWAP, trend, ORB, volume, session)
  penalties           (against-trend, low volume)
  a hard veto         (strat_direction contradicts setup → score=0)

Grade tiers: A+ (9–10) | A (8) | B (6–7) | C (5) | WEAK (<5)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from context.market_context import MarketState
from strategy.signal_engine import SetupDetail


# ─── Result type ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConfluenceScore:
    score: int              # 0–10, clamped
    grade: str              # A+ | A | B | C | WEAK
    factors: list           # positive contributors  → ✅ in Discord
    penalties: list         # negative contributors  → ⚠️ in Discord


# ─── Grade helper ────────────────────────────────────────────────────────────

def _grade(score: int) -> str:
    if score >= 9:
        return "A+"
    if score >= 8:
        return "A"
    if score >= 6:
        return "B"
    if score == 5:
        return "C"
    return "WEAK"


# ─── Strat sequences that earn the +3 confirmation bonus ─────────────────────

_SCORED_STRAT_SEQUENCES = frozenset({"strat_212", "strat_122", "strat_inside_break"})


# ─── Main scorer ─────────────────────────────────────────────────────────────

def score_setup(state: MarketState, setup: SetupDetail) -> ConfluenceScore:
    """
    Score a trade setup's confluence against the current market state.

    Returns a ConfluenceScore with score (0–10), grade, and the lists of
    factors/penalties that contributed — ready to render in a Discord alert.

    Edge cases handled without crashing:
      - state.strat is None          → skip strat checks, no veto
      - state.trend is None          → skip trend checks, no penalty
      - state.volume.relative is None → skip both volume checks
    """
    direction = setup.direction  # "LONG" | "SHORT"

    # ── Veto: strat_direction contradicts setup ───────────────────────────────
    if state.strat is not None and state.strat.strat_direction is not None:
        if state.strat.strat_direction != direction:
            return ConfluenceScore(
                score=0,
                grade="WEAK",
                factors=[],
                penalties=[f"Strat direction {state.strat.strat_direction} contradicts {direction}"],
            )

    raw = 0
    factors: list[str] = []
    penalties: list[str] = []

    # ── +3 Strat pattern confirmed ────────────────────────────────────────────
    if (
        state.strat is not None
        and state.strat.strat_sequence in _SCORED_STRAT_SEQUENCES
        and state.strat.strat_direction == direction
    ):
        raw += 3
        factors.append(f"Strat {state.strat.strat_sequence} confirmed (+3)")

    # ── +2 VWAP aligned ──────────────────────────────────────────────────────
    vwap_pos = state.vwap.price_vs_vwap
    vwap_aligned = (direction == "LONG" and vwap_pos == "above") or \
                   (direction == "SHORT" and vwap_pos == "below")
    if vwap_aligned:
        raw += 2
        factors.append("VWAP aligned (+2)")

    # ── +2 Trend aligned (+1 bonus if STRONG) ────────────────────────────────
    trend_aligned = False
    if state.trend is not None and state.trend.direction is not None:
        trend_dir = state.trend.direction
        if (direction == "LONG" and trend_dir == "UP") or \
           (direction == "SHORT" and trend_dir == "DOWN"):
            trend_aligned = True
            strength = state.trend.strength or "MODERATE"
            raw += 2
            factors.append(f"Trend {trend_dir} {strength} (+2)")
            if state.trend.strength == "STRONG":
                raw += 1
                factors.append("Strong trend bonus (+1)")

    # ── +2 Volume ≥ 1.2× average ─────────────────────────────────────────────
    rel_vol = state.volume.relative
    if rel_vol is not None and rel_vol >= 1.2:
        raw += 2
        factors.append(f"Volume {rel_vol:.1f}x avg (+2)")

    # ── +1 NY session ────────────────────────────────────────────────────────
    if state.session == "new_york":
        raw += 1
        factors.append("NY session (+1)")

    # ── +1 ORB confirms direction ─────────────────────────────────────────────
    orb_status = state.orb.status
    orb_confirms = (
        direction == "LONG" and orb_status in ("reclaimed_high", "above")
    ) or (
        direction == "SHORT" and orb_status in ("rejected_high", "below")
    )
    if orb_confirms:
        raw += 1
        factors.append("ORB confirms direction (+1)")

    # ── -3 Against trend ─────────────────────────────────────────────────────
    if state.trend is not None and state.trend.direction is not None and not trend_aligned:
        # Only penalise if trend has a directional opinion (UP or DOWN, not SIDEWAYS)
        if state.trend.direction in ("UP", "DOWN"):
            raw -= 3
            penalties.append(f"Against trend {state.trend.direction} (-3)")

    # ── -2 Low volume ─────────────────────────────────────────────────────────
    if rel_vol is not None and rel_vol < 0.8:
        raw -= 2
        penalties.append(f"Low volume {rel_vol:.1f}x (-2)")

    # ── Key levels scoring ────────────────────────────────────────────────────
    kl = state.key_levels
    if kl is not None:
        close = state.ohlc.close

        # +2 EMA 9/21 crossover aligned with direction (momentum confirmed)
        if kl.ema_9_above_21 is not None:
            ema_aligned = (direction == "LONG" and kl.ema_9_above_21) or \
                          (direction == "SHORT" and not kl.ema_9_above_21)
            if ema_aligned:
                raw += 2
                factors.append("EMA 9/21 crossover aligned (+2)")
            else:
                raw -= 1
                penalties.append("EMA 9/21 crossover against direction (-1)")

        # +1 EMA 55 bias aligned (price on right side of trend filter)
        if kl.price_above_ema_55 is not None:
            ema55_aligned = (direction == "LONG" and kl.price_above_ema_55) or \
                            (direction == "SHORT" and not kl.price_above_ema_55)
            if ema55_aligned:
                raw += 1
                factors.append("EMA 55 bias aligned (+1)")

        # +1 EMA 200 macro bias aligned (strongest trend confirmation)
        if kl.price_above_ema_200 is not None:
            ema200_aligned = (direction == "LONG" and kl.price_above_ema_200) or \
                             (direction == "SHORT" and not kl.price_above_ema_200)
            if ema200_aligned:
                raw += 1
                factors.append("EMA 200 macro bias aligned (+1)")

        # +1 Entry near HOD/LOD — price testing a meaningful intraday level
        if direction == "LONG" and kl.lod is not None and kl.near_level(close, kl.lod):
            raw += 1
            factors.append(f"Near LOD {kl.lod:.2f} — strong entry level (+1)")
        if direction == "SHORT" and kl.hod is not None and kl.near_level(close, kl.hod):
            raw += 1
            factors.append(f"Near HOD {kl.hod:.2f} — strong entry level (+1)")

        # +1 Target aligns with PDH/PDL or PWH/PWL (natural magnet)
        pdh = state.previous_day.high
        pdl = state.previous_day.low
        pwh = kl.prev_week_high
        pwl = kl.prev_week_low
        target = setup.target
        target_magnets = []
        if direction == "LONG":
            for level, label in ((pdh, "PDH"), (pwh, "PWH")):
                if level is not None and kl.near_level(target, level, ticks=20):
                    target_magnets.append(label)
        else:
            for level, label in ((pdl, "PDL"), (pwl, "PWL")):
                if level is not None and kl.near_level(target, level, ticks=20):
                    target_magnets.append(label)
        if target_magnets:
            raw += 1
            factors.append(f"Target near {'/'.join(target_magnets)} (+1)")

    score = max(0, min(10, raw))
    return ConfluenceScore(score=score, grade=_grade(score), factors=factors, penalties=penalties)
