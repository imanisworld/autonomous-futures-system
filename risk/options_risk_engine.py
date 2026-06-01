"""Deterministic risk checks for the isolated Alpaca options lane.

This is intentionally separate from risk.risk_engine because the futures engine
validates point/tick brackets, while options risk is premium/debit based.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as _time
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

_ET = ZoneInfo("America/New_York")


def _parse_hhmm(value: str) -> _time:
    hour, minute = value.split(":", 1)
    return _time(int(hour), int(minute))


def _time_in_window(value: _time, start: _time, end: _time) -> bool:
    if start <= end:
        return start <= value < end
    return value >= start or value < end


def _window_allows(rules: list[dict], timestamp: datetime) -> tuple[bool, str | None]:
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


@dataclass(frozen=True)
class OptionsRiskConfig:
    enabled: bool = False
    paper_only: bool = True
    allowed_underlyings: list[str] = field(default_factory=lambda: ["SPY", "QQQ"])
    allowed_contract_types: list[str] = field(default_factory=lambda: ["CALL", "PUT"])
    allowed_sessions: list[str] = field(default_factory=lambda: ["new_york"])
    session_windows: dict = field(default_factory=dict)
    max_contracts: int = 1
    max_premium_per_contract: float = 250.0
    max_total_premium: float = 250.0
    max_daily_trades: int = 3
    max_daily_loss: float = 150.0
    max_consecutive_losses: int = 2
    max_open_positions: int = 1
    require_entry: bool = True
    require_stop: bool = True
    require_target: bool = True
    min_rr_ratio: float = 2.0
    allow_market_orders: bool = False
    require_confluence_grade: str = "B"

    @classmethod
    def from_rules_file(cls, path: str | Path = "risk_rules.yaml") -> "OptionsRiskConfig":
        with open(path, encoding="utf-8") as handle:
            rules = yaml.safe_load(handle) or {}
        return cls.from_dict(rules.get("options_trading", {}) or {})

    @classmethod
    def from_dict(cls, raw: dict) -> "OptionsRiskConfig":
        raw = dict(raw or {})
        return cls(
            enabled=bool(raw.get("enabled", False)),
            paper_only=bool(raw.get("paper_only", True)),
            allowed_underlyings=[str(v).upper() for v in raw.get("allowed_underlyings", ["SPY", "QQQ"])],
            allowed_contract_types=[str(v).upper() for v in raw.get("allowed_contract_types", ["CALL", "PUT"])],
            allowed_sessions=[str(v) for v in raw.get("allowed_sessions", ["new_york"])],
            session_windows=raw.get("session_windows", {}) or {},
            max_contracts=int(raw.get("max_contracts", 1) or 1),
            max_premium_per_contract=float(raw.get("max_premium_per_contract", 250) or 0),
            max_total_premium=float(raw.get("max_total_premium", 250) or 0),
            max_daily_trades=int(raw.get("max_daily_trades", 3) or 0),
            max_daily_loss=float(raw.get("max_daily_loss", 150) or 0),
            max_consecutive_losses=int(raw.get("max_consecutive_losses", 2) or 0),
            max_open_positions=int(raw.get("max_open_positions", 1) or 0),
            require_entry=bool(raw.get("require_entry", True)),
            require_stop=bool(raw.get("require_stop", True)),
            require_target=bool(raw.get("require_target", True)),
            min_rr_ratio=float(raw.get("min_rr_ratio", 2.0) or 0),
            allow_market_orders=bool(raw.get("allow_market_orders", False)),
            require_confluence_grade=str(raw.get("require_confluence_grade", "B") or "B").upper(),
        )


@dataclass
class OptionsDailyState:
    trade_count: int = 0
    consecutive_losses: int = 0
    has_open_position: bool = False
    open_positions: int = 0
    realized_pnl_dollars: float = 0.0


@dataclass(frozen=True)
class OptionTradePlan:
    underlying: str
    symbol: str
    contract_type: str  # CALL | PUT
    side: str  # BUY | SELL
    quantity: int
    entry_premium: Optional[float]
    stop_premium: Optional[float]
    target_premium: Optional[float]
    strategy: str
    session: str
    timestamp: datetime
    order_type: str = "limit"
    confluence_grade: Optional[str] = None


@dataclass(frozen=True)
class OptionsRiskResult:
    result: str
    failed_rule: Optional[str] = None
    reason: Optional[str] = None

    @property
    def approved(self) -> bool:
        return self.result == "APPROVED"

    @property
    def rejected(self) -> bool:
        return self.result == "REJECTED"


class OptionsRiskEngine:
    """Futures-like deterministic gates for long premium options trades."""

    GRADE_RANK = {"A+": 5, "A": 4, "B": 3, "C": 2, "D": 1, "F": 0}

    def __init__(self, config: OptionsRiskConfig | None = None):
        self.config = config or OptionsRiskConfig.from_rules_file()

    def validate(
        self,
        plan: OptionTradePlan,
        daily_state: OptionsDailyState,
        *,
        broker_is_live: bool = False,
    ) -> OptionsRiskResult:
        checks = [
            lambda: self._check_enabled(),
            lambda: self._check_paper_only(broker_is_live),
            lambda: self._check_universe(plan),
            lambda: self._check_session(plan),
            lambda: self._check_session_window(plan),
            lambda: self._check_daily_limits(daily_state),
            lambda: self._check_open_positions(daily_state),
            lambda: self._check_order_shape(plan),
            lambda: self._check_bracket(plan),
            lambda: self._check_premium_risk(plan),
            lambda: self._check_rr(plan),
            lambda: self._check_confluence(plan),
        ]
        for check in checks:
            result = check()
            if result is not None:
                return result
        return OptionsRiskResult("APPROVED")

    def _reject(self, rule: str, reason: str) -> OptionsRiskResult:
        return OptionsRiskResult("REJECTED", failed_rule=rule, reason=reason)

    def _check_enabled(self) -> Optional[OptionsRiskResult]:
        if not self.config.enabled:
            return self._reject("options_disabled", "Options lane is disabled in risk rules.")
        return None

    def _check_paper_only(self, broker_is_live: bool) -> Optional[OptionsRiskResult]:
        if self.config.paper_only and broker_is_live:
            return self._reject("live_options_blocked", "Options lane is configured paper_only.")
        return None

    def _check_universe(self, plan: OptionTradePlan) -> Optional[OptionsRiskResult]:
        if plan.underlying.upper() not in self.config.allowed_underlyings:
            return self._reject("underlying_not_allowed", f"{plan.underlying} is not in allowed options underlyings.")
        if plan.contract_type.upper() not in self.config.allowed_contract_types:
            return self._reject("contract_type_not_allowed", f"{plan.contract_type} is not allowed.")
        if plan.side.upper() != "BUY":
            return self._reject("short_options_blocked", "Only long premium BUY options are allowed.")
        return None

    def _check_session(self, plan: OptionTradePlan) -> Optional[OptionsRiskResult]:
        if plan.session not in self.config.allowed_sessions:
            return self._reject("session_not_allowed", f"Session {plan.session} is not allowed for options.")
        return None

    def _check_session_window(self, plan: OptionTradePlan) -> Optional[OptionsRiskResult]:
        rules = self.config.session_windows.get(plan.session)
        if not rules:
            return None
        allowed, note = _window_allows(rules, plan.timestamp)
        if allowed:
            return None
        detail = f" ({note})" if note else ""
        et = plan.timestamp.astimezone(_ET)
        return self._reject(
            "session_window",
            f"Entry at {et.strftime('%H:%M')} ET is outside allowed options windows{detail}.",
        )

    def _check_daily_limits(self, daily_state: OptionsDailyState) -> Optional[OptionsRiskResult]:
        if daily_state.trade_count >= self.config.max_daily_trades:
            return self._reject("daily_trade_limit", "Options daily trade limit reached.")
        if self.config.max_daily_loss > 0 and daily_state.realized_pnl_dollars <= -abs(self.config.max_daily_loss):
            return self._reject("daily_loss_limit", "Options daily loss limit reached.")
        if self.config.max_consecutive_losses > 0 and daily_state.consecutive_losses >= self.config.max_consecutive_losses:
            return self._reject("consecutive_losses", "Options consecutive loss limit reached.")
        return None

    def _check_open_positions(self, daily_state: OptionsDailyState) -> Optional[OptionsRiskResult]:
        open_positions = daily_state.open_positions or int(daily_state.has_open_position)
        if open_positions >= self.config.max_open_positions:
            return self._reject("max_open_positions", "Options max open positions reached.")
        return None

    def _check_order_shape(self, plan: OptionTradePlan) -> Optional[OptionsRiskResult]:
        if plan.quantity < 1:
            return self._reject("quantity_invalid", "Options quantity must be >= 1.")
        if plan.quantity > self.config.max_contracts:
            return self._reject("max_contracts", f"Options quantity {plan.quantity} exceeds max {self.config.max_contracts}.")
        if plan.order_type.lower() == "market" and not self.config.allow_market_orders:
            return self._reject("market_order_blocked", "Options market orders are disabled; use limit orders.")
        if plan.order_type.lower() not in {"market", "limit"}:
            return self._reject("order_type_invalid", f"Unsupported options order type: {plan.order_type}.")
        return None

    def _check_bracket(self, plan: OptionTradePlan) -> Optional[OptionsRiskResult]:
        if self.config.require_entry and plan.entry_premium is None:
            return self._reject("entry_required", "Options entry premium is required.")
        if self.config.require_stop and plan.stop_premium is None:
            return self._reject("stop_required", "Options stop premium is required.")
        if self.config.require_target and plan.target_premium is None:
            return self._reject("target_required", "Options target premium is required.")
        if None in {plan.entry_premium, plan.stop_premium, plan.target_premium}:
            return None
        assert plan.entry_premium is not None and plan.stop_premium is not None and plan.target_premium is not None
        if not (plan.stop_premium < plan.entry_premium < plan.target_premium):
            return self._reject(
                "bracket_invalid",
                "Long premium options require stop < entry < target.",
            )
        return None

    def _check_premium_risk(self, plan: OptionTradePlan) -> Optional[OptionsRiskResult]:
        if plan.entry_premium is None:
            return None
        total_premium = plan.entry_premium * 100 * plan.quantity
        if self.config.max_premium_per_contract > 0 and plan.entry_premium * 100 > self.config.max_premium_per_contract:
            return self._reject(
                "premium_per_contract",
                f"Premium ${plan.entry_premium * 100:.2f}/contract exceeds max ${self.config.max_premium_per_contract:.2f}.",
            )
        if self.config.max_total_premium > 0 and total_premium > self.config.max_total_premium:
            return self._reject(
                "total_premium",
                f"Total debit ${total_premium:.2f} exceeds max ${self.config.max_total_premium:.2f}.",
            )
        return None

    def _check_rr(self, plan: OptionTradePlan) -> Optional[OptionsRiskResult]:
        if None in {plan.entry_premium, plan.stop_premium, plan.target_premium}:
            return None
        assert plan.entry_premium is not None and plan.stop_premium is not None and plan.target_premium is not None
        risk = plan.entry_premium - plan.stop_premium
        reward = plan.target_premium - plan.entry_premium
        if risk <= 0:
            return self._reject("risk_invalid", "Options premium risk must be positive.")
        rr = reward / risk
        if rr < self.config.min_rr_ratio:
            return self._reject("rr_too_low", f"Options R:R {rr:.2f} below minimum {self.config.min_rr_ratio:.2f}.")
        return None

    def _check_confluence(self, plan: OptionTradePlan) -> Optional[OptionsRiskResult]:
        required = self.config.require_confluence_grade.upper()
        if not required:
            return None
        grade = (plan.confluence_grade or "").upper()
        if self.GRADE_RANK.get(grade, -1) < self.GRADE_RANK.get(required, 0):
            return self._reject(
                "confluence_grade",
                f"Options confluence grade {grade or 'missing'} below required {required}.",
            )
        return None
