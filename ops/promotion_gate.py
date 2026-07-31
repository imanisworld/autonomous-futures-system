"""Read-only Strategy Promotion Proof Gate.

Answers one question per `.claude/commands/futures-forward-measurement-gate.md`
and `docs/strategy-rules/Strategy_Inventory.md`'s pipeline-gates discipline:
has this strategy proven itself through the REAL executable path (journal
TRADE -> RiskEngine APPROVED -> broker fill/no-fill -> OUTCOME), not merely
in a standalone backtest/replay?

This module never runs a replay, never changes config, never promotes a
strategy, and never mutates the journal. It composes the already-trusted
journal readers (``ops.proof_30_mnq``, ``ops.build_honest_baseline``,
``ops.reconciler_outcome_audit``, ``ops.audit_plain_cancelled``,
``ops.evidence_readiness``) rather than re-parsing journals itself.

If a research/standalone evidence JSON is supplied (the output of the
strategy's own detector/replay research scripts under research/ or
scripts/), it is read verbatim for comparison; this module does not
regenerate or validate that evidence, only reports what it says next to
what the real system actually did.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ops.audit_plain_cancelled import build_audit as build_cancelled_audit
from ops.build_honest_baseline import INSTRUMENTS, build_baseline
from ops.evidence_readiness import (
    MIN_PROFIT_FACTOR,
    STRATEGY_MIN_DAYS,
    STRATEGY_MIN_EXAMPLES,
    build_evidence_readiness,
)
from ops.proof_30_mnq import read_journal_entries
from ops.reconciler_outcome_audit import build_audit_report as build_reconciler_audit

# Entry-model/tolerance env vars that determine what "filled" actually meant
# for this evidence. Kept in sync with ops.live_box_guard's
# PROOF_CRITICAL_RUNTIME_OVERRIDES list rather than duplicating its logic.
EXECUTION_CONTEXT_ENV_VARS = (
    "ENTRY_FILL_MODEL",
    "ENTRY_SLIPPAGE_TOLERANCE_TICKS",
    "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES",
    "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ",
    "TRADOVATE_ENTRY_EXECUTION_MODE",
    "MAX_CONTRACTS_HARD_CAP",
    "FILL_SLIPPAGE_TICKS",
    "FILL_PESSIMISTIC_BOTH_HIT",
)

# Below this fraction of the standalone/research candidate population
# surviving to a real filled trade, treat the gap as the Miyagi / 60M 3-2-2
# precedent: standalone evidence looked positive, but the real system
# rejected nearly the whole population. This does not by itself mean the
# strategy is bad -- it means the standalone number cannot be trusted as
# promotion evidence until the gap is explained.
LOW_PARITY_SURVIVAL_RATIO = 0.05


def _env(name: str) -> str | None:
    import os
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _strategy_rows(baseline: dict[str, Any], strategy: str, instrument: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for inst, report in baseline["instruments"].items():
        if instrument and inst.upper() != instrument.upper():
            continue
        rows.extend(row for row in report["trades"] if row.get("strategy") == strategy)
    return rows


def _open_position_count(entries: list[dict[str, Any]], strategy: str, instruments: list[str]) -> dict[str, int]:
    """Approved-decision count minus resolved count, per instrument.

    Mirrors (does not reimplement) ops.proof_30_mnq.pair_resolved_trades's
    FIFO trade<->outcome matching rule: every APPROVED TRADE decision is
    either resolved (present in the honest baseline) or still open. The
    baseline already gives us the resolved count; this only needs the raw
    approved-decision count to complete the identity.
    """
    approved: dict[str, int] = {inst: 0 for inst in instruments}
    for entry in entries:
        inst = str(entry.get("instrument") or "").upper()
        if inst not in approved:
            continue
        if entry.get("decision") != "TRADE":
            continue
        if (entry.get("risk_check") or {}).get("result") != "APPROVED":
            continue
        if (entry.get("setup") or {}).get("strategy") != strategy:
            continue
        approved[inst] += 1
    return approved


def _load_research_evidence(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"provided": False, "path": None}
    if not path.exists():
        return {"provided": False, "path": str(path), "error": "file not found"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"provided": False, "path": str(path), "error": str(exc)}
    if not isinstance(payload, dict):
        return {"provided": False, "path": str(path), "error": "top-level JSON is not an object"}
    payload = dict(payload)
    payload["provided"] = True
    payload["path"] = str(path)
    return payload


def _performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [r for r in rows if r["category"] == "filled_win_loss"]
    wins = [r for r in filled if r["result"] == "WIN"]
    losses = [r for r in filled if r["result"] == "LOSS"]
    pnls = [float(r["pnl_dollars"] or 0.0) for r in filled]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    net = round(sum(pnls), 2)
    profit_factor: Any
    if gross_loss:
        profit_factor = round(gross_win / gross_loss, 2)
    elif gross_win:
        profit_factor = "infinite"
    else:
        profit_factor = None
    long_rows = [r for r in filled if r.get("direction") == "LONG"]
    short_rows = [r for r in filled if r.get("direction") == "SHORT"]
    by_instrument: dict[str, dict[str, Any]] = {}
    for r in filled:
        inst = r.get("instrument") or "UNKNOWN"
        bucket = by_instrument.setdefault(inst, {"count": 0, "net_pnl_dollars": 0.0})
        bucket["count"] += 1
        bucket["net_pnl_dollars"] = round(bucket["net_pnl_dollars"] + float(r["pnl_dollars"] or 0.0), 2)
    running = peak = max_dd = 0.0
    loss_streak = max_loss_streak = 0
    for r in filled:
        pnl = float(r["pnl_dollars"] or 0.0)
        running += pnl
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
        if r["result"] == "LOSS":
            loss_streak += 1
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            loss_streak = 0
    return {
        "filled_count": len(filled),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(filled), 4) if filled else None,
        "net_pnl_dollars": net,
        "expectancy_dollars": round(net / len(filled), 2) if filled else None,
        "profit_factor": profit_factor,
        "long_count": len(long_rows),
        "short_count": len(short_rows),
        "by_instrument": by_instrument,
        "max_drawdown_dollars": round(max_dd, 2),
        "max_loss_streak": max_loss_streak,
    }


def _classify(
    *,
    performance: dict[str, Any],
    distinct_days: int,
    unclassified_reconciler_rows: int,
    mislabeled_fill_suspects: int,
    parity_ratio: float | None,
) -> dict[str, Any]:
    filled_count = performance["filled_count"]
    reasons: list[str] = []

    if filled_count == 0:
        return {
            "classification": "WAIT",
            "reasons": ["zero executable fills through the real path (RiskEngine/PaperBroker); nothing to score yet"],
        }
    if unclassified_reconciler_rows:
        reasons.append(f"{unclassified_reconciler_rows} reconciler-touched outcome row(s) still unaudited")
    if mislabeled_fill_suspects:
        reasons.append(f"{mislabeled_fill_suspects} plain-CANCELLED row(s) flagged MISLABELED_FILL_SUSPECT")
    if reasons:
        return {"classification": "UNSAFE", "reasons": reasons + ["accounting must be clean before any classification is trusted"]}

    if filled_count < STRATEGY_MIN_EXAMPLES or distinct_days < STRATEGY_MIN_DAYS:
        return {
            "classification": "PROMISING BUT UNPROVEN",
            "reasons": [
                f"real-path sample below the gate: {filled_count}/{STRATEGY_MIN_EXAMPLES} filled trades, "
                f"{distinct_days}/{STRATEGY_MIN_DAYS} distinct days"
            ],
        }

    pf = performance["profit_factor"]
    pf_ok = pf == "infinite" or (isinstance(pf, (int, float)) and pf >= MIN_PROFIT_FACTOR)
    if performance["net_pnl_dollars"] <= 0 or not pf_ok:
        return {
            "classification": "BROKEN",
            "reasons": [f"real-path net_pnl=${performance['net_pnl_dollars']}, profit_factor={pf} (min {MIN_PROFIT_FACTOR})"],
        }

    if parity_ratio is not None and parity_ratio < LOW_PARITY_SURVIVAL_RATIO:
        return {
            "classification": "OVERFIT",
            "reasons": [
                f"only {parity_ratio:.1%} of the standalone/research candidate population survived to a "
                "real filled trade -- Miyagi / 60M 3-2-2 precedent: standalone evidence does not carry over"
            ],
        }

    return {
        "classification": "VALIDATED",
        "reasons": [
            f"real-path sample meets the gate ({filled_count} filled / {distinct_days} days), "
            f"net positive with profit_factor={pf}, no unresolved accounting gaps"
        ],
    }


def build_promotion_report(
    *,
    journal_dir: str | Path,
    strategy: str,
    instrument: str | None = None,
    research_evidence_path: str | Path | None = None,
    overrides_doc: str | Path | None = None,
) -> dict[str, Any]:
    journal_path = Path(journal_dir)
    entries = read_journal_entries(journal_path)
    baseline = build_baseline(journal_path)
    rows = _strategy_rows(baseline, strategy, instrument)
    instruments = [instrument.upper()] if instrument else list(INSTRUMENTS)

    reconciler_audit = build_reconciler_audit(
        journal_dir=journal_path,
        overrides_doc=Path(overrides_doc) if overrides_doc else None,
    )
    strategy_reconciler_rows = [
        item for item in reconciler_audit["unaudited"]
        if (item.get("trade") or {}).get("strategy") == strategy
        and str(item.get("instrument") or "").upper() in instruments
    ]

    cancelled_audit = build_cancelled_audit(journal_path)
    strategy_cancelled_suspects = [
        row for inst in instruments
        for row in cancelled_audit.get(inst, {}).get("suspect_rows", [])
        if row.get("strategy") == strategy
    ]

    readiness = build_evidence_readiness(journal_path)

    performance = _performance(rows)
    distinct_days = len({
        (r.get("outcome_ts") or "")[:10] for r in rows
        if r["category"] == "filled_win_loss" and r.get("outcome_ts")
    })

    open_counts = _open_position_count(entries, strategy, instruments)
    total_approved = sum(open_counts.values())
    resolved_count = len(rows)
    legitimately_open = max(0, total_approved - resolved_count)
    filled = performance["filled_count"] + sum(1 for r in rows if r["category"] == "breakeven")
    cancellations = sum(1 for r in rows if r["category"] == "cancelled_nofill")
    rejects_or_unresolved = sum(
        1 for r in rows if r["category"] in ("reconciler_touched", "unresolved_excluded", "other")
    )
    accounting_identity_holds = total_approved == (filled + cancellations + rejects_or_unresolved + legitimately_open)

    research = _load_research_evidence(Path(research_evidence_path) if research_evidence_path else None)
    raw_candidate_count = research.get("raw_candidate_count") if research.get("provided") else None
    parity_ratio = None
    if isinstance(raw_candidate_count, (int, float)) and raw_candidate_count > 0:
        parity_ratio = performance["filled_count"] / raw_candidate_count

    classification = _classify(
        performance=performance,
        distinct_days=distinct_days,
        unclassified_reconciler_rows=len(strategy_reconciler_rows),
        mislabeled_fill_suspects=len(strategy_cancelled_suspects),
        parity_ratio=parity_ratio,
    )

    execution_context = {name: _env(name) or "UNKNOWN" for name in EXECUTION_CONTEXT_ENV_VARS}

    return {
        "routine": "promotion",
        "strategy": strategy,
        "instruments_scoped": instruments,
        "identity_parity": {
            "raw_candidate_count": raw_candidate_count if raw_candidate_count is not None else "UNKNOWN (no --research-evidence provided)",
            "research_evidence": {k: v for k, v in research.items() if k not in ("trades", "candidates")},
            "note": (
                "Direction/entry/stop/target/timeframe parity and lookahead checks require reading the "
                "specific research replay against the live decision path; not automatically derivable "
                "from the journal alone. Use /futures-live-replay-parity-audit for that comparison."
            ),
        },
        "gate_attrition": (
            research.get("gate_attrition")
            if research.get("provided") and isinstance(research.get("gate_attrition"), (dict, list))
            else "UNKNOWN (supply --research-evidence with a gate_attrition field to populate this)"
        ),
        "execution": {
            "candidates_reaching_risk_engine": total_approved,
            "candidates_approved": total_approved,
            "resolved_pairs": resolved_count,
            "filled": filled,
            "cancellations": cancellations,
            "rejects_or_unclassified": rejects_or_unresolved,
            "legitimately_open": legitimately_open,
            "accounting_identity": "approved == filled + cancellations + rejects_unresolved + legitimately_open",
            "accounting_identity_holds": accounting_identity_holds,
            "unaudited_reconciler_touched_rows": strategy_reconciler_rows,
            "mislabeled_fill_suspects": strategy_cancelled_suspects,
        },
        "performance": {
            **performance,
            "distinct_days": distinct_days,
            "parity_survival_ratio": parity_ratio,
        },
        "execution_context": execution_context,
        "runtime_readiness_tracks": readiness["tracks"],
        "classification": {
            **classification,
            "advisory_only": True,
            "policy": (
                "This is an automated first-pass suggestion from pinned thresholds "
                f"(min {STRATEGY_MIN_EXAMPLES} filled / {STRATEGY_MIN_DAYS} days / "
                f"PF>={MIN_PROFIT_FACTOR}), not a promotion decision. No config, gate, or "
                "runtime state is changed by this report. A human reviews before any promotion."
            ),
        },
        "forbidden_actions_taken": [],
    }
