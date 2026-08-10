"""Strategy Promotion Proof Gate.

Reports whether a strategy's journal evidence went through the REAL executable
path (candidate -> DecisionEngine -> RiskEngine -> PaperBroker -> resolved
outcome), not just a standalone backtest/detector run. Read-only: reads
journal_*.jsonl, risk_rules.yaml, and env; never edits config, never enables a
strategy, never merges/deploys anything, never produces a "rescue" re-run.

Precedent this gate exists to catch (see docs/strategy-rules/Strategy_Inventory.md
and the Miyagi / 60M 3-2-2 evidence notes): a strategy can look profitable in a
standalone replay while the real system rejects most or all of its candidate
population. Classification therefore never exceeds PROMISING BUT UNPROVEN on
its own -- VALIDATED requires a human to review identity/parity evidence this
tool cannot independently certify (see identity_parity.status below).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ops.live_box_guard import PROOF_CRITICAL_RUNTIME_OVERRIDES, live_box_drift_report
from ops.trade_chain import (
    build_accounting_identity,
    gate_attrition,
    pair_trades,
    read_entries,
)

MIN_SAMPLE_FOR_REVIEW = 30
MIN_SAMPLE_THIN = 10
MIN_PROFIT_FACTOR = 1.20

EXECUTION_CONTEXT_OVERRIDES = tuple(
    name for name in PROOF_CRITICAL_RUNTIME_OVERRIDES
    if any(term in name for term in (
        "ENTRY_FILL_MODEL", "FILL_", "SLIPPAGE", "MARKETABLE_LIMIT", "STOP_LIMIT",
        "ENTRY_EXECUTION_MODE", "STOP_ENTRY_CONFIRM",
    ))
)

REPLAY_PARITY_MODULES = (
    "strategy/strat_classifier.py",
    "strategy/signal_engine.py",
    "webhook/state_builder.py",
    "replay/replay_engine.py",
    "execution/paper_broker.py",
    "execution/tradovate_broker.py",
)


def _load_risk_rules(repo_root: Path, risk_rules_path: str | Path) -> dict[str, Any]:
    path = Path(risk_rules_path)
    if not path.is_absolute():
        path = repo_root / path
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _matches_strategy(entry: dict[str, Any], strategy: str) -> bool:
    return (entry.get("setup") or {}).get("strategy") == strategy


def _entries_for_strategy(entries: list[dict[str, Any]], strategy: str) -> list[dict[str, Any]]:
    """Every row with a formed setup for this strategy, TRADE or not -- the
    population a gate-attrition report needs."""
    return [entry for entry in entries if _matches_strategy(entry, strategy)]


def _shadow_or_ranked_mentions(entries: list[dict[str, Any]], strategy: str) -> int:
    count = 0
    for entry in entries:
        for candidate in (entry.get("shadow_candidates") or []) + (entry.get("candidate_audit") or []):
            if isinstance(candidate, dict) and candidate.get("strategy") == strategy:
                count += 1
    return count


def _split_half(pnls: list[float]) -> dict[str, Any]:
    if not pnls:
        return {"h1_net": 0.0, "h2_net": 0.0, "h1_count": 0, "h2_count": 0}
    mid = len(pnls) // 2 or 1
    h1, h2 = pnls[:mid], pnls[mid:]
    return {
        "h1_net": round(sum(h1), 2), "h1_count": len(h1),
        "h2_net": round(sum(h2), 2), "h2_count": len(h2),
    }


def _performance(resolved_trades) -> dict[str, Any]:
    filled = [
        trade for trade in resolved_trades
        if str((trade.outcome_body or {}).get("result") or "").upper() in ("WIN", "LOSS", "BREAKEVEN")
    ]
    pnls = [float((trade.outcome_body.get("pnl_dollars") or 0.0)) for trade in filled]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    net = round(sum(pnls), 2)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    win_rate = (len(wins) / len(pnls)) if pnls else 0.0
    expectancy = (net / len(pnls)) if pnls else 0.0

    by_instrument: dict[str, float] = {}
    by_direction: dict[str, float] = {}
    for trade in filled:
        inst = trade.trade.get("instrument") or "UNKNOWN"
        direction = trade.setup.get("direction") or "UNKNOWN"
        pnl = float((trade.outcome_body.get("pnl_dollars") or 0.0))
        by_instrument[inst] = round(by_instrument.get(inst, 0.0) + pnl, 2)
        by_direction[direction] = round(by_direction.get(direction, 0.0) + pnl, 2)

    # Max drawdown / loss streak off the resolved-trade equity curve (chronological
    # journal order, which is the order they resolved in).
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    loss_streak = 0
    max_loss_streak = 0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if pnl < 0:
            loss_streak += 1
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            loss_streak = 0

    top_winner = max(wins) if wins else 0.0
    top_winner_concentration = (top_winner / gross_win) if gross_win > 0 else 0.0

    return {
        "filled_count": len(filled),
        "net_pnl_dollars": net,
        "profit_factor": None if profit_factor == float("inf") else round(profit_factor, 3),
        "win_rate": round(win_rate, 3),
        "expectancy_dollars": round(expectancy, 2),
        "half_period_split": _split_half(pnls),
        "instrument_split_pnl": by_instrument,
        "direction_split_pnl": by_direction,
        "max_drawdown_dollars": round(max_dd, 2),
        "max_consecutive_losses": max_loss_streak,
        "top_winner_dollars": round(top_winner, 2),
        "top_winner_concentration": round(top_winner_concentration, 3),
    }


def _execution_context(repo_root: Path, risk_rules_path: str | Path, log_dir: str | Path,
                        rules: dict[str, Any], strategy: str) -> dict[str, Any]:
    drift = live_box_drift_report(repo_root=repo_root, risk_rules_path=risk_rules_path, log_dir=log_dir)
    overrides = {
        item["name"]: item["observed"]
        for item in drift["proof_critical_runtime_overrides"]
        if item["name"] in EXECUTION_CONTEXT_OVERRIDES
    }
    gate = rules.get("strategy_permission_gate") or {}
    status = (gate.get("strategy_status") or {}).get(strategy, gate.get("default_status", "UNKNOWN"))
    instruments = (rules.get("instruments") or {}).get("allowed") or []
    caps = (rules.get("position_rules") or {}).get("max_contracts_per_instrument") or {}
    return {
        "strategy_permission_gate_status": status,
        "entry_fill_model_config": rules.get("fill_model") or {},
        "observed_runtime_overrides": overrides,
        "contract_caps_by_allowed_instrument": {inst: caps.get(inst, "UNKNOWN") for inst in instruments},
    }


def _classify(
    *,
    strategy: str,
    permission_status: str,
    candidate_count: int,
    identity,
    performance: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    if candidate_count == 0:
        return {
            "verdict": "WAIT",
            "reasons": ["no candidates observed for this strategy in the scanned journal window"],
        }
    if identity.attempts == 0:
        return {
            "verdict": "WAIT",
            "reasons": [
                "0 RiskEngine-approved order attempts despite candidates existing -- "
                "check strategy_permission_gate / enabled_concepts / disabled_concepts_per_instrument"
            ],
        }
    if not identity.ok:
        return {
            "verdict": "UNSAFE",
            "reasons": [
                "accounting identity failed "
                f"(attempts={identity.attempts}, resolved+open={identity.resolved + identity.legitimately_open}) "
                "-- do not trust these numbers until the discrepancy is explained"
            ],
        }
    if identity.fills == 0:
        return {
            "verdict": "BROKEN",
            "reasons": [
                f"zero executable fills across {identity.attempts} order attempt(s) "
                f"({identity.cancellations} cancelled/no-fill, "
                f"{identity.needs_manual_classification} need manual reconciliation)"
            ],
        }
    if permission_status != "PAPER_ELIGIBLE":
        reasons.append(
            f"strategy_permission_gate status is {permission_status!r}, not PAPER_ELIGIBLE -- "
            "any fills counted here happened outside (or before) the current fail-closed posture"
        )
    pf = performance["profit_factor"]
    if pf is not None and pf < 1.0:
        reasons.append(f"negative expectancy: profit_factor={pf}, net_pnl=${performance['net_pnl_dollars']}")
        return {"verdict": "BROKEN", "reasons": reasons}
    n = performance["filled_count"]
    if n < MIN_SAMPLE_THIN:
        reasons.append(f"sample too thin to draw a conclusion (n={n} filled)")
        return {"verdict": "WAIT", "reasons": reasons}
    if n < MIN_SAMPLE_FOR_REVIEW:
        reasons.append(f"sample below the {MIN_SAMPLE_FOR_REVIEW}-trade review threshold (n={n} filled)")
    if identity.needs_manual_classification > 0:
        reasons.append(
            f"{identity.needs_manual_classification} resolved outcome(s) are reconciler-touched/other and "
            "need manual classification via docs/proof-operator-overrides.md before they can be trusted"
        )
    if pf is None or pf >= MIN_PROFIT_FACTOR:
        reasons.append(
            f"profit_factor={pf} (n={n}) clears the {MIN_PROFIT_FACTOR} bar through the real executable path"
        )
        reasons.append(
            "capped at PROMISING BUT UNPROVEN -- VALIDATED requires human review of "
            "identity/parity evidence (see identity_parity section) this tool cannot certify"
        )
        return {"verdict": "PROMISING BUT UNPROVEN", "reasons": reasons}
    reasons.append(f"profit_factor={pf} below the {MIN_PROFIT_FACTOR} bar")
    return {"verdict": "WAIT", "reasons": reasons}


def build_promotion_report(
    strategy: str,
    *,
    repo_root: str | Path | None = None,
    log_dir: str | Path = "logs",
    risk_rules_path: str | Path = "risk_rules.yaml",
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve() if repo_root else Path(__file__).resolve().parents[1]
    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = root / log_path

    entries = read_entries(log_path, from_date=from_date, to_date=to_date)
    read_errors = [entry for entry in entries if entry.get("type") == "READ_ERROR"]

    strategy_entries = _entries_for_strategy(entries, strategy)
    attrition = gate_attrition(strategy_entries)
    shadow_mentions = _shadow_or_ranked_mentions(entries, strategy)

    resolved, still_open, unmatched_outcomes = pair_trades(
        entries, lambda entry: _matches_strategy(entry, strategy)
    )
    identity = build_accounting_identity(resolved, still_open)
    performance = _performance(resolved)

    rules = _load_risk_rules(root, risk_rules_path)
    execution_context = _execution_context(root, risk_rules_path, log_dir, rules, strategy)

    classification = _classify(
        strategy=strategy,
        permission_status=execution_context["strategy_permission_gate_status"],
        candidate_count=attrition["candidate_count"],
        identity=identity,
        performance=performance,
    )

    identity_parity = {
        "status": "UNKNOWN -- not independently verifiable by this offline gate",
        "note": (
            "This gate reports journal evidence from the real executable path (candidates "
            "already passed through DecisionEngine/RiskEngine/PaperBroker to be journaled at "
            "all). It cannot itself diff replay-vs-live formulas. Run a replay/live parity check "
            "(e.g. /futures-live-replay-parity-audit) against these modules before treating this "
            "report's PROMISING BUT UNPROVEN verdict as anything more:"
        ),
        "modules_to_diff": list(REPLAY_PARITY_MODULES),
        "lookahead_or_partial_bar_dependency": "UNKNOWN -- requires the replay/live parity check above",
    }

    return {
        "routine": "strategy-promotion-proof-gate",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy,
        "journal_dir": str(log_path),
        "window": {"from_date": from_date, "to_date": to_date},
        "journal_read_errors": read_errors,
        "identity_parity": identity_parity,
        "gate_attrition": attrition,
        "shadow_or_ranked_candidate_mentions": shadow_mentions,
        "execution": {
            "accounting_identity": identity.as_dict(),
            "unmatched_outcomes_for_strategy": len(unmatched_outcomes),
        },
        "performance": performance,
        "execution_context": execution_context,
        "classification": classification,
        "no_promotion_side_effects": (
            "This report never edits risk_rules.yaml, never enables a strategy, never merges, "
            "never deploys. It is a read of existing journal evidence only."
        ),
    }
