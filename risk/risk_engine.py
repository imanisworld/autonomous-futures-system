"""
risk/risk_engine.py

Deterministic risk rule enforcement.
Every check is explicit, ordered, and logged.
No check may be skipped or overridden.

Returns RiskResult(APPROVED) or RiskResult(REJECTED, failed_rule, reason).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

from config.settings import SystemConfig, load_config, LiveTradingBlockedError


# ─── Result Types ─────────────────────────────────────────────────────────────

@dataclass
class DailyState:
    """Tracks per-day trading activity. Populated from journal at startup."""
    trade_count: int = 0
    consecutive_losses: int = 0
    has_open_position: bool = False
    date: Optional[str] = None  # YYYY-MM-DD
    # ORB break played — prevents re-entry on the same directional ORB break.
    # Cleared automatically when the ORB reclaim/rejection strategy fires
    # (price returned to the ORB, resetting the setup).
    orb_break_long_played: bool = False
    orb_break_short_played: bool = False
    # Per-session trade counts — keyed by session name (asian/london/new_york).
    session_trade_counts: Dict[str, int] = field(default_factory=dict)


@dataclass
class TradeSetup:
    """A proposed trade, output from DecisionEngine."""
    direction: str          # LONG | SHORT
    entry: float
    stop: float
    target: float
    rr_ratio: float
    strategy: str
    instrument: str
    session: str
    contracts: int = 1
    notes: Optional[str] = None
    entry_time: Optional[datetime] = None  # UTC; used for session cutoff check


@dataclass
class RiskResult:
    result: str                     # APPROVED | REJECTED
    failed_rule: Optional[str] = None
    reason: Optional[str] = None

    @property
    def approved(self) -> bool:
        return self.result == "APPROVED"

    @property
    def rejected(self) -> bool:
        return self.result == "REJECTED"


# ─── Risk Engine ──────────────────────────────────────────────────────────────

class RiskEngine:
    """
    Deterministic enforcement of all risk rules from risk_rules.yaml.

    Rule checks are performed in a fixed order. The first failure terminates
    the check and returns REJECTED with the specific rule name and reason.
    All checks are independent and can be unit-tested in isolation.
    """

    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or load_config()

    def validate(self, setup: TradeSetup, daily_state: DailyState) -> RiskResult:
        """
        Run all risk checks against a proposed trade setup.

        Returns RiskResult(APPROVED) only if every check passes.
        Returns RiskResult(REJECTED, failed_rule, reason) on first failure.
        """
        checks = [
            self._check_live_trading_disabled,
            self._check_instrument,
            self._check_max_contracts,
            self._check_session,
            self._check_daily_trade_limit,
            self._check_per_session_trade_limit,
            self._check_session_cutoff,
            self._check_consecutive_losses,
            self._check_no_open_position,
            self._check_bracket_completeness,
            self._check_direction,
            self._check_entry_stop_target_distinct,  # structural check before computed R:R
            self._check_rr_ratio,
            self._check_min_target_distance,
            self._check_max_stop_distance,
        ]

        for check in checks:
            result = check(setup, daily_state)
            if result is not None:
                return result

        return RiskResult(result="APPROVED")

    # ── Individual Checks ─────────────────────────────────────────────────────

    def _check_live_trading_disabled(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Live trading must never be enabled in Phase 1."""
        if self.config.live_trading_enabled:
            raise LiveTradingBlockedError(source="RiskEngine._check_live_trading_disabled")
        return None

    def _check_max_contracts(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Contract count must not exceed instrument-specific maximum."""
        max_allowed = self.config.max_contracts_per_instrument.get(setup.instrument, 1)
        if setup.contracts > max_allowed:
            return RiskResult(
                result="REJECTED",
                failed_rule="max_contracts_exceeded",
                reason=(
                    f"Contracts {setup.contracts} exceeds max {max_allowed} "
                    f"for {setup.instrument}."
                ),
            )
        return None

    def _check_instrument(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Instrument must be in allowed list."""
        if setup.instrument not in self.config.allowed_instruments:
            return RiskResult(
                result="REJECTED",
                failed_rule="instrument_not_allowed",
                reason=(
                    f"Instrument '{setup.instrument}' is not in allowed list: "
                    f"{self.config.allowed_instruments}"
                ),
            )
        return None

    def _check_session(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Session must be in allowed list."""
        if setup.session not in self.config.allowed_sessions:
            return RiskResult(
                result="REJECTED",
                failed_rule="session_not_allowed",
                reason=(
                    f"Session '{setup.session}' is not in allowed list: "
                    f"{self.config.allowed_sessions}"
                ),
            )
        return None

    def _check_daily_trade_limit(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Daily trade count must be below maximum."""
        if daily_state.trade_count >= self.config.max_trades_per_day:
            return RiskResult(
                result="REJECTED",
                failed_rule="daily_trade_limit",
                reason=(
                    f"Daily trade limit reached: {daily_state.trade_count} trades "
                    f"(max {self.config.max_trades_per_day})"
                ),
            )
        return None

    def _check_per_session_trade_limit(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Per-session trade count must not exceed session-specific limit."""
        limit = self.config.per_session_limits.get(setup.session)
        if limit is None:
            return None
        count = daily_state.session_trade_counts.get(setup.session, 0)
        if count >= limit:
            return RiskResult(
                result="REJECTED",
                failed_rule="session_trade_limit",
                reason=(
                    f"Session trade limit reached for '{setup.session}': "
                    f"{count} trades (max {limit})"
                ),
            )
        return None

    def _check_session_cutoff(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """No new entries after the session's time-of-day cutoff (ET)."""
        cutoffs = self.config.session_cutoffs
        if not cutoffs or setup.session not in cutoffs:
            return None
        if setup.entry_time is None:
            return None
        cutoff_str = cutoffs[setup.session]
        cutoff_h, cutoff_m = map(int, cutoff_str.split(":"))
        et = setup.entry_time.astimezone(_ET)
        if et.hour > cutoff_h or (et.hour == cutoff_h and et.minute >= cutoff_m):
            return RiskResult(
                result="REJECTED",
                failed_rule="session_cutoff",
                reason=(
                    f"Entry at {et.strftime('%H:%M')} ET is after "
                    f"{cutoff_str} cutoff for '{setup.session}'"
                ),
            )
        return None

    def _check_min_target_distance(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Target must be at least N points away from entry (T1 room check)."""
        min_pts = self.config.min_target_points.get(setup.instrument, 0)
        if min_pts <= 0:
            return None
        distance = (
            setup.target - setup.entry
            if setup.direction == "LONG"
            else setup.entry - setup.target
        )
        if distance < min_pts:
            return RiskResult(
                result="REJECTED",
                failed_rule="target_too_close",
                reason=(
                    f"Target is {distance:.1f} pts from entry — "
                    f"minimum {min_pts} pts required for {setup.instrument}"
                ),
            )
        return None

    def _check_max_stop_distance(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Stop must not be more than N ticks from entry (rejects wide-ORB setups)."""
        max_ticks = self.config.max_stop_ticks.get(setup.instrument, 0)
        if max_ticks <= 0:
            return None
        tick_size = {"MNQ": 0.25, "MES": 0.25}.get(setup.instrument, 0.25)
        risk_pts = (
            setup.entry - setup.stop
            if setup.direction == "LONG"
            else setup.stop - setup.entry
        )
        risk_ticks = risk_pts / tick_size
        if risk_ticks > max_ticks:
            return RiskResult(
                result="REJECTED",
                failed_rule="stop_too_wide",
                reason=(
                    f"Stop is {risk_ticks:.0f} ticks from entry — "
                    f"max {max_ticks} ticks allowed for {setup.instrument}"
                ),
            )
        return None

    def _check_consecutive_losses(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Must stop after N consecutive losses."""
        if daily_state.consecutive_losses >= self.config.max_consecutive_losses:
            return RiskResult(
                result="REJECTED",
                failed_rule="consecutive_loss_limit",
                reason=(
                    f"Consecutive loss limit reached: {daily_state.consecutive_losses} losses "
                    f"(max {self.config.max_consecutive_losses})"
                ),
            )
        return None

    def _check_no_open_position(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """No new trade may be entered while a position is open."""
        if daily_state.has_open_position:
            return RiskResult(
                result="REJECTED",
                failed_rule="open_position_exists",
                reason="Cannot enter a new position while one is already open.",
            )
        return None

    def _check_bracket_completeness(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Entry, stop, and target must all be present and non-zero."""
        missing = []
        if not setup.entry or setup.entry <= 0:
            missing.append("entry")
        if not setup.stop or setup.stop <= 0:
            missing.append("stop")
        if not setup.target or setup.target <= 0:
            missing.append("target")

        if missing:
            return RiskResult(
                result="REJECTED",
                failed_rule="incomplete_bracket",
                reason=f"Bracket order is incomplete. Missing or zero: {missing}",
            )
        return None

    def _check_rr_ratio(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """R:R ratio must meet minimum threshold."""
        if setup.rr_ratio < self.config.min_rr_ratio:
            return RiskResult(
                result="REJECTED",
                failed_rule="rr_below_minimum",
                reason=(
                    f"R:R ratio {setup.rr_ratio:.2f} is below minimum "
                    f"{self.config.min_rr_ratio:.2f}"
                ),
            )
        return None

    def _check_direction(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Direction must be LONG or SHORT."""
        if setup.direction not in ("LONG", "SHORT"):
            return RiskResult(
                result="REJECTED",
                failed_rule="invalid_direction",
                reason=f"Direction must be LONG or SHORT, got: '{setup.direction}'",
            )
        return None

    def _check_entry_stop_target_distinct(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Entry, stop, and target must all be different prices."""
        if setup.entry == setup.stop:
            return RiskResult(
                result="REJECTED",
                failed_rule="entry_equals_stop",
                reason=f"Entry ({setup.entry}) and stop ({setup.stop}) are the same price.",
            )
        if setup.entry == setup.target:
            return RiskResult(
                result="REJECTED",
                failed_rule="entry_equals_target",
                reason=f"Entry ({setup.entry}) and target ({setup.target}) are the same price.",
            )
        if setup.stop == setup.target:
            return RiskResult(
                result="REJECTED",
                failed_rule="stop_equals_target",
                reason=f"Stop ({setup.stop}) and target ({setup.target}) are the same price.",
            )
        return None

    # ── Convenience ────────────────────────────────────────────────────────────

    @staticmethod
    def calculate_rr(direction: str, entry: float, stop: float, target: float) -> float:
        """
        Calculate R:R ratio for a proposed setup.

        Long:  (target - entry) / (entry - stop)
        Short: (entry - target) / (stop - entry)
        """
        if direction == "LONG":
            risk = entry - stop
            reward = target - entry
        elif direction == "SHORT":
            risk = stop - entry
            reward = entry - target
        else:
            return 0.0

        if risk <= 0:
            return 0.0
        return round(reward / risk, 4)
