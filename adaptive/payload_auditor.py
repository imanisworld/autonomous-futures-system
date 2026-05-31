"""
adaptive/payload_auditor.py

Audits recent webhook decisions and approved TRADE entries for payload-quality
problems. It intentionally looks at all decisions so broken Pine payloads that
cause every alert to become NO_TRADE still surface as PAYLOAD_FIX_REQUIRED.
"""

from __future__ import annotations

from collections import Counter

from .models import (
    AgentReport, Recommendation,
    PAYLOAD_FIX_REQUIRED, WATCH,
    worst_status,
)
from .models import DecisionRecord, TradeRecord


class PayloadAuditor:
    def audit(
        self,
        trades: list[TradeRecord],
        decisions: list[DecisionRecord] | None = None,
    ) -> AgentReport:
        decisions = decisions or []
        payload_source = decisions if decisions else _decisions_from_trades(trades)
        if not payload_source and not trades:
            return AgentReport(
                agent="payload_auditor",
                status="OK",
                recommendations=[],
                findings={"audited": 0, "decisions_audited": 0, "message": "No decisions to audit."},
            )

        recs: list[Recommendation] = []
        status = "OK"

        total_decisions = len(payload_source)
        null_trend = [d for d in payload_source if not d.trend_strength]
        null_vwap = [d for d in payload_source if d.vwap_value is None]
        zero_volume = [d for d in payload_source if (d.volume or 0) == 0]
        null_market_condition = [d for d in payload_source if not d.market_condition]
        failed_gate_counts = Counter(
            gate for d in payload_source for gate in (d.failed_gates or [])
        )

        if total_decisions:
            status = _recommend_if_threshold(
                recs=recs,
                status=status,
                subject="trend_strength_field",
                missing=null_trend,
                total=total_decisions,
                threshold_pct=30,
                reason=(
                    "arrived with null trend_strength. Missing trend data blocks setups "
                    "via TREND_STRENGTH_BELOW_REQUIRED when strong-trend gating is enabled."
                ),
                evidence_extra={"failed_gates": dict(failed_gate_counts)},
            )
            status = _recommend_if_threshold(
                recs=recs,
                status=status,
                subject="vwap_field",
                missing=null_vwap,
                total=total_decisions,
                threshold_pct=50,
                reason="arrived with null VWAP. VWAP-dependent strategies may produce incorrect setups.",
            )
            status = _recommend_if_threshold(
                recs=recs,
                status=status,
                subject="market_condition_field",
                missing=null_market_condition,
                total=total_decisions,
                threshold_pct=30,
                reason="arrived with null market_condition. The market-condition gate may reject otherwise valid alerts.",
            )

            if zero_volume:
                pct = len(zero_volume) / total_decisions * 100
                if pct > 20:
                    recs.append(Recommendation(
                        code=WATCH,
                        subject="volume_field",
                        reason=(
                            f"{len(zero_volume)}/{total_decisions} decisions ({pct:.0f}%) show volume=0. "
                            "Volume-ratio quality gate may not be enforcing correctly."
                        ),
                        evidence={"count": len(zero_volume), "pct": round(pct, 1)},
                    ))

        trade_total = len(trades)
        missing_bracket = [t for t in trades if not t.entry or not t.stop or not t.target]
        bracket_ignored = [t for t in trades if t.pine_bracket_ignored]

        if missing_bracket:
            pct = len(missing_bracket) / trade_total * 100
            status = worst_status(status, "WARNING")
            recs.append(Recommendation(
                code=PAYLOAD_FIX_REQUIRED,
                subject="pine_advisory_bracket",
                reason=(
                    f"{len(missing_bracket)}/{trade_total} approved trades ({pct:.0f}%) missing Pine "
                    "entry/stop/target — backend computed its own bracket. "
                    "Verify risksentinel_context.pine is publishing these fields."
                ),
                evidence={
                    "count": len(missing_bracket),
                    "pct": round(pct, 1),
                    "sample_ts": [t.ts for t in missing_bracket[:3]],
                },
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

        return AgentReport(
            agent="payload_auditor",
            status=status,
            recommendations=recs,
            findings={
                "audited": trade_total,
                "decisions_audited": total_decisions,
                "missing_bracket": len(missing_bracket),
                "null_trend_strength": len(null_trend),
                "null_vwap": len(null_vwap),
                "zero_volume": len(zero_volume),
                "null_market_condition": len(null_market_condition),
                "pine_bracket_ignored": len(bracket_ignored),
                "failed_gate_counts": dict(failed_gate_counts),
            },
        )


def _recommend_if_threshold(
    *,
    recs: list[Recommendation],
    status: str,
    subject: str,
    missing: list[DecisionRecord],
    total: int,
    threshold_pct: float,
    reason: str,
    evidence_extra: dict | None = None,
) -> str:
    if not missing or total <= 0:
        return status
    pct = len(missing) / total * 100
    if pct <= threshold_pct:
        return status
    evidence = {
        "count": len(missing),
        "pct": round(pct, 1),
        "sample_ts": [d.ts for d in missing[:3]],
    }
    if evidence_extra:
        evidence.update(evidence_extra)
    recs.append(Recommendation(
        code=PAYLOAD_FIX_REQUIRED,
        subject=subject,
        reason=f"{len(missing)}/{total} decisions ({pct:.0f}%) {reason}",
        evidence=evidence,
    ))
    return worst_status(status, "WARNING")


def _decisions_from_trades(trades: list[TradeRecord]) -> list[DecisionRecord]:
    records: list[DecisionRecord] = []
    for trade in trades:
        records.append(DecisionRecord(
            date=trade.date,
            ts=trade.ts,
            instrument=trade.instrument,
            session=trade.session,
            decision="TRADE",
            reason=None,
            failed_gates=[],
            risk_failed_rule=None,
            strategy=trade.strategy,
            direction=trade.direction,
            entry=trade.entry,
            stop=trade.stop,
            target=trade.target,
            rr_ratio=trade.rr_ratio,
            trend_strength=trade.trend_strength,
            vwap_value=trade.vwap_value,
            volume=trade.volume,
            market_condition="TRADE",
            pine_bracket_overridden=trade.pine_bracket_overridden,
            pine_bracket_ignored=trade.pine_bracket_ignored,
        ))
    return records
