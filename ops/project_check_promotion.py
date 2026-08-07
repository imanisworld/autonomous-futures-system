"""Strategy Promotion Proof Gate — ops.project_check's `promotion` subcommand.

Prevents standalone research/backtest results from being mistaken for
executable strategy evidence. A strategy earns promotion by proving itself
through the REAL executable path — ReplayEngine -> DecisionEngine ->
RiskEngine -> PaperBroker, wired in replay/replay_engine.py — not by a
detector/replay run judged in isolation. Precedent: the Miyagi and 60M 3-2-2
strategies, where standalone evidence looked positive but the real system
rejected most/all of the population (see docs/strategy-rules/Strategy_
Inventory.md's PROMISING BUT UNPROVEN rows for both).

This tool does NOT run replay itself — it audits journal output already
produced by `python -m replay ...` or live/paper operation. That keeps it
read-only and avoids re-implementing the strategy/risk/broker pipeline a
second time.

Reused, not duplicated:
  - ops.strategy_intent_audit.build_audit() — candidate/gate/decision rows,
    the source for identity counts and gate attrition.
  - ops.proof_30_mnq.read_journal_entries / pair_resolved_trades /
    classify_outcome — the trusted trade-pairing/outcome-classification
    logic every other evidence script in this repo already relies on.
  - ops.project_check_runtime — the strategy's configured execution context.

No rescue/tuning variant is computed in the same pass, no runtime change is
ever made, and nothing here writes to risk_rules.yaml, config, or the
journal. The suggested classification is advisory only — final promotion
classification is a human judgment call recorded in
docs/strategy-rules/Strategy_Inventory.md, not something this tool sets.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from ops import proof_30_mnq as p30
from ops import strategy_intent_audit as sia
from ops import project_check_runtime as pcr

UNKNOWN = "UNKNOWN"
# Matches ops.evidence_readiness.STRATEGY_MIN_EXAMPLES — the repo's existing
# minimum-sample bar for "enough trades to draw directional conclusions".
MIN_SAMPLE_FOR_CLASSIFICATION = 30

# strat_212/strat_122 can resolve same-bar with NO broker order row at all
# (causal same-bar resolution) — a finding from PR #371's journal-lifecycle
# audit. The attempts=fills+cancellations+rejects identity does not hold for
# these; flag rather than silently assert it.
CAUSAL_RESOLUTION_STRATEGIES = {"strat_212", "strat_122"}


def _journal_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    return Path(os.getenv("LOG_DIR", "logs"))


def _candidates_for_strategy(decisions: list[dict[str, Any]], strategy: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in decisions:
        for candidate in item.get("candidates", []):
            if candidate.get("strategy") != strategy:
                continue
            merged = dict(candidate)
            merged["_ts"] = item.get("ts")
            merged["_instrument"] = item.get("instrument")
            merged["_parent_decision"] = item.get("decision")
            out.append(merged)
    return out


def gate_attrition(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Candidate-funnel attrition for one strategy's candidates across all bars.

    Every candidate row this strategy produced, whether it won ranking or
    not, with a breakdown of what stopped the ones that didn't reach a
    TRADE decision.
    """
    selected = [c for c in candidates if c.get("selected")]
    attempted = [c for c in candidates if c.get("attempted")]
    reject_counts: Counter[str] = Counter()
    for candidate in candidates:
        codes = list(candidate.get("failed_gates") or [])
        if not codes and candidate.get("reject_code"):
            codes = [candidate["reject_code"]]
        if not codes:
            reject_counts["none_recorded"] += 1
            continue
        for code in codes:
            reject_counts[str(code)] += 1
    direction_counts = Counter(candidate.get("direction") or UNKNOWN for candidate in candidates)
    instrument_counts = Counter(candidate.get("_instrument") or UNKNOWN for candidate in candidates)
    return {
        "raw_candidate_count": len(candidates),
        "selected_as_winner_count": len(selected),
        "attempted_count": len(attempted),
        "reject_reason_counts": dict(sorted(reject_counts.items(), key=lambda kv: -kv[1])),
        "direction_counts": dict(sorted(direction_counts.items())),
        "instrument_counts": dict(sorted(instrument_counts.items())),
    }


