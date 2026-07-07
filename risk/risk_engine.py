"""
risk/risk_engine.py

Deterministic risk rule enforcement.
Every check is explicit, ordered, and logged.
No check may be skipped or overridden.

Returns RiskResult(APPROVED) or RiskResult(REJECTED, failed_rule, reason).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time as _time, timedelta, timezone
from typing import Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Small clock-skew allowance for the alert-freshness check: an alert timestamped
# a few seconds ahead of "now" is normal clock drift between the webhook sender
# and this server, not a corrupted/future-dated payload. Not configurable —
# this is a skew tolerance, not a risk policy knob.
_ALERT_FUTURE_TOLERANCE_SECONDS = 5.0


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
    consecutive_wins: int = 0
    session_start_pnl: Dict[str, float] = field(default_factory=dict)
    session_start_time: Dict[str, datetime] = field(default_factory=dict)


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

    def __init__(self, config: Optional[SystemConfig] = None,
                 schedule_mode: Optional[str] = None):
        self.config = config or load_config()
        # Only "current" enforces the session-eligibility gates (allowlist,
        # window, cutoff). Always-on modes bypass them; ALL other risk gates
        # (daily cap, loss, drawdown, open-position, consecutive-loss, per-session
        # count) remain shared and enforced in every mode.
        self.schedule_mode = schedule_mode or getattr(self.config, "schedule_mode", "current")
        self._enforce_schedule = self.schedule_mode == "current"

    def validate(self, setup: TradeSetup, daily_state: DailyState) -> RiskResult:
        """
        Run all risk checks against a proposed trade setup.

        Returns RiskResult(APPROVED) only if every check passes.
        Returns RiskResult(REJECTED, failed_rule, reason) on first failure.
        """
        checks = [
            self._check_live_trading_disabled,
            self._check_instrument,
            self._check_alert_freshness,
            self._check_position_sizing,
            self._check_max_contracts,
            self._check_win_streak_contracts,
            self._check_session,
            self._check_session_window,
            self._check_news_blackout,
            self._check_daily_trade_limit,
            self._check_daily_loss_limit,
            self._check_profit_protect_gate,
            self._check_early_session_loss_floor,
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
            self._check_min_confluence_grade,
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

    def _check_alert_freshness(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Reject a candidate built from a missing, stale, or future-dated alert.

        setup.entry_time traces back to the TradingView webhook payload's own
        timestamp (webhook/state_builder.py -> MarketState.timestamp ->
        TradeSetup.entry_time). No proof, no run: an untrustworthy alert age
        must not silently pass.

        execution_safety.log_alert_age_only (default True) starts this gate in
        observe-only mode — age is logged, never rejected for age — until a
        real max_alert_age_seconds threshold is set from observed latency data.
        The missing-timestamp and future-timestamp rejections are NOT gated by
        log_alert_age_only: those are data-integrity failures, not a threshold
        being tuned.
        """
        if setup.entry_time is None:
            if getattr(self.config, "reject_on_missing_alert_timestamp", True):
                return RiskResult(
                    result="REJECTED",
                    failed_rule="alert_timestamp_missing",
                    reason="TradeSetup.entry_time is missing; cannot verify alert freshness.",
                )
            return None

        entry_time = setup.entry_time
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - entry_time).total_seconds()

        if age_seconds < -_ALERT_FUTURE_TOLERANCE_SECONDS:
            return RiskResult(
                result="REJECTED",
                failed_rule="alert_timestamp_future",
                reason=(
                    f"Alert timestamp for {setup.instrument} is "
                    f"{abs(age_seconds):.1f}s in the future "
                    f"(tolerance {_ALERT_FUTURE_TOLERANCE_SECONDS:.0f}s)."
                ),
            )

        if getattr(self.config, "log_alert_age_only", True):
            logger.info(
                "alert_age_seconds instrument=%s age=%.1f (log-only, not enforced)",
                setup.instrument, age_seconds,
            )
            return None

        max_age = getattr(self.config, "max_alert_age_seconds", None)
        if max_age is not None and age_seconds > max_age:
            return RiskResult(
                result="REJECTED",
                failed_rule="stale_alert",
                reason=(
                    f"Alert age {age_seconds:.1f}s for {setup.instrument} exceeds "
                    f"max_alert_age_seconds={max_age}."
                ),
            )
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
        rule = self._position_sizing_rule_for_balance(balance, setup.instrument)
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

    def _position_sizing_rule_for_balance(self, balance: float, instrument: str | None = None):
        sizing = self.config.position_sizing
        effective_balance = self._effective_sizing_balance(float(balance))
        matching = []
        for rule in sizing.sizing_rules:
            upper_ok = rule.max_balance is None or effective_balance < rule.max_balance
            if effective_balance >= rule.min_balance and upper_ok:
                matching.append(rule)
        if instrument:
            instrument = instrument.upper()
            for rule in reversed(matching):
                if rule.instrument == instrument:
                    return rule
        selected = matching[-1] if matching else None
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
                sizing.starting_balance if balance is None else balance,
                instrument,
            )
            if rule is not None and instrument == rule.instrument:
                return self._cap_contracts(rule.max_contracts)
        return self._cap_contracts(int(self.config.max_contracts_per_instrument.get(instrument, 1)))

    def _cap_contracts(self, n: int) -> int:
        """Apply the hard contract ceiling (e.g. 1 for demo/live) on top of
        balance-tiered sizing. None = no cap."""
        cap = getattr(self.config, "max_contracts_hard_cap", None)
        if cap is not None and cap > 0:
            return max(1, min(int(n), int(cap)))
        return int(n)

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
        """Session must be in allowed list (skipped in always-on modes)."""
        if not self._enforce_schedule:
            return None
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
        """Optional allow/deny windows inside a session (skipped in always-on)."""
        if not self._enforce_schedule:
            return None
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

    def _check_min_confluence_grade(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Optional baseline confluence grade gate for ordinary entries."""
        minimum = (getattr(self.config, "min_confluence_grade", "") or "").strip().upper()
        if not minimum:
            return None

        grade = (setup.confluence_grade or "").strip().upper()
        if self._grade_meets_minimum(grade, minimum):
            return None

        return RiskResult(
            result="REJECTED",
            failed_rule="min_confluence_grade",
            reason=(
                f"Confluence grade {grade or 'NONE'} is below required "
                f"minimum {minimum}."
            ),
        )

    def _news_date_map(self) -> Dict[str, Optional[str]]:
        """Map blackout date -> optional 'HH:MM' ET release time.

        Each news_blackout_dates entry is 'YYYY-MM-DD' (uses the default release
        time) or 'YYYY-MM-DD HH:MM' (explicit ET release time, e.g. '08:30' for a
        CPI/NFP print, '14:00' for an FOMC statement).
        """
        out: Dict[str, Optional[str]] = {}
        for raw in (getattr(self.config, "news_blackout_dates", []) or []):
            text = str(raw).strip()
            if not text:
                continue
            parts = text.split()
            out[parts[0]] = parts[1] if len(parts) > 1 else None
        return out

    def _is_news_blackout_date(self, setup: TradeSetup) -> bool:
        if not setup.entry_time:
            return False
        date_map = self._news_date_map()
        if not date_map:
            return False
        return setup.entry_time.astimezone(_ET).date().isoformat() in date_map

    def _check_news_release_window(self, setup: TradeSetup) -> Optional[RiskResult]:
        """Block entries ONLY within a window centered on the release time.

        Outside the window, normal daily limits apply — there is no special trade
        cap or all-day cutoff in this mode. Window length is
        news_blackout_release_window_minutes (total, centered on the release).
        """
        if not setup.entry_time:
            return None
        et = setup.entry_time.astimezone(_ET)
        release_str = (
            self._news_date_map().get(et.date().isoformat())
            or getattr(self.config, "news_blackout_release_default_et", "14:00")
            or "14:00"
        )
        try:
            release_h, release_m = map(int, str(release_str).split(":"))
        except ValueError:
            return None
        window = int(getattr(self.config, "news_blackout_release_window_minutes", 30) or 30)
        if window <= 0:
            return None
        release_dt = et.replace(hour=release_h, minute=release_m, second=0, microsecond=0)
        half = timedelta(minutes=window / 2)
        start, end = release_dt - half, release_dt + half
        if start <= et < end:
            return RiskResult(
                result="REJECTED",
                failed_rule="news_release_window",
                reason=(
                    f"High-impact release blackout {start.strftime('%H:%M')}-"
                    f"{end.strftime('%H:%M')} ET (±{window // 2}m around "
                    f"{release_h:02d}:{release_m:02d} ET release). "
                    f"No entries inside the window; normal trading otherwise."
                ),
            )
        return None

    def _check_news_blackout(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Block or restrict entries on configured high-impact news dates."""
        mode = (getattr(self.config, "news_blackout_mode", "off") or "off").lower()
        if mode == "off" or not self._is_news_blackout_date(setup):
            return None

        if mode == "release_window":
            return self._check_news_release_window(setup)

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


    # Point value per instrument (dollars per 1-point move, 1 contract).
    _POINT_VALUES: Dict[str, float] = {"MES": 5.0, "MNQ": 2.0, "ES": 50.0, "NQ": 20.0}

    def _check_profit_protect_gate(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """After daily P&L >= threshold, only allow trades whose planned risk ≤ daily profit."""
        threshold = float(getattr(self.config, "daily_profit_protect_threshold", 0) or 0)
        if threshold <= 0:
            return None
        day_pnl = daily_state.realized_pnl_dollars
        if day_pnl < threshold:
            return None
        pv = self._POINT_VALUES.get(setup.instrument, 1.0)
        contracts = max(1, int(setup.contracts or 1))
        risk_distance = abs(
            (setup.entry - setup.stop)
            if setup.direction == "LONG"
            else (setup.stop - setup.entry)
        )
        planned_risk = risk_distance * pv * contracts
        if planned_risk > day_pnl:
            return RiskResult(
                result="REJECTED",
                failed_rule="profit_protect_gate",
                reason=(
                    f"Profit protection: planned risk ${planned_risk:.2f} > "
                    f"daily P&L ${day_pnl:.2f} (threshold ${threshold:.0f})"
                ),
            )
        return None

    def _check_early_session_loss_floor(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Block entries if session P&L fell below the floor in the opening window.

        Prevents revenge trading in the first N minutes of a session. The floor
        is measured from P&L at session open, derived from session_start_pnl
        which is populated when daily_state is built from the journal.
        """
        floor = float(getattr(self.config, "early_session_loss_floor", 0) or 0)
        window_minutes = int(getattr(self.config, "early_session_minutes", 30) or 30)
        if floor >= 0 or not setup.entry_time or not setup.session:
            return None  # disabled or misconfigured

        session = setup.session
        entry_et = setup.entry_time.astimezone(_ET)

        # session_start_pnl is populated by journal_logger when building DailyState.
        # If not present for this session, nothing has traded yet — floor not applicable.
        if session not in daily_state.session_start_pnl:
            return None

        session_start = daily_state.session_start_time.get(session)
        if session_start:
            session_start_et = session_start.astimezone(_ET)
            minutes_elapsed = (entry_et - session_start_et).total_seconds() / 60
            if minutes_elapsed > window_minutes:
                return None  # outside the early window

        pnl_at_open = daily_state.session_start_pnl[session]
        session_pnl = daily_state.realized_pnl_dollars - pnl_at_open

        if session_pnl <= floor:
            return RiskResult(
                result="REJECTED",
                failed_rule="early_session_loss_floor",
                reason=(
                    f"Early-session loss floor hit in '{session}': "
                    f"${session_pnl:.2f} within first {window_minutes}m "
                    f"(floor ${floor:.2f}). No new entries until next session."
                ),
            )
        return None

    def _check_win_streak_contracts(
        self, setup: TradeSetup, daily_state: DailyState
    ) -> Optional[RiskResult]:
        """Allow +1 bonus contract after N consecutive wins on A/A+ setups.

        This check is a modifier, not a blocker — it only rejects if the setup
        is requesting MORE contracts than either the sizing ladder OR the win-streak
        bonus allows. If no streak is active, normal sizing limits apply.
        """
        bonus_after = int(getattr(self.config, "win_streak_bonus_after", 0) or 0)
        if bonus_after <= 0:
            return None  # disabled

        bonus_contracts = int(getattr(self.config, "win_streak_bonus_contracts", 1) or 1)
        min_grade = str(getattr(self.config, "win_streak_bonus_min_grade", "A") or "A").upper()
        grade = (setup.confluence_grade or "").strip().upper()

        streak = getattr(daily_state, "consecutive_wins", 0)
        if streak < bonus_after:
            return None  # streak not yet reached — normal sizing handles this

        # Streak is active — allow up to base_max + bonus on qualifying setups
        base_max = self.config.max_contracts_per_instrument.get(setup.instrument, 1)
        if self._grade_meets_minimum(grade, min_grade):
            allowed = base_max + bonus_contracts
        else:
            allowed = base_max  # streak active but grade too low — revert to base

        if setup.contracts > allowed:
            return RiskResult(
                result="REJECTED",
                failed_rule="win_streak_contracts_exceeded",
                reason=(
                    f"Win streak ({streak}w) allows max {allowed}c for {setup.instrument} "
                    f"(grade {grade or 'NONE'} vs required {min_grade}); "
                    f"requested {setup.contracts}c."
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
        if daily_state.realized_pnl_dollars <= -abs(max_loss):
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
        """No new entries after the session's time-of-day cutoff (ET).

        The comparison is anchored to the session START so sessions that wrap
        past midnight work correctly. The Asian session runs 19:00 -> 03:00 ET
        with an 02:30 cutoff; a naive clock compare (et.hour > cutoff_h) would
        wrongly flag a 20:45 start-of-session entry as "after 02:30". Measuring
        minutes-since-session-start (mod 24h) for both entry and cutoff fixes it,
        and reduces to the old behavior for non-wrapping sessions.

        Skipped in always-on modes (the cutoff is a schedule gate).
        """
        if not self._enforce_schedule:
            return None
        cutoffs = self.config.session_cutoffs
        if not cutoffs or setup.session not in cutoffs:
            return None
        if setup.entry_time is None:
            return None
        cutoff_str = cutoffs[setup.session]
        cutoff_h, cutoff_m = map(int, cutoff_str.split(":"))
        et = setup.entry_time.astimezone(_ET)
        entry_min = et.hour * 60 + et.minute
        cutoff_min = cutoff_h * 60 + cutoff_m

        # Only sessions that WRAP past midnight (start-of-day > end-of-day, e.g.
        # asian 19:00 -> 03:00) need the start-anchored comparison. For ordinary
        # same-day sessions the simple clock compare is correct AND must be kept —
        # anchoring there would wrongly reject pre-session entries (e.g. an 08:00
        # entry measured against a 09:30 start wraps to ~22h "after").
        hours = (self.config.session_hours or {}).get(setup.session) or {}
        start_str, end_str = hours.get("start"), hours.get("end")
        wraps = False
        start_min = 0
        if start_str and end_str:
            start_h, start_m = map(int, start_str.split(":"))
            end_h, end_m = map(int, end_str.split(":"))
            start_min = start_h * 60 + start_m
            wraps = start_min > (end_h * 60 + end_m)

        if wraps:
            day = 24 * 60
            after_cutoff = ((entry_min - start_min) % day) >= ((cutoff_min - start_min) % day)
        else:
            after_cutoff = entry_min >= cutoff_min

        if after_cutoff:
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
        """R:R ratio must meet minimum threshold.

        Skipped when the runner exit is on: the runner DROPS the fixed target and
        trails instead, so the fixed-target R:R is meaningless (a wider stop only
        lowers it on paper). Gating on it would reject exactly the wider-stop
        setups the runner is designed to ride.
        """
        if getattr(self.config, "runner_mode", False):
            return None
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
