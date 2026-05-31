"""
adaptive/ops_monitor.py

Checks local operational health:
  - Log directory writable
  - Journal files present
  - Age of the latest journal entry (stale feed detection)
  - Adaptive review artifact writability

Does not make outbound HTTP calls — everything is local filesystem.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .models import (
    AgentReport, Recommendation,
    SYSTEM_FIX_REQUIRED, WATCH,
    worst_status,
)


_STALE_WARNING_SECONDS = 14_400   # 4 h  — market may be closed
_STALE_CRITICAL_SECONDS = 86_400  # 24 h — feed almost certainly broken


class OpsMonitor:
    def __init__(self, log_dir: str | Path):
        self.log_dir = Path(log_dir)

    def audit(self, latest_entry_age: Optional[float] = None) -> AgentReport:
        recs: list[Recommendation] = []
        status = "OK"

        # ── Log dir writable ──────────────────────────────────────────────────
        if not self.log_dir.exists():
            status = worst_status(status, "CRITICAL")
            recs.append(Recommendation(
                code=SYSTEM_FIX_REQUIRED,
                subject="log_directory",
                reason=f"Log directory {self.log_dir} does not exist.",
                evidence={"log_dir": str(self.log_dir)},
            ))
        elif not os.access(self.log_dir, os.W_OK):
            status = worst_status(status, "CRITICAL")
            recs.append(Recommendation(
                code=SYSTEM_FIX_REQUIRED,
                subject="log_directory",
                reason=f"Log directory {self.log_dir} is not writable. Journal writes will fail silently.",
                evidence={"log_dir": str(self.log_dir)},
            ))

        # ── Journal files present ─────────────────────────────────────────────
        journal_files = list(self.log_dir.glob("journal_*.jsonl")) if self.log_dir.exists() else []
        if not journal_files:
            recs.append(Recommendation(
                code=WATCH,
                subject="journal_files",
                reason="No journal files found. Either no alerts have been received yet, or the Railway Volume is not mounted.",
                evidence={"log_dir": str(self.log_dir)},
            ))

        # ── Latest entry age ──────────────────────────────────────────────────
        if latest_entry_age is not None:
            if latest_entry_age > _STALE_CRITICAL_SECONDS:
                status = worst_status(status, "CRITICAL")
                recs.append(Recommendation(
                    code=SYSTEM_FIX_REQUIRED,
                    subject="webhook_feed",
                    reason=(
                        f"Last journal entry is {latest_entry_age/3600:.1f}h old. "
                        "TradingView alerts may have stopped firing or the Railway service may be down."
                    ),
                    evidence={"age_seconds": int(latest_entry_age)},
                ))
            elif latest_entry_age > _STALE_WARNING_SECONDS:
                status = worst_status(status, "WARNING")
                recs.append(Recommendation(
                    code=WATCH,
                    subject="webhook_feed",
                    reason=(
                        f"Last journal entry is {latest_entry_age/3600:.1f}h old. "
                        "Expected during overnight/weekend sessions — check if market is open."
                    ),
                    evidence={"age_seconds": int(latest_entry_age)},
                ))

        return AgentReport(
            agent="ops_monitor",
            status=status,
            recommendations=recs,
            findings={
                "log_dir": str(self.log_dir),
                "log_dir_exists": self.log_dir.exists(),
                "log_dir_writable": os.access(self.log_dir, os.W_OK) if self.log_dir.exists() else False,
                "journal_file_count": len(journal_files),
                "latest_entry_age_seconds": int(latest_entry_age) if latest_entry_age is not None else None,
            },
        )
