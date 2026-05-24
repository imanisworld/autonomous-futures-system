"""
journal/journal_logger.py

Append-only JSONL journal of all decisions and trade outcomes.
Logs are the authoritative record of system behavior.

File format: logs/journal_YYYY-MM-DD.jsonl
One JSON object per line, never edited after write.

Also provides daily state reconstruction by reading today's journal.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional, List

from risk.risk_engine import DailyState


logger = logging.getLogger(__name__)


class JournalLogger:
    """
    Append-only JSONL decision and trade journal.

    Responsibilities:
    - Write every decision (TRADE, NO_TRADE, DONE_FOR_DAY, WAIT) to disk
    - Update trade outcomes when paper broker resolves a position
    - Reconstruct daily state (trade count, loss streak) from today's journal
    """

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._error_log = self.log_dir / "errors.log"

    def _journal_path(self, for_date: Optional[date] = None) -> Path:
        d = for_date or date.today()
        return self.log_dir / f"journal_{d.isoformat()}.jsonl"

    # ── Write ──────────────────────────────────────────────────────────────────

    def log_decision(
        self,
        decision_dict: dict,
        risk_result: Optional[dict] = None,
        for_date: Optional[date] = None,
    ) -> None:
        """
        Append a decision entry to today's journal.

        Args:
            decision_dict: Output from DecisionOutput.to_dict()
            risk_result: Optional dict with {result, failed_rule, reason}
        """
        entry = dict(decision_dict)
        if risk_result:
            entry["risk_check"] = risk_result
        entry.setdefault("outcome", None)
        self._append(entry, for_date)

    def log_outcome(
        self,
        instrument: str,
        session: str,
        result: str,
        entry_price: float,
        exit_price: Optional[float],
        exit_reason: Optional[str],
        pnl_ticks: Optional[float],
        pnl_dollars: Optional[float],
        for_date: Optional[date] = None,
    ) -> None:
        """
        Append a trade outcome entry to today's journal.
        Called after paper broker resolves a position.
        """
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "OUTCOME",
            "instrument": instrument,
            "session": session,
            "outcome": {
                "result": result,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "pnl_ticks": pnl_ticks,
                "pnl_dollars": pnl_dollars,
            },
        }
        self._append(entry, for_date)

    def log_error(self, message: str, exc: Optional[Exception] = None) -> None:
        """Append to the error log."""
        ts = datetime.now(timezone.utc).isoformat()
        line = f"[{ts}] ERROR: {message}"
        if exc:
            line += f" | {type(exc).__name__}: {exc}"
        with open(self._error_log, "a") as f:
            f.write(line + "\n")
        logger.error(line)

    def _append(self, entry: dict, for_date: Optional[date] = None) -> None:
        """Append a single JSON entry to today's journal file."""
        path = self._journal_path(for_date)
        try:
            with open(path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            self.log_error(f"Failed to write journal entry: {entry}", exc=e)

    # ── Read / Reconstruct ────────────────────────────────────────────────────

    def get_daily_state(self, for_date: Optional[date] = None) -> DailyState:
        """
        Reconstruct DailyState from today's journal.

        Counts:
        - trade_count: number of entries where decision==TRADE and risk_check.result==APPROVED
        - consecutive_losses: trailing count of LOSS outcomes
        - has_open_position: True if last approved trade has no outcome yet
        """
        path = self._journal_path(for_date)
        if not path.exists():
            return DailyState(
                trade_count=0,
                consecutive_losses=0,
                has_open_position=False,
                date=(for_date or date.today()).isoformat(),
            )

        entries = self._read_entries(path)
        return self._compute_daily_state(entries, for_date)

    def _read_entries(self, path: Path) -> List[dict]:
        entries = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries

    def _compute_daily_state(
        self, entries: List[dict], for_date: Optional[date]
    ) -> DailyState:
        trade_count = 0
        last_outcomes: List[str] = []  # WIN or LOSS in order
        has_open_position = False

        for entry in entries:
            entry_type = entry.get("type")

            if entry_type == "OUTCOME":
                outcome_data = entry.get("outcome", {})
                result = outcome_data.get("result")
                if result in ("WIN", "LOSS"):
                    last_outcomes.append(result)
                    has_open_position = False
                continue

            decision = entry.get("decision")
            risk_check = entry.get("risk_check") or {}
            risk_approved = risk_check.get("result") == "APPROVED"
            outcome = entry.get("outcome") or {}
            outcome_result = outcome.get("result")

            if decision == "TRADE" and risk_approved:
                trade_count += 1
                if outcome_result in ("WIN", "LOSS"):
                    last_outcomes.append(outcome_result)
                    has_open_position = False
                else:
                    has_open_position = True

        # Count trailing consecutive losses
        consecutive_losses = 0
        for r in reversed(last_outcomes):
            if r == "LOSS":
                consecutive_losses += 1
            else:
                break

        return DailyState(
            trade_count=trade_count,
            consecutive_losses=consecutive_losses,
            has_open_position=has_open_position,
            date=(for_date or date.today()).isoformat(),
        )

    def get_summary(self, for_date: Optional[date] = None) -> dict:
        """Return a human-readable summary of today's trading activity."""
        path = self._journal_path(for_date)
        if not path.exists():
            return {"message": "No journal for this date.", "trades": 0, "no_trades": 0}

        entries = self._read_entries(path)
        trades = sum(
            1 for e in entries
            if e.get("decision") == "TRADE"
            and (e.get("risk_check") or {}).get("result") == "APPROVED"
        )
        no_trades = sum(1 for e in entries if e.get("decision") == "NO_TRADE")
        wins = sum(
            1 for e in entries
            if (e.get("outcome") or {}).get("result") == "WIN"
        )
        losses = sum(
            1 for e in entries
            if (e.get("outcome") or {}).get("result") == "LOSS"
        )

        return {
            "date": (for_date or date.today()).isoformat(),
            "trades": trades,
            "no_trades": no_trades,
            "wins": wins,
            "losses": losses,
            "journal_path": str(path),
        }
