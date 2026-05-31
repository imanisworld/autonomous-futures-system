"""
adaptive/committee.py

AdaptiveCommittee — orchestrates all four agents and produces a
CommitteeReport.  Never places trades, edits config, or modifies journals.

Usage:
    committee = AdaptiveCommittee(log_dir=config.log_dir, config=config)
    report = committee.run(days=7)   # returns CommitteeReport
    report = committee.run_and_persist(days=7)  # same + writes JSON artifact
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from config.settings import SystemConfig, load_config

from .journal_reader import JournalReader
from .models import CommitteeReport, Recommendation, sample_sufficiency, worst_status
from .ops_monitor import OpsMonitor
from .payload_auditor import PayloadAuditor
from .risk_steward import RiskSteward
from .strategy_analyst import StrategyAnalyst


class AdaptiveCommittee:
    """Read-only risk committee: four agents, one aggregated report."""

    def __init__(
        self,
        log_dir: Optional[str | Path] = None,
        config: Optional[SystemConfig] = None,
    ):
        cfg = config or load_config()
        self.log_dir = Path(log_dir or cfg.log_dir)
        self.reader = JournalReader(self.log_dir)
        self.payload_auditor = PayloadAuditor()
        self.risk_steward = RiskSteward(
            starting_balance=cfg.position_sizing.starting_balance,
            max_drawdown_percent=float(getattr(cfg, "max_drawdown_percent", 0.20) or 0.20),
            max_daily_loss_per_contract=float(getattr(cfg, "max_daily_loss", 150) or 150),
            circuit_breaker_losses=int(getattr(cfg, "circuit_breaker_losses", 3) or 3),
            max_trades_per_day=int(cfg.max_trades_per_day),
        )
        self.strategy_analyst = StrategyAnalyst()
        self.ops_monitor = OpsMonitor(self.log_dir)

    def run(self, days: int = 30) -> CommitteeReport:
        """Run all four agents and return an aggregated CommitteeReport."""
        trades = self.reader.read_trades(days=days)
        latest_age = self.reader.latest_entry_age_seconds()

        reports = [
            self.payload_auditor.audit(trades),
            self.risk_steward.audit(trades),
            self.strategy_analyst.audit(trades),
            self.ops_monitor.audit(latest_entry_age=latest_age),
        ]

        overall = "OK"
        all_recs: list[Recommendation] = []
        for r in reports:
            overall = worst_status(overall, r.status)
            all_recs.extend(r.recommendations)

        # Prioritise: CRITICAL codes first, then SYSTEM_FIX > PAYLOAD_FIX > PAUSE > REDUCE > WATCH > KEEP
        _priority = {
            "SYSTEM_FIX_REQUIRED": 0,
            "PAYLOAD_FIX_REQUIRED": 1,
            "PAUSE_STRATEGY": 2,
            "DISABLE_STRATEGY_CANDIDATE": 3,
            "REDUCE_SIZE": 4,
            "WATCH": 5,
            "KEEP_ACTIVE": 6,
        }
        all_recs.sort(key=lambda r: _priority.get(r.code, 99))
        top_recs = all_recs[:5]

        resolved_count = len([t for t in trades if t.result in ("WIN", "LOSS", "BREAKEVEN")])

        return CommitteeReport(
            date=date.today().isoformat(),
            overall_status=overall,
            agents=reports,
            top_recommendations=top_recs,
            sample_size=resolved_count,
            sample_sufficiency=sample_sufficiency(resolved_count),
        )

    def run_and_persist(self, days: int = 30) -> CommitteeReport:
        """Run committee and write adaptive_review_YYYY-MM-DD.json artifact."""
        report = self.run(days=days)
        self._write_artifact(report)
        return report

    def load_cached(self, for_date: Optional[date] = None) -> Optional[dict]:
        """Load a previously persisted committee report, or None if absent."""
        day = for_date or date.today()
        path = self.log_dir / f"adaptive_review_{day.isoformat()}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def load_history(self, days: int = 7) -> list[dict]:
        """Return up to `days` cached committee reports, newest first."""
        today = date.today()
        history: list[dict] = []
        for offset in range(days):
            day = today - __import__("datetime").timedelta(days=offset)
            cached = self.load_cached(day)
            if cached:
                history.append(cached)
        return history

    # ── Artifact writer ────────────────────────────────────────────────────────

    def _write_artifact(self, report: CommitteeReport) -> None:
        path = self.log_dir / f"adaptive_review_{report.date}.json"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
