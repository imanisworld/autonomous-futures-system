"""
adaptive/models.py

Shared dataclasses for the Adaptive Risk Committee layer.

The committee is read-only: it produces Recommendations but never mutates
risk_rules.yaml, journal entries, or live config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── Recommendation codes ───────────────────────────────────────────────────────

KEEP_ACTIVE              = "KEEP_ACTIVE"
WATCH                    = "WATCH"
REDUCE_SIZE              = "REDUCE_SIZE"
PAUSE_STRATEGY           = "PAUSE_STRATEGY"
DISABLE_STRATEGY_CANDIDATE = "DISABLE_STRATEGY_CANDIDATE"
PAYLOAD_FIX_REQUIRED     = "PAYLOAD_FIX_REQUIRED"
SYSTEM_FIX_REQUIRED      = "SYSTEM_FIX_REQUIRED"

# Severity order (higher index = worse)
_STATUS_ORDER = ["OK", "WARNING", "CRITICAL"]


def worst_status(*statuses: str) -> str:
    return max(statuses, key=lambda s: _STATUS_ORDER.index(s) if s in _STATUS_ORDER else 0)


def sample_sufficiency(n: int) -> str:
    if n < 10:
        return "insufficient_sample"
    if n < 30:
        return "early_signal"
    return "actionable"


# ── Core dataclasses ───────────────────────────────────────────────────────────

@dataclass
class Recommendation:
    code: str
    subject: str
    reason: str
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "subject": self.subject,
            "reason": self.reason,
            "evidence": self.evidence,
        }


@dataclass
class AgentReport:
    agent: str
    status: str          # OK | WARNING | CRITICAL
    recommendations: list[Recommendation]
    findings: dict

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "status": self.status,
            "recommendations": [r.to_dict() for r in self.recommendations],
            "findings": self.findings,
        }


@dataclass
class CommitteeReport:
    date: str
    overall_status: str  # OK | WARNING | CRITICAL
    agents: list[AgentReport]
    top_recommendations: list[Recommendation]
    sample_size: int
    sample_sufficiency: str  # insufficient_sample | early_signal | actionable

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "overall_status": self.overall_status,
            "agents": [a.to_dict() for a in self.agents],
            "top_recommendations": [r.to_dict() for r in self.top_recommendations],
            "sample_size": self.sample_size,
            "sample_sufficiency": self.sample_sufficiency,
        }


# ── Trade record (produced by journal_reader, consumed by agents) ──────────────

@dataclass
class TradeRecord:
    date: str
    ts: str
    instrument: str
    session: str
    strategy: str
    direction: str
    contracts: int
    confluence_grade: Optional[str]
    entry: Optional[float]
    stop: Optional[float]
    target: Optional[float]
    rr_ratio: Optional[float]
    result: Optional[str]        # WIN | LOSS | BREAKEVEN | None (still open)
    pnl_dollars: Optional[float]
    # payload-quality fields
    trend_strength: Optional[str]
    vwap_value: Optional[float]
    volume: Optional[int]
    pine_bracket_overridden: bool  # True if Pine advisory bracket was accepted
    pine_bracket_ignored: bool     # True if Pine sent bracket but it was rejected
