"""
agent/trade_grader.py

Read-only trade grading from journal entries.

The grader rewards rule-following and selectivity. A profitable rule violation
still receives a poor grade because outcome must never override process quality.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from config.settings import SystemConfig, load_config


@dataclass
class TradeGrade:
    index: int
    timestamp: Optional[str]
    instrument: Optional[str]
    session: Optional[str]
    strategy: Optional[str]
    decision: str
    risk_result: Optional[str]
    outcome: Optional[str]
    score: int
    grade: str
    rule_compliant: bool
    risk_notes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


class TradeGrader:
    """Grades approved trade journal entries without touching execution."""

    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or load_config()

    def grade_entries(self, entries: Iterable[dict]) -> list[TradeGrade]:
        entries_list = list(entries)
        outcomes = self._outcome_queue(entries_list)
        grades: list[TradeGrade] = []

        for entry in entries_list:
            if not self._is_trade_decision(entry):
                continue

            outcome = self._matching_outcome(entry, outcomes)
            grades.append(self.grade_trade(entry, len(grades) + 1, outcome))

        return grades

    def grade_trade(
        self,
        entry: dict,
        index: int = 1,
        outcome_entry: Optional[dict] = None,
    ) -> TradeGrade:
        notes: list[str] = []
        score = 100

        risk_check = entry.get("risk_check") or {}
        risk_result = risk_check.get("result")
        setup = entry.get("setup") or {}
        instrument = entry.get("instrument")
        session = entry.get("session")
        strategy = setup.get("strategy")
        rr_ratio = setup.get("rr_ratio")

        if risk_result != "APPROVED":
            notes.append("risk_not_approved")
            score -= 45
            if risk_check.get("failed_rule"):
                notes.append(str(risk_check["failed_rule"]))

        if instrument not in self.config.allowed_instruments:
            notes.append("instrument_not_allowed")
            score -= 35

        if session not in self.config.allowed_sessions:
            notes.append("session_not_allowed")
            score -= 35

        if strategy not in self.config.enabled_concepts:
            notes.append("setup_not_enabled")
            score -= 20

        for field in ("entry", "stop", "target"):
            if not self._positive_number(setup.get(field)):
                notes.append(f"missing_{field}")
                score -= 30

        if not self._positive_number(rr_ratio) or rr_ratio < self.config.min_rr_ratio:
            notes.append("rr_below_minimum")
            score -= 30

        if not outcome_entry:
            notes.append("missing_outcome_or_open_trade")
            score -= 10

        score = max(0, min(100, score))
        rule_compliant = not any(
            note
            in {
                "risk_not_approved",
                "instrument_not_allowed",
                "session_not_allowed",
                "setup_not_enabled",
                "missing_entry",
                "missing_stop",
                "missing_target",
                "rr_below_minimum",
            }
            for note in notes
        )

        if not rule_compliant:
            grade = "F"
        else:
            grade = self._letter_grade(score)

        return TradeGrade(
            index=index,
            timestamp=entry.get("ts"),
            instrument=instrument,
            session=session,
            strategy=strategy,
            decision=entry.get("decision", "UNKNOWN"),
            risk_result=risk_result,
            outcome=self._outcome_result(outcome_entry),
            score=score,
            grade=grade,
            rule_compliant=rule_compliant,
            risk_notes=notes,
        )

    @staticmethod
    def _is_trade_decision(entry: dict) -> bool:
        return entry.get("decision") == "TRADE"

    @staticmethod
    def _positive_number(value: object) -> bool:
        return isinstance(value, (int, float)) and value > 0

    @staticmethod
    def _letter_grade(score: int) -> str:
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"

    @staticmethod
    def _outcome_queue(entries: list[dict]) -> list[dict]:
        return [entry for entry in entries if entry.get("type") == "OUTCOME"]

    @staticmethod
    def _matching_outcome(entry: dict, outcomes: list[dict]) -> Optional[dict]:
        for idx, outcome in enumerate(outcomes):
            if (
                outcome.get("instrument") == entry.get("instrument")
                and outcome.get("session") == entry.get("session")
            ):
                return outcomes.pop(idx)
        return None

    @staticmethod
    def _outcome_result(outcome_entry: Optional[dict]) -> Optional[str]:
        if not outcome_entry:
            return None
        outcome = outcome_entry.get("outcome") or {}
        return outcome.get("result")