def execution_funnel(entries: list[dict[str, Any]], strategy: str, instruments: list[str]) -> dict[str, Any]:
    """Resolved-trade funnel via the shared proof_30_mnq pairing/classification."""
    per_instrument: dict[str, Any] = {}
    all_resolved = []
    for inst in instruments:
        resolved, unmatched = p30.pair_resolved_trades(entries, instrument=inst, limit=10_000)
        strat_resolved = [r for r in resolved if r.setup.get("strategy") == strategy]
        all_resolved.extend(strat_resolved)
        per_instrument[inst] = {
            "resolved_for_strategy": len(strat_resolved),
            "resolved_total_for_instrument_all_strategies": len(resolved),
            "unmatched_outcomes_for_instrument": len(unmatched),
        }
    summaries = [r.to_summary() for r in all_resolved]
    category_counts = Counter(s["category"] for s in summaries)
    fills = category_counts.get("filled_win_loss", 0) + category_counts.get("breakeven", 0)
    return {
        "resolved_trades_total": len(summaries),
        "category_counts": dict(category_counts),
        "fills": fills,
        "cancellations_no_fill": category_counts.get("cancelled_nofill", 0),
        "reconciler_touched_needs_manual_verification": category_counts.get("reconciler_touched", 0),
        "other_unclassified": category_counts.get("other", 0),
        "per_instrument": per_instrument,
        "resolved_trade_summaries": summaries,
        "note": (
            "This journal does not distinguish a broker-level order reject from a "
            "plain no-fill IOC cancellation — both are recorded as CANCELLED and "
            "counted together in cancellations_no_fill."
        ),
    }


def accounting_identity(gate: dict[str, Any], funnel: dict[str, Any], order_attempts: int, strategy: str) -> dict[str, Any]:
    if strategy in CAUSAL_RESOLUTION_STRATEGIES:
        return {
            "identity_check": "NOT_EVALUATED",
            "detail": (
                f"{strategy} can resolve same-bar with no broker order row at all "
                "(causal same-bar resolution) — attempts=fills+cancellations+rejects "
                "does not hold for it. See PR #371's journal-lifecycle audit."
            ),
            "order_attempts": order_attempts,
        }
    resolved_total = funnel["resolved_trades_total"]
    computed_open = order_attempts - resolved_total
    if computed_open < 0:
        return {
            "identity_check": "MISMATCH",
            "detail": (
                f"resolved trades ({resolved_total}) exceed order attempts ({order_attempts}) "
                "recorded as TRADE decisions for this strategy — likely a pairing or "
                "instrument-attribution issue. Do not trust these numbers without manual review."
            ),
            "order_attempts": order_attempts,
            "resolved_total": resolved_total,
        }
    return {
        "identity_check": "PASS",
        "order_attempts": order_attempts,
        "fills": funnel["fills"],
        "cancellations_no_fill": funnel["cancellations_no_fill"],
        "resolved_total": resolved_total,
        "legitimately_open": computed_open,
        "detail": "order_attempts == resolved_total + legitimately_open, by construction; see funnel category_counts for the fills/cancellations split.",
    }


