"""
risk/risk_engine.py

Deterministic risk rule enforcement.
Every check is explicit, ordered, and logged.
No check may be skipped or overridden.

Returns RiskResult(APPROVED) or RiskResult(REJECTED, failed_rule, reason).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as _time, timedelta, timezone
from typing import Dict, Optional
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _parse_hhmm(value: str) -> _time:
    hour, minute = value.split(":", 1)
    return _time(int(hour), int(minute))


def _time_in_window(value: _time, start: _time, end: _time) -> bool:
    if start <= end:
        return start <= value < end
    return value >= start or value < end


def _session_window_decision(rules: list[dict], timestamp: datetime) -> tuple[bool, str | None]:
    current = timestamp.astimezone(_ET).time().replace(second=0, microsecond=0)
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
    account_balance: Optional[float] = None
    account_peak_balance: Optional[float] = None
    realized_pnl_dollars: float = 0.0
    last_loss_at: Optional[datetime] = None


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
    confluence_grade: Optional[str] = None
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
            self._check_position_sizing,
            self._check_max_contracts,
            self._check_session,
            self._check_session_window,
            self._check_news_blackout,
            self._check_daily_trade_limit,
            self._check_daily_loss_limit,
            self._check_max_drawdown,
            self._check_circuit_breaker,
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


    def _check_position_sizing(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Dynamic account-balance sizing ladder with aggressive upward rounding."""
        sizing = self.config.position_sizing
        if not sizing.enabled or not sizing.sizing_rules:
            return None

        balance = (
            daily_state.account_balance
            if daily_state.account_balance is not None
            else sizing.starting_balance
        )
        rule = self._position_sizing_rule_for_balance(balance)
        if rule is None:
            return RiskResult(
                result="REJECTED",
                failed_rule="position_sizing_no_tier",
                reason=f"No position sizing tier found for balance ${balance:,.2f}.",
            )

        if setup.instrument != rule.instrument:
            return RiskResult(
                result="REJECTED",
                failed_rule="position_sizing_instrument",
                reason=(
                    f"Balance ${balance:,.2f} allows {rule.instrument} only; "
                    f"received {setup.instrument}."
                ),
            )

        if setup.contracts > rule.max_contracts:
            return RiskResult(
                result="REJECTED",
                failed_rule="position_sizing_contracts",
                reason=(
                    f"Balance ${balance:,.2f} allows max {rule.max_contracts} "
                    f"{rule.instrument} contract(s); received {setup.contracts}."
                ),
            )
        return None

    def _position_sizing_rule_for_balance(self, balance: float):
        sizing = self.config.position_sizing
        effective_balance = self._effective_sizing_balance(float(balance))
        selected = None
        for rule in sizing.sizing_rules:
            upper_ok = rule.max_balance is None or effective_balance < rule.max_balance
            if effective_balance >= rule.min_balance and upper_ok:
                selected = rule
        return selected

    def _effective_sizing_balance(self, balance: float) -> float:
        sizing = self.config.position_sizing
        if not sizing.aggressive_rounding:
            return balance
        pct = sizing.rounding_threshold_percent / 100
        effective = balance
        for rule in sizing.sizing_rules:
            threshold = rule.min_balance
            if balance < threshold and balance >= threshold * (1 - pct):
                effective = max(effective, threshold)
        return effective

    def recommended_contracts(self, instrument: str, balance: Optional[float]) -> int:
        sizing = self.config.position_sizing
        if sizing.enabled and sizing.sizing_rules:
            rule = self._position_sizing_rule_for_balance(
                sizing.starting_balance if balance is None else balance
            )
            if rule is not None and instrument == rule.instrument:
                return rule.max_contracts
        return int(self.config.max_contracts_per_instrument.get(instrument, 1))

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

    def _check_session_window(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Optional allow/deny windows inside an otherwise allowed session."""
        windows = getattr(self.config, "session_windows", {}) or {}
        rules = windows.get(setup.session)
        if not rules:
            return None
        if setup.entry_time is None:
            return RiskResult(
                result="REJECTED",
                failed_rule="session_window",
                reason=f"Session '{setup.session}' requires an entry_time for window gating.",
            )

        allowed, note = _session_window_decision(rules, setup.entry_time)
        if allowed:
            return None
        detail = f" ({note})" if note else ""
        et = setup.entry_time.astimezone(_ET)
        return RiskResult(
            result="REJECTED",
            failed_rule="session_window",
            reason=(
                f"Entry at {et.strftime('%H:%M')} ET is outside allowed "
                f"'{setup.session}' session windows{detail}."
            ),
        )

    def _check_daily_trade_limit(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Daily trade count must be below max, except configured A-grade bonus trades."""
        if daily_state.trade_count < self.config.max_trades_per_day:
            return None

        bonus_max = int(getattr(self.config, "bonus_trades_after_max", 0) or 0)
        total_cap = self.config.max_trades_per_day + bonus_max
        grade = (setup.confluence_grade or "").strip().upper()
        min_grade = (getattr(self.config, "bonus_min_confluence_grade", "A") or "A").strip().upper()

        if bonus_max > 0 and daily_state.trade_count < total_cap:
            if self._grade_meets_minimum(grade, min_grade):
                return None
            return RiskResult(
                result="REJECTED",
                failed_rule="daily_trade_limit_bonus_grade",
                reason=(
                    f"Daily trade limit reached; bonus trade requires confluence "
                    f"grade {min_grade} or better (received {grade or 'NONE'})."
                ),
            )

        return RiskResult(
            result="REJECTED",
            failed_rule="daily_trade_limit",
            reason=(
                f"Daily trade limit reached: {daily_state.trade_count} trades "
                f"(max {self.config.max_trades_per_day}, bonus {bonus_max})"
            ),
        )

    @staticmethod
    def _grade_meets_minimum(grade: str, minimum: str) -> bool:
        order = {"A+": 5, "A": 4, "B": 3, "C": 2, "WEAK": 1, "F": 0, "": -1}
        return order.get(grade.upper(), -1) >= order.get(minimum.upper(), 4)

    def _is_news_blackout_date(self, setup: TradeSetup) -> bool:
        if not setup.entry_time:
            return False
        dates = set(getattr(self.config, "news_blackout_dates", []) or [])
        if not dates:
            return False
        return setup.entry_time.astimezone(_ET).date().isoformat() in dates

    def _check_news_blackout(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Block or restrict entries on configured high-impact news dates."""
        mode = (getattr(self.config, "news_blackout_mode", "off") or "off").lower()
        if mode == "off" or not self._is_news_blackout_date(setup):
            return None

        if mode == "block":
            return RiskResult(
                result="REJECTED",
                failed_rule="news_blackout",
                reason="High-impact news blackout date: no new entries allowed.",
            )

        if mode == "reduced":
            max_trades = int(getattr(self.config, "news_blackout_max_trades", 1) or 1)
            if daily_state.trade_count >= max_trades:
                return RiskResult(
                    result="REJECTED",
                    failed_rule="news_blackout_trade_limit",
                    reason=(
                        f"High-impact news date limit reached: {daily_state.trade_count} "
                        f"trade(s), max {max_trades}."
                    ),
                )
            cutoff = getattr(self.config, "news_blackout_cutoff_et", "13:30") or "13:30"
            cutoff_h, cutoff_m = map(int, cutoff.split(":"))
            et = setup.entry_time.astimezone(_ET)
            if et.hour > cutoff_h or (et.hour == cutoff_h and et.minute >= cutoff_m):
                return RiskResult(
                    result="REJECTED",
                    failed_rule="news_blackout_cutoff",
                    reason=(
                        f"High-impact news date cutoff active: entry at "
                        f"{et.strftime('%H:%M')} ET is after {cutoff} ET."
                    ),
                )
        return None


    def _check_daily_loss_limit(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Stop entries once realized daily P&L reaches the contract-adjusted loss cap."""
        base_max_loss = float(getattr(self.config, "max_daily_loss", 0) or 0)
        if base_max_loss <= 0:
            return None

        contracts = max(1, int(setup.contracts or 1))
        max_loss = abs(base_max_loss) * contracts
        if daily_state.realized_pnl_dollars <= -max_loss:
            return RiskResult(
                result="REJECTED",
                failed_rule="max_daily_loss",
                reason=(
                    f"Daily loss limit reached: ${daily_state.realized_pnl_dollars:.2f} "
                    f"realized P&L (max loss ${max_loss:.2f} for {contracts}c)."
                ),
            )
        return None

    def _check_max_drawdown(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Stop entries if account balance is below peak by configured percent."""
        max_dd = float(getattr(self.config, "max_drawdown_percent", 0) or 0)
        if max_dd <= 0 or daily_state.account_balance is None:
            return None
        peak = daily_state.account_peak_balance or max(
            daily_state.account_balance,
            self.config.position_sizing.starting_balance,
        )
        if peak <= 0:
            return None
        drawdown = (peak - daily_state.account_balance) / peak
        if drawdown >= max_dd:
            return RiskResult(
                result="REJECTED",
                failed_rule="max_drawdown",
                reason=(
                    f"Account drawdown {drawdown:.1%} exceeds max {max_dd:.1%} "
                    f"from peak ${peak:,.2f}."
                ),
            )
        return None

    def _check_circuit_breaker(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Pause entries after N consecutive losses for configured minutes."""
        losses = int(getattr(self.config, "circuit_breaker_losses", 0) or 0)
        if losses <= 0 or daily_state.consecutive_losses < losses:
            return None
        pause_minutes = int(getattr(self.config, "circuit_breaker_pause_minutes", 30) or 30)
        now = setup.entry_time or datetime.now(timezone.utc)
        last_loss_at = daily_state.last_loss_at
        if last_loss_at is None:
            return RiskResult(
                result="REJECTED",
                failed_rule="circuit_breaker",
                reason=f"Circuit breaker active after {daily_state.consecutive_losses} consecutive losses.",
            )
        if last_loss_at.tzinfo is None:
            last_loss_at = last_loss_at.replace(tzinfo=timezone.utc)
        until = last_loss_at + timedelta(minutes=pause_minutes)
        if now < until:
            return RiskResult(
                result="REJECTED",
                failed_rule="circuit_breaker",
                reason=(
                    f"Circuit breaker active after {daily_state.consecutive_losses} losses; "
                    f"paused until {until.astimezone(_ET).strftime('%H:%M')} ET."
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
        tick_size = {"MNQ": 0.25, "MES": 0.25, "ES": 0.25, "NQ": 0.25}.get(setup.instrument, 0.25)
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
        if getattr(self.config, "circuit_breaker_losses", 0):
            return None
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
