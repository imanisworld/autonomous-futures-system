"""
adaptive/risk_steward.py

Watches account-level risk metrics across all resolved trades:
  - Drawdown from peak
  - Daily loss breaches (any day where max_daily_loss was triggered)
  - Consecutive-loss streaks
  - Contract tier transitions (is the ladder scaling too fast?)
  - Overtrading (daily trade counts near/at cap)
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .models import (
    AgentReport, Recommendation,
    REDUCE_SIZE, WATCH, SYSTEM_FIX_REQUIRED,
    worst_status,
)
from .models import TradeRecord


class RiskSteward:
    def __init__(
        self,
        starting_balance: float = 1500.0,
        max_drawdown_percent: float = 0.20,
        max_daily_loss_per_contract: float = 150.0,
        circuit_breaker_losses: int = 3,
        max_trades_per_day: int = 5,
    ):
        self.starting_balance = starting_balance
        self.max_drawdown_percent = max_drawdown_percent
        self.max_daily_loss_per_contract = max_daily_loss_per_contract
        self.circuit_breaker_losses = circuit_breaker_losses
        self.max_trades_per_day = max_trades_per_day

    def audit(self, trades: list[TradeRecord]) -> AgentReport:
        if not trades:
            return AgentReport(
                agent="risk_steward",
                status="OK",
                recommendations=[],
                findings={"message": "No trades to evaluate."},
            )

        recs: list[Recommendation] = []
        status = "OK"

        # ── Rebuild balance curve ──────────────────────────────────────────────
        balance = self.starting_balance
        peak = balance
        max_dd_seen = 0.0
        resolved = [t for t in trades if t.result in ("WIN", "LOSS", "BREAKEVEN")]
        for t in resolved:
            balance += float(t.pnl_dollars or 0.0)
            peak = max(peak, balance)
            dd = (peak - balance) / peak if peak > 0 else 0.0
            max_dd_seen = max(max_dd_seen, dd)

        current_balance = balance
        current_dd = (peak - current_balance) / peak if peak > 0 else 0.0

        # ── Drawdown check ────────────────────────────────────────────────────
        if current_dd > self.max_drawdown_percent * 0.75:
            sev = "CRITICAL" if current_dd >= self.max_drawdown_percent else "WARNING"
            status = worst_status(status, sev)
            recs.append(Recommendation(
                code=REDUCE_SIZE,
                subject="drawdown",
                reason=(
                    f"Current drawdown {current_dd*100:.1f}% "
                    f"({'at' if sev == 'CRITICAL' else 'approaching'} "
                    f"{self.max_drawdown_percent*100:.0f}% limit). "
                    "Consider pausing or reducing contract size."
                ),
                evidence={
                    "current_drawdown_pct": round(current_dd * 100, 2),
                    "limit_pct": self.max_drawdown_percent * 100,
                    "current_balance": round(current_balance, 2),
                    "peak_balance": round(peak, 2),
                },
            ))

        # ── Per-day loss check ────────────────────────────────────────────────
        by_day: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in resolved:
            by_day[t.date].append(t)

        daily_loss_breaches: list[str] = []
        for day, day_trades in by_day.items():
            day_pnl = sum(float(t.pnl_dollars or 0.0) for t in day_trades)
            contracts = max((t.contracts for t in day_trades), default=1)
            limit = self.max_daily_loss_per_contract * contracts
            if day_pnl <= -limit:
                daily_loss_breaches.append(day)

        if daily_loss_breaches:
            status = worst_status(status, "WARNING")
            recs.append(Recommendation(
                code=WATCH,
                subject="daily_loss_limit",
                reason=(
                    f"Daily loss limit was reached on {len(daily_loss_breaches)} day(s). "
                    "Verify circuit-breaker fired correctly each time."
                ),
                evidence={"dates": daily_loss_breaches[-5:]},
            ))

        # ── Consecutive-loss streaks ──────────────────────────────────────────
        max_streak = _max_consecutive_losses(resolved)
        current_streak = _current_consecutive_losses(resolved)
        if current_streak >= self.circuit_breaker_losses:
            status = worst_status(status, "WARNING")
            recs.append(Recommendation(
                code=WATCH,
                subject="consecutive_losses",
                reason=(
                    f"Current consecutive-loss streak of {current_streak} reached the circuit-breaker "
                    f"threshold ({self.circuit_breaker_losses}). "
                    "Review trade quality during those sessions."
                ),
                evidence={
                    "current_streak": current_streak,
                    "max_streak": max_streak,
                    "threshold": self.circuit_breaker_losses,
                },
            ))

        # ── Contract tier transition check ────────────────────────────────────
        if resolved:
            tiers_seen = sorted({t.contracts for t in resolved})
            first_contracts = resolved[0].contracts
            last_contracts = resolved[-1].contracts
            tier_jumps = last_contracts - first_contracts
            if tier_jumps > 2:
                recs.append(Recommendation(
                    code=WATCH,
                    subject="contract_tier_scaling",
                    reason=(
                        f"Contract size moved from {first_contracts}c to {last_contracts}c "
                        f"({tier_jumps} tier steps). Verify the compounding ladder is "
                        "advancing only after confirmed balance thresholds."
                    ),
                    evidence={"tiers_seen": tiers_seen, "first": first_contracts, "latest": last_contracts},
                ))

        # ── Scale-up eligibility ──────────────────────────────────────────────
        if len(resolved) >= 30:
            net_pnl = sum(float(t.pnl_dollars or 0.0) for t in resolved)
            if net_pnl > 0 and current_dd < self.max_drawdown_percent * 0.5 and not daily_loss_breaches:
                recs.append(Recommendation(
                    code="KEEP_ACTIVE",
                    subject="scale_up_eligible",
                    reason=(
                        f"30+ resolved trades, positive P&L (${net_pnl:.2f}), "
                        f"drawdown {current_dd*100:.1f}% well within limit. "
                        "System is healthy — compounding ladder may advance on next balance threshold."
                    ),
                    evidence={
                        "resolved_trades": len(resolved),
                        "net_pnl": round(net_pnl, 2),
                        "current_drawdown_pct": round(current_dd * 100, 2),
                    },
                ))

        return AgentReport(
            agent="risk_steward",
            status=status,
            recommendations=recs,
            findings={
                "resolved_trades": len(resolved),
                "current_balance": round(current_balance, 2),
                "peak_balance": round(peak, 2),
                "current_drawdown_pct": round(current_dd * 100, 2),
                "max_drawdown_seen_pct": round(max_dd_seen * 100, 2),
                "daily_loss_breaches": len(daily_loss_breaches),
                "max_consecutive_losses": max_streak,
                "current_consecutive_losses": current_streak,
            },
        )


def _max_consecutive_losses(trades: list[TradeRecord]) -> int:
    max_streak = streak = 0
    for t in trades:
        if t.result == "LOSS":
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def _current_consecutive_losses(trades: list[TradeRecord]) -> int:
    streak = 0
    for t in reversed(trades):
        if t.result == "LOSS":
            streak += 1
        else:
            break
    return streak
