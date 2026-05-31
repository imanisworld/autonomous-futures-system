"""
adaptive/ops_monitor.py

Checks local operational health, including session-aware stale feed detection.
Does not make outbound HTTP calls — everything is local filesystem.
"""

from __future__ import annotations

import os
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from .models import (
    AgentReport, Recommendation,
    SYSTEM_FIX_REQUIRED, WATCH,
    worst_status,
)


_ET = ZoneInfo("America/New_York")
_ACTIVE_STALE_SECONDS = 900       # 15m tolerance for 5m bar-close alerts
_STALE_CRITICAL_SECONDS = 86_400  # 24h while market should be active


class OpsMonitor:
    def __init__(self, log_dir: str | Path):
        self.log_dir = Path(log_dir)

    def audit(
        self,
        latest_entry_age: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> AgentReport:
        recs: list[Recommendation] = []
        status = "OK"
        now = _coerce_now(now)
        market_active = _futures_alert_window_active(now)

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
        if latest_entry_age is not None and market_active:
            if latest_entry_age > _STALE_CRITICAL_SECONDS:
                status = worst_status(status, "CRITICAL")
                recs.append(Recommendation(
                    code=SYSTEM_FIX_REQUIRED,
                    subject="webhook_feed",
                    reason=(
                        f"Last journal entry is {latest_entry_age/3600:.1f}h old during an active futures alert window. "
                        "TradingView alerts may have stopped firing or the Railway service may be down."
                    ),
                    evidence={"age_seconds": int(latest_entry_age), "market_active": True},
                ))
            elif latest_entry_age > _ACTIVE_STALE_SECONDS:
                status = worst_status(status, "WARNING")
                recs.append(Recommendation(
                    code=WATCH,
                    subject="webhook_feed",
                    reason=(
                        f"Last journal entry is {latest_entry_age/60:.0f}m old during an active futures alert window. "
                        "Expected 5m bar-close alerts may be missing."
                    ),
                    evidence={"age_seconds": int(latest_entry_age), "market_active": True},
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


def _futures_alert_window_active(now: datetime) -> bool:
    """CME equity futures are broadly closed Friday evening through Sunday 18:00 ET."""
    et = now.astimezone(_ET)
    weekday = et.weekday()  # Monday=0, Sunday=6
    current = et.time()
    if weekday == 5:  # Saturday
        return False
    if weekday == 6 and current < time(18, 0):
        return False
    if weekday == 4 and current >= time(17, 0):
        return False
    return True
