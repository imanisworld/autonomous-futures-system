"""
agent/risk_reviewer.py

Read-only daily risk audit from journal entries.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from config.settings import SystemConfig, load_config


@dataclass
class RiskReview:
    date: str
    approved_trades: int
    no_trades: int
    wins: int
    losses: int
    open_trades: int
    violations: list[str]
    warnings: list[str]
    recommended_state: str

    def to_dict(self) -> dict:
        return asdict(self)


class RiskReviewer:
    """Audits journal history for overtrading, drift, and lockout conditions."""

    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or load_config()

    def review_entries(self, entries: Iterable[dict], review_date: str) -> RiskReview:
        entries_list = list(entries)
        approved_trades = self._approved_trades(entries_list)
        outcomes = self._outcomes(entries_list)
        no_trades = sum(1 for entry in entries_list if entry.get("decision") == "NO_TRADE")
        wins = sum(1 for outcome in outcomes if self._outcome_result(outcome) == "WIN")
        losses = sum(1 for outcome in outcomes if self._outcome_result(outcome) == "LOSS")
        open_trades = max(0, len(approved_trades) - len(outcomes))

        violations: list[str] = []
        warnings: list[str] = []

        total_trade_capacity = self.config.max_trades_per_day + int(getattr(self.config, "bonus_trades_after_max", 0) or 0)
        if len(approved_trades) > total_trade_capacity:
            violations.append("max_trades_per_day_exceeded")

        if self._max_consecutive_losses(outcomes) >= self.config.max_consecutive_losses:
            warnings.append("loss_lockout_active")

        if open_trades > 0:
            warnings.append("open_trade_without_outcome")

        self._audit_approved_trades(approved_trades, violations, warnings)
        self._audit_position_stacking(entries_list, violations)

        recommended_state = (
            "NO_TRADE"
            if violations or "loss_lockout_active" in warnings or open_trades > 0
            else "OK_TO_PAPER_TRADE"
        )

        return RiskReview(
            date=review_date,
            approved_trades=len(approved_trades),
            no_trades=no_trades,
            wins=wins,
            losses=losses,
            open_trades=open_trades,
            violations=sorted(set(violations)),
            warnings=sorted(set(warnings)),
            recommended_state=recommended_state,
        )

    def _audit_approved_trades(
        self,
        trades: list[dict],
        violations: list[str],
        warnings: list[str],
    ) -> None:
        prior_loss = False
        for trade in trades:
            if trade.get("instrument") not in self.config.allowed_instruments:
                violations.append("instrument_not_allowed")
            if trade.get("session") not in self.config.allowed_sessions:
                violations.append("session_not_allowed")

            setup = trade.get("setup") or {}
            if setup.get("strategy") not in self.config.enabled_concepts:
                violations.append("setup_not_enabled")

            for field in ("entry", "stop", "target"):
                if not self._positive_number(setup.get(field)):
                    violations.append(f"missing_{field}")

            rr_ratio = setup.get("rr_ratio")
            if not self._positive_number(rr_ratio) or rr_ratio < self.config.min_rr_ratio:
                violations.append("rr_below_minimum")

            if prior_loss:
                warnings.append("revenge_risk_after_loss")

            outcome = trade.get("outcome") or {}
            prior_loss = outcome.get("result") == "LOSS"

    @staticmethod
    def _audit_position_stacking(entries: list[dict], violations: list[str]) -> None:
        open_positions = 0
        for entry in entries:
            if RiskReviewer._is_approved_trade(entry):
                if open_positions > 0:
                    violations.append("duplicate_position_risk")
                outcome = entry.get("outcome") or {}
                if outcome.get("result") in ("WIN", "LOSS"):
                    open_positions = 0
                else:
                    open_positions += 1
            elif entry.get("type") == "OUTCOME":
                open_positions = max(0, open_positions - 1)

    @staticmethod
    def _approved_trades(entries: list[dict]) -> list[dict]:
        return [entry for entry in entries if RiskReviewer._is_approved_trade(entry)]

    @staticmethod
    def _outcomes(entries: list[dict]) -> list[dict]:
        outcomes = []
        for entry in entries:
            if entry.get("type") == "OUTCOME":
                outcomes.append(entry)
            elif RiskReviewer._is_approved_trade(entry):
                outcome = entry.get("outcome") or {}
                if outcome.get("result") in ("WIN", "LOSS"):
                    outcomes.append(entry)
        return outcomes

    @staticmethod
    def _is_approved_trade(entry: dict) -> bool:
        risk_check = entry.get("risk_check") or {}
        return entry.get("decision") == "TRADE" and risk_check.get("result") == "APPROVED"

    @staticmethod
    def _outcome_result(entry: dict) -> Optional[str]:
        outcome = entry.get("outcome") or {}
        return outcome.get("result")

    @staticmethod
    def _max_consecutive_losses(outcomes: list[dict]) -> int:
        max_streak = 0
        current = 0
        for outcome in outcomes:
            if RiskReviewer._outcome_result(outcome) == "LOSS":
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        return max_streak

    @staticmethod
    def _positive_number(value: object) -> bool:
        return isinstance(value, (int, float)) and value > 0
