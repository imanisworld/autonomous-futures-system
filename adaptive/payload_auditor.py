"""
adaptive/payload_auditor.py

Audits recent TRADE entries for payload-quality problems:
  - Pine advisory bracket fields missing (entry/stop/target null)
  - trend_strength null or absent
  - VWAP null
  - volume == 0
  - Pine bracket present but silently ignored (strategy/direction name mismatch)

Operates only on resolved TradeRecord objects — never modifies anything.
"""

from __future__ import annotations

from .models import (
    AgentReport, Recommendation,
    PAYLOAD_FIX_REQUIRED, WATCH,
    worst_status,
)
from .models import TradeRecord


class PayloadAuditor:
    def audit(self, trades: list[TradeRecord]) -> AgentReport:
        if not trades:
            return AgentReport(
                agent="payload_auditor",
                status="OK",
                recommendations=[],
                findings={"audited": 0, "message": "No approved trades to audit."},
            )

        total = len(trades)
        missing_bracket = [t for t in trades if not t.entry or not t.stop or not t.target]
        null_trend       = [t for t in trades if not t.trend_strength]
        null_vwap        = [t for t in trades if t.vwap_value is None]
        zero_volume      = [t for t in trades if (t.volume or 0) == 0]
        bracket_ignored  = [t for t in trades if t.pine_bracket_ignored]

        recs: list[Recommendation] = []
        status = "OK"

        if missing_bracket:
            pct = len(missing_bracket) / total * 100
            status = worst_status(status, "WARNING")
            recs.append(Recommendation(
                code=PAYLOAD_FIX_REQUIRED,
                subject="pine_advisory_bracket",
                reason=(
                    f"{len(missing_bracket)}/{total} trades ({pct:.0f}%) missing Pine "
                    "entry/stop/target — backend computed its own bracket. "
                    "Verify risksentinel_context.pine is publishing these fields."
                ),
                evidence={
                    "count": len(missing_bracket),
                    "pct": round(pct, 1),
                    "sample_ts": [t.ts for t in missing_bracket[:3]],
                },
            ))

        if null_trend:
            pct = len(null_trend) / total * 100
            if pct > 30:
                status = worst_status(status, "WARNING")
                recs.append(Recommendation(
                    code=PAYLOAD_FIX_REQUIRED,
                    subject="trend_strength_field",
                    reason=(
                        f"{len(null_trend)}/{total} trades ({pct:.0f}%) arrived with null "
                        "trend_strength. require_strong_trend gate is bypassed when this "
                        "field is absent."
                    ),
                    evidence={"count": len(null_trend), "pct": round(pct, 1)},
                ))

        if null_vwap:
            pct = len(null_vwap) / total * 100
            if pct > 50:
                status = worst_status(status, "WARNING")
                recs.append(Recommendation(
                    code=PAYLOAD_FIX_REQUIRED,
                    subject="vwap_field",
                    reason=(
                        f"{len(null_vwap)}/{total} trades ({pct:.0f}%) arrived with null "
                        "VWAP. VWAP-dependent strategies may produce incorrect setups."
                    ),
                    evidence={"count": len(null_vwap), "pct": round(pct, 1)},
                ))

        if bracket_ignored:
            status = worst_status(status, "WARNING")
            strategies = list({t.strategy for t in bracket_ignored})
            recs.append(Recommendation(
                code=PAYLOAD_FIX_REQUIRED,
                subject="pine_strategy_name_mismatch",
                reason=(
                    f"{len(bracket_ignored)} Pine bracket(s) were sent but ignored due to "
                    "direction or strategy name mismatch. Pine signal_strategy must exactly "
                    "match the backend strategy name."
                ),
                evidence={
                    "count": len(bracket_ignored),
                    "affected_strategies": strategies,
                    "sample_ts": [t.ts for t in bracket_ignored[:3]],
                },
            ))

        if zero_volume:
            pct = len(zero_volume) / total * 100
            if pct > 20:
                recs.append(Recommendation(
                    code=WATCH,
                    subject="volume_field",
                    reason=(
                        f"{len(zero_volume)}/{total} trades ({pct:.0f}%) show volume=0. "
                        "Volume-ratio quality gate may not be enforcing correctly."
                    ),
                    evidence={"count": len(zero_volume), "pct": round(pct, 1)},
                ))

        return AgentReport(
            agent="payload_auditor",
            status=status,
            recommendations=recs,
            findings={
                "audited": total,
                "missing_bracket": len(missing_bracket),
                "null_trend_strength": len(null_trend),
                "null_vwap": len(null_vwap),
                "zero_volume": len(zero_volume),
                "pine_bracket_ignored": len(bracket_ignored),
            },
        )
