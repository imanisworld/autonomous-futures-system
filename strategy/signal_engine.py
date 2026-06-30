"""
strategy/signal_engine.py

Decision engine: scores market conditions and generates trade setups.

Principle: LLM/agent may classify context and explain reasoning.
Code validates deterministically before passing to RiskEngine.

Outputs DecisionOutput with decision=TRADE only if a complete,
valid setup can be formed. Otherwise decision=NO_TRADE with reason.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time as _time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)

from config.settings import SystemConfig, load_config
from context.market_context import MarketState
from risk.risk_engine import RiskEngine, TradeSetup, DailyState
from strategy.gex_gate import evaluate_gex
from strategy.regime_classifier import classify_regime
from strategy.signa_gate import evaluate_signa


# Setups eligible for momentum re-anchor: entry rests AT a level, so price leaving
# that level in the trade's favor produces a no-fill. This includes the ORB
# breakout/reclaim setups: although their entry sits beyond the ORB boundary, the
# live broker places the entry as an IOC *limit* (the ENTRY_SLIPPAGE_TOLERANCE_TICKS
# cap path), NOT a stop — so a momentum break that closes past the entry leaves the
# limit unfilled (observed 100% live no-fill on orb_breakout/orb_reclaim, 2026-06-29).
# continuation_pullback already enters at the close, so it needs no re-anchor.
_MOMENTUM_REANCHOR_SETUPS = frozenset(
    {
        "vwap_reclaim", "vwap_hold", "vwap_rejection", "pdh_reclaim", "pdl_reclaim",
        "orb_breakout", "orb_reclaim",
    }
)


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


@dataclass(frozen=True)
class StrategyCandidate:
    """A setup plus deterministic ranking metadata for opt-in portfolio selection."""
    setup: SetupDetail
    confluence_score: int
    confluence_grade: str
    rank_score: float
    rank_reason: str
    priority_index: int


@dataclass
class DecisionOutput:
    timestamp: datetime
    instrument: str
    session: str
    decision: str           # TRADE | NO_TRADE | DONE_FOR_DAY | WAIT
    reason: str
    market_condition: Optional[str] = None
    setup: Optional[SetupDetail] = None
    regime: Optional[str] = None
    gex_status: Optional[str] = None
    signa_status: Optional[str] = None
    failed_gates: list[str] = field(default_factory=list)
    confidence_score: Optional[int] = None

    def to_dict(self) -> dict:
        d = {
            "ts": self.timestamp.isoformat(),
            "instrument": self.instrument,
            "session": self.session,
            "decision": self.decision,
            "reason": self.reason,
            "market_condition": self.market_condition,
            "setup": None,
            "regime": self.regime,
            "gex_status": self.gex_status,
            "signa_status": self.signa_status,
            "failed_gates": self.failed_gates,
            "confidence_score": self.confidence_score,
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

def _parse_hhmm(value: str) -> _time:
    hour, minute = value.split(":", 1)
    return _time(int(hour), int(minute))


def _time_in_window(value: _time, start: _time, end: _time) -> bool:
    if start <= end:
        return start <= value < end
    return value >= start or value < end


def _session_window_decision(rules: list[dict], timestamp: datetime) -> tuple[bool, str | None]:
    current = timestamp.astimezone(ZoneInfo("America/New_York")).time().replace(second=0, microsecond=0)
    default_allow = True
    default_note = None
    for rule in rules:
        if "default" in rule:
            default_allow = bool(rule.get("default"))
            default_note = rule.get("note")
            continue
        start = rule.get("start")
        end = rule.get("end")
        if not start or not end:
            continue
        if _time_in_window(current, _parse_hhmm(str(start)), _parse_hhmm(str(end))):
            return bool(rule.get("allow", False)), rule.get("note")
    return default_allow, default_note


class DecisionEngine:
    WINDOWS = {
        "opening":   {"start": "09:30", "end": "10:45", "allow": "all"},
        "mid_early": {"start": "10:45", "end": "11:30", "allow": "all"},   # opened — STRONG-trend quality gate still applies downstream
        "mid_late":  {"start": "11:30", "end": "12:00", "allow": "all"},    # opened (was lunch block) — full NY session tradeable
        "afternoon": {"start": "12:00", "end": "14:00", "allow": "all"},    # open afternoon
        "late":      {"start": "14:00", "end": "16:00", "allow": "all"},    # opened (was afternoon block) — full NY session tradeable
    }
    _NY_RESTRICTED_STRAT_SEQUENCES = {"strat_212", "strat_122"}

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

    # Max ticks from entry for ORB-anchored stops.
    # Prevents the full ORB range from becoming the stop on wide-ORB days.
    # Stop is capped at entry - (MAX_ORB_STOP_TICKS * tick_size).
    MAX_ORB_STOP_TICKS = {
        "MNQ": 80,   # 20 points
        "MES": 40,   # 10 points
        "MGC": 20,
        "MCL": 40,
    }

    TICK_SIZE = {
        "MNQ": 0.25,
        "MES": 0.25,
        "MGC": 0.10,
        "MCL": 0.01,
    }

    # Ranked-selection research weights. These are deliberately opt-in via
    # strategy_selection_mode="ranked" and do not affect first-match/live default.
    _RANK_EXPECTANCY_BONUS = {
        ("MNQ", "strat_4hr_retrigger"): 80.0,
        ("MNQ", "orb_reclaim"): 80.0,
    }

    def __init__(self, config: Optional[SystemConfig] = None,
                 schedule_mode: Optional[str] = None):
        self.config = config or load_config()
        # schedule_mode overrides the config's; default falls back to config
        # ("current"). Only "current" enforces the schedule/session gates — the
        # always-on modes bypass them (used by the read-only shadow generator).
        self.schedule_mode = schedule_mode or getattr(self.config, "schedule_mode", "current")
        self._enforce_schedule = self.schedule_mode == "current"

    def evaluate(self, state: MarketState, daily_state: DailyState) -> DecisionOutput:
        """
        Main evaluation method. Runs through the full decision flow.
        Always returns a DecisionOutput — never raises.
        """
        now = datetime.now(timezone.utc)

        # ── Pre-flight: daily capacity ────────────────────────────────────────
        total_daily_capacity = (
            self.config.max_trades_per_day
            + int(getattr(self.config, "bonus_trades_after_max", 0) or 0)
        )
        if daily_state.trade_count >= total_daily_capacity:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="DONE_FOR_DAY",
                reason=(
                    f"Daily trade capacity reached "
                    f"({daily_state.trade_count}/{total_daily_capacity})."
                ),
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
        # Skipped in always-on modes (schedule bypassed); enforced in "current".
        if self._enforce_schedule and state.session not in self.config.allowed_sessions:
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

        # ── Session window gate ───────────────────────────────────────────────
        session_window_result = (
            self._check_session_window(state) if self._enforce_schedule else None
        )
        if session_window_result is not None:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                reason=session_window_result,
                failed_gates=["SESSION_WINDOW"],
                confidence_score=0,
            )

        # ── New York entry window gate ────────────────────────────────────────
        window_result = (
            self._check_new_york_entry_window(state) if self._enforce_schedule else None
        )
        if window_result is not None:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                reason=window_result,
                failed_gates=["NY_SESSION_WINDOW"],
                confidence_score=0,
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
                failed_gates=["MARKET_CONDITION_NOT_TRADABLE"],
                confidence_score=0,
            )

        # TRENDING-only gate (#1): the validated edge was earned exclusively in
        # TRENDING conditions (the 555-day replay took 0/1274 trades in any other
        # state). RANGE_BOUND passes the non_tradable_states check above but is
        # out-of-distribution for every setup — block it here so a live Pine
        # RANGE_BOUND label can't admit a false-breakout the backtest never saw.
        if self.config.require_trending_condition and condition != "TRENDING":
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                market_condition=condition,
                reason=f"Market condition is {condition}, not TRENDING. "
                       "Only TRENDING conditions are traded.",
                failed_gates=["MARKET_CONDITION_NOT_TRENDING"],
                confidence_score=0,
            )

        gate_direction = self._infer_gate_direction(state)
        failed_gates: list[str] = []

        regime = classify_regime(state, daily_state)
        if regime.regime == "NO_TRADE":
            failed_gate = regime.failed_gate or "REGIME_NO_TRADE"
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                market_condition=condition,
                reason=f"Regime gate rejected: {failed_gate}",
                regime=regime.regime,
                failed_gates=[failed_gate],
                confidence_score=0,
            )
        if regime.regime == "RESTRICTED" and regime.failed_gate:
            failed_gates.append(regime.failed_gate)

        gex_gate = evaluate_gex(state, gate_direction)
        if gex_gate.status == "RED_LIGHT":
            failed_gate = gex_gate.failed_gate or "GEX_RED_LIGHT"
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                market_condition=condition,
                reason=f"GEX gate rejected: {failed_gate}",
                regime=regime.regime,
                gex_status=gex_gate.status,
                signa_status=None,
                failed_gates=failed_gates + [failed_gate],
                confidence_score=0,
            )

        # Signa is SHADOW by default: the status is still computed and recorded
        # on every DecisionOutput below (for journaling/measurement), but a FAIL
        # only blocks the trade when signa_gate_enforced is explicitly on.
        signa_gate = evaluate_signa(state, gate_direction)
        if signa_gate.status == "FAIL" and getattr(self.config, "signa_gate_enforced", False):
            failed_gate = signa_gate.failed_gate or "SIGNA_FAIL"
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                market_condition=condition,
                reason=f"Signa gate rejected: {failed_gate}",
                regime=regime.regime,
                gex_status=gex_gate.status,
                signa_status=signa_gate.status,
                failed_gates=failed_gates + [failed_gate],
                confidence_score=0,
            )

        # ── Quality gate: trend strength ─────────────────────────────────────
        if self.config.require_strong_trend.get(state.instrument, False):
            if not (state.trend and state.trend.strength == "STRONG") \
                    and not self._admit_moderate(state):
                actual = state.trend.strength if state.trend else "none"
                return DecisionOutput(
                    timestamp=now,
                    instrument=state.instrument,
                    session=state.session,
                    decision="NO_TRADE",
                    market_condition=condition,
                    reason=f"Trend strength '{actual}' below STRONG required for {state.instrument}",
                    regime=regime.regime,
                    gex_status=gex_gate.status,
                    signa_status=signa_gate.status,
                    failed_gates=failed_gates + ["TREND_STRENGTH_BELOW_REQUIRED"],
                    confidence_score=0,
                )

        # ── Quality gate: signal-bar volume ──────────────────────────────────
        min_vol = self.config.min_signal_bar_volume.get(state.instrument, 0.0)
        if min_vol > 0.0:
            rel_vol = state.volume.relative
            if rel_vol is None or rel_vol < min_vol:
                actual_vol = f"{rel_vol:.2f}" if rel_vol is not None else "n/a"
                return DecisionOutput(
                    timestamp=now,
                    instrument=state.instrument,
                    session=state.session,
                    decision="NO_TRADE",
                    market_condition=condition,
                    reason=f"Signal-bar volume too low ({actual_vol} < {min_vol:.2f}) for {state.instrument}",
                    regime=regime.regime,
                    gex_status=gex_gate.status,
                    signa_status=signa_gate.status,
                    failed_gates=failed_gates + ["SIGNAL_BAR_VOLUME_TOO_LOW"],
                    confidence_score=0,
                )

        # ── Quality gate: HTF / FTFC alignment ────────────────────────────────
        if self.config.require_htf_alignment.get(state.instrument, False):
            htf_failure = self._check_htf_alignment(state, gate_direction)
            if htf_failure is not None:
                return DecisionOutput(
                    timestamp=now,
                    instrument=state.instrument,
                    session=state.session,
                    decision="NO_TRADE",
                    market_condition=condition,
                    reason=htf_failure,
                    regime=regime.regime,
                    gex_status=gex_gate.status,
                    signa_status=signa_gate.status,
                    failed_gates=failed_gates + ["HTF_ALIGNMENT_FAIL"],
                    confidence_score=0,
                )

        # ── Quality gate: bar close location ─────────────────────────────────
        # Require the signal bar to close with conviction in the trade direction.
        # A bar closing mid-range (doji, wick) on a key level is a weak entry.
        # Gate direction is derived from regime/trend — if unknown, skip.
        bar_range = state.ohlc.high - state.ohlc.low
        if bar_range > 0 and gate_direction in ("LONG", "SHORT"):
            close_pct = (state.ohlc.close - state.ohlc.low) / bar_range
            # LONG: bar must close in top 40% of range (close_pct >= 0.60)
            # SHORT: bar must close in bottom 40% (close_pct <= 0.40)
            weak_close = (
                (gate_direction == "LONG" and close_pct < 0.40) or
                (gate_direction == "SHORT" and close_pct > 0.60)
            )
            if weak_close:
                failed_gates.append("WEAK_BAR_CLOSE")
                # Soft gate — log it but don't hard-block. Strategies can still
                # fire; the failed gate will appear in the journal for analysis.

        # ── Quality gate: EMA stack alignment ────────────────────────────────
        # When Pine sends EMA values, require price to be on the correct side
        # of the EMA stack (above ema_9 > ema_21 > ema_55 for LONG, reverse for SHORT).
        # If EMAs are absent (null), this gate is skipped — activates automatically
        # once Pine populates the fields.
        if state.key_levels and gate_direction in ("LONG", "SHORT"):
            kl = state.key_levels
            ema9  = kl.ema_9
            ema21 = kl.ema_21
            ema55 = kl.ema_55
            close = state.ohlc.close
            if None not in (ema9, ema21, ema55):
                if gate_direction == "LONG":
                    ema_aligned = close > ema9 > ema21 > ema55
                else:
                    ema_aligned = close < ema9 < ema21 < ema55
                if not ema_aligned and not self._admit_moderate(state):
                    return DecisionOutput(
                        timestamp=now,
                        instrument=state.instrument,
                        session=state.session,
                        decision="NO_TRADE",
                        market_condition=condition,
                        reason=f"EMA stack not aligned for {gate_direction}: "
                               f"close={close} ema9={ema9} ema21={ema21} ema55={ema55}",
                        regime=regime.regime,
                        gex_status=gex_gate.status,
                        signa_status=signa_gate.status,
                        failed_gates=failed_gates + ["EMA_STACK_NOT_ALIGNED"],
                        confidence_score=0,
                    )
                if not ema_aligned:
                    # Admitted MODERATE bar — record the soft miss for the journal
                    # but let the setups decide (EXPERIMENT path only).
                    failed_gates.append("EMA_STACK_NOT_ALIGNED_SOFT")

        # ── Strategy evaluation ───────────────────────────────────────────────
        setup = self._find_setup(state, condition, daily_state)

        if setup is None:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                market_condition=condition,
                reason="No qualifying setup found in current market structure.",
                regime=regime.regime,
                gex_status=gex_gate.status,
                signa_status=signa_gate.status,
                failed_gates=failed_gates,
                confidence_score=0,
            )
        setup = self._apply_strat_confirmation(setup, state)
        if setup is None:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                market_condition=condition,
                reason="Strat bar sequence contradicts setup direction.",
                regime=regime.regime,
                gex_status=gex_gate.status,
                signa_status=signa_gate.status,
                failed_gates=failed_gates + ["STRAT_DIRECTION_CONFLICT"],
                confidence_score=0,
            )

        setup = self._apply_advisory_bracket(setup, state)
        setup = self._enforce_min_target_distance(setup, state.instrument)
        setup = self._maybe_reanchor_entry(setup, state)

        # ── Entry-sanity guard (stale/detached level) ─────────────────────────
        # Every setup anchors its entry to a level (VWAP/ORB/PDH...). After a
        # feed gap that level can be stranded far from the live price, so a
        # MARKET entry would fill ~120pt from plan and the absolute bracket lands
        # on the wrong side of the fill (this caused the 2026-06-05 03:15 ET MNQ
        # scratch: SHORT with target 30178.75 ABOVE the 30080.75 fill). Require
        # the bracket to still straddle the current price; otherwise the signal
        # is stale — refuse rather than chase a broken market fill.
        entry_price = state.ohlc.close if state.ohlc else None
        if entry_price is not None and not self._entry_bracket_straddles_price(
            setup.direction, setup.entry, setup.stop, setup.target, entry_price
        ):
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                market_condition=condition,
                reason=(
                    f"Entry {setup.entry:g} detached from price {entry_price:g} "
                    f"(stop {setup.stop:g} / target {setup.target:g} no longer "
                    f"straddle the live price) — stale level after a feed gap; "
                    f"not chasing a market fill."
                ),
                regime=regime.regime,
                gex_status=gex_gate.status,
                signa_status=signa_gate.status,
                failed_gates=failed_gates + ["ENTRY_DETACHED_FROM_PRICE"],
                confidence_score=0,
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
                regime=regime.regime,
                gex_status=gex_gate.status,
                signa_status=signa_gate.status,
                failed_gates=failed_gates + ["RR_BELOW_MINIMUM"],
                confidence_score=0,
            )

        # ── TRADE: mark ORB break as played so continuation strategies are
        # blocked on subsequent bars above/below the same ORB level.
        # Pull-back strategies (orb_reclaim, vwap_reclaim, etc.) remain eligible.
        # When orb_reclaim fires it means price returned to the ORB, which
        # resets the break — clear the flag so a fresh break can be traded again.
        if state.orb.status == "above":
            daily_state.orb_break_long_played = True
        elif state.orb.status == "below":
            daily_state.orb_break_short_played = True
        elif setup.strategy in ("orb_reclaim", "strat_4hr_retrigger"):
            # Price pulled back to the ORB and is reclaiming — reset so the
            # next clean break above is eligible again.
            daily_state.orb_break_long_played = False
        elif setup.strategy == "orb_rejection":
            daily_state.orb_break_short_played = False

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
            regime=regime.regime,
            gex_status=gex_gate.status,
            signa_status=signa_gate.status,
            failed_gates=failed_gates,
            confidence_score=None,
        )

    def _infer_gate_direction(self, state: MarketState) -> Optional[str]:
        if state.strat and state.strat.strat_direction in ("LONG", "SHORT"):
            return state.strat.strat_direction
        if state.trend and state.trend.direction == "UP" and state.vwap.price_vs_vwap == "above":
            return "LONG"
        if state.trend and state.trend.direction == "DOWN" and state.vwap.price_vs_vwap == "below":
            return "SHORT"
        if state.orb.status in ("above", "reclaimed_high"):
            return "LONG"
        if state.orb.status in ("below", "rejected_high", "rejected_low"):
            return "SHORT"
        return None

    def _apply_strat_confirmation(
        self, setup: SetupDetail, state: MarketState
    ) -> Optional[SetupDetail]:
        """
        Annotate setup with Strat bar context. Vetoes the trade (returns None)
        when a classified sequence explicitly points in the opposing direction.
        Strat never upgrades a NO_TRADE to a TRADE — it only confirms or blocks.
        """
        strat = state.strat
        if not strat or not strat.current_bar_type:
            return setup

        # Veto: a confirmed sequence in the opposite direction contradicts the setup
        if strat.strat_sequence and strat.strat_direction and strat.strat_direction != setup.direction:
            return None

        parts = [f"Strat current={strat.current_bar_type}"]
        if strat.strat_sequence:
            parts.append(f"sequence={strat.strat_sequence}")
        if strat.strat_direction:
            parts.append(f"direction={strat.strat_direction} (aligned)")

        notes = setup.notes or ""
        suffix = "; ".join(parts)
        return SetupDetail(
            direction=setup.direction,
            entry=setup.entry,
            stop=setup.stop,
            target=setup.target,
            rr_ratio=setup.rr_ratio,
            strategy=setup.strategy,
            notes=f"{notes} | {suffix}" if notes else suffix,
        )

    def _apply_advisory_bracket(self, setup: SetupDetail, state: MarketState) -> SetupDetail:
        """
        Use Pine-sent bracket coordinates when they are complete and sane.

        Pine brackets are advisory, not authoritative: the backend only accepts
        them after it has independently found the same setup direction/strategy,
        then normal risk checks and minimum target enforcement still run.
        """
        raw = state.raw or {}
        if not isinstance(raw, dict):
            return setup

        pine_has_bracket = all(raw.get(f) is not None for f in ("entry", "stop", "target"))

        required = ("entry", "stop", "target")
        if any(raw.get(field) is None for field in required):
            return setup

        raw_direction = str(raw.get("signal_direction") or "").upper()
        if raw_direction and raw_direction != setup.direction:
            _log.warning(
                "Pine bracket ignored: direction mismatch (Pine=%s backend=%s)",
                raw_direction, setup.direction,
            )
            return setup

        raw_strategy = raw.get("signal_strategy")
        if raw_strategy and raw_strategy != setup.strategy:
            _log.warning(
                "Pine bracket ignored: strategy mismatch (Pine=%r backend=%r)",
                raw_strategy, setup.strategy,
            )
            return setup

        try:
            entry = float(raw["entry"])
            stop = float(raw["stop"])
            target = float(raw["target"])
        except (TypeError, ValueError):
            if pine_has_bracket:
                _log.warning("Pine bracket ignored: could not parse entry/stop/target as floats")
            return setup

        if entry <= 0 or stop <= 0 or target <= 0:
            if pine_has_bracket:
                _log.warning("Pine bracket ignored: non-positive value (entry=%s stop=%s target=%s)", entry, stop, target)
            return setup

        if setup.direction == "LONG":
            if not (stop < entry < target):
                if pine_has_bracket:
                    _log.warning("Pine bracket ignored: LONG order invalid (stop=%s entry=%s target=%s)", stop, entry, target)
                return setup
        elif setup.direction == "SHORT":
            if not (target < entry < stop):
                if pine_has_bracket:
                    _log.warning("Pine bracket ignored: SHORT order invalid (target=%s entry=%s stop=%s)", target, entry, stop)
                return setup
        else:
            return setup

        rr = RiskEngine.calculate_rr(setup.direction, entry, stop, target)
        if rr <= 0:
            if pine_has_bracket:
                _log.warning("Pine bracket ignored: RR <= 0")
            return setup

        note = "Pine bracket override"
        notes = f"{setup.notes} | {note}" if setup.notes else note
        return SetupDetail(
            direction=setup.direction,
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy=setup.strategy,
            notes=notes,
        )

    # ── Market Condition Scoring ───────────────────────────────────────────────

    def _check_htf_alignment(self, state: MarketState, direction: str | None) -> Optional[str]:
        """Block only when HTF data is present and explicitly conflicts."""
        htf = getattr(state, "htf", None)
        if htf is None or direction not in {"LONG", "SHORT"}:
            return None
        if htf.ftfc_aligned is False:
            return "HTF/FTFC alignment failed"

        expected = "UP" if direction == "LONG" else "DOWN"
        directions = [
            str(value).strip().upper()
            for value in (
                htf.daily_direction,
                htf.four_hour_direction,
                htf.one_hour_direction,
                htf.ftfc_direction,
            )
            if value not in (None, "")
        ]
        opposing = "DOWN" if expected == "UP" else "UP"
        if opposing in directions and expected not in directions:
            return f"HTF direction opposes {direction}"
        return None

    def _check_session_window(self, state: MarketState) -> Optional[str]:
        windows = getattr(self.config, "session_windows", {}) or {}
        rules = windows.get(state.session)
        if not rules:
            return None
        allowed, note = _session_window_decision(rules, state.timestamp)
        if allowed:
            return None
        detail = f" ({note})" if note else ""
        et = state.timestamp.astimezone(ZoneInfo("America/New_York"))
        return (
            f"Entry at {et.strftime('%H:%M')} ET is outside allowed "
            f"'{state.session}' session windows{detail}."
        )

    def _admit_moderate(self, state: MarketState) -> bool:
        """EXPERIMENT gate: admit a MODERATE-trend bar past the two full-stack walls.

        Two redundant gates normally require a full EMA stack (STRONG): the
        trend-strength gate and the pre-setup EMA-stack-alignment gate. This
        helper, per per-instrument flags, lets a MODERATE bar through *both* so
        the individual setups become the deciders:

          - allow_moderate_pullback → admit PULLBACK bars (stack intact, dip to
            ema9 inside a confirmed trend).
          - allow_moderate_early    → admit EARLY bars (trend forming, ema55 not
            yet flipped).

        With both on, "trade any MODERATE trend, not only STRONG." When
        `moderate_pullback_require_vwap_align` is set, also require price on the
        trend side of VWAP. All flags default off → no production behavior change.
        """
        trend = state.trend
        if not trend or trend.strength != "MODERATE":
            return False
        if trend.direction not in ("UP", "DOWN"):
            return False
        kind = trend.moderate_kind
        pull_ok = (kind == "PULLBACK"
                   and self.config.allow_moderate_pullback.get(state.instrument, False))
        early_ok = (kind == "EARLY"
                    and self.config.allow_moderate_early.get(state.instrument, False))
        if not (pull_ok or early_ok):
            return False
        if self.config.moderate_pullback_require_vwap_align.get(state.instrument, False):
            want = "above" if trend.direction == "UP" else "below"
            if state.vwap.price_vs_vwap != want:
                return False
        return True

    def _check_new_york_entry_window(self, state: MarketState) -> Optional[str]:
        if state.session != "new_york":
            return None

        result = self._ny_window_for(state.timestamp)
        if result is None:
            return None

        name, window = result
        allow = window["allow"]
        if allow == "none":
            return "NY session window blocked"
        if allow == "all":
            return None

        # "restricted" — conditions differ by window
        strong_trend = bool(state.trend and state.trend.strength == "STRONG")
        vwap_aligned = state.vwap.price_vs_vwap in ("above", "below")
        orb_active = state.orb.status in (
            "reclaimed_high", "reclaimed_low", "rejected_high", "rejected_low"
        )
        volume_surge = bool(state.volume.relative is not None and state.volume.relative >= 1.2)
        strat_sequence = state.strat.strat_sequence if state.strat else None
        confirmed_strat = strat_sequence in self._NY_RESTRICTED_STRAT_SEQUENCES

        if name == "mid_early":
            # 10:45–11:30: strong or moderate trend with VWAP clarity or active ORB
            moderate_trend = bool(state.trend and state.trend.strength in ("STRONG", "MODERATE"))
            if strong_trend and (vwap_aligned or orb_active):
                return None
            if moderate_trend and vwap_aligned and orb_active:
                return None
            return "Mid-morning window requires trend with VWAP and ORB structure"

        return "NY session window restricted"

    def _ny_window_for(self, ts: datetime) -> Optional[tuple[str, dict]]:
        et_time = ts.astimezone(self._ET).time()
        for name, window in self.WINDOWS.items():
            start = _parse_hhmm(window["start"])
            end = _parse_hhmm(window["end"])
            if start <= et_time < end:
                return name, window
        return None

    @staticmethod
    def _strat_run_direction(strat) -> Optional[str]:
        """Direction of a FULL 3-bar consecutive directional run, else None.

        Returns "UP"/"DOWN" only when all three classified bars (two_bars_back,
        previous, current) are two_up (or all two_down) — a sustained run that no
        reasonable definition calls chop (each bar takes out the prior in the same
        direction). Deliberately strict: a lone or 2-bar directional bar (common
        inside consolidation above a breakout) does NOT qualify, so it cannot
        override a chop label.
        """
        if strat is None:
            return None
        seq = (strat.two_bars_back_type, strat.previous_bar_type, strat.current_bar_type)
        if all(t == "two_up" for t in seq):
            return "UP"
        if all(t == "two_down" for t in seq):
            return "DOWN"
        return None

    def _is_structural_breakout(self, state: MarketState) -> bool:
        """True when the bar unambiguously shows a confirmed trend-day breakout.

        Three-way agreement required: EMA-stack STRONG in one direction, price
        confirmed through the prior-day extreme in that direction, and VWAP
        supporting the bias. All three must agree — any missing piece falls
        back to trusting Pine's RANGE_BOUND label.

        This upgrades RANGE_BOUND → TRENDING on bars where Pine's oscillator
        lags the structural reality (e.g. the RTH open breakout bar that closes
        20+ points above PDH on a STRONG-trend day, which Pine still calls
        RANGE_BOUND because its lookback window hasn't caught up).
        """
        if not getattr(self.config, "range_bound_breakout_override", True):
            return False
        trend = state.trend
        if trend is None or trend.strength != "STRONG":
            return False
        if trend.direction == "UP":
            pdh_cleared = (
                state.previous_day is not None
                and state.previous_day.price_vs_pdh == "above"
            )
            vwap_above = state.vwap.price_vs_vwap == "above"
            return pdh_cleared and vwap_above
        if trend.direction == "DOWN":
            pdl_broken = (
                state.previous_day is not None
                and state.previous_day.price_vs_pdl == "below"
            )
            vwap_below = state.vwap.price_vs_vwap == "below"
            return pdl_broken and vwap_below
        return False

    def _has_directional_structure(self, state: MarketState) -> bool:
        """True when the bar shows an UNAMBIGUOUS directional impulse: a full
        3-bar strat run agreeing with the EMA-stack trend direction.

        This is what distinguishes the live MES short (trend DOWN + three
        consecutive two_down bars) from consolidation/chop above a breakout (a
        lone up-bar, trend-ish, on the right side of VWAP) that Pine reasonably
        labels CHOPPY. Only the former vetoes a chop label.

        A windowed continuation also qualifies: when a multi-bar window of recent
        closes (context.bar_history) is decisively directional in the trend's
        direction, that is continuous-data evidence of a real move — the Phase 3
        "judge regime over a window, not one snapshot" signal. It is only set on
        the live ingest path (None elsewhere), so it never alters replay/tests.
        """
        trend = state.trend
        if trend is None or trend.direction not in ("UP", "DOWN"):
            return False
        if self._strat_run_direction(state.strat) == trend.direction:
            return True
        if state.window_direction is not None and state.window_direction == trend.direction:
            return True
        return False

    def _score_market_condition(self, state: MarketState) -> str:
        """
        Decide the market regime, intervening on chop ONLY.

        The fix is deliberately minimal-surface: Pine's TRADABLE labels
        (TRENDING / RANGE_BOUND) are trusted verbatim so downstream regime-
        dependent setup/confluence logic is unchanged. We intervene in exactly
        one case — a CHOPPY label on a bar with clear directional structure — to
        VETO the chop call. This is the same lesson already applied to
        trend_strength (context.trend): never let an external chop label override
        the structural evidence we already have (e.g. the live MES bar dropping
        with three consecutive two_down bars + trend DOWN + price below VWAP, which
        Pine mislabeled CHOPPY → NO_TRADE). DEAD (illiquid / low volume) stays a
        hard floor and is never vetoed.
        """
        pine = state.market_condition if state.market_condition in (
            "TRENDING", "RANGE_BOUND", "CHOPPY", "DEAD"
        ) else None

        # Trust Pine's tradable labels as-is (no downstream perturbation).
        # Exception: RANGE_BOUND is upgraded to TRENDING when the bar shows
        # all three hallmarks of a confirmed structural breakout — strong
        # EMA-stack, price confirmed through the prior-day extreme, and VWAP
        # aligned. This is the condition that produced two consecutive missed
        # trend days (2026-06-29/30): Pine called the RTH breakout bar
        # RANGE_BOUND; the TRENDING gate blocked every entry while MES ran 50+
        # points. The gate exists to block false-breakouts in range conditions,
        # not to block confirmed trend days.
        if pine in ("TRENDING", "RANGE_BOUND"):
            if pine == "RANGE_BOUND" and self._is_structural_breakout(state):
                return "TRENDING"
            return pine

        # The core fix: a CHOPPY label is vetoed by clear directional structure.
        if pine == "CHOPPY":
            return "RANGE_BOUND" if self._has_directional_structure(state) else "CHOPPY"

        # DEAD is a hard floor (illiquid tape) — not overridden.
        if pine == "DEAD":
            return "DEAD"

        # ── No Pine label: score from structure (legacy fallback) ───────────
        score = 0

        # Volume check: dead market if relative volume < 0.4 (hard floor).
        if state.volume.relative is not None:
            if state.volume.relative < 0.4:
                return "DEAD"
            elif state.volume.relative >= 0.8:
                score += 2
            elif state.volume.relative >= 0.5:
                score += 1

        # Trend check (EMA-stack direction/strength from context.trend)
        if state.trend:
            if state.trend.direction in ("UP", "DOWN") and state.trend.strength == "STRONG":
                score += 3
            elif state.trend.direction in ("UP", "DOWN"):
                score += 1
            elif state.trend.direction == "SIDEWAYS":
                score -= 1

        # Strat run: consecutive same-direction directional bars = trending
        if self._strat_run_direction(state.strat) is not None:
            score += 2

        # ORB structure
        if state.orb.status in ("reclaimed_high", "reclaimed_low", "rejected_high", "rejected_low"):
            score += 2
        elif state.orb.status == "inside":
            score -= 1

        # VWAP position clarity
        if state.vwap.price_vs_vwap in ("above", "below"):
            score += 1

        # VETO: a clear directional move is never chop. At minimum RANGE_BOUND.
        if self._has_directional_structure(state):
            return "TRENDING" if score >= 4 else "RANGE_BOUND"

        # Range check (tight range = choppy)
        bar_range = state.ohlc.high - state.ohlc.low
        tick_size = self.TICK_SIZE.get(state.instrument, 0.25)
        min_ticks = self.MIN_STOP_TICKS.get(state.instrument, 4)
        if bar_range < (tick_size * min_ticks * 2):
            return "CHOPPY"

        if score >= 4:
            return "TRENDING"
        if score >= 1:
            return "RANGE_BOUND"
        return "CHOPPY"

    # ── Setup Generation ──────────────────────────────────────────────────────

    # Strategies that require price to return to the ORB level (status != "above"/"below").
    # These are allowed even after the initial ORB break trade has been taken because
    # they only fire on the specific reclaim/rejection transition — never persistently.
    _ORB_PULLBACK_STRATEGIES: frozenset[str] = frozenset({
        "orb_reclaim",
        "orb_rejection",
        "strat_4hr_retrigger",
    })

    def _find_setup(
        self,
        state: MarketState,
        condition: str,
        daily_state: Optional[DailyState] = None,
    ) -> Optional[SetupDetail]:
        """
        Try each enabled strategy concept and return the first qualifying setup.
        Returns None if no valid setup is found.

        When daily_state is provided, continuation strategies are skipped after
        the first ORB break trade in each direction — only pull-back setups
        (orb_reclaim, orb_rejection, vwap_reclaim, strat_4hr_retrigger) remain
        eligible until price returns to the ORB level.
        """
        enabled = self.config.enabled_concepts
        instrument_disabled = set(
            self.config.disabled_concepts_per_instrument.get(state.instrument, [])
        )

        if getattr(self.config, "strategy_selection_mode", "first_match") == "ranked":
            candidate = self._find_ranked_setup(state, daily_state)
            return candidate.setup if candidate is not None else None

        # Gate: if the ORB break has already been played in this direction,
        # block continuation strategies so we don't re-enter on every bar
        # that stays above/below the ORB. Pull-back strategies remain eligible.
        orb_continuation_blocked = False
        if daily_state is not None:
            if state.orb.status == "above" and daily_state.orb_break_long_played:
                orb_continuation_blocked = True
            elif state.orb.status == "below" and daily_state.orb_break_short_played:
                orb_continuation_blocked = True

        strategies = [
            # ── The Strat 4HR Re-Trigger must be evaluated BEFORE orb_reclaim.
            # Both share the reclaimed_high condition; 4hr_retrigger is the more
            # specific setup (MNQ/MES only, early NY, strong trend, volume).
            # If it doesn't fire, orb_reclaim gets the same bar as a fallback.
            ("strat_4hr_retrigger", self._try_strat_4hr_retrigger),
            ("strat_4hr_retrigger", self._try_strat_4hr_retrigger_short),
            # ── Core structural setups ─────────────────────────────────────
            ("orb_breakout", self._try_orb_breakout),
            ("orb_reclaim", self._try_orb_reclaim),
            ("orb_rejection", self._try_orb_rejection),
            ("vwap_reclaim", self._try_vwap_reclaim),
            ("vwap_rejection", self._try_vwap_rejection),
            ("vwap_hold", self._try_vwap_hold),
            ("pdh_reclaim", self._try_pdh_reclaim),
            ("pdl_reclaim", self._try_pdl_reclaim),
            ("continuation_pullback", self._try_continuation_pullback),
            # ── The Strat patterns ─────────────────────────────────────────
            # Phase 1: approximated from market_state flags.
            # Full multi-bar classification in Phase 2.
            # Full proprietary pattern doctrine lives outside the public repo.
            ("strat_212", self._try_strat_212),
            ("strat_122", self._try_strat_122),
            ("strat_inside_break", self._try_strat_inside_break),
            ("strat_outside_continuation", self._try_strat_outside_continuation),
        ]

        for name, fn in strategies:
            if name not in enabled:
                continue
            if name in instrument_disabled:
                continue
            if orb_continuation_blocked and name not in self._ORB_PULLBACK_STRATEGIES:
                continue
            setup = fn(state)
            if setup is not None:
                return self._enforce_min_target_distance(setup, state.instrument)

        return None

    def collect_strategy_candidates(
        self,
        state: MarketState,
        condition: str,
        daily_state: Optional[DailyState] = None,
    ) -> list[StrategyCandidate]:
        """
        Return every enabled setup candidate with ranking metadata.

        This is the test/shadow path for moving from "first matching setup wins"
        toward a measured strategy portfolio. It does not place trades, mutate
        state, or alter default live behavior.
        """
        del condition  # retained for API symmetry with _find_setup
        candidates: list[StrategyCandidate] = []
        for priority_index, setup in self._iter_enabled_setups(state, daily_state):
            candidates.append(self._score_strategy_candidate(state, setup, priority_index))
        return sorted(
            candidates,
            key=lambda c: (c.rank_score, -c.priority_index),
            reverse=True,
        )

    def _find_ranked_setup(
        self,
        state: MarketState,
        daily_state: Optional[DailyState] = None,
    ) -> Optional[StrategyCandidate]:
        candidates = self.collect_strategy_candidates(state, "", daily_state)
        return candidates[0] if candidates else None

    def _iter_enabled_setups(
        self,
        state: MarketState,
        daily_state: Optional[DailyState] = None,
    ):
        enabled = self.config.enabled_concepts
        instrument_disabled = set(
            self.config.disabled_concepts_per_instrument.get(state.instrument, [])
        )

        orb_continuation_blocked = False
        if daily_state is not None:
            if state.orb.status == "above" and daily_state.orb_break_long_played:
                orb_continuation_blocked = True
            elif state.orb.status == "below" and daily_state.orb_break_short_played:
                orb_continuation_blocked = True

        strategies = [
            ("strat_4hr_retrigger", self._try_strat_4hr_retrigger),
            ("strat_4hr_retrigger", self._try_strat_4hr_retrigger_short),
            ("orb_breakout", self._try_orb_breakout),
            ("orb_reclaim", self._try_orb_reclaim),
            ("orb_rejection", self._try_orb_rejection),
            ("vwap_reclaim", self._try_vwap_reclaim),
            ("vwap_rejection", self._try_vwap_rejection),
            ("vwap_hold", self._try_vwap_hold),
            ("pdh_reclaim", self._try_pdh_reclaim),
            ("pdl_reclaim", self._try_pdl_reclaim),
            ("continuation_pullback", self._try_continuation_pullback),
            ("strat_212", self._try_strat_212),
            ("strat_122", self._try_strat_122),
            ("strat_inside_break", self._try_strat_inside_break),
            ("strat_outside_continuation", self._try_strat_outside_continuation),
        ]

        for priority_index, (name, fn) in enumerate(strategies):
            if name not in enabled:
                continue
            if name in instrument_disabled:
                continue
            if orb_continuation_blocked and name not in self._ORB_PULLBACK_STRATEGIES:
                continue
            setup = fn(state)
            if setup is not None:
                yield priority_index, self._enforce_min_target_distance(setup, state.instrument)

    def _score_strategy_candidate(
        self,
        state: MarketState,
        setup: SetupDetail,
        priority_index: int,
    ) -> StrategyCandidate:
        from strategy.confluence_scorer import score_setup

        confluence = score_setup(state, setup)
        expectancy_bonus = self._RANK_EXPECTANCY_BONUS.get(
            (state.instrument, setup.strategy), 0.0
        )
        rank_score = (
            (confluence.score * 100.0)
            + (setup.rr_ratio * 10.0)
            + expectancy_bonus
            - priority_index
        )
        reason = (
            f"ranked candidate: confluence {confluence.score}/10 {confluence.grade}, "
            f"R:R {setup.rr_ratio:.2f}, expectancy_bonus {expectancy_bonus:.1f}, "
            f"priority {priority_index}"
        )
        notes = f"{setup.notes} | {reason}" if setup.notes else reason
        ranked_setup = SetupDetail(
            direction=setup.direction,
            entry=setup.entry,
            stop=setup.stop,
            target=setup.target,
            rr_ratio=setup.rr_ratio,
            strategy=setup.strategy,
            notes=notes,
        )
        return StrategyCandidate(
            setup=ranked_setup,
            confluence_score=confluence.score,
            confluence_grade=confluence.grade,
            rank_score=rank_score,
            rank_reason=reason,
            priority_index=priority_index,
        )

    def _enforce_min_target_distance(
        self, setup: SetupDetail, instrument: str
    ) -> SetupDetail:
        """Expand tiny live targets to the configured instrument minimum."""
        min_points = float(self.config.min_target_points.get(instrument, 0) or 0)
        if min_points <= 0:
            return setup

        if setup.direction == "LONG":
            current_distance = setup.target - setup.entry
            if current_distance >= min_points:
                return setup
            target = setup.entry + min_points
        elif setup.direction == "SHORT":
            current_distance = setup.entry - setup.target
            if current_distance >= min_points:
                return setup
            target = setup.entry - min_points
        else:
            return setup

        rr = RiskEngine.calculate_rr(setup.direction, setup.entry, setup.stop, target)
        note = (
            f"target expanded to {min_points:g}pt minimum for {instrument} "
            f"(was {current_distance:.2f}pt)"
        )
        notes = f"{setup.notes} | {note}" if setup.notes else note
        return SetupDetail(
            direction=setup.direction,
            entry=setup.entry,
            stop=setup.stop,
            target=round(target, 4),
            rr_ratio=rr,
            strategy=setup.strategy,
            notes=notes,
        )

    def _maybe_reanchor_entry(self, setup: SetupDetail, state: MarketState) -> SetupDetail:
        """Momentum re-anchor for LIMIT/level setups that would miss on a trend day.

        When price has already moved in the trade's favor PAST the resting entry — the
        exact trend-day no-fill case (~54% of limit setups; see fill_realism_report) —
        re-anchor the entry to the live close and rebuild stop/target preserving the
        ORIGINAL risk and reward distances (R:R unchanged). Bounded to the
        favorable-but-inside-the-original-bracket zone (1 tick < gap <= reward), so it
        can NEVER chase a feed-gap dislocation: anything past the bracket still falls to
        the entry-detachment guard. Gated on config.momentum_entry_reanchor (default off).
        """
        if not getattr(self.config, "momentum_entry_reanchor", False):
            return setup
        if setup.strategy not in _MOMENTUM_REANCHOR_SETUPS:
            return setup
        close = state.ohlc.close if state.ohlc else None
        if close is None:
            return setup
        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        risk = abs(setup.entry - setup.stop)
        reward = abs(setup.target - setup.entry)
        if risk <= 0 or reward <= 0:
            return setup

        if setup.direction == "LONG":
            gap = close - setup.entry           # >0: price ran up past the buy-limit
            if not (tick < gap <= reward):
                return setup
            new_entry, new_stop, new_target = close, close - risk, close + reward
        elif setup.direction == "SHORT":
            gap = setup.entry - close           # >0: price ran down past the sell-limit
            if not (tick < gap <= reward):
                return setup
            new_entry, new_stop, new_target = close, close + risk, close - reward
        else:
            return setup

        rr = RiskEngine.calculate_rr(setup.direction, new_entry, new_stop, new_target)
        note = f"momentum re-anchor: entry {setup.entry:g}→{new_entry:g} (favorable gap {gap:+.2f})"
        notes = f"{setup.notes} | {note}" if setup.notes else note
        return SetupDetail(
            direction=setup.direction,
            entry=round(new_entry, 4),
            stop=round(new_stop, 4),
            target=round(new_target, 4),
            rr_ratio=rr,
            strategy=setup.strategy,
            notes=notes,
        )

    @staticmethod
    def _entry_bracket_straddles_price(
        direction: str, entry: float, stop: float, target: float, price: float
    ) -> bool:
        """Is the bracket still valid if we fill at the live `price`?

        Entries are placed as MARKET orders, so the fill happens at the current
        price — not the planned entry. The protective orders must therefore still
        sit on the correct sides of that fill: stop on the loss side, target on
        the profit side. When a level (VWAP/ORB/...) is stale/detached from price
        (e.g. after a feed gap), they don't, and the trade would fill at/through
        its own bracket. Returns False in that case so the setup is rejected.
        """
        if price is None:
            return True  # no price to check against — don't block here
        if direction == "LONG":
            return stop < price < target
        if direction == "SHORT":
            return target < price < stop
        return True

    @staticmethod
    def _gex_allows_orb(state: MarketState) -> bool:
        """Return False when GEX regime is positive gamma — ORB breaks are unreliable.

        In positive gamma (dealers are long gamma), market makers hedge against
        moves by selling rallies and buying dips, compressing range and causing
        ORB breakouts to fade. Negative or neutral gamma means MMs are short gamma
        and must chase moves — ORB breaks are more likely to follow through.
        """
        gex = state.gex
        if gex is None or not gex.gex_regime:
            return True  # no GEX data — don't block
        regime = str(gex.gex_regime).lower()
        # Block in known positive gamma regimes
        if any(k in regime for k in ("positive", "pos_gamma", "long_gamma", "compressed")):
            return False
        return True

    def _try_orb_breakout(self, state: MarketState) -> Optional[SetupDetail]:
        """
        ORB Breakout: First bar where price breaks above ORB high (long) or
        below ORB low (short), with trend and VWAP aligned.
        Entry just beyond ORB boundary, stop just inside, target 2.2R.
        Only fires once per direction per day (orb_continuation_blocked gates repeats).
        GEX gate: skipped in positive gamma regime (MMs compress ORB breaks).
        """
        if not self._gex_allows_orb(state):
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        max_stop_ticks = self.MAX_ORB_STOP_TICKS.get(state.instrument, 80)
        # Stop offset beyond the ORB boundary (#3). Legacy default = 8 ticks, which
        # places the stop only ~10 ticks from entry — noise-width. Widen per
        # instrument via config.orb_stop_ticks (validated on replay).
        orb_stop_off = tick * float(self.config.orb_stop_ticks.get(state.instrument, 8))

        if state.orb.status == "above":
            if state.vwap.price_vs_vwap != "above":
                return None
            if not (state.trend and state.trend.direction == "UP"):
                return None
            # Bar must close meaningfully above ORB high — not just a wick tap
            if state.ohlc.close < state.orb.high + (tick * 2):
                return None
            # Volume must confirm the breakout
            if state.volume.relative is not None and state.volume.relative < 1.2:
                return None
            entry = state.orb.high + (tick * 2)
            orb_stop = state.orb.high - orb_stop_off
            max_stop = entry - (tick * max_stop_ticks)
            stop = max(orb_stop, max_stop)
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
                strategy="orb_breakout",
                notes="ORB high breakout — bar closes above ORB + volume confirmed",
            )

        if state.orb.status == "below":
            if state.vwap.price_vs_vwap != "below":
                return None
            if not (state.trend and state.trend.direction == "DOWN"):
                return None
            # Bar must close meaningfully below ORB low
            if state.ohlc.close > state.orb.low - (tick * 2):
                return None
            # Volume must confirm
            if state.volume.relative is not None and state.volume.relative < 1.2:
                return None
            entry = state.orb.low - (tick * 2)
            orb_stop = state.orb.low + orb_stop_off
            max_stop = entry + (tick * max_stop_ticks)
            stop = min(orb_stop, max_stop)
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
                strategy="orb_breakout",
                notes="Initial ORB low breakdown with trend and VWAP alignment",
            )

        return None

    def _try_orb_reclaim(self, state: MarketState) -> Optional[SetupDetail]:
        """
        ORB Reclaim: Price rejected above ORB high, pulled back, now reclaiming.
        Entry above ORB high, stop below ORB high, target = entry + 2x risk.
        GEX gate: skipped in positive gamma regime.
        """
        if not self._gex_allows_orb(state):
            return None
        if state.orb.status != "reclaimed_high":
            return None
        if state.vwap.price_vs_vwap != "above":
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        entry = state.orb.high + (tick * 2)
        orb_stop = state.orb.low - (tick * 4)
        max_stop = entry - (tick * self.MAX_ORB_STOP_TICKS.get(state.instrument, 80))
        stop = max(orb_stop, max_stop)
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

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        entry = state.orb.high - (tick * 2)
        orb_stop = state.orb.high + (tick * 6)
        max_stop = entry + (tick * self.MAX_ORB_STOP_TICKS.get(state.instrument, 80))
        stop = min(orb_stop, max_stop)
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

    def _vwap_entry_out_of_range(self, state: MarketState) -> bool:
        """True when the live close sits further from VWAP than the configured gate.

        VWAP setups place their entry AT VWAP (a retest play). If the live close has
        run far from VWAP there is no retest to short/buy — the entry would rest
        off-market and never fill, while (being higher-priority) it blocks momentum
        setups below it. 0 = disabled (legacy behaviour: never gates).
        """
        max_ticks = getattr(self.config, "vwap_entry_max_distance_ticks", 0.0) or 0.0
        if max_ticks <= 0:
            return False
        if not (state.vwap and state.vwap.value and state.ohlc):
            return False
        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        return abs(state.ohlc.close - state.vwap.value) > max_ticks * tick

    def _try_vwap_reclaim(self, state: MarketState) -> Optional[SetupDetail]:
        """
        VWAP Reclaim: Price was below VWAP, crossed above and held.
        Entry just above VWAP; stop 7 pts below VWAP (structural — if price
        returns 7 pts below VWAP the reclaim has failed). Target 2.2R.
        """
        if not (state.vwap.reclaimed and state.vwap.holding and state.vwap.price_vs_vwap == "above"):
            return None
        if not (state.trend and state.trend.direction == "UP"):
            return None
        if self._vwap_entry_out_of_range(state):
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        entry = state.vwap.value + (tick * 2)
        stop = state.vwap.value - (tick * 28)   # 7 pts below VWAP
        risk = entry - stop
        if risk <= 0:
            return None
        target = entry + (risk * 3.0)

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
        VWAP Hold: Price is holding below VWAP in downtrend with Strat confirmation.
        Short from VWAP; stop 7 pts above VWAP. Target 3.0R.

        Requires current bar type = two_down — this confirms the bar is actively
        extending lower, not just sitting below VWAP. Cuts low-conviction entries
        that happen to be below VWAP without directional momentum.
        """
        if not (state.vwap.holding and state.vwap.price_vs_vwap == "below"):
            return None
        if not (state.trend and state.trend.direction == "DOWN"):
            return None
        if self._vwap_entry_out_of_range(state):
            return None
        # Strat confirmation: bar must be a two_down (lower high AND lower low)
        if state.strat and state.strat.current_bar_type not in ("two_down", "2d", "2"):
            return None

        # BOS/MSS boost: if raw data is present, require bearish structure break.
        # This filters vwap_hold entries that happen on consolidation bars with no
        # actual structure break — the highest WR entries have a BOS/MSS confirming.
        raw = state.raw or {}
        bos = str(raw.get("bos_direction") or "").lower()
        mss = str(raw.get("mss_direction") or "").lower()
        ms  = str(raw.get("market_structure") or "").lower()
        if bos or mss or ms:
            # Data present — require bearish structure confirmation
            has_bearish_structure = (
                bos == "bearish" or mss == "bearish"
                or ms in ("bearish_bos", "bearish_mss")
            )
            if not has_bearish_structure:
                return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        entry = state.vwap.value - (tick * 2)
        stop = state.vwap.value + (tick * 28)   # 7 pts above VWAP
        risk = stop - entry
        if risk <= 0:
            return None
        target = entry - (risk * 3.0)

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

    def _try_vwap_rejection(self, state: MarketState) -> Optional[SetupDetail]:
        """
        VWAP Rejection: Price attempted to reclaim VWAP from below, failed,
        and closed back below it. Mirror image of vwap_reclaim.

        Conditions:
        - vwap.reclaimed was True (price crossed above VWAP this bar)
          BUT close is below VWAP (failed to hold) — the reclaim was rejected
        - Downtrend confirms direction
        - Strat bar type = two_down preferred (bar extended lower after rejection)

        This is a high-conviction SHORT: the reclaim attempt is the stop-run,
        and the close back below VWAP is the failed reclaim confirmation.
        """
        # Price crossed above VWAP this bar (attempted reclaim) but closed below it
        if not state.vwap.reclaimed:
            return None
        if state.vwap.price_vs_vwap != "below":
            return None
        if not (state.trend and state.trend.direction == "DOWN"):
            return None
        if self._vwap_entry_out_of_range(state):
            return None

        # If BOS/MSS data is present, prefer MSS bearish (highest conviction)
        # A bearish MSS means the prior bullish structure just failed — the
        # vwap_rejection is the entry bar for that structural shift.
        raw = state.raw or {}
        bos = str(raw.get("bos_direction") or "").lower()
        mss = str(raw.get("mss_direction") or "").lower()
        ms  = str(raw.get("market_structure") or "").lower()
        if bos or mss or ms:
            has_bearish_structure = (
                bos == "bearish" or mss == "bearish"
                or ms in ("bearish_bos", "bearish_mss")
            )
            if not has_bearish_structure:
                return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        entry = state.vwap.value - (tick * 2)       # just below VWAP
        stop = state.vwap.value + (tick * 20)        # above the failed reclaim high
        risk = stop - entry
        if risk <= 0:
            return None
        target = entry - (risk * 3.0)               # 3R — rejection moves fast

        rr = RiskEngine.calculate_rr("SHORT", entry, stop, target)
        return SetupDetail(
            direction="SHORT",
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy="vwap_rejection",
            notes="VWAP reclaim attempt failed — closed back below VWAP in downtrend",
        )

    def _try_pdh_reclaim(self, state: MarketState) -> Optional[SetupDetail]:
        """
        PDH Reclaim: Close is above previous day high with trend and VWAP confirmation.

        The Pine alert emits "above"/"below"/"at" — not "reclaimed" (which would
        require tracking a bar-by-bar level crossing).  We treat "above" + uptrend
        + VWAP above as the functional equivalent: price has cleared PDH and is
        holding with momentum support.
        """
        if state.previous_day.price_vs_pdh != "above":
            return None
        if not (state.trend and state.trend.direction == "UP"):
            return None
        if state.vwap.price_vs_vwap != "above":
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        entry = state.previous_day.high + (tick * 2)
        stop = state.previous_day.high - (tick * 26)  # 6.5 pts below PDH
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
            strategy="pdh_reclaim",
            notes="Price above PDH with uptrend and VWAP support",
        )

    def _try_pdl_reclaim(self, state: MarketState) -> Optional[SetupDetail]:
        """
        PDL Reclaim: Close is below previous day low with trend and VWAP confirmation.

        Same reasoning as pdh_reclaim — Pine sends "below" not "reclaimed".
        """
        if state.previous_day.price_vs_pdl != "below":
            return None
        if not (state.trend and state.trend.direction == "DOWN"):
            return None
        if state.vwap.price_vs_vwap != "below":
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        entry = state.previous_day.low - (tick * 2)
        stop = state.previous_day.low + (tick * 26)  # 6.5 pts above PDL
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
            strategy="pdl_reclaim",
            notes="Price below PDL with downtrend and VWAP below",
        )

    # ── The Strat Patterns ────────────────────────────────────────────────────

    def _try_strat_212(self, state: MarketState) -> Optional[SetupDetail]:
        """
        The Strat: 2-1-2 Continuation.

        Full pattern: 2UP → 1 (inside bar) → 2UP  (bullish continuation)
                      2DOWN → 1 → 2DOWN             (bearish continuation)

        Uses classified bar sequence from state.strat when available.
        Falls back to Phase 1 proxy (ORB inside + trend + VWAP) when absent.
        """
        tick = self.TICK_SIZE.get(state.instrument, 0.25)

        max_stop_ticks = self.MAX_ORB_STOP_TICKS.get(state.instrument, 80)

        # Use actual classified strat sequence when present
        strat = state.strat
        if strat and strat.strat_sequence == "strat_212" and strat.strat_direction:
            direction = strat.strat_direction
            if direction == "LONG":
                entry = state.ohlc.high + tick
                raw_stop = state.ohlc.low - (tick * 4)
                stop = max(raw_stop, entry - (tick * max_stop_ticks))
                risk = entry - stop
            else:
                entry = state.ohlc.low - tick
                raw_stop = state.ohlc.high + (tick * 4)
                stop = min(raw_stop, entry + (tick * max_stop_ticks))
                risk = stop - entry
            if risk <= 0:
                return None
            target = entry + (risk * 2.2) if direction == "LONG" else entry - (risk * 2.2)
            rr = RiskEngine.calculate_rr(direction, entry, stop, target)
            return SetupDetail(
                direction=direction,
                entry=round(entry, 4),
                stop=round(stop, 4),
                target=round(target, 4),
                rr_ratio=rr,
                strategy="strat_212",
                notes=f"2-1-2 continuation ({direction}): classified from bar sequence",
            )

        # Phase 1 fallback proxy: ORB inside + trend + VWAP
        if not (state.trend and state.trend.direction in ("UP", "DOWN")):
            return None
        if state.trend.strength not in ("STRONG", "MODERATE"):
            return None
        if state.orb.status != "inside":
            return None

        if state.trend.direction == "UP":
            if state.vwap.price_vs_vwap != "above":
                return None
            entry = state.ohlc.high + tick
            raw_stop = state.ohlc.low - (tick * 4)
            stop = max(raw_stop, entry - (tick * max_stop_ticks))
            risk = entry - stop
            if risk <= 0:
                return None
            target = entry + (risk * 2.2)
            direction = "LONG"
        else:
            if state.vwap.price_vs_vwap != "below":
                return None
            entry = state.ohlc.low - tick
            raw_stop = state.ohlc.high + (tick * 4)
            stop = min(raw_stop, entry + (tick * max_stop_ticks))
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
                f"inside-bar compression in trending market above/below VWAP"
            ),
        )

    def _try_strat_122(self, state: MarketState) -> Optional[SetupDetail]:
        """
        The Strat: 1-2-2 Reversal.

        Full pattern: 1 (inside) → 2DOWN → 2UP  (bullish reversal)
                      1 → 2UP → 2DOWN             (bearish reversal)

        Uses classified bar sequence from state.strat when available.
        Falls back to Phase 1 proxy (ORB rejection + opposing trend) when absent.
        """
        tick = self.TICK_SIZE.get(state.instrument, 0.25)

        # Use actual classified strat sequence when present
        strat = state.strat
        if strat and strat.strat_sequence == "strat_122" and strat.strat_direction:
            direction = strat.strat_direction
            if direction == "LONG":
                entry = state.ohlc.high + tick
                stop = state.ohlc.low - (tick * 4)
                risk = entry - stop
            else:
                entry = state.ohlc.low - tick
                stop = state.ohlc.high + (tick * 4)
                risk = stop - entry
            if risk <= 0:
                return None
            target = entry + (risk * 2.0) if direction == "LONG" else entry - (risk * 2.0)
            rr = RiskEngine.calculate_rr(direction, entry, stop, target)
            return SetupDetail(
                direction=direction,
                entry=round(entry, 4),
                stop=round(stop, 4),
                target=round(target, 4),
                rr_ratio=rr,
                strategy="strat_122",
                notes=f"1-2-2 reversal ({direction}): classified from bar sequence",
            )

        # Phase 1 fallback proxy: ORB rejection + opposing trend
        # Bullish 1-2-2: ORB high rejected but underlying trend is UP
        if (state.orb.status == "rejected_high"
                and state.trend
                and state.trend.direction == "UP"
                and state.vwap.price_vs_vwap == "above"):
            entry = state.orb.high + (tick * 2)
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
                notes="1-2-2 bullish reversal proxy: ORB high rejected with uptrend context",
            )

        # Bearish 1-2-2: ORB low rejected but underlying trend is DOWN
        if (state.orb.status == "rejected_low"
                and state.trend
                and state.trend.direction == "DOWN"
                and state.vwap.price_vs_vwap == "below"):
            entry = state.orb.low - (tick * 2)
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
                notes="1-2-2 bearish reversal proxy: ORB low rejected with downtrend context",
            )

        return None

    def _try_strat_inside_break(self, state: MarketState) -> Optional[SetupDetail]:
        """
        The Strat: Inside Bar Breakout.

        Pattern: any bar → 1 (inside bar) → 2UP or 2DOWN
        The inside bar compressed range; the breakout bar is the entry signal.
        Differs from strat_212 in that the two-bars-back bar is not directionally
        aligned — this is a pure compression-breakout, not a continuation.

        Requires trend and VWAP alignment to filter noise: an inside bar can
        break either direction, so we only take the side that agrees with
        the broader market context.

        Phase 2 only — no proxy. Requires classified strat_sequence.
        """
        strat = state.strat
        if not (strat and strat.strat_sequence == "strat_inside_break"
                and strat.strat_direction):
            return None

        direction = strat.strat_direction

        # Require trend alignment — inside break can fire either way
        if not (state.trend and state.trend.direction in ("UP", "DOWN")):
            return None
        trend_aligned = (
            (direction == "LONG"  and state.trend.direction == "UP") or
            (direction == "SHORT" and state.trend.direction == "DOWN")
        )
        if not trend_aligned:
            return None

        # VWAP must support the direction
        if direction == "LONG"  and state.vwap.price_vs_vwap not in ("above", "at"):
            return None
        if direction == "SHORT" and state.vwap.price_vs_vwap not in ("below", "at"):
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        if direction == "LONG":
            entry = state.ohlc.high + tick
            stop  = state.ohlc.low  - (tick * 4)
            risk  = entry - stop
            if risk <= 0:
                return None
            target = entry + (risk * 2.2)
        else:
            entry = state.ohlc.low  - tick
            stop  = state.ohlc.high + (tick * 4)
            risk  = stop - entry
            if risk <= 0:
                return None
            target = entry - (risk * 2.2)

        rr = RiskEngine.calculate_rr(direction, entry, stop, target)
        return SetupDetail(
            direction=direction,
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy="strat_inside_break",
            notes=(
                f"Inside bar breakout ({direction}): compression resolved "
                f"with trend and VWAP alignment"
            ),
        )

    def _try_strat_outside_continuation(self, state: MarketState) -> Optional[SetupDetail]:
        """
        The Strat: Outside Bar Continuation.

        Pattern: any bar → outside bar (2UP+2DOWN simultaneously) → 2UP or 2DOWN
        The outside bar engulfed the prior range; the follow-through bar shows
        which side won and signals continuation in that direction.

        Uses a wider stop than inside_break because the outside bar had a large
        range — the natural invalidation point is beyond that full range.
        Requires volume confirmation (outside bar follow-throughs on low volume
        are traps).

        Phase 2 only — no proxy. Requires classified strat_sequence.
        """
        strat = state.strat
        if not (strat and strat.strat_sequence == "strat_outside_continuation"
                and strat.strat_direction):
            return None

        direction = strat.strat_direction

        # Volume must confirm — outside bar follow-through on low volume = trap
        if state.volume.relative and state.volume.relative < 0.8:
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        if direction == "LONG":
            entry = state.ohlc.high + tick
            stop  = state.ohlc.low  - (tick * 6)   # wider: outside bar had large range
            risk  = entry - stop
            if risk <= 0:
                return None
            target = entry + (risk * 2.0)
        else:
            entry = state.ohlc.low  - tick
            stop  = state.ohlc.high + (tick * 6)
            risk  = stop - entry
            if risk <= 0:
                return None
            target = entry - (risk * 2.0)

        rr = RiskEngine.calculate_rr(direction, entry, stop, target)
        return SetupDetail(
            direction=direction,
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy="strat_outside_continuation",
            notes=(
                f"Outside bar follow-through ({direction}): engulf resolved "
                f"with volume confirmation"
            ),
        )

    _ET = ZoneInfo("America/New_York")
    _4HR_WINDOW_START = _time(9, 30)
    _4HR_WINDOW_END   = _time(11, 0)

    def _try_strat_4hr_retrigger(self, state: MarketState) -> Optional[SetupDetail]:
        """
        The Strat: 4HR Re-Trigger (Phase 1 approximation).

        Full pattern (1H/4H timeframe):
        4AM candle: 2DOWN (bearish)
        8AM candle: 2UP (reclaim)
        NY open: Price dips below 8AM 2UP candle high, then reclaims it = LONG entry

        Phase 1 proxy: session is new_york, bar is in the first 90 minutes of NY
        (9:30–11:00 ET — outside that window the pre-market structure is stale),
        ORB status is reclaimed_high, trend is STRONG UP, VWAP above, volume >= 0.7.

        The time gate and STRONG-trend requirement differentiate this from plain
        orb_reclaim (which fires any time, any trend strength). Evaluated first
        in the strategy list so it claims the bar when conditions are met; if it
        returns None, orb_reclaim acts as the fallback on the same bar.

        This is specifically for MNQ and MES.

        Full proprietary pattern doctrine lives outside the public repo.
        """
        if state.instrument not in ("MNQ", "MES"):
            return None
        if state.session != "new_york":
            return None
        # Time gate: pre-market structure only meaningful in early NY session
        et_time = state.timestamp.astimezone(self._ET).time()
        if not (self._4HR_WINDOW_START <= et_time <= self._4HR_WINDOW_END):
            return None
        if state.orb.status != "reclaimed_high":
            return None
        # Require STRONG trend — distinguishes from plain orb_reclaim (any strength)
        if not (state.trend and state.trend.direction == "UP"
                and state.trend.strength == "STRONG"):
            return None
        if state.vwap.price_vs_vwap != "above":
            return None
        # Volume should confirm the retrigger
        if state.volume.relative and state.volume.relative < 0.7:
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        max_stop_ticks = self.MAX_ORB_STOP_TICKS.get(state.instrument, 80)
        # Entry at ORB high (the "trigger" level)
        entry = state.orb.high + (tick * 1)
        raw_stop = state.orb.low - (tick * 6)
        stop = max(raw_stop, entry - (tick * max_stop_ticks))  # cap at 20 pts for MNQ
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

    def _try_strat_4hr_retrigger_short(self, state: MarketState) -> Optional[SetupDetail]:
        """
        SHORT mirror of the 4HR Re-Trigger.

        Pattern: pre-market established a rejection high, NY open bounces up
        to reclaim the ORB low level briefly then fails back below it.
        ORB status = reclaimed_low, trend STRONG DOWN, price below VWAP.

        Same time gate (9:30–11:00 ET), same instrument restriction (MNQ/MES).
        """
        if state.instrument not in ("MNQ", "MES"):
            return None
        if state.session != "new_york":
            return None
        et_time = state.timestamp.astimezone(self._ET).time()
        if not (self._4HR_WINDOW_START <= et_time <= self._4HR_WINDOW_END):
            return None
        if state.orb.status != "reclaimed_low":
            return None
        if not (state.trend and state.trend.direction == "DOWN"
                and state.trend.strength == "STRONG"):
            return None
        if state.vwap.price_vs_vwap != "below":
            return None
        if state.volume.relative and state.volume.relative < 0.7:
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        max_stop_ticks = self.MAX_ORB_STOP_TICKS.get(state.instrument, 80)
        entry = state.orb.low - (tick * 1)
        raw_stop = state.orb.high + (tick * 6)
        stop = min(raw_stop, entry + (tick * max_stop_ticks))
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
            strategy="strat_4hr_retrigger",
            notes=(
                "4HR Re-Trigger SHORT proxy: NY open rejects pre-market low level "
                "(ORB low) with downtrend and VWAP confirmation."
            ),
        )

    # Continuation pullback: max ticks from VWAP to qualify as "near"
    _PULLBACK_PROXIMITY_TICKS = 6

    def _try_continuation_pullback(self, state: MarketState) -> Optional[SetupDetail]:
        """
        Continuation Pullback: price in a trending market has pulled back to
        within _PULLBACK_PROXIMITY_TICKS of VWAP and is still on the correct
        side (above VWAP for LONG, below for SHORT).

        Stop uses ORB structure (ORB low for LONG, ORB high for SHORT) rather
        than a fixed VWAP offset — ORB levels are the natural invalidation
        point for intraday continuation setups. Falls back to VWAP ± 20 ticks
        when ORB isn't established yet.
        """
        if not (state.trend and state.trend.direction in ("UP", "DOWN")):
            return None
        if state.trend.strength not in ("STRONG", "MODERATE"):
            return None

        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        proximity = tick * self._PULLBACK_PROXIMITY_TICKS
        close = state.ohlc.close
        vwap = state.vwap.value

        if state.trend.direction == "UP":
            if not (state.vwap.price_vs_vwap == "above"
                    and (close - vwap) <= proximity):
                return None
            entry = close
            # Use ORB low as stop if ORB is established, otherwise wide VWAP stop
            if state.orb.low and state.orb.low > 0 and state.orb.status not in ("undefined", None):
                stop = state.orb.low - (tick * 4)
            else:
                stop = vwap - (tick * 20)
            risk = entry - stop
            if risk <= 0:
                return None
            target = entry + (risk * 2.2)
            direction = "LONG"
        else:
            if not (state.vwap.price_vs_vwap == "below"
                    and (vwap - close) <= proximity):
                return None
            entry = close
            # Use ORB high as stop if ORB is established
            if state.orb.high and state.orb.high > 0 and state.orb.status not in ("undefined", None):
                stop = state.orb.high + (tick * 4)
            else:
                stop = vwap + (tick * 20)
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
            strategy="continuation_pullback",
            notes=(
                f"Trend continuation pullback to VWAP ({state.trend.direction}): "
                f"close within {self._PULLBACK_PROXIMITY_TICKS} ticks, "
                f"stop at ORB structure"
            ),
        )
