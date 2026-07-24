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
from dataclasses import dataclass, field, replace
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
from strategy.four_hr_retrigger import advance_4hr_retrigger
from strategy.strat_212_122 import STRAT_122, STRAT_212, advance_strat_212_122
from strategy.strat_classifier import TWO_DOWN, normalize_bar_type


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
    entry_time: Optional[datetime] = None
    notes: Optional[str] = None
    direction_role: Optional[str] = None
    htf_primary_direction: Optional[str] = None
    daily_direction: Optional[str] = None
    four_hour_direction: Optional[str] = None
    direction_reason: Optional[str] = None
    rank_score: Optional[float] = None
    rank_reason: Optional[str] = None
    rank_priority_index: Optional[int] = None
    rank_confluence_score: Optional[int] = None
    rank_confluence_grade: Optional[str] = None
    selection_mode: Optional[str] = None
    # Strat 2-1-2 / 1-2-2 same-bar-both-sides-touched case only: when set, the
    # caller must journal this pre-computed outcome directly and must NEVER
    # submit it as a live order (see strategy/strat_212_122.py and the
    # webhook/runner.py / replay/replay_engine.py consumption of this field).
    pre_resolved: Optional[dict] = None


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
    candidate_audit: list[dict] = field(default_factory=list)
    blocked_candidate_audit: Optional[dict] = None

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
            "candidate_audit": self.candidate_audit,
        }
        if self.blocked_candidate_audit is not None:
            d["blocked_candidate_audit"] = self.blocked_candidate_audit
        if self.setup:
            d["setup"] = {
                "direction": self.setup.direction,
                "entry": self.setup.entry,
                "stop": self.setup.stop,
                "target": self.setup.target,
                "rr_ratio": self.setup.rr_ratio,
                "strategy": self.setup.strategy,
                "entry_time": (
                    self.setup.entry_time.isoformat()
                    if hasattr(self.setup.entry_time, "isoformat")
                    else str(self.setup.entry_time)
                    if self.setup.entry_time is not None
                    else None
                ),
                "notes": self.setup.notes,
                "direction_role": self.setup.direction_role,
                "htf_primary_direction": self.setup.htf_primary_direction,
                "daily_direction": self.setup.daily_direction,
                "four_hour_direction": self.setup.four_hour_direction,
                "direction_reason": self.setup.direction_reason,
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
    _ET = ZoneInfo("America/New_York")
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
        self._advance_4hr_retrigger(state, daily_state)
        self._advance_strat_212_122(state, daily_state)

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
            blocked_candidate_audit = self._collect_blocked_candidate_audit(
                state=state,
                condition=condition,
                daily_state=daily_state,
                blocking_gate="MARKET_CONDITION_NOT_TRENDING",
            )
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
                blocked_candidate_audit=blocked_candidate_audit,
            )

        direction_mode = getattr(self.config, "htf_direction_mode", "off")
        gate_direction = (
            self._primary_setup_direction(state)
            if direction_mode == "prioritize"
            else None
        ) or self._infer_gate_direction(state)
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
        if regime.regime == "RESTRICTED" and getattr(
            self.config, "block_restricted_regime", False
        ):
            failed_gate = regime.failed_gate or "REGIME_RESTRICTED"
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
        # Try candidates in selection order (ranked by rank_score, or fixed
        # first-match order). By default only the top candidate is tried, same
        # as historical behavior. With strategy_fallback_enabled, a candidate
        # that fails STRAT_DIRECTION_CONFLICT/ENTRY_DETACHED_FROM_PRICE/
        # RR_BELOW_MINIMUM no longer kills the whole bar — the next candidate on
        # the same bar gets a chance instead.
        candidates = self._find_setup_candidates(state, condition, daily_state)

        if not candidates:
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

        fallback_enabled = getattr(self.config, "strategy_fallback_enabled", False)

        if direction_mode == "off" and self.config.require_htf_alignment.get(
            state.instrument, False
        ):
            direction_mode = "strict"
        if direction_mode == "strict":
            aligned: list[SetupDetail] = []
            first_failure = None
            for candidate in candidates:
                failure = self._check_htf_alignment(state, candidate.direction)
                if failure is None:
                    aligned.append(
                        self._classify_setup_direction(candidate, state)
                    )
                elif first_failure is None:
                    first_failure = failure
            if not aligned:
                rejected_audit = [
                    self._setup_audit_row(
                        self._classify_setup_direction(candidate, state),
                        reject_code="HTF_ALIGNMENT_FAIL",
                    )
                    for candidate in candidates
                ]
                self._add_context_to_candidate_audit(
                    rejected_audit,
                    condition=condition,
                    regime=regime.regime,
                    state=state,
                    fallback_enabled=fallback_enabled,
                )
                return DecisionOutput(
                    timestamp=now,
                    instrument=state.instrument,
                    session=state.session,
                    decision="NO_TRADE",
                    market_condition=condition,
                    reason=first_failure or "HTF alignment failed",
                    setup=self._classify_setup_direction(candidates[0], state),
                    regime=regime.regime,
                    gex_status=gex_gate.status,
                    signa_status=signa_gate.status,
                    failed_gates=failed_gates + ["HTF_ALIGNMENT_FAIL"],
                    confidence_score=0,
                    candidate_audit=rejected_audit,
                )
            candidates = aligned
        elif direction_mode == "prioritize":
            candidates = [
                self._classify_setup_direction(candidate, state)
                for candidate in candidates
            ]
            role_priority = {"PRIMARY": 0, "COUNTERTREND_SCALP": 1, "UNRESOLVED": 2}
            candidates.sort(
                key=lambda candidate: role_priority.get(
                    candidate.direction_role or "UNRESOLVED", 2
                )
            )

        candidate_audit = [self._setup_audit_row(candidate) for candidate in candidates]
        self._add_context_to_candidate_audit(
            candidate_audit,
            condition=condition,
            regime=regime.regime,
            state=state,
            fallback_enabled=fallback_enabled,
        )

        setup = None
        last_setup = None
        reject_code = None
        reject_reason = None
        fallback_stop_index = None
        for idx, candidate in enumerate(candidates):
            candidate_audit[idx]["attempted"] = True
            candidate_audit[idx]["fallback_attempt"] = idx > 0
            if direction_mode == "prioritize" and candidate.direction_role == "UNRESOLVED":
                last_setup = candidate
                reject_code = "HTF_DIRECTION_UNRESOLVED"
                reject_reason = candidate.direction_reason or "HTF direction is unresolved"
                candidate_audit[idx]["reject_code"] = reject_code
                candidate_audit[idx]["reject_reason"] = reject_reason
                candidate_audit[idx]["failed_gates"] = [reject_code]
                if not fallback_enabled:
                    fallback_stop_index = idx
                    break
                continue
            evaluated, reject_code, reject_reason = self._evaluate_candidate(candidate, state)
            if reject_code is None:
                setup = evaluated
                candidate_audit[idx]["selected"] = True
                candidate_audit[idx]["winner"] = True
                if idx > 0:
                    note = (
                        f"fallback candidate {idx + 1}/{len(candidates)} "
                        f"(earlier candidate(s) rejected on this bar)"
                    )
                    setup.notes = f"{setup.notes} | {note}" if setup.notes else note
                break
            last_setup = evaluated
            candidate_audit[idx]["reject_code"] = reject_code
            candidate_audit[idx]["reject_reason"] = reject_reason
            candidate_audit[idx]["failed_gates"] = [reject_code] if reject_code else []
            if not fallback_enabled:
                fallback_stop_index = idx
                break

        if fallback_stop_index is not None and not fallback_enabled:
            for row in candidate_audit[fallback_stop_index + 1:]:
                row["fallback_skipped"] = True
                row["skip_reason"] = "fallback_disabled_after_rejection"

        if setup is None:
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                market_condition=condition,
                reason=reject_reason,
                setup=last_setup,
                regime=regime.regime,
                gex_status=gex_gate.status,
                signa_status=signa_gate.status,
                failed_gates=failed_gates + [reject_code],
                confidence_score=0,
                candidate_audit=candidate_audit,
            )

        if (
            direction_mode == "prioritize"
            and setup.direction_role == "COUNTERTREND_SCALP"
        ):
            return DecisionOutput(
                timestamp=now,
                instrument=state.instrument,
                session=state.session,
                decision="NO_TRADE",
                market_condition=condition,
                reason=(
                    "Qualified countertrend scalp requires the matching armed "
                    "5-minute entry confirmation."
                ),
                setup=setup,
                regime=regime.regime,
                gex_status=gex_gate.status,
                signa_status=signa_gate.status,
                failed_gates=failed_gates + ["COUNTERTREND_REQUIRES_5M"],
                confidence_score=0,
                candidate_audit=candidate_audit,
            )

        # ── Strategy permission gate ──────────────────────────────────────────
        # Separate from every gate above: those decide whether a setup is
        # technically valid; this decides whether its strategy has earned the
        # right to reach paper/live execution at all. Disabled by default
        # (see config.strategy_permission_gate_enabled) so this is a no-op
        # until risk_rules.yaml explicitly turns it on.
        if getattr(self.config, "strategy_permission_gate_enabled", False):
            status = self.config.strategy_status.get(
                setup.strategy,
                getattr(self.config, "strategy_permission_default_status", "SHADOW_ONLY"),
            )
            # MNQ vwap_hold proof lane (restoration candidate #3, 2026-07-14):
            # the ONLY exception this gate has. Opens exclusively for
            # MNQ + vwap_hold + new_york + MNQ_VWAP_HOLD_PROOF_MODE=paper_sim
            # — in which case webhook/runner.py's proof hook forces PaperBroker
            # + market entry + runner exit (or suppresses a duplicate), so the
            # excepted decision can never reach the normal IOC/static/Tradovate
            # path. Every other strategy/instrument/session/mode is unaffected.
            from context.mnq_vwap_hold_proof import permission_gate_exception

            _vwap_hold_proof_exception = permission_gate_exception(
                state.instrument, setup.strategy, state.session, self.config
            )
            if status != "PAPER_ELIGIBLE" and not _vwap_hold_proof_exception:
                for row in candidate_audit:
                    if row.get("winner"):
                        row["strategy_permission_status"] = status
                        row["strategy_permission_blocked"] = True
                        row["reject_code"] = "STRATEGY_NOT_PAPER_ELIGIBLE"
                return DecisionOutput(
                    timestamp=now,
                    instrument=state.instrument,
                    session=state.session,
                    decision="NO_TRADE",
                    market_condition=condition,
                    reason=(
                        f"Strategy '{setup.strategy}' is not paper-eligible "
                        f"(status={status}). Setup otherwise qualified."
                    ),
                    setup=setup,
                    regime=regime.regime,
                    gex_status=gex_gate.status,
                    signa_status=signa_gate.status,
                    failed_gates=failed_gates + ["STRATEGY_NOT_PAPER_ELIGIBLE"],
                    confidence_score=0,
                    candidate_audit=candidate_audit,
                )

        # ── TRADE: mark ORB break as played so continuation strategies are
        # blocked on subsequent bars above/below the same ORB level.
        # Pull-back strategies (orb_reclaim, vwap_reclaim, etc.) remain eligible.
        # When orb_reclaim fires it means price returned to the ORB, which
        # resets the break — clear the flag so a fresh break can be traded again.
        if state.orb.status == "above":
            daily_state.orb_break_long_played[state.instrument] = True
        elif state.orb.status == "below":
            daily_state.orb_break_short_played[state.instrument] = True
        elif setup.strategy == "orb_reclaim":
            # Price pulled back to the ORB and is reclaiming — reset so the
            # next clean break above is eligible again.
            daily_state.orb_break_long_played[state.instrument] = False
        elif setup.strategy == "orb_rejection":
            daily_state.orb_break_short_played[state.instrument] = False

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
            candidate_audit=candidate_audit,
        )

    @staticmethod
    def _setup_audit_row(
        setup: SetupDetail, reject_code: Optional[str] = None
    ) -> dict:
        return {
            "strategy": setup.strategy,
            "direction": setup.direction,
            "candidate_direction": setup.direction,
            "entry": setup.entry,
            "stop": setup.stop,
            "target": setup.target,
            "rr_ratio": setup.rr_ratio,
            "rank_score": setup.rank_score,
            "rank_reason": setup.rank_reason,
            "rank_priority_index": setup.rank_priority_index,
            "rank_confluence_score": setup.rank_confluence_score,
            "rank_confluence_grade": setup.rank_confluence_grade,
            "selection_mode": setup.selection_mode,
            "direction_role": setup.direction_role,
            "htf_primary_direction": setup.htf_primary_direction,
            "daily_direction": setup.daily_direction,
            "four_hour_direction": setup.four_hour_direction,
            "direction_reason": setup.direction_reason,
            "attempted": False,
            "selected": False,
            "winner": False,
            "fallback_attempt": False,
            "fallback_enabled": False,
            "fallback_skipped": False,
            "skip_reason": None,
            "reject_code": reject_code,
            "reject_reason": None,
            "failed_gates": [reject_code] if reject_code else [],
            "context_ref": "journal.context",
            "market_condition": None,
            "regime": None,
            "stale_data_flags": [],
            "strategy_permission_status": None,
            "strategy_permission_blocked": False,
        }

    @staticmethod
    def _stale_data_flags(state: MarketState) -> list[str]:
        raw = state.raw if isinstance(getattr(state, "raw", None), dict) else {}
        flags: list[str] = []
        if str(raw.get("zone_state") or "").lower() == "stale":
            flags.append("zone_state_stale")
        if str(raw.get("data_status") or "").lower() == "stale":
            flags.append("data_status_stale")
        return flags

    def _add_context_to_candidate_audit(
        self,
        rows: list[dict],
        *,
        condition: str,
        regime: str,
        state: MarketState,
        fallback_enabled: bool,
    ) -> None:
        stale_flags = self._stale_data_flags(state)
        for row in rows:
            row["market_condition"] = condition
            row["regime"] = regime
            row["fallback_enabled"] = fallback_enabled
            row["stale_data_flags"] = list(stale_flags)

    @staticmethod
    def _direction_value(value: object) -> Optional[str]:
        normalized = str(value or "").strip().upper()
        return normalized if normalized in {"UP", "DOWN"} else None

    def _primary_setup_direction(self, state: MarketState) -> Optional[str]:
        htf = getattr(state, "htf", None)
        daily = self._direction_value(getattr(htf, "daily_direction", None))
        four_hour = self._direction_value(
            getattr(htf, "four_hour_direction", None)
        )
        # The Daily candle is slow context, not an intraday execution signal.
        # When Daily and 4H disagree, using Daily first can keep every downstream
        # gate latched to yesterday's direction through a full 4H reversal. The
        # 4H direction is the actionable primary in that conflict; Daily remains
        # recorded on the setup for audit and countertrend classification.
        conflict_policy = getattr(
            self.config, "htf_conflict_policy", "four_hour_confirmed"
        )
        conflict_primary = daily if conflict_policy == "daily" else four_hour
        if conflict_policy == "block":
            conflict_primary = None
        if (
            conflict_policy == "four_hour_confirmed"
            and daily
            and four_hour
            and daily != four_hour
        ):
            one_hour = self._direction_value(
                getattr(htf, "one_hour_direction", None)
            )
            trend = self._direction_value(
                getattr(getattr(state, "trend", None), "direction", None)
            )
            if one_hour != four_hour or trend != four_hour:
                conflict_primary = None
        primary = (
            conflict_primary
            if daily and four_hour and daily != four_hour
            else daily or four_hour
        )
        return "LONG" if primary == "UP" else "SHORT" if primary == "DOWN" else None

    def _classify_setup_direction(
        self, setup: SetupDetail, state: MarketState
    ) -> SetupDetail:
        """Classify a setup without changing its bracket or strategy semantics."""
        htf = getattr(state, "htf", None)
        daily = self._direction_value(getattr(htf, "daily_direction", None))
        four_hour = self._direction_value(
            getattr(htf, "four_hour_direction", None)
        )
        primary_direction = self._primary_setup_direction(state)
        htf_conflict = bool(daily and four_hour and daily != four_hour)
        conflict_policy = getattr(
            self.config, "htf_conflict_policy", "four_hour_confirmed"
        )
        conflict_primary_direction = self._primary_setup_direction(state)
        conflict_primary = (
            "UP" if conflict_primary_direction == "LONG"
            else "DOWN" if conflict_primary_direction == "SHORT"
            else None
        )
        primary_htf = conflict_primary if htf_conflict else daily or four_hour

        if primary_direction is None:
            role = "UNRESOLVED"
            reason = (
                f"Daily {daily} conflicts with 4H {four_hour}; "
                + (
                    "conflict policy blocks execution."
                    if conflict_policy == "block"
                    else "4H lacks matching 1H and active-trend confirmation."
                )
                if htf_conflict and conflict_policy in {"block", "four_hour_confirmed"}
                else "Daily and 4H directions are unavailable or neutral."
            )
        elif setup.direction == primary_direction:
            role = "PRIMARY"
            if htf_conflict:
                reason = (
                    f"Daily {daily} conflicts with 4H {four_hour}; configured "
                    f"{conflict_policy} direction supports {setup.direction}."
                )
            else:
                source = "Daily" if daily else "4H fallback"
                reason = f"{source} direction {primary_htf} supports {setup.direction}."
        else:
            expected_four_hour = "UP" if setup.direction == "LONG" else "DOWN"
            local_direction = getattr(getattr(state, "strat", None), "strat_direction", None)
            if (
                not htf_conflict
                and daily
                and four_hour == expected_four_hour
                and local_direction == setup.direction
            ):
                role = "COUNTERTREND_SCALP"
                reason = (
                    f"Daily {daily} remains primary; 4H {four_hour} and 15m "
                    f"Strat {local_direction} confirm a tactical {setup.direction} pullback."
                )
            else:
                role = "UNRESOLVED"
                reason = (
                    f"{setup.direction} opposes primary {primary_direction} without "
                    "matching 4H and 15m confirmation."
                )

        return replace(
            setup,
            direction_role=role,
            htf_primary_direction=primary_direction,
            daily_direction=daily,
            four_hour_direction=four_hour,
            direction_reason=reason,
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
        return replace(setup, notes=f"{notes} | {suffix}" if notes else suffix)

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
        return replace(
            setup,
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            notes=notes,
        )

    # ── Market Condition Scoring ───────────────────────────────────────────────

    def _check_htf_alignment(self, state: MarketState, direction: str | None) -> Optional[str]:
        """Require explicit, directionally consistent HTF context.

        This gate is fail-closed when enabled: missing or mixed HTF data cannot
        authorize an entry, and the daily direction may never oppose the order.
        """
        htf = getattr(state, "htf", None)
        if direction not in {"LONG", "SHORT"}:
            return "Trade direction unavailable for HTF alignment"
        if htf is None:
            return "HTF context unavailable"
        if htf.ftfc_aligned is False:
            return "HTF/FTFC alignment failed"

        expected = "UP" if direction == "LONG" else "DOWN"
        opposing = "DOWN" if expected == "UP" else "UP"
        daily = str(htf.daily_direction or "").strip().upper()
        if not daily:
            return "Daily direction unavailable"
        if daily == opposing:
            return f"Daily direction {daily} opposes {direction}"

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
        if not directions:
            return "HTF directions unavailable"
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
        if pine in ("TRENDING", "RANGE_BOUND"):
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

    # Strategies not governed by the one-directional ORB-break replay flag.
    # The canonical 4HR state machine is structurally independent of ORB.
    _ORB_CONTINUATION_EXEMPT: frozenset[str] = frozenset({
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
        """Return the single top-priority setup (highest rank_score, or first
        match in fixed order). Kept for callers that only want one candidate;
        the engine's own decide() uses _find_setup_candidates directly so it
        can fall back to the next candidate when the top one is rejected."""
        candidates = self._find_setup_candidates(state, condition, daily_state)
        return candidates[0] if candidates else None

    def _find_setup_candidates(
        self,
        state: MarketState,
        condition: str,
        daily_state: Optional[DailyState] = None,
    ) -> list[SetupDetail]:
        """
        Return every currently-qualifying setup, in selection-priority order
        (highest rank_score first in "ranked" mode, fixed strategy order in
        "first_match" mode — see collect_strategy_candidates / _iter_enabled_setups).

        When daily_state is provided, continuation strategies are skipped after
        the first ORB break trade in each direction — only pull-back setups
        (orb_reclaim, orb_rejection, vwap_reclaim) remain
        eligible until price returns to the ORB level.
        """
        del condition  # retained for API symmetry with collect_strategy_candidates

        if getattr(self.config, "strategy_selection_mode", "first_match") == "ranked":
            return [
                c.setup
                for c in self.collect_strategy_candidates(state, "", daily_state)
            ]

        return [
            self._with_candidate_audit_metadata(
                state,
                setup,
                priority_index,
                selection_mode="first_match",
                add_rank_note=False,
            )
            for priority_index, setup in self._iter_enabled_setups(state, daily_state)
        ]

    def _evaluate_candidate(
        self, setup: SetupDetail, state: MarketState
    ) -> tuple[Optional[SetupDetail], Optional[str], Optional[str]]:
        """
        Apply the per-candidate confirmation / entry-sanity / R:R gates to one
        setup. Returns (validated_setup, None, None) on success, or
        (setup_or_None, reject_code, reject_reason) on rejection — setup is
        None for STRAT_DIRECTION_CONFLICT (no bracket to show) and the fully
        transformed setup for the other two rejections (matches the audit
        shape decide() has always produced for a single-candidate rejection).
        """
        if setup.strategy in ("strat_4hr_retrigger", STRAT_212, STRAT_122):
            # Entry/stop/target are already the canonical resolved formula
            # (causal boundary anchor for 212/122; completed-1H stop and
            # prior-4PM target for 4HR). Generic Pine/advisory and distance
            # transforms must not rewrite these strategies' identity — for a
            # pre_resolved (same-bar-both-sides) 212/122 candidate this is
            # doubly required: mutating entry/stop here would desync the
            # SetupDetail from the already-fixed P&L the caller journals.
            confirmed = setup
        else:
            confirmed = self._apply_strat_confirmation(setup, state)
            if confirmed is None:
                return None, "STRAT_DIRECTION_CONFLICT", "Strat bar sequence contradicts setup direction."

            confirmed = self._apply_advisory_bracket(confirmed, state)
            confirmed = self._enforce_min_target_distance(confirmed, state.instrument)
            confirmed = self._maybe_reanchor_entry(confirmed, state)

        # ── Entry-sanity guard (stale/detached level) ─────────────────────────
        # Every setup anchors its entry to a level (VWAP/ORB/PDH...). After a
        # feed gap that level can be stranded far from the live price, so a
        # MARKET entry would fill ~120pt from plan and the absolute bracket lands
        # on the wrong side of the fill (this caused the 2026-06-05 03:15 ET MNQ
        # scratch: SHORT with target 30178.75 ABOVE the 30080.75 fill). Require
        # the bracket to still straddle the current price; otherwise the signal
        # is stale — refuse rather than chase a broken market fill.
        # A pre_resolved candidate (strat_212/122 same-bar-both-sides case) is
        # never filled as a live MARKET order at the current close — it is an
        # already-fixed, fully-resolved evidence record — so this guard,
        # built for "is a market fill near current price still sane," does
        # not apply to it.
        entry_price = state.ohlc.close if (state.ohlc and confirmed.pre_resolved is None) else None
        if entry_price is not None and not self._entry_bracket_straddles_price(
            confirmed.direction, confirmed.entry, confirmed.stop, confirmed.target, entry_price
        ):
            # Scoped proof-lane carve-out: this guard exists to stop a MARKET
            # fill landing on the wrong side of an anchored bracket. When a
            # proof lane's active mode forces a market entry AT THE LIVE PRICE
            # for this exact candidate (MNQ orb_breakout, or MNQ vwap_hold in
            # new_york), the anchor no longer determines the fill and the
            # rejection would only re-create the defect the lane exists to
            # measure (detachment is orb_breakout's dominant failure mode:
            # 18/18 in the study + the first natural candidate, 2026-07-14).
            # orb_reclaim is deliberately NOT carved out — its detachment
            # question belongs to the entry-refresh shadow lane.
            from context.mnq_orb_breakout_proof import proof_market_entry_active
            from context.mnq_vwap_hold_proof import permission_gate_exception

            _proof_market_entry = proof_market_entry_active(
                state.instrument, confirmed.strategy, self.config
            ) or permission_gate_exception(
                state.instrument, confirmed.strategy, state.session, self.config
            )
            if not _proof_market_entry:
                reason = (
                    f"Entry {confirmed.entry:g} detached from price {entry_price:g} "
                    f"(stop {confirmed.stop:g} / target {confirmed.target:g} no longer "
                    f"straddle the live price) — stale level after a feed gap; "
                    f"not chasing a market fill."
                )
                return confirmed, "ENTRY_DETACHED_FROM_PRICE", reason

        # ── R:R validation ────────────────────────────────────────────────────
        if confirmed.rr_ratio < self.config.min_rr_ratio:
            reason = (
                f"Setup found ({confirmed.strategy}) but R:R {confirmed.rr_ratio:.2f} "
                f"is below minimum {self.config.min_rr_ratio:.2f}."
            )
            return confirmed, "RR_BELOW_MINIMUM", reason

        return confirmed, None, None

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

    def _collect_blocked_candidate_audit(
        self,
        *,
        state: MarketState,
        condition: str,
        daily_state: DailyState,
        blocking_gate: str,
    ) -> dict:
        """Describe candidates hidden by an early market-condition return.

        Observation only: this method never selects a setup, mutates daily state,
        evaluates risk, or reaches order/broker code.  Keeping this payload
        separate from ``candidate_audit`` also prevents opportunity/lifecycle
        tracking from treating these diagnostics as executable candidates.
        """
        permission_gate = bool(
            getattr(self.config, "strategy_permission_gate_enabled", False)
        )
        default_status = getattr(
            self.config, "strategy_permission_default_status", "SHADOW_ONLY"
        )
        strategy_status = getattr(self.config, "strategy_status", {}) or {}
        candidates: list[dict] = []
        for candidate in self.collect_strategy_candidates(state, condition, daily_state):
            setup = candidate.setup
            permission_status = (
                strategy_status.get(setup.strategy, default_status)
                if permission_gate
                else "NOT_ENFORCED"
            )
            candidates.append(
                {
                    "strategy": setup.strategy,
                    "instrument": state.instrument,
                    "direction": setup.direction,
                    "entry": setup.entry,
                    "stop": setup.stop,
                    "target": setup.target,
                    "rr_ratio": setup.rr_ratio,
                    "rank_score": candidate.rank_score,
                    "rank_reason": candidate.rank_reason,
                    "rank_priority_index": candidate.priority_index,
                    "confluence_score": candidate.confluence_score,
                    "confluence_grade": candidate.confluence_grade,
                    "market_condition": condition,
                    "trend_direction": getattr(state.trend, "direction", None),
                    "trend_strength": getattr(state.trend, "strength", None),
                    "session": state.session,
                    "blocking_gate": blocking_gate,
                    "candidate_validation_code": None,
                    "candidate_validation_reason": None,
                    "strategy_permission_status": permission_status,
                    "strategy_permission_blocked": (
                        permission_gate and permission_status != "PAPER_ELIGIBLE"
                    ),
                    "strategy_permission_diagnostic_only": True,
                    "observation_only": True,
                    "selected": False,
                    "winner": False,
                    "attempted": False,
                    "risk_evaluated": False,
                    "broker_evaluated": False,
                    "downstream_gates_evaluated": False,
                }
            )
        return {
            "blocking_gate": blocking_gate,
            "observation_only": True,
            "final_decision_unchanged": True,
            "risk_evaluated": False,
            "broker_evaluated": False,
            "downstream_gates_evaluated": False,
            "candidates": candidates,
        }

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
        if state.canonical_4hr_only:
            # The established 5-minute lane is entry authority only.  The
            # canonical 4HR strategy is the sole setup allowed to originate
            # directly from it; all legacy 15-minute concepts remain excluded.
            enabled = [
                name for name in enabled if name == "strat_4hr_retrigger"
            ]
        instrument_disabled = set(
            self.config.disabled_concepts_per_instrument.get(state.instrument, [])
        )

        orb_continuation_blocked = False
        if daily_state is not None:
            if state.orb.status == "above" and daily_state.orb_break_long_played.get(state.instrument, False):
                orb_continuation_blocked = True
            elif state.orb.status == "below" and daily_state.orb_break_short_played.get(state.instrument, False):
                orb_continuation_blocked = True

        strategies = [
            ("strat_4hr_retrigger", self._try_strat_4hr_retrigger),
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
            if orb_continuation_blocked and name not in self._ORB_CONTINUATION_EXEMPT:
                continue
            setup = fn(state)
            if setup is not None:
                if setup.strategy == "strat_4hr_retrigger":
                    yield priority_index, setup
                else:
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
        ranked_setup = replace(
            setup,
            notes=notes,
            rank_score=rank_score,
            rank_reason=reason,
            rank_priority_index=priority_index,
            rank_confluence_score=confluence.score,
            rank_confluence_grade=confluence.grade,
            selection_mode="ranked",
        )
        return StrategyCandidate(
            setup=ranked_setup,
            confluence_score=confluence.score,
            confluence_grade=confluence.grade,
            rank_score=rank_score,
            rank_reason=reason,
            priority_index=priority_index,
        )

    def _with_candidate_audit_metadata(
        self,
        state: MarketState,
        setup: SetupDetail,
        priority_index: int,
        *,
        selection_mode: str,
        add_rank_note: bool,
    ) -> SetupDetail:
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
            f"{selection_mode} candidate audit: confluence {confluence.score}/10 "
            f"{confluence.grade}, R:R {setup.rr_ratio:.2f}, "
            f"expectancy_bonus {expectancy_bonus:.1f}, priority {priority_index}"
        )
        notes = setup.notes
        if add_rank_note:
            notes = f"{setup.notes} | {reason}" if setup.notes else reason
        return replace(
            setup,
            notes=notes,
            rank_score=rank_score,
            rank_reason=reason,
            rank_priority_index=priority_index,
            rank_confluence_score=confluence.score,
            rank_confluence_grade=confluence.grade,
            selection_mode=selection_mode,
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
        return replace(
            setup,
            target=round(target, 4),
            rr_ratio=rr,
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
        return replace(
            setup,
            entry=round(new_entry, 4),
            stop=round(new_stop, 4),
            target=round(new_target, 4),
            rr_ratio=rr,
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
        # Strat confirmation: bar must be a two_down (lower high AND lower low).
        # normalize_bar_type resolves known dialects (2D/2d -> two_down); it
        # deliberately does NOT resolve bare "2" — that token carries no
        # direction (CSV sources that lack a directional column can still
        # emit it) and must never be silently treated as confirming
        # two_down. Fail closed when Strat context exists but its
        # directional bar type is ambiguous; preserve the existing
        # behavior when Strat context is absent entirely (confirmation
        # skipped by design in that case, not evaluated here at all).
        if state.strat and normalize_bar_type(state.strat.current_bar_type) != TWO_DOWN:
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
        VWAP Rejection: Price reclaimed VWAP, failed to hold, and closed back
        below it on the VERY NEXT bar. Mirror image of vwap_reclaim.

        Causal, one-bar-lookback condition: state.vwap.failed_reclaim is True
        only on the bar immediately following a genuine VWAP reclaim (the
        PRIOR bar's own reclaimed status) that then closes back below VWAP
        THIS bar. Populated upstream — live: Pine sends it directly (Pine
        tracks its own crossover state across bars, unaffected by whether
        the backend even evaluates a given bar); replay:
        replay/replay_engine.py derives it from the candle sequence itself,
        independent of DecisionEngine/DailyState. A prior version of this
        check required vwap.reclaimed and price_vs_vwap == "below" on the
        SAME bar — structurally impossible, since reclaimed can only be True
        on a bar where price closed above VWAP (see
        docs/vwap-hold-vs-vwap-rejection-overlap-audit-2026-07-23.md,
        PR #308) — so this setup could never fire. Fixed here.

        This is a high-conviction SHORT: the reclaim attempt is the stop-run,
        and the close back below VWAP is the failed reclaim confirmation.
        """
        if not (state.vwap and state.vwap.failed_reclaim):
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

    def _strat_212_122_setup(
        self, state: MarketState, pattern: str, label: str
    ) -> Optional[SetupDetail]:
        """Shared consumer for both canonical Strat patterns' candidates.

        Returns only what strategy/strat_212_122.py's causal state machine
        produced this bar for the requested pattern — no proxy, no fallback.
        A "RESOLVED" candidate (same-bar both-sides-touched) still returns a
        SetupDetail, carrying pre_resolved: the caller (webhook/runner.py /
        replay/replay_engine.py) must journal it directly and must never
        submit it as a live order.
        """
        candidate = state.strat_212_122_candidate
        if not candidate or candidate.get("pattern") != pattern:
            return None
        direction = str(candidate["direction"])

        if candidate["kind"] == "RESOLVED":
            entry = float(candidate["entry"])
            stop = float(candidate["stop"])
            target = float(candidate["target"])
            exit_price = float(candidate["exit"])
            result = str(candidate["result"])
            rr = RiskEngine.calculate_rr(direction, entry, stop, target)
            if result == "WIN":
                notes = (
                    f"{label} ({direction}): watched bar reached both the "
                    f"armed entry boundary and the target in the same bar — "
                    f"resolved same-bar as WIN."
                )
            else:
                notes = (
                    f"{label} ({direction}): armed entry boundary and its "
                    f"opposite (stop) boundary both crossed on the watched "
                    f"bar — OHLC cannot establish order, resolved "
                    f"pessimistically as LOSS."
                )
            return SetupDetail(
                direction=direction,
                entry=round(entry, 4),
                stop=round(stop, 4),
                target=round(target, 4),
                rr_ratio=rr,
                strategy=pattern,
                notes=notes,
                pre_resolved={
                    "result": result,
                    "exit_price": exit_price,
                    "exit_reason": candidate["exit_reason"],
                },
            )

        entry = float(candidate["entry"])
        stop = float(candidate["stop"])
        target = float(candidate["target"])
        rr = RiskEngine.calculate_rr(direction, entry, stop, target)
        return SetupDetail(
            direction=direction,
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy=pattern,
            notes=(
                f"{label} ({direction}): causal armed-boundary trigger — "
                f"entry/stop anchored to the prior reference bar, target is "
                f"a fixed 2R VP convention (not canonical Strat doctrine)."
            ),
        )

    def _try_strat_212(self, state: MarketState) -> Optional[SetupDetail]:
        """
        The Strat: 2-1-2 Continuation — canonical sequence only, no proxy.

        Full pattern: 2UP → 1 (inside bar) → 2UP  (bullish continuation)
                      2DOWN → 1 → 2DOWN             (bearish continuation)

        Entry/stop anchor to the inside bar (the prior reference bar), never
        to the bar being evaluated — see strategy/strat_212_122.py.
        """
        return self._strat_212_122_setup(state, STRAT_212, "2-1-2 continuation")

    def _try_strat_122(self, state: MarketState) -> Optional[SetupDetail]:
        """
        The Strat: 1-2-2 Reversal — canonical sequence only, no proxy.

        Full pattern: 1 (inside) → 2DOWN → 2UP  (bullish reversal)
                      1 → 2UP → 2DOWN             (bearish reversal)

        Entry/stop anchor to the prior directional bar (the reference bar
        being reversed), never to the bar being evaluated — see
        strategy/strat_212_122.py.
        """
        return self._strat_212_122_setup(state, STRAT_122, "1-2-2 reversal")

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

    @staticmethod
    def _is_five_minute_state(state: MarketState) -> bool:
        value = str(getattr(state.ohlc, "timeframe", "") or "").strip().lower()
        return value in {"5", "5m", "5min", "5minute", "5minutes"}

    def _advance_4hr_retrigger(
        self, state: MarketState, daily_state: DailyState
    ) -> None:
        """Advance the canonical persisted state before ordinary strategy gates."""
        state.four_hr_retrigger_candidate = None
        if "strat_4hr_retrigger" not in self.config.enabled_concepts:
            return
        if not self._is_five_minute_state(state):
            return
        next_state, candidate = advance_4hr_retrigger(
            bars_5m=state.bar_history_5m,
            current_bar_ts=state.timestamp,
            instrument=state.instrument,
            persisted_state=daily_state.four_hr_retrigger_state.get(state.instrument, {}),
        )
        daily_state.four_hr_retrigger_state[state.instrument] = next_state
        state.four_hr_retrigger_candidate = candidate

    def _advance_strat_212_122(
        self, state: MarketState, daily_state: DailyState
    ) -> None:
        """Advance the canonical causal 2-1-2/1-2-2 state before ordinary gates.

        Genuine two-phase armed state machine (mirrors #317's shape and, for
        this pattern, actually needs the cross-bar memory: phase 1 arms from
        THIS bar's own type/OHLC when it completes a precursor; phase 2
        resolves that already-fixed boundary against the NEXT bar's OHLC).
        Advanced unconditionally each bar so a precursor forming while other
        gates would otherwise block a trade this bar is still armed and
        watched on the correct next bar. Whether a resulting candidate is
        ever actually acted on is gated entirely by the normal
        enabled_concepts/session/capacity chain in _iter_enabled_setups,
        exactly like any other strategy.
        """
        state.strat_212_122_candidate = None
        if not (
            "strat_212" in self.config.enabled_concepts
            or "strat_122" in self.config.enabled_concepts
        ):
            return
        strat = state.strat
        next_state, candidate = advance_strat_212_122(
            current_bar_type=strat.current_bar_type if strat else None,
            previous_bar_type=strat.previous_bar_type if strat else None,
            current_open=state.ohlc.open,
            current_high=state.ohlc.high,
            current_low=state.ohlc.low,
            tick_size=self.TICK_SIZE.get(state.instrument, 0.25),
            trading_date=state.timestamp.date().isoformat(),
            persisted_state=daily_state.strat_212_122_state.get(state.instrument, {}),
        )
        daily_state.strat_212_122_state[state.instrument] = next_state
        state.strat_212_122_candidate = candidate

    def _try_strat_4hr_retrigger(self, state: MarketState) -> Optional[SetupDetail]:
        """Return only the candidate produced by the resolved state machine."""
        candidate = state.four_hr_retrigger_candidate
        if not candidate:
            return None
        direction = str(candidate["direction"])
        entry = float(candidate["entry"])
        stop = float(candidate["stop"])
        target = float(candidate["target"])
        rr = RiskEngine.calculate_rr(direction, entry, stop, target)
        return SetupDetail(
            direction=direction,
            entry=round(entry, 4),
            stop=round(stop, 4),
            target=round(target, 4),
            rr_ratio=rr,
            strategy="strat_4hr_retrigger",
            entry_time=candidate["entry_time"],
            notes=(
                "Resolved 4HR Re-Trigger: prior-4PM/4AM classification, "
                "pre-09:30 break-retrigger, dynamic completed-1H fixed stop."
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
