"""Read-only strategy promotion proof-gate report.

Answers one question conservatively: does journal evidence for a named
strategy show it surviving the REAL executable path (decision -> risk ->
order -> fill), or does it only look good in isolated research?

This module never runs a strategy detector, ReplayEngine, DecisionEngine,
RiskEngine, or PaperBroker itself -- it only reads what those components
already wrote to the journal (``ops.proof_30_mnq``'s trusted TRADE<->OUTCOME
pairing and outcome classification) and to ``risk_rules.yaml`` (the
strategy-permission-gate config). It never edits config, never enables or
disables a strategy, and its classification is hard-capped: it can report
PROMISING BUT UNPROVEN, WAIT, BROKEN, or UNSAFE, but it never emits
VALIDATED -- that requires the replay/live-parity and multi-month sample
confirmation this journal-only tool cannot perform (see
``.claude/commands/futures-strategy-audit.md`` and
``.claude/commands/futures-live-replay-parity-audit.md`` for the manual
audits that can).
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from ops.proof_30_mnq import classify_outcome, pair_resolved_trades, read_journal_entries

INSTRUMENTS = ("MNQ", "MES")


def _normalize_strategy_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _matches_strategy(name: str | None, strategy: str) -> bool:
    if not name:
        return False
    name_n, strategy_n = _normalize_strategy_name(name), _normalize_strategy_name(strategy)
    if strategy_n in name_n or name_n in strategy_n:
        return True
    name_tokens, strategy_tokens = set(name_n.split()), set(strategy_n.split())
    if not name_tokens or not strategy_tokens:
        return False
    return name_tokens <= strategy_tokens or strategy_tokens <= name_tokens


def load_strategy_inventory_row(strategy: str, path: Path) -> dict[str, Any] | None:
    """Best-effort match of ``strategy`` against a Master Table row in
    ``docs/strategy-rules/Strategy_Inventory.md``. Returns the raw cells so
    callers can quote the operator's own classification without this module
    re-deriving it."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    header_seen = False
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not header_seen:
            if cells and cells[0].lower() == "strategy":
                header_seen = True
            continue
        if set(cells[0]) <= {"-", " "}:
            continue
        if cells and _matches_strategy(cells[0], strategy):
            return {"name": cells[0], "cells": cells}
    return None


def load_permission_gate(risk_rules_path: str | Path = "risk_rules.yaml") -> dict[str, Any]:
    path = Path(risk_rules_path)
    try:
        rules = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {"error": str(exc)}
    gate = rules.get("strategy_permission_gate", {}) or {}
    strategy_cfg = rules.get("strategy", {}) or {}
    return {
        "enabled": gate.get("enabled"),
        "default_status": gate.get("default_status"),
        "strategy_status": dict(gate.get("strategy_status") or {}),
        "enabled_concepts": list(strategy_cfg.get("enabled_concepts") or []),
    }


