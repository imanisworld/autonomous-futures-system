"""
strategy/signal_engine.py

Decision engine: scores market conditions and generates trade setups.

Principle: LLM/agent may classify context and explain reasoning.
Code validates deterministically before passing to RiskEngine.

Outputs DecisionOutput with decision=TRADE only if a complete,
valid setup can be formed. Otherwise decision=NO_TRADE with reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from config.settings import SystemConfig, load_config
from context.market_context import MarketState
from risk.risk_engine import RiskEngine, TradeSetup, DailyState


# ─── Output Types ─────────────────────────────────────────────────────────────

@dataclass
class SetupDetail:
    direction: str          # LONG | SHORT
    entry: float
    stop: float
    target: float
    rr_ratio: float
    strategy: str
    notes: Optional[str] = None


@dataclass
class DecisionOutput:
    timestamp: datetime
    instrument: str
    session: str
    decision: str           # TRADE | NO_TRADE | DONE_FOR_DAY | WAIT
    reason: str
    market_condition: Optional[str] = None
    setup: Optional[SetupDetail] = None

    def to_dict(self) -> dict:
        d = {
            "ts": self.timestamp.isoformat(),
            "instrument": self.instrument,
            "session": self.session,
            "decision": self.decision,
            "reason": self.reason,
            "market_condition": self.market_condition,
            "setup": None,
        }
        if self.setup:
            d["setup"] = {
                "direction": self.setup.direction,
                "entry": self.setup.entry,
                "stop": self.setup.stop,
                "target": self.setup.target,
                "rr_ratio": self.setup.rr_ratio,
                "strategy": self.setup.strategy,
                "notes": self.setup.notes,
            }
        return d


# ─── Decision Engine ──────────────────────────────────────────────────────────

class DecisionEngine:
    """
    Evaluates market state and produces a trading decision.

    The engine scores market condition, identifies applicable strategy concepts,
    and generates a bracket setup. It is NOT responsible for risk enforcement —
    that is the RiskEngine's job. The engine only decides: is there a trade here?

    NO_TRADE is the default outcome. A TRADE requires all of:
    - Tradable session
    - Tradable instrument
    - Non-choppy/dead market condition
    - A matching strategy concept with all three bracket levels
    - R:R >= min_rr_ratio
    """

    # Minimum tick sizes for bracket logic (approximate)
    MIN_STOP_TICKS = {
        "MNQ": 4,   # 4 ticks = 1 point for MNQ
        "MES": 4,
        "MGC": 2,
        "MCL": 4,
    }

    TICK_SIZE = {
        "MNQ": 0.25,
        "MES": 0.25,
        "MGC": 0.10,
        "MCL": 0.01,
    }

    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or load_config()

    def evaluate(self, state: MarketState, daily_state: DailyState) -> DecisionOutput:
        """
        Main evaluation method. Runs through the full decision flow.
        Always returns a DecisionOutput — never raises.
        """
        now = datetime.now(timezone.utc)

        # ── Pre-flight: daily limits ──────────────────────────────────────────
        if daily_state.trade_count >= self.config.max_trades_per_day:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="DONE_FOR_DAY",
                reason=f"Daily trade limit reached ({daily_state.trade_count}/{self.config.max_trades_per_day}).",
            )

        if daily_state.consecutive_losses >= self.config.max_consecutive_losses:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="DONE_FOR_DAY",
                reason=f"Consecutive loss limit reached ({daily_state.consecutive_losses}). Stopping for the day.",
            )

        if daily_state.has_open_position:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="WAIT",
                reason="Position is open. Waiting for resolution before evaluating new setups.",
            )

        # ── Session check ─────────────────────────────────────────────────────
        if state.session not in self.config.allowed_sessions:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                reason=f"Session '{state.session}' is not allowed. Allowed: {self.config.allowed_sessions}.",
            )

        # ── Instrument check ──────────────────────────────────────────────────
        if state.instrument not in self.config.allowed_instruments:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                reason=f"Instrument '{state.instrument}' is not in allowed universe.",
            )

        # ── Market condition scoring ──────────────────────────────────────────
        condition = self._score_market_condition(state)

        if condition in self.config.non_tradable_states:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                market_condition=condition,
                reason=f"Market condition is {condition}. Avoiding dead/choppy markets.",
            )

        # ── Strategy evaluation ───────────────────────────────────────────────
        setup = self._find_setup(state, condition)

        if setup is None:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                market_condition=condition,
                reason="No qualifying setup found in current market structure.",
            )

        # ── R:R validation ────────────────────────────────────────────────────
        if setup.rr_ratio < self.config.min_rr_ratio:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                market_condition=condition,
                reason=(
                    f"Setup found ({setup.strategy}) but R:R {setup.rr_ratio:.2f} "
                    f"is below minimum {self.config.min_rr_ratio:.2f}."
                ),
            )

        # ── TRADE ─────────────────────────────────────────────────────────────
        return DecisionOutput(
            timestamp=now,
            instrument=state.instrument,
            session=state.session,
            decision="TRADE",
            market_condition=condition,
            reason=(
                f"Setup qualified: {setup.strategy} | "
                f"{setup.direction} @ {setup.entry} "
                f"stop={setup.stop} target={setup.target} "
                f"R:R={setup.rr_ratio:.2f}"
            ),
            setup=setup,
        )

    # ── Market Condition Scoring ───────────────────────────────────────────────

    def _score_market_condition(self, state: MarketState) -> str:
        """
        Score market condition from available indicators.
        Prefers externally provided condition; falls back to internal scoring.
        """
        # Use provided condition if already assessed
        if state.market_condition and state.market_condition in (
            "TRENDING", "RANGE_BOUND", "CHOPPY", "DEAD"
        ):
            return state.market_condition

        # Internal scoring from volume and structure
        score = 0

        # Volume check: dead market if relative volume < 0.4
        if state.volume.relative is not None:
            if state.volume.relative < 0.4:
                return "DEAD"
            elif state.volume.relative >= 0.8:
                score += 2
            elif state.volume.relative >= 0.5:
                score += 1

        # Trend check
        if state.trend:
            if state.trend.direction in ("UP", "DOWN") and state.trend.strength == "STRONG":
                score += 3
            elif state.trend.direction in ("UP", "DOWN"):
                score += 1
            elif state.trend.direction == "SIDEWAYS":
                score -= 1

        # ORB structure
        if state.orb.status in ("reclaimed_high", "reclaimed_low", "rejected_high", "rejected_low"):
            score += 2
        elif state.orb.status == "inside":
            score -= 1

        # VWAP position clarity
        if state.vwap.price_vs_vwap in ("above", "below"):
            score += 1

        # Range check (tight range = choppy)
        bar_range = state.ohlc.high - state.ohlc.low
        tick_size = self.TICK_SIZE.get(state.instrument, 0.25)
        min_ticks = self.MIN_STOP_TICKS.get(state.instrument, 4)
        if bar_range < (tick_size * min_ticks * 2):
            return "CHOPPY"

        if score >= 4:
            return "TRENDING"
        elif score >= 1:
            return "RANGE_BOUND"
        else:
            return "CHOPPY"

    # ── Setup Generation ──────────────────────────────────────────────────────

    def _find_setup(self, state: MarketState, condition: str) -> Optional[SetupDetail]:
        """
        Try each enabled strategy concept and return the first qualifying setup.
        Returns None if no valid setup is found.
        """
        enabled = self.config.enabled_concepts

        strategies = [
            # ── Core structural setups ─────────────────────────────────────
            ("orb_reclaim", self._try_orb_reclaim),
            ("orb_rejection", self._try_orb_rejection),
            ("vwap_reclaim", self._try_vwap_reclaim),
            ("vwap_hold", self._try_vwap_hold),
            ("pdh_reclaim", self._try_pdh_reclaim),
            ("pdl_reclaim", self._try_pdl_reclaim),
            ("continuation_pullback", self._try_continuation_pullback),
            # ── The Strat patterns ─────────────────────────────────────────
            # Phase 1: approximated from market_state flags.
            # Full multi-bar classification in Phase 2.
            # See sources/strat_definitions.md for full pattern definitions.
            ("strat_212", self._try_strat_212),
            ("strat_122", self._try_strat_122),
            ("strat_4hr_retrigger", self._try_strat_4hr_retrigger),
        ]

        for name, fn in strategies:
            if name not in enabled:
                continue
            setup = fn(state)
            if setup is not None:
                return setup

        return None

    def _try_orb_reclaim(self, state: MarketState) -> Optional[SetupDetail]:
        """
        ORB Reclaim: Price rejected above ORB high, pulled back, now reclaiming.
        Entry above ORB high, stop below ORB high, target = entry + 2x risk.
        """
        if state.orb.status != "reclaimed_high":
            return None
        if state.vwap.price_vs_vwap != "above":
            return None

        entry = state.orb.high + (self.TICK_SIZE.get(state.instrument, 0.25) * 2)
        stop = state.orb.low - (self.TICK_SIZE.get(state.instrument, 0.25) * 4)
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + (risk * 2.5)

        rr = RiskEngine.calculate_rr("LONG", entry, stop, target)
        return SetupDetail(
            direction="LONG",
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy="orb_reclaim",
            notes="ORB high reclaimed with VWAP support",
        )

    def _try_orb_rejection(self, state: MarketState) -> Optional[SetupDetail]:
        """
        ORB Rejection: Failed breakout above ORB high, price reverting inside range.
        Short entry below ORB high, stop above ORB high, target = ORB low.
        """
        if state.orb.status != "rejected_high":
            return None

        entry = state.orb.high - (self.TICK_SIZE.get(state.instrument, 0.25) * 2)
        stop = state.orb.high + (self.TICK_SIZE.get(state.instrument, 0.25) * 6)
        risk = stop - entry
        if risk <= 0:
            return None
        target = entry - (risk * 2.5)

        rr = RiskEngine.calculate_rr("SHORT", entry, stop, target)
        return SetupDetail(
            direction="SHORT",
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy="orb_rejection",
            notes="ORB high rejection, fading failed breakout",
        )

    def _try_vwap_reclaim(self, state: MarketState) -> Optional[SetupDetail]:
        """
        VWAP Reclaim: Price was below VWAP, crossed above and held.
        Long entry above VWAP, stop below VWAP, target 2x+ risk.
        """
        if not (state.vwap.reclaimed and state.vwap.holding and state.vwap.price_vs_vwap == "above"):
            return None
        if not (state.trend and state.trend.direction == "UP"):
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        entry = state.vwap.value + (tick * 3)
        stop = state.vwap.value - (tick * 6)
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + (risk * 2.2)

        rr = RiskEngine.calculate_rr("LONG", entry, stop, target)
        return SetupDetail(
            direction="LONG",
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy="vwap_reclaim",
            notes="VWAP reclaimed and holding with uptrend context",
        )

    def _try_vwap_hold(self, state: MarketState) -> Optional[SetupDetail]:
        """
        VWAP Hold: Price is holding below VWAP in downtrend.
        Short from VWAP resistance, stop above VWAP.
        """
        if not (state.vwap.holding and state.vwap.price_vs_vwap == "below"):
            return None
        if not (state.trend and state.trend.direction == "DOWN"):
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        entry = state.vwap.value - (tick * 3)
        stop = state.vwap.value + (tick * 6)
        risk = stop - entry
        if risk <= 0:
            return None
        target = entry - (risk * 2.2)

        rr = RiskEngine.calculate_rr("SHORT", entry, stop, target)
        return SetupDetail(
            direction="SHORT",
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy="vwap_hold",
            notes="Price holding below VWAP in downtrend",
        )

    def _try_pdh_reclaim(self, state: MarketState) -> Optional[SetupDetail]:
        """PDH Reclaim: Price back above previous day high."""
        if state.previous_day.price_vs_pdh != "reclaimed":
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        entry = state.previous_day.high + (tick * 2)
        stop = state.previous_day.high - (tick * 8)
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + (risk * 2.0)

        rr = RiskEngine.calculate_rr("LONG", entry, stop, target)
        return SetupDetail(
            direction="LONG",
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy="pdh_reclaim",
            notes="Previous day high reclaimed",
        )

    def _try_pdl_reclaim(self, state: MarketState) -> Optional[SetupDetail]:
        """PDL Reclaim: Price back below previous day low (short)."""
        if state.previous_day.price_vs_pdl != "reclaimed":
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        entry = state.previous_day.low - (tick * 2)
        stop = state.previous_day.low + (tick * 8)
        risk = stop - entry
        if risk <= 0:
            return None
        target = entry - (risk * 2.0)

        rr = RiskEngine.calculate_rr("SHORT", entry, stop, target)
        return SetupDetail(
            direction="SHORT",
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy="pdl_reclaim",
            notes="Previous day low reclaimed (breakdown continuation)",
        )

    # ── The Strat Patterns ────────────────────────────────────────────────────

    def _try_strat_212(self, state: MarketState) -> Optional[SetupDetail]:
        """
        The Strat: 2-1-2 Continuation (Phase 1 approximation).

        Full pattern: 2UP → 1 (inside bar) → 2UP  (bullish continuation)
                      2DOWN → 1 → 2DOWN             (bearish continuation)

        Phase 1 proxy: detect via trend direction + ORB inside status + VWAP hold.
        The inside bar compression stage is approximated by ORB status=inside
        with price above/below VWAP in a trending market.

        Phase 2 note: replace with actual multi-bar candle classification.
        See sources/strat_definitions.md for full pattern definition.
        """
        if not (state.trend and state.trend.direction in ("UP", "DOWN")):
            return None
        if state.trend.strength not in ("STRONG", "MODERATE"):
            return None
        # Phase 1 proxy: ORB inside = inside bar compression in progress
        if state.orb.status != "inside":
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)

        if state.trend.direction == "UP":
            if state.vwap.price_vs_vwap != "above":
                return None
            # Entry above current bar high (break of compression)
            entry = state.ohlc.high + (tick * 1)
            stop = state.ohlc.low - (tick * 4)
            risk = entry - stop
            if risk <= 0:
                return None
            target = entry + (risk * 2.2)
            direction = "LONG"
        else:
            if state.vwap.price_vs_vwap != "below":
                return None
            entry = state.ohlc.low - (tick * 1)
            stop = state.ohlc.high + (tick * 4)
            risk = stop - entry
            if risk <= 0:
                return None
            target = entry - (risk * 2.2)
            direction = "SHORT"

        rr = RiskEngine.calculate_rr(direction, entry, stop, target)
        return SetupDetail(
            direction=direction,
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy="strat_212",
            notes=(
                f"2-1-2 continuation proxy ({state.trend.direction}): "
                f"inside-bar compression in trending market above/below VWAP. "
                f"Phase 1 approximation — full Strat classification in Phase 2."
            ),
        )

    def _try_strat_122(self, state: MarketState) -> Optional[SetupDetail]:
        """
        The Strat: 1-2-2 Reversal (Phase 1 approximation).

        Full pattern: 1 (inside) → 2DOWN → 2UP  (bullish reversal)
                      1 → 2UP → 2DOWN             (bearish reversal)

        Phase 1 proxy: detect an ORB rejection (failed breakout) when the
        broader trend is in the opposite direction — the classic bull/bear trap.
        ORB rejection_high + downtrend = bearish trap resolved → bullish 1-2-2.
        ORB rejection_low + uptrend = bullish trap resolved → bearish 1-2-2.

        See sources/strat_definitions.md for full pattern definition.
        """
        tick = self.TICK_SIZE.get(state.instrument, 0.25)

        # Bullish 1-2-2: ORB high rejected, but underlying trend is UP → reversal long
        if (state.orb.status == "rejected_high"
                and state.trend
                and state.trend.direction == "UP"
                and state.vwap.price_vs_vwap == "above"):
            entry = state.orb.high + (tick * 2)  # Re-entry above ORB high
            stop = state.orb.low - (tick * 4)
            risk = entry - stop
            if risk <= 0:
                return None
            target = entry + (risk * 2.0)
            rr = RiskEngine.calculate_rr("LONG", entry, stop, target)
            return SetupDetail(
                direction="LONG",
                entry=round(entry, 4),
                stop=round(stop, 4),
                target=round(target, 4),
                rr_ratio=rr,
                strategy="strat_122",
                notes=(
                    "1-2-2 bullish reversal proxy: ORB high rejected then reclaimed "
                    "with uptrend context. Phase 1 approximation."
                ),
            )

        # Bearish 1-2-2: ORB low rejected, but underlying trend is DOWN → reversal short
        if (state.orb.status == "rejected_low"
                and state.trend
                and state.trend.direction == "DOWN"
                and state.vwap.price_vs_vwap == "below"):
            entry = state.orb.low - (tick * 2)  # Re-entry below ORB low
            stop = state.orb.high + (tick * 4)
            risk = stop - entry
            if risk <= 0:
                return None
            target = entry - (risk * 2.0)
            rr = RiskEngine.calculate_rr("SHORT", entry, stop, target)
            return SetupDetail(
                direction="SHORT",
                entry=round(entry, 4),
                stop=round(stop, 4),
                target=round(target, 4),
                rr_ratio=rr,
                strategy="strat_122",
                notes=(
                    "1-2-2 bearish reversal proxy: ORB low rejected then reclaimed "
                    "with downtrend context. Phase 1 approximation."
                ),
            )

        return None

    def _try_strat_4hr_retrigger(self, state: MarketState) -> Optional[SetupDetail]:
        """
        The Strat: 4HR Re-Trigger (Phase 1 approximation).

        Full pattern (1H/4H timeframe):
        4AM candle: 2DOWN (bearish)
        8AM candle: 2UP (reclaim)
        NY open: Price dips below 8AM 2UP candle high, then reclaims it = LONG entry

        Phase 1 proxy: session is new_york, ORB status is reclaimed_high
        (approximates the 8AM level reclaim at NY open), with broader uptrend context
        and VWAP support. Price must have dipped first (orb status implies that pattern).

        This is specifically for MNQ and MES.

        See sources/strat_definitions.md for full pattern definition.
        """
        if state.instrument not in ("MNQ", "MES"):
            return None
        if state.session != "new_york":
            return None
        if state.orb.status != "reclaimed_high":
            return None
        if not (state.trend and state.trend.direction == "UP"):
            return None
        if state.vwap.price_vs_vwap != "above":
            return None
        # Volume should confirm the retrigger
        if state.volume.relative and state.volume.relative < 0.7:
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        # Entry at ORB high (the "trigger" level)
        entry = state.orb.high + (tick * 1)
        stop = state.orb.low - (tick * 6)  # Wide stop — below full ORB range
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + (risk * 2.0)

        rr = RiskEngine.calculate_rr("LONG", entry, stop, target)
        return SetupDetail(
            direction="LONG",
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy="strat_4hr_retrigger",
            notes=(
                "4HR Re-Trigger proxy: NY open reclaims pre-market high level "
                "(ORB high) with uptrend and VWAP confirmation. "
                "Phase 1 approximation — full 4AM/8AM candle check in Phase 2."
            ),
        )

    def _try_continuation_pullback(self, state: MarketState) -> Optional[SetupDetail]:
        """
        Continuation Pullback: Trending market pulled back to VWAP or ORB level.
        """
        if not (state.trend and state.trend.direction in ("UP", "DOWN")):
            return None
        if state.trend.strength not in ("STRONG", "MODERATE"):
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)

        if state.trend.direction == "UP":
            # Pullback to VWAP in uptrend
            if state.vwap.price_vs_vwap != "at" and not state.vwap.holding:
                return None
            entry = state.ohlc.close
            stop = state.vwap.value - (tick * 8)
            risk = entry - stop
            if risk <= 0:
                return None
            target = entry + (risk * 2.0)
            direction = "LONG"
        else:
            # Pullback to VWAP in downtrend
            if state.vwap.price_vs_vwap != "at" and not state.vwap.holding:
                return None
            entry = state.ohlc.close
            stop = state.vwap.value + (tick * 8)
            risk = stop - entry
            if risk <= 0:
                return None
            target = entry - (risk * 2.0)
            direction = "SHORT"

        rr = RiskEngine.calculate_rr(direction, entry, stop, target)
        return SetupDetail(
            direction=direction,
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy="continuation_pullback",
            notes=f"Trend continuation pullback to VWAP ({state.trend.direction})",
        )
