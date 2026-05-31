"""
adaptive/strategy_analyst.py

Tracks win rate, profit factor, and expectancy per strategy and session.

Sample-size rules (enforced before making any actionable claim):
  < 10 trades  → "insufficient_sample" — no recommendations
  10–29 trades → "early_signal"        — WATCH only, no pause/disable
  30+ trades   → "actionable"          — full recommendation set available

Pause criteria (needs "actionable" sample):
  - expectancy < 0, OR
  - profit factor < 1.0

Scale-back criteria (needs "early_signal" or "actionable"):
  - win rate < 40% AND profit factor < 1.0
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .models import (
    AgentReport, Recommendation, TradeRecord,
    KEEP_ACTIVE, WATCH, PAUSE_STRATEGY, DISABLE_STRATEGY_CANDIDATE,
    sample_sufficiency, worst_status,
)


class StrategyAnalyst:
    def audit(self, trades: list[TradeRecord]) -> AgentReport:
        resolved = [t for t in trades if t.result in ("WIN", "LOSS", "BREAKEVEN")]

        if not trades:
            return AgentReport(
                agent="strategy_analyst",
                status="OK",
                recommendations=[],
                findings={"message": "No trades to analyse."},
            )

        # ── Per-strategy stats ─────────────────────────────────────────────────
        by_strategy: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in resolved:
            by_strategy[t.strategy].append(t)

        strategy_stats: dict[str, dict] = {}
        recs: list[Recommendation] = []
        status = "OK"

        for strat, strat_trades in by_strategy.items():
            stats = _compute_stats(strat_trades)
            strategy_stats[strat] = stats
            sufficiency = sample_sufficiency(stats["count"])

            if sufficiency == "insufficient_sample":
                continue  # never recommend on tiny samples

            if sufficiency in ("early_signal", "actionable"):
                pf_early = stats["profit_factor"]
                if stats["win_rate"] < 40 and (pf_early is not None and pf_early < 1.0):
                    status = worst_status(status, "WARNING")
                    recs.append(Recommendation(
                        code=WATCH,
                        subject=strat,
                        reason=(
                            f"{strat}: win rate {stats['win_rate']:.1f}%, "
                            f"profit factor {stats['profit_factor']:.2f} "
                            f"({sufficiency}, {stats['count']} trades). "
                            "Early underperformance — monitor next 10 trades."
                        ),
                        evidence=stats,
                    ))

            if sufficiency == "actionable":
                pf = stats["profit_factor"]  # None means no losses (treat as inf)
                if stats["expectancy"] < 0:
                    status = worst_status(status, "WARNING")
                    recs.append(Recommendation(
                        code=PAUSE_STRATEGY,
                        subject=strat,
                        reason=(
                            f"{strat}: negative expectancy "
                            f"${stats['expectancy']:.2f}/trade over {stats['count']} trades. "
                            "Pause this strategy until cause is identified."
                        ),
                        evidence=stats,
                    ))
                elif pf is not None and pf < 1.0:
                    status = worst_status(status, "WARNING")
                    recs.append(Recommendation(
                        code=PAUSE_STRATEGY,
                        subject=strat,
                        reason=(
                            f"{strat}: profit factor {pf:.2f} < 1.0 "
                            f"over {stats['count']} trades. "
                            "Strategy is net-negative. Pause and review."
                        ),
                        evidence=stats,
                    ))
                elif (pf is None or pf >= 2.0) and stats["win_rate"] >= 55:
                    pf_label = f"{pf:.2f}" if pf is not None else "∞"
                    recs.append(Recommendation(
                        code=KEEP_ACTIVE,
                        subject=strat,
                        reason=(
                            f"{strat}: profit factor {pf_label}, "
                            f"win rate {stats['win_rate']:.1f}% over {stats['count']} trades. "
                            "Strong performer — keep active."
                        ),
                        evidence=stats,
                    ))

        # ── Per-session stats ──────────────────────────────────────────────────
        by_session: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in resolved:
            by_session[t.session].append(t)

        session_stats: dict[str, dict] = {}
        for sess, sess_trades in by_session.items():
            stats = _compute_stats(sess_trades)
            session_stats[sess] = stats
            sufficiency = sample_sufficiency(stats["count"])
            if sufficiency == "actionable" and stats["expectancy"] < 0:
                status = worst_status(status, "WARNING")
                recs.append(Recommendation(
                    code=WATCH,
                    subject=f"session:{sess}",
                    reason=(
                        f"{sess} session: negative expectancy "
                        f"${stats['expectancy']:.2f}/trade over {stats['count']} trades."
                    ),
                    evidence=stats,
                ))

        return AgentReport(
            agent="strategy_analyst",
            status=status,
            recommendations=recs,
            findings={
                "total_resolved": len(resolved),
                "by_strategy": strategy_stats,
                "by_session": session_stats,
                "overall": _compute_stats(resolved),
            },
        )


# ── Statistics helpers ─────────────────────────────────────────────────────────

def _compute_stats(trades: list[TradeRecord]) -> dict:
    if not trades:
        return {"count": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "profit_factor": 0.0, "expectancy": 0.0, "net_pnl": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0}

    wins   = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]

    gross_win  = sum(float(t.pnl_dollars or 0.0) for t in wins)
    gross_loss = abs(sum(float(t.pnl_dollars or 0.0) for t in losses))
    net_pnl    = gross_win - gross_loss
    count      = len(trades)

    win_rate      = len(wins) / count * 100 if count else 0.0
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    expectancy    = net_pnl / count if count else 0.0
    avg_win       = gross_win / len(wins) if wins else 0.0
    avg_loss      = -gross_loss / len(losses) if losses else 0.0

    return {
        "count": count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "expectancy": round(expectancy, 2),
        "net_pnl": round(net_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
    }
