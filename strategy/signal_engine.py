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
from datetime import datetime, time as _time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from config.settings import SystemConfig, load_config
from context.market_context import MarketState
from risk.risk_engine import RiskEngine, TradeSetup, DailyState
from strategy.gex_gate import evaluate_gex
from strategy.regime_classifier import classify_regime
from strategy.signa_gate import evaluate_signa


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


class DecisionEngine:
    WINDOWS = {
        "opening":   {"start": "09:30", "end": "10:45", "allow": "all"},
        "mid_early": {"start": "10:45", "end": "11:30", "allow": "restricted"},
        "mid_late":  {"start": "11:30", "end": "12:00", "allow": "none"},   # lunch block
        "afternoon": {"start": "12:00", "end": "14:00", "allow": "all"},    # open afternoon
        "late":      {"start": "14:00", "end": "16:00", "allow": "none"},
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

        # ── New York entry window gate ────────────────────────────────────────
        window_result = self._check_new_york_entry_window(state)
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

        signa_gate = evaluate_signa(state, gate_direction)
        if signa_gate.status == "FAIL":
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
            if not (state.trend and state.trend.strength == "STRONG"):
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

        required = ("entry", "stop", "target")
        if any(raw.get(field) is None for field in required):
            return setup

        raw_direction = str(raw.get("signal_direction") or "").upper()
        if raw_direction and raw_direction != setup.direction:
            return setup

        raw_strategy = raw.get("signal_strategy")
        if raw_strategy and raw_strategy != setup.strategy:
            return setup

        try:
            entry = float(raw["entry"])
            stop = float(raw["stop"])
            target = float(raw["target"])
        except (TypeError, ValueError):
            return setup

        if entry <= 0 or stop <= 0 or target <= 0:
            return setup

        if setup.direction == "LONG":
            if not (stop < entry < target):
                return setup
        elif setup.direction == "SHORT":
            if not (target < entry < stop):
                return setup
        else:
            return setup

        rr = RiskEngine.calculate_rr(setup.direction, entry, stop, target)
        if rr <= 0:
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
            # ── Core structural setups ─────────────────────────────────────
            ("orb_breakout", self._try_orb_breakout),
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

    def _try_orb_breakout(self, state: MarketState) -> Optional[SetupDetail]:
        """
        ORB Breakout: First bar where price breaks above ORB high (long) or
        below ORB low (short), with trend and VWAP aligned.
        Entry just beyond ORB boundary, stop just inside, target 2.2R.
        Only fires once per direction per day (orb_continuation_blocked gates repeats).
        """
        tick = self.TICK_SIZE.get(state.instrument, 0.25)
        max_stop_ticks = self.MAX_ORB_STOP_TICKS.get(state.instrument, 80)

        if state.orb.status == "above":
            if state.vwap.price_vs_vwap != "above":
                return None
            if not (state.trend and state.trend.direction == "UP"):
                return None
            entry = state.orb.high + (tick * 2)
            orb_stop = state.orb.high - (tick * 8)
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
                notes="Initial ORB high breakout with trend and VWAP alignment",
            )

        if state.orb.status == "below":
            if state.vwap.price_vs_vwap != "below":
                return None
            if not (state.trend and state.trend.direction == "DOWN"):
                return None
            entry = state.orb.low - (tick * 2)
            orb_stop = state.orb.low + (tick * 8)
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
        """
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
        VWAP Hold: Price is holding below VWAP in downtrend.
        Short from VWAP; stop 7 pts above VWAP. Target 2.2R.
        """
        if not (state.vwap.holding and state.vwap.price_vs_vwap == "below"):
            return None
        if not (state.trend and state.trend.direction == "DOWN"):
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

        See sources/strat_definitions.md for full pattern definition.
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

    # Continuation pullback: max ticks from VWAP to qualify as "near"
    _PULLBACK_PROXIMITY_TICKS = 6

    def _try_continuation_pullback(self, state: MarketState) -> Optional[SetupDetail]:
        """
        Continuation Pullback: price in a trending market has pulled back to
        within _PULLBACK_PROXIMITY_TICKS of VWAP and is still on the correct
        side (above VWAP for LONG, below for SHORT).

        Previous implementation used `price_vs_vwap == "at"` OR `holding`, but
        `holding` is True whenever price is above or below VWAP — i.e. almost
        always — making the check a no-op.  Replaced with a tick-distance gate
        so the strategy only fires when price has actually pulled back to VWAP.
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
            # Price must be above VWAP but within proximity — true pullback
            if not (state.vwap.price_vs_vwap == "above"
                    and (close - vwap) <= proximity):
                return None
            entry = close
            stop  = vwap - (tick * 8)
            risk  = entry - stop
            if risk <= 0:
                return None
            target = entry + (risk * 2.0)
            direction = "LONG"
        else:
            # Price must be below VWAP but within proximity — true pullback
            if not (state.vwap.price_vs_vwap == "below"
                    and (vwap - close) <= proximity):
                return None
            entry = close
            stop  = vwap + (tick * 8)
            risk  = stop - entry
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
            notes=(
                f"Trend continuation pullback to VWAP ({state.trend.direction}): "
                f"close within {self._PULLBACK_PROXIMITY_TICKS} ticks of VWAP"
            ),
        )