def gate_attrition(entries: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    """Failed-gate counts across every bar where this strategy appears as a
    formed setup, a shadow candidate, or a ranked candidate_audit row --
    reusing the same fields ``ops.strategy_intent_audit``/
    ``scripts/session_audit.py`` already extract, not a new gate re-check."""
    gates: Counter[str] = Counter()
    touched_bars = 0
    for e in entries:
        setup = e.get("setup") or {}
        touched = _matches_strategy(setup.get("strategy"), strategy)
        candidates = list(e.get("shadow_candidates") or []) + list(e.get("candidate_audit") or [])
        for cand in candidates:
            if isinstance(cand, dict) and _matches_strategy(cand.get("strategy"), strategy):
                touched = True
        if not touched:
            continue
        touched_bars += 1
        for g in e.get("failed_gates") or []:
            gates[g] += 1
    return {"candidate_bars": touched_bars, "failed_gate_counts": dict(gates.most_common())}


def _instrument_pairs(entries: list[dict[str, Any]], strategy: str) -> dict[str, list]:
    by_instrument: dict[str, list] = {}
    for instrument in INSTRUMENTS:
        resolved, _unmatched = pair_resolved_trades(entries, instrument=instrument, limit=10_000)
        by_instrument[instrument] = [
            r for r in resolved if _matches_strategy(r.setup.get("strategy"), strategy)
        ]
    return by_instrument


def execution_section(entries: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    decisions = [
        e for e in entries
        if e.get("decision") in ("TRADE", "NO_TRADE", "RISK_REJECTED", "CONFIG_BLOCKED")
        and _matches_strategy((e.get("setup") or {}).get("strategy"), strategy)
    ]
    approved = [e for e in decisions if e.get("decision") == "TRADE"]
    risk_rejected = [e for e in decisions if e.get("decision") == "RISK_REJECTED"]
    pairs_by_instrument = _instrument_pairs(entries, strategy)
    all_pairs = [r for rows in pairs_by_instrument.values() for r in rows]
    categories = Counter(classify_outcome(r.outcome_body) for r in all_pairs)
    fills = categories.get("filled_win_loss", 0) + categories.get("breakeven", 0)
    cancellations = categories.get("cancelled_nofill", 0)
    needs_review = categories.get("reconciler_touched", 0) + categories.get("other", 0)
    return {
        "candidates_reaching_risk_engine": len(decisions),
        "candidates_approved": len(approved),
        "risk_rejected": len(risk_rejected),
        "resolved_pairs_by_instrument": {k: len(v) for k, v in pairs_by_instrument.items()},
        "entry_attempts": len(all_pairs),
        "fills": fills,
        "cancellations_or_no_fills": cancellations,
        "needs_broker_verification": needs_review,
        "zero_executable_fills": fills == 0,
    }


def performance_section(entries: list[dict[str, Any]], strategy: str) -> dict[str, Any]:
    pairs_by_instrument = _instrument_pairs(entries, strategy)
    all_pairs = [r for rows in pairs_by_instrument.values() for r in rows]
    filled = [r for r in all_pairs if classify_outcome(r.outcome_body) in ("filled_win_loss", "breakeven")]
    if not filled:
        return {
            "sample_size": 0,
            "net_pnl": 0.0,
            "profit_factor": None,
            "expectancy_per_fill": None,
            "win_rate": None,
            "by_instrument": {k: len(v) for k, v in pairs_by_instrument.items()},
            "by_direction": {},
            "chronological_half_split": {"h1": None, "h2": None},
        }
    filled.sort(key=lambda r: r.trade_ts)
    pnls = [float(r.outcome_body.get("pnl_dollars") or 0.0) for r in filled]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    mid = len(filled) // 2
    h1, h2 = filled[:mid], filled[mid:]

    def _half_stats(rows: list) -> dict[str, Any]:
        if not rows:
            return {"n": 0, "net_pnl": 0.0}
        net = sum(float(r.outcome_body.get("pnl_dollars") or 0.0) for r in rows)
        return {"n": len(rows), "net_pnl": round(net, 2)}

    by_direction: Counter[str] = Counter()
    pnl_by_direction: dict[str, float] = {}
    for r in filled:
        direction = str(r.setup.get("direction") or "UNKNOWN")
        by_direction[direction] += 1
        pnl_by_direction[direction] = pnl_by_direction.get(direction, 0.0) + float(
            r.outcome_body.get("pnl_dollars") or 0.0
        )

    return {
        "sample_size": len(filled),
        "net_pnl": round(sum(pnls), 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy_per_fill": round(sum(pnls) / len(filled), 2),
        "win_rate": round(len(wins) / len(filled), 3),
        "by_instrument": {k: len(v) for k, v in pairs_by_instrument.items()},
        "by_direction_count": dict(by_direction),
        "by_direction_pnl": {k: round(v, 2) for k, v in pnl_by_direction.items()},
        "chronological_half_split": {"h1": _half_stats(h1), "h2": _half_stats(h2)},
    }


def execution_context_section() -> dict[str, Any]:
    from ops.session_snapshot import entry_execution_mode, entry_tolerance_by_instrument, risk_config_posture

    return {
        "entry_execution_mode": _safe(entry_execution_mode),
        "entry_tolerance_ticks_by_instrument": _safe(entry_tolerance_by_instrument),
        "risk_config_posture": _safe(risk_config_posture),
        "caveat": "reflects this process's local environment/code, not a confirmed read of "
        "the deployed box's environment",
    }


def _safe(fn):
    try:
        return fn()
    except Exception as exc:  # defensive: a read-only report must never crash
        return {"error": f"{type(exc).__name__}: {exc}"}


def classify(execution: dict[str, Any], performance: dict[str, Any], fill: dict[str, Any]) -> tuple[str, list[str]]:
    """Conservative, hard-capped classification. Never returns VALIDATED --
    that requires replay/live-parity + multi-month-sample confirmation this
    journal-only tool cannot perform (see module docstring)."""
    reasons: list[str] = []
    if fill.get("incomplete_bracket_count", 0) > 0:
        reasons.append(f"{fill['incomplete_bracket_count']} trade(s) with an incomplete stop/target bracket")
        return "UNSAFE", reasons
    if execution["zero_executable_fills"]:
        reasons.append("zero executable fills reached resolution — cannot classify performance")
        return "WAIT", reasons
    n = performance["sample_size"]
    if n < 10:
        reasons.append(f"sample size {n} is below the 10-fill minimum this gate requires before judging direction")
        return "WAIT", reasons
    net = performance["net_pnl"]
    h1 = performance["chronological_half_split"]["h1"]
    h2 = performance["chronological_half_split"]["h2"]
    if net < 0:
        reasons.append(f"negative net P&L (${net:,.2f}) at adequate sample size ({n} fills)")
        return "BROKEN", reasons
    if h1 and h2 and (h1["net_pnl"] < 0 or h2["net_pnl"] < 0):
        reasons.append("one chronological half is negative — inconsistent, not yet validated")
        return "PROMISING BUT UNPROVEN", reasons
    reasons.append(
        f"positive net P&L (${net:,.2f}) over {n} fills, but replay/live parity and "
        "multi-month sample adequacy are NOT confirmed by this gate — run "
        "/futures-strategy-audit and /futures-live-replay-parity-audit before any promotion decision"
    )
    return "PROMISING BUT UNPROVEN", reasons


def build_promotion_report(
    strategy: str,
    *,
    journal_dir: str | Path = "logs",
    risk_rules_path: str | Path = "risk_rules.yaml",
    inventory_path: str | Path = "docs/strategy-rules/Strategy_Inventory.md",
) -> dict[str, Any]:
    entries = read_journal_entries(Path(journal_dir))
    read_errors = [e for e in entries if e.get("type") == "READ_ERROR"]

    inventory_row = load_strategy_inventory_row(strategy, Path(inventory_path))
    permission_gate = load_permission_gate(risk_rules_path)

    gate_att = gate_attrition(entries, strategy)
    execution = execution_section(entries, strategy)
    performance = performance_section(entries, strategy)
    context = execution_context_section()

    pairs_by_instrument = _instrument_pairs(entries, strategy)
    all_pairs = [r for rows in pairs_by_instrument.values() for r in rows]
    incomplete = [
        r for r in all_pairs
        if r.setup.get("entry") is None or r.setup.get("stop") is None or r.setup.get("target") is None
    ]
    fill_section = {"incomplete_bracket_count": len(incomplete)}

    classification, reasons = classify(execution, performance, fill_section)

    return {
        "read_only": True,
        "strategy": strategy,
        "journal_dir": str(journal_dir),
        "journal_read_errors": len(read_errors),
        "strategy_inventory_row": inventory_row,
        "strategy_permission_gate": permission_gate,
        "identity_parity": {
            "note": "candidate identity/direction/entry/stop/target/timeframe parity and "
            "lookahead/causal-data checks require comparing this strategy's code path against "
            "replay -- REQUIRES OPERATOR REVIEW via /futures-live-replay-parity-audit, not "
            "mechanically checkable from journal rows alone",
        },
        "gate_attrition": gate_att,
        "execution": execution,
        "fill": fill_section,
        "performance": performance,
        "execution_context": context,
        "classification": classification,
        "classification_reasons": reasons,
        "classification_hard_cap": "This gate never emits VALIDATED. Promotion to VALIDATED "
        "requires a separate, manual /futures-strategy-audit + /futures-live-replay-parity-audit "
        "pass, per repo policy.",
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [f"STRATEGY PROMOTION PROOF GATE — {report['strategy']}", ""]
    row = report["strategy_inventory_row"]
    if row:
        lines.append(f"Inventory row found: {row['name']} — cells: {row['cells']}")
    else:
        lines.append("Inventory row: NOT FOUND in Strategy_Inventory.md (name match failed)")
    gate = report["strategy_permission_gate"]
    lines.append(f"strategy_permission_gate: {gate}")
    lines.append("")
    lines.append(f"GATE ATTRITION: {report['gate_attrition']}")
    lines.append(f"EXECUTION: {report['execution']}")
    lines.append(f"FILL: {report['fill']}")
    lines.append(f"PERFORMANCE: {report['performance']}")
    lines.append(f"EXECUTION CONTEXT: {report['execution_context']}")
    lines.append("")
    lines.append(f"CLASSIFICATION: {report['classification']}")
    for reason in report["classification_reasons"]:
        lines.append(f"  - {reason}")
    lines.append(report["classification_hard_cap"])
    return "\n".join(lines)