def performance_stats(resolved_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    pnl_values = [
        s["pnl_dollars"]
        for s in resolved_summaries
        if isinstance(s.get("pnl_dollars"), (int, float)) and s["category"] in ("filled_win_loss", "breakeven")
    ]
    if not pnl_values:
        return {"available": False, "detail": "no resolved filled/breakeven trades with pnl_dollars", "n": 0}
    wins = [p for p in pnl_values if p > 0]
    losses = [p for p in pnl_values if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    n = len(pnl_values)
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    max_loss_streak = 0
    for pnl in pnl_values:
        running += pnl
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
        streak = streak + 1 if pnl < 0 else 0
        max_loss_streak = max(max_loss_streak, streak)
    return {
        "available": True,
        "n": n,
        "net_pnl": sum(pnl_values),
        "win_rate": len(wins) / n,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else (
            "undefined_no_losses" if gross_profit > 0 else None
        ),
        "expectancy": sum(pnl_values) / n,
        "max_drawdown": max_dd,
        "max_loss_streak": max_loss_streak,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "note": (
            "Raw pnl_dollars only — no commission adjustment, no walk-forward split, "
            "no slippage-sensitivity sweep. For that level of rigor see the strategy's "
            "own scripts/*_canonical_evidence_report.py if one exists."
        ),
    }


def advisory_classification(
    gate: dict[str, Any], funnel: dict[str, Any], perf: dict[str, Any], accounting: dict[str, Any]
) -> dict[str, Any]:
    reasons: list[str] = []
    if funnel["fills"] == 0:
        classification = "BROKEN" if gate["raw_candidate_count"] > 0 else "WAIT"
        reasons.append("zero executable fills through the real pipeline")
    elif accounting.get("identity_check") == "MISMATCH":
        classification = "UNSAFE"
        reasons.append("accounting identity mismatch — data integrity issue, not a performance judgment")
    elif funnel["resolved_trades_total"] < MIN_SAMPLE_FOR_CLASSIFICATION:
        classification = "PROMISING BUT UNPROVEN"
        reasons.append(f"only {funnel['resolved_trades_total']} resolved trades (< {MIN_SAMPLE_FOR_CLASSIFICATION})")
    elif perf.get("available") and perf.get("net_pnl", 0) <= 0:
        classification = "BROKEN"
        reasons.append("non-positive net P&L over the resolved runtime sample")
    elif perf.get("available") and isinstance(perf.get("profit_factor"), (int, float)) and perf["profit_factor"] < 1.2:
        classification = "OVERFIT"
        reasons.append("profit factor below 1.2 on the resolved runtime sample")
    else:
        classification = "VALIDATED"
        reasons.append("adequately sampled, positive runtime evidence through the real pipeline")
    return {
        "advisory_classification": classification,
        "reasons": reasons,
        "requires_human_judgment": True,
        "note": (
            "Advisory only. This tool never edits docs/strategy-rules/Strategy_Inventory.md "
            "or risk_rules.yaml — a human records the final classification there."
        ),
    }


def build_promotion_report(
    *, journal_dir: Path, strategy: str, instrument: str | None
) -> dict[str, Any]:
    audit = sia.build_audit(journal_dir=journal_dir)
    decisions = audit["decisions"]
    candidates = _candidates_for_strategy(decisions, strategy)
    gate = gate_attrition(candidates)

    if instrument:
        instruments = [instrument.upper()]
    else:
        seen = sorted({c.get("_instrument") for c in candidates if c.get("_instrument")})
        instruments = seen or []

    entries = p30.read_journal_entries(journal_dir)
    funnel = execution_funnel(entries, strategy, instruments) if instruments else {
        "resolved_trades_total": 0,
        "category_counts": {},
        "fills": 0,
        "cancellations_no_fill": 0,
        "reconciler_touched_needs_manual_verification": 0,
        "other_unclassified": 0,
        "per_instrument": {},
        "resolved_trade_summaries": [],
        "note": "no instrument determined for this strategy — pass --instrument explicitly",
    }

    order_attempts = sum(
        1
        for item in decisions
        if item.get("decision") == "TRADE" and (item.get("selected_setup") or {}).get("strategy") == strategy
    )
    accounting = accounting_identity(gate, funnel, order_attempts, strategy)
    perf = performance_stats(funnel["resolved_trade_summaries"])
    classification = advisory_classification(gate, funnel, perf, accounting)

    config, config_error = None, None
    try:
        from config.settings import load_config

        config = load_config()
    except Exception as exc:  # noqa: BLE001
        config_error = str(exc)
    lanes = pcr.active_lanes(config)
    matching_lanes = [lane for lane in lanes if strategy.replace("_", "") in lane["lane"].replace("_", "")]

    return {
        "routine": "promotion",
        "read_only": True,
        "strategy": strategy,
        "instruments_considered": instruments,
        "journal_dir": str(journal_dir),
        "files_scanned": audit["files"],
        "research_result": {
            "status": "NOT_EVALUATED",
            "detail": (
                "This tool audits the real executable pipeline's own journal output only. "
                "It does not compare against a standalone backtest/research candidate set — "
                "cross-check manually against the strategy's scripts/*_study.py or "
                "*_canonical_evidence_report.py output for identity/direction/entry parity."
            ),
        },
        "runtime_parity": {
            "gate_attrition": gate,
            "config_load_error": config_error,
            "matching_configured_lanes": matching_lanes,
        },
        "paper_forward_evidence": {
            "execution_funnel": funnel,
            "accounting_identity": accounting,
            "performance": perf,
        },
        "classification": classification,
    }


def _print_report(report: dict[str, Any]) -> None:
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


def cmd_promotion(args: argparse.Namespace) -> int:
    journal_dir = _journal_dir(args.journal_dir)
    if not journal_dir.exists():
        print(f"FAIL CLOSED: journal dir does not exist: {journal_dir}")
        return 2
    report = build_promotion_report(journal_dir=journal_dir, strategy=args.strategy, instrument=args.instrument)
    _print_report(report)
    if report["classification"]["advisory_classification"] in ("BROKEN", "UNSAFE"):
        return 1
    return 0
