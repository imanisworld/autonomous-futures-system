"""Routine 2: Strategy Promotion Proof Gate.

Prevents standalone research/backtest results from being mistaken for
executable strategy evidence, per the Miyagi and 60M 3-2-2 precedent
(standalone evidence looked positive; the real system — DecisionEngine ->
RiskEngine -> PaperBroker, journaled end to end — rejected most/all of the
population).

Reuses existing machinery rather than re-deriving it:
- ``ops.trade_chain`` for the pairing/accounting-identity pass through the
  REAL executable path (the journal *is* the record of every candidate this
  strategy actually pushed through DecisionEngine -> RiskEngine ->
  PaperBroker; there is no separate "run it again" step for strategies
  already wired into paper/live).
- ``ops.build_honest_baseline`` for per-instrument win/loss/net-P&L.
- ``ops.strategy_inventory`` for the standalone research-result row(s).
- ``config.settings.load_config`` for the actual runtime execution context
  (entry fill model, effective tolerance, contract cap, permission-gate
  status).

This module never modifies strategy code, risk rules, or config, and never
authorizes a promotion — it only classifies. It NEVER outputs ``VALIDATED``:
that bar (adequate sample, honest fills, *confirmed* replay/live parity via
`/futures-live-replay-parity-audit`, controlled drawdown, no lookahead, clear
invalidation rule — see `.claude/commands/futures-strategy-audit.md`)
requires a mechanism-level parity judgment this script cannot make from
journal data alone. It caps its automatic suggestion at
``PROMISING BUT UNPROVEN`` and says explicitly what a human still needs to
confirm.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ops import strategy_inventory
from ops import trade_chain

# Forward-measurement-gate thresholds (.claude/commands/futures-forward-measurement-gate.md):
# >=30 resolved pairs, >=10 filled. Reused here as the "adequate sample" bar
# rather than inventing a different number.
MIN_RESOLVED_SAMPLE = 30
MIN_FILLED_SAMPLE = 10

CLASSIFICATIONS = (
    "VALIDATED",
    "PROMISING BUT UNPROVEN",
    "BROKEN",
    "OVERFIT",
    "UNSAFE",
    "WAIT",
)


def _research_result(strategy: str, repo_root: Path) -> dict[str, Any]:
    inventory_path = repo_root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    rows = strategy_inventory.load_master_table(inventory_path)
    matches = strategy_inventory.match_strategy_rows(strategy, rows)
    return {
        "inventory_path": str(inventory_path),
        "inventory_found": inventory_path.exists(),
        "matched_rows": matches,
        "match_count": len(matches),
        "note": (
            "Best-effort name match against docs/strategy-rules/Strategy_Inventory.md; "
            "0 matches means the inventory has no row recognizably named for "
            "this strategy (verify manually), >1 means multiple "
            "instrument/variant rows matched."
        ),
    }


def _runtime_parity(strategy: str, repo_root: Path) -> dict[str, Any]:
    try:
        from config.settings import load_config

        config = load_config(str(repo_root / "risk_rules.yaml"))
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    status = config.strategy_status.get(strategy, config.strategy_permission_default_status)
    return {
        "ok": True,
        "strategy_permission_gate_enabled": config.strategy_permission_gate_enabled,
        "strategy_status": status,
        "paper_eligible": status == "PAPER_ELIGIBLE",
        "enabled_concept": strategy in config.enabled_concepts,
        "reachable": status == "PAPER_ELIGIBLE" and strategy in config.enabled_concepts,
        "entry_fill_model": config.entry_fill_model,
        "entry_tolerance_ticks_by_root": dict(config.entry_tolerance_ticks_by_root),
        "max_contracts_hard_cap": config.max_contracts_hard_cap,
        "max_account_risk_per_trade_percent": config.max_account_risk_per_trade_percent,
        "max_daily_loss_percent": config.max_daily_loss_percent,
        "note": (
            "'reachable' means DecisionEngine's own selection could actually "
            "route a winning candidate to this strategy today, per "
            "risk_rules.yaml's strategy_permission_gate AND enabled_concepts "
            "(both must allow it — see comment at risk_rules.yaml around "
            "'enabled_concepts' for why the gate alone is not enough)."
        ),
    }


def _performance(strategy: str, journal_dir: Path) -> dict[str, Any]:
    from ops.build_honest_baseline import build_baseline

    baseline = build_baseline(journal_dir)
    per_instrument = {}
    for instrument, report in baseline["instruments"].items():
        bucket = report["by_strategy"].get(strategy)
        if bucket is None:
            per_instrument[instrument] = {
                "wins": 0, "losses": 0, "pnl_dollars": 0.0, "filled_count": 0,
            }
            continue
        filled_count = bucket["wins"] + bucket["losses"]
        win_rate = round(100.0 * bucket["wins"] / filled_count, 1) if filled_count else None
        expectancy_per_fill = round(bucket["pnl_dollars"] / filled_count, 2) if filled_count else None
        per_instrument[instrument] = {
            **bucket,
            "filled_count": filled_count,
            "win_rate_pct": win_rate,
            "expectancy_per_fill_dollars": expectancy_per_fill,
        }
    combined_pnl = round(sum(v["pnl_dollars"] for v in per_instrument.values()), 2)
    combined_filled = sum(v["filled_count"] for v in per_instrument.values())
    return {
        "by_instrument": per_instrument,
        "combined_net_pnl_dollars": combined_pnl,
        "combined_filled_count": combined_filled,
        "source": "ops.build_honest_baseline (honest fills, operator overrides applied)",
    }


def _classify(
    *,
    accounting: dict[str, Any],
    performance: dict[str, Any],
    runtime_parity: dict[str, Any],
    research_result: dict[str, Any],
    read_errors: list[Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    resolved = accounting["resolved_fills"]
    filled = accounting["resolved_fills"] + accounting["legitimately_open"]
    net_pnl = performance["combined_net_pnl_dollars"]

    if read_errors:
        reasons.append(f"{len(read_errors)} unparseable journal line(s) — data integrity not trustworthy")
        return {"suggested": "WAIT", "reasons": reasons, "requires_manual_confirmation_for": "VALIDATED"}

    if not accounting["identity_ok"]:
        reasons.append(
            "accounting identity failed (attempts = fills + cancellations + "
            "no-fills, fills = resolved + legitimately open) — see accounting "
            "section for the mismatch"
        )
        return {"suggested": "WAIT", "reasons": reasons, "requires_manual_confirmation_for": "VALIDATED"}

    if resolved == 0:
        reasons.append("zero resolved fills — this strategy has produced no executable outcome to judge")
        return {"suggested": "WAIT", "reasons": reasons, "requires_manual_confirmation_for": "VALIDATED"}

    if not runtime_parity.get("reachable", True):
        reasons.append(
            f"runtime status is {runtime_parity.get('strategy_status')!r} / "
            f"enabled_concept={runtime_parity.get('enabled_concept')} — this "
            "strategy cannot currently be selected by DecisionEngine even "
            "when it wins ranking, so any journaled evidence predates or "
            "postdates its live-reachable window; treat sample as historical, "
            "not current"
        )

    thin_sample = resolved < MIN_RESOLVED_SAMPLE or filled < MIN_FILLED_SAMPLE
    if thin_sample:
        reasons.append(
            f"sample below the forward-measurement-gate bar "
            f"(resolved={resolved}/{MIN_RESOLVED_SAMPLE}, filled={filled}/{MIN_FILLED_SAMPLE})"
        )
        suggested = "PROMISING BUT UNPROVEN" if net_pnl > 0 else "WAIT"
        reasons.append(f"net P&L is {'positive' if net_pnl > 0 else 'non-positive'} (${net_pnl}) on a thin sample")
        return {"suggested": suggested, "reasons": reasons, "requires_manual_confirmation_for": "VALIDATED"}

    if net_pnl <= 0:
        reasons.append(f"adequate sample (resolved={resolved}, filled={filled}) with net P&L <= 0 (${net_pnl})")
        suggested = "BROKEN"
    else:
        reasons.append(f"adequate sample (resolved={resolved}, filled={filled}) with positive net P&L (${net_pnl})")
        reasons.append(
            "capped at PROMISING BUT UNPROVEN — VALIDATED additionally requires "
            "confirmed replay/live parity, controlled drawdown, no lookahead, "
            "and a clear invalidation rule (run /futures-live-replay-parity-audit); "
            "this script does not judge those"
        )
        suggested = "PROMISING BUT UNPROVEN"

    inventory_verdicts = {row["verdict_normalized"] for row in research_result["matched_rows"]}
    conflicting = {"BROKEN", "OVERFIT", "RETIRE"} & inventory_verdicts
    if conflicting and suggested not in ("BROKEN",):
        reasons.append(
            f"Strategy_Inventory.md records {sorted(conflicting)} for a matched "
            f"row, which conflicts with this run's computed evidence — "
            "reconcile before treating either as authoritative"
        )
        suggested = "WAIT"

    return {"suggested": suggested, "reasons": reasons, "requires_manual_confirmation_for": "VALIDATED"}


def build_promotion_report(
    strategy: str,
    *,
    repo_root: str | Path,
    journal_dir: str | Path | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root)
    journal_path = Path(journal_dir) if journal_dir else root / "logs"

    chain_report = trade_chain.build_report(
        journal_path, strategy=strategy, from_date=from_date, to_date=to_date
    )
    research_result = _research_result(strategy, root)
    runtime_parity = _runtime_parity(strategy, root)
    performance = _performance(strategy, journal_path)
    classification = _classify(
        accounting=chain_report["accounting"],
        performance=performance,
        runtime_parity=runtime_parity,
        research_result=research_result,
        read_errors=chain_report["read_errors"],
    )

    return {
        "strategy": strategy,
        "read_only": True,
        "identity_parity": {
            "note": (
                "Candidate identity/direction/entry/stop/target/timeframe parity "
                "and causal-data/lookahead review require reading this "
                "strategy's detector against the journaled setups by hand — not "
                "automated here. journal setups (direction/entry/stop/target/"
                "rr_ratio) are available per-pair below under execution.pairs "
                "for that manual check."
            ),
            "raw_candidate_count": chain_report["decision_counts"].get("TRADE", 0),
        },
        "gate_attrition": {
            "decision_counts": chain_report["decision_counts"],
            "risk_rejection_reasons": chain_report["risk_rejections"],
            "market_condition_at_decision": chain_report["market_condition_counts"],
            "note": (
                "Only decision-level (TRADE/NO_TRADE/WAIT/DONE_FOR_DAY) and "
                "risk-engine failed_rule counts are journaled at per-candidate "
                "granularity. Strategy-internal gate attrition (EMA alignment, "
                "confluence grade, specific R:R/session/volume filters) is not "
                "individually journaled — those thresholds live in the "
                "strategy's own detector/rules doc, not reconstructable from "
                "the journal alone."
            ),
        },
        "execution": {
            "accounting": chain_report["accounting"],
            "legitimately_open": chain_report["legitimately_open"],
            "orphaned_pending": chain_report["orphaned_pending"],
            "outcomes_without_matching_trade": chain_report["outcomes_without_matching_trade"],
            "protection": chain_report["protection"],
            "read_errors": chain_report["read_errors"],
        },
        "performance": performance,
        "execution_context": runtime_parity,
        "research_result": research_result,
        "runtime_parity": runtime_parity,
        "paper_forward_evidence": {
            "resolved_fills": chain_report["accounting"]["resolved_fills"],
            "legitimately_open": chain_report["accounting"]["legitimately_open"],
            "combined_net_pnl_dollars": performance["combined_net_pnl_dollars"],
        },
        "classification": {
            "options": list(CLASSIFICATIONS),
            "suggested": classification["suggested"],
            "reasons": classification["reasons"],
            "note": (
                "This is an automatic, evidence-only suggestion. It never "
                "outputs VALIDATED — see module docstring. No rescue/tuning "
                "variant, no automatic runtime change, no automatic merge, no "
                "deployment, no config edit follows from this report."
            ),
        },
        "window": {"from_date": from_date, "to_date": to_date},
    }
