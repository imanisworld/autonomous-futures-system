"""
adaptive/ops_monitor.py

Checks local operational health, including session-aware stale feed detection.
Does not make outbound HTTP calls — everything is local filesystem.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from context.futures_session import futures_session_active

from .models import (
    AgentReport, Recommendation,
    SYSTEM_FIX_REQUIRED, WATCH,
    worst_status,
)
_STALE_CRITICAL_SECONDS = 86_400  # 24h while market should be active


class OpsMonitor:
    def __init__(self, log_dir: str | Path, expected_tf_minutes: int = 15):
        self.log_dir = Path(log_dir)
        # Drive staleness off the configured bar timeframe instead of a hardcoded
        # value — the system runs 15m bars now, not 5m. Tolerate ~2 missed bars
        # plus a 1-minute delivery-lag grace before warning (matches the UI).
        self.expected_tf_minutes = int(expected_tf_minutes or 15)
        self.active_stale_seconds = (self.expected_tf_minutes * 2 + 1) * 60

    def audit(
        self,
        latest_entry_age: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> AgentReport:
        recs: list[Recommendation] = []
        status = "OK"
        now = _coerce_now(now)
        market_active = futures_session_active(now)

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
                reason="No journal files found. Either no alerts have been received yet, or the log volume is not mounted.",
                evidence={"log_dir": str(self.log_dir)},
            ))

        # ── Latest entry age ──────────────────────────────────────────────────
        if latest_entry_age is not None and market_active:
            tf = self.expected_tf_minutes
            if latest_entry_age > _STALE_CRITICAL_SECONDS:
                status = worst_status(status, "CRITICAL")
                recs.append(Recommendation(
                    code=SYSTEM_FIX_REQUIRED,
                    subject="webhook_feed",
                    reason=(
                        f"Last journal entry is {latest_entry_age/3600:.1f}h old during an active futures alert window "
                        f"(expected a {tf}m bar every {tf} minutes). Check the TradingView alert log for webhook "
                        "delivery failures, then confirm the server is up."
                    ),
                    evidence={"age_seconds": int(latest_entry_age), "market_active": True, "expected_tf_minutes": tf},
                ))
            elif latest_entry_age > self.active_stale_seconds:
                status = worst_status(status, "WARNING")
                recs.append(Recommendation(
                    code=WATCH,
                    subject="webhook_feed",
                    reason=(
                        f"Last journal entry is {latest_entry_age/60:.0f}m old during an active futures alert window "
                        f"(expected a {tf}m bar every {tf} minutes). Check the TradingView alert log for webhook "
                        "delivery failures."
                    ),
                    evidence={"age_seconds": int(latest_entry_age), "market_active": True, "expected_tf_minutes": tf},
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
                "market_active": market_active,
            },
        )


def _coerce_now(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
