"""
agent/daily_summary.py

Morning and end-of-day read-only review reports.

Usage:
    python -m agent.daily_summary --date 2026-05-23 --mode morning
    python -m agent.daily_summary --date 2026-05-23 --mode eod
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Iterable, Iterator, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from config.settings import ConfigError, LiveTradingBlockedError, SystemConfig, load_config
from .risk_reviewer import RiskReview, RiskReviewer
from .trade_grader import TradeGrade, TradeGrader


def read_journal(log_dir: str | Path, review_date: str) -> list[dict]:
    review_date = validate_review_date(review_date)
    path = Path(log_dir) / f"journal_{review_date}.jsonl"
    if not path.exists():
        return []

    entries: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                entries.append(
                    {
                        "type": "REVIEW_WARNING",
                        "decision": "NO_TRADE",
                        "reason": "malformed_journal_line",
                    }
                )
    return entries


def validate_review_date(review_date: str) -> str:
    """Require exact YYYY-MM-DD review dates before building file paths."""
    try:
        parsed = date.fromisoformat(review_date)
    except ValueError as exc:
        raise ValueError("review_date must be YYYY-MM-DD") from exc
    if parsed.isoformat() != review_date:
        raise ValueError("review_date must be YYYY-MM-DD")
    return review_date


def atomic_write_text(path: Path, content: str) -> Path:
    """Write text via same-directory temp file, then atomically replace target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return path


class DailySummaryAgent:
    """Creates preflight and end-of-day reports from the journal."""

    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or load_config()
        self.log_dir = Path(self.config.log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.risk_reviewer = RiskReviewer(self.config)
        self.trade_grader = TradeGrader(self.config)

    def morning(self, review_date: str) -> dict:
        review_date = validate_review_date(review_date)
        report = self.preview_morning(review_date)
        with self._artifact_lock():
            self._write_json(review_date, report)
            self._write_markdown(review_date, report, [])
        return report

    def preview_morning(self, review_date: str) -> dict:
        """Build a morning report without writing review artifacts."""
        review_date = validate_review_date(review_date)
        entries = read_journal(self.log_dir, review_date)
        review = self.risk_reviewer.review_entries(entries, review_date)
        return {
            "mode": "morning",
            "date": review_date,
            "recommended_state": review.recommended_state,
            "preflight": {
                "allowed_sessions": self.config.allowed_sessions,
                "allowed_instruments": self.config.allowed_instruments,
                "max_trades_per_day": self.config.max_trades_per_day,
                "max_consecutive_losses": self.config.max_consecutive_losses,
                "paper_only": True,
            },
            "risk_review": review.to_dict(),
            "message": self._morning_message(review),
        }

    def eod(self, review_date: str) -> dict:
        review_date = validate_review_date(review_date)
        report, grades = self.preview_eod_with_grades(review_date)
        with self._artifact_lock():
            self._write_json(review_date, report)
            self._write_trade_grades_csv(review_date, grades)
            self._write_markdown(review_date, report, grades)
        return report

    def preview_eod(self, review_date: str) -> dict:
        """Build an end-of-day report without writing review artifacts."""
        review_date = validate_review_date(review_date)
        report, _ = self.preview_eod_with_grades(review_date)
        return report

    def preview_eod_with_grades(self, review_date: str) -> tuple[dict, list[TradeGrade]]:
        """Build an EOD report and return internal grade objects for writers."""
        review_date = validate_review_date(review_date)
        entries = read_journal(self.log_dir, review_date)
        review = self.risk_reviewer.review_entries(entries, review_date)
        grades = self.trade_grader.grade_entries(entries)
        report = {
            "mode": "eod",
            "date": review_date,
            "recommended_state": review.recommended_state,
            "risk_review": review.to_dict(),
            "trade_grades": [grade.to_dict() for grade in grades],
            "message": self._eod_message(review, grades),
        }
        return report, grades

    def _write_json(self, review_date: str, report: dict) -> Path:
        review_date = validate_review_date(review_date)
        path = self.log_dir / f"review_{review_date}.json"
        return atomic_write_text(path, json.dumps(report, indent=2, sort_keys=True))

    def _write_trade_grades_csv(self, review_date: str, grades: Iterable[TradeGrade]) -> Path:
        review_date = validate_review_date(review_date)
        path = self.log_dir / f"trade_grades_{review_date}.csv"
        fieldnames = [
            "index",
            "timestamp",
            "instrument",
            "session",
            "strategy",
            "decision",
            "risk_result",
            "outcome",
            "score",
            "grade",
            "rule_compliant",
            "risk_notes",
        ]
        buffer = StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        for grade in grades:
            row = asdict(grade)
            row["risk_notes"] = ";".join(grade.risk_notes)
            writer.writerow(row)
        return atomic_write_text(path, buffer.getvalue())

    def _write_markdown(self, review_date: str, report: dict, grades: list[TradeGrade]) -> Path:
        review_date = validate_review_date(review_date)
        path = self.log_dir / f"daily_review_{review_date}.md"
        review = report["risk_review"]
        lines = [
            f"# Daily Review - {review_date}",
            "",
            f"Mode: {report['mode']}",
            f"Recommended state: {report['recommended_state']}",
            "",
            "## Risk",
            f"- Approved trades: {review['approved_trades']}",
            f"- NO_TRADE decisions: {review['no_trades']}",
            f"- Wins: {review['wins']}",
            f"- Losses: {review['losses']}",
            f"- Open trades: {review['open_trades']}",
            f"- Violations: {', '.join(review['violations']) if review['violations'] else 'none'}",
            f"- Warnings: {', '.join(review['warnings']) if review['warnings'] else 'none'}",
            "",
        ]
        if grades:
            lines.extend(["## Trade Grades"])
            for grade in grades:
                lines.append(
                    f"- #{grade.index} {grade.instrument} {grade.strategy}: "
                    f"{grade.grade} ({grade.score}) outcome={grade.outcome or 'OPEN'}"
                )
            lines.append("")
        lines.append(f"## Summary\n{report['message']}")
        return atomic_write_text(path, "\n".join(lines) + "\n")

    @contextmanager
    def _artifact_lock(self) -> Iterator[None]:
        """Serialize multi-file review artifact writes."""
        lock_path = self.log_dir / ".daily_review.lock"
        with open(lock_path, "a") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file, fcntl.LOCK_UN)

    @staticmethod
    def _morning_message(review: RiskReview) -> str:
        if review.violations:
            return "Morning preflight found rule violations from journal history. Recommended state is NO_TRADE."
        if "loss_lockout_active" in review.warnings:
            return "Loss lockout is active from journal history. Recommended state is NO_TRADE."
        if review.open_trades:
            return "Open trade detected. Resolve or review before taking new paper trades."
        return "Morning preflight is clean for paper trading under configured rules."

    @staticmethod
    def _eod_message(review: RiskReview, grades: list[TradeGrade]) -> str:
        if not grades:
            return "No approved trades to grade. NO_TRADE or no activity is acceptable."
        worst = min(grades, key=lambda grade: grade.score)
        best = max(grades, key=lambda grade: grade.score)
        return (
            f"Best trade grade: #{best.index} {best.grade}. "
            f"Worst trade grade: #{worst.index} {worst.grade}. "
            f"Rule violations: {len(review.violations)}."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only daily trade review reports")
    parser.add_argument("--date", default=date.today().isoformat(), help="Review date YYYY-MM-DD")
    parser.add_argument("--mode", choices=["morning", "eod"], required=True)
    parser.add_argument("--risk-rules", default="risk_rules.yaml")
    args = parser.parse_args()

    try:
        review_date = validate_review_date(args.date)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        config = load_config(args.risk_rules)
    except (ConfigError, LiveTradingBlockedError) as exc:
        parser.error(str(exc))

    agent = DailySummaryAgent(config)
    report = agent.morning(review_date) if args.mode == "morning" else agent.eod(review_date)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
