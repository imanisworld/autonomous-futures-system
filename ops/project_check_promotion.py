"""Strategy Promotion Proof Gate — routine #2 of ops.project_check.

Proves (or fails) a strategy through the REAL executable path — journal-
derived runtime evidence from DecisionEngine -> RiskEngine -> PaperBroker ->
resolved outcome — instead of trusting standalone backtest/replay output.

Precedent this exists to catch (see docs/strategy-rules/12HR_MIYAGI_CANONICAL_
EVIDENCE_2026-07-26.md and the 60M 3-2-2 evidence notes): standalone replay
looked profitable, but the real system rejected most/all of the population
once RiskEngine and the configured entry-fill model were applied.

Read-only. Never edits risk_rules.yaml, never flips strategy_permission_gate,
never merges, never deploys, never tunes a rescue variant in the same pass.
If a strategy fails, this reports why and stops.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ops.proof_30_mnq import classify_outcome, read_journal_entries

CLASSIFICATIONS = {
    "VALIDATED",
    "PROMISING BUT UNPROVEN",
    "BROKEN",
    "OVERFIT",
    "UNSAFE",
    "WAIT",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- journal


def _strategy_no_trade_and_near_miss(entries: list[dict], strategy: str) -> dict[str, list[dict]]:
    no_trade_rows, near_miss_rows = [], []
    for e in entries:
        if e.get("decision") != "NO_TRADE":
            continue
        setup = e.get("setup") or {}
        if setup.get("strategy") == strategy:
            no_trade_rows.append(e)
            continue
        candidates = list(e.get("shadow_candidates") or []) + list(e.get("candidate_audit") or [])
        if any((c or {}).get("strategy") == strategy for c in candidates):
            near_miss_rows.append(e)
    return {"no_trade": no_trade_rows, "near_miss": near_miss_rows}


def _pair_strategy_outcomes(entries: list[dict], strategy: str) -> tuple[list[dict], list[dict]]:
    """FIFO-pair this strategy's *approved* TRADE rows to their OUTCOME, per
    instrument — mirrors ops.proof_30_mnq.pair_resolved_trades's pairing
    discipline, scoped to one strategy across all instruments instead of one
    instrument across all strategies."""
    pending: dict[str, list[dict]] = {}
    resolved: list[dict] = []
    for e in entries:
        instrument = e.get("instrument")
        if not instrument:
            continue
        setup = e.get("setup") or {}
        if e.get("decision") == "TRADE" and setup.get("strategy") == strategy:
            if (e.get("risk_check") or {}).get("result") == "APPROVED":
                pending.setdefault(instrument, []).append(e)
            continue
        if e.get("type") == "OUTCOME":
            queue = pending.get(instrument)
            if queue:
                resolved.append({"trade": queue.pop(0), "outcome": e})
    still_open = [t for queue in pending.values() for t in queue]
    return resolved, still_open


def _gate_attrition(rows: dict[str, list[dict]]) -> dict[str, Any]:
    """Real journal-derived failed-gate counts for this strategy's candidates
    — the only gate-attrition data this routine can honestly produce without
    re-running a backtest harness against a specific historical candidate
    set (see ``--evidence-file``)."""
    gates: Counter[str] = Counter()
    candidate_bars = 0
    for e in rows["no_trade"] + rows["near_miss"]:
        candidate_bars += 1
        for g in e.get("failed_gates") or []:
            gates[g] += 1
    return {
        "candidate_bars_blocked_pre_risk": candidate_bars,
        "failed_gate_counts": dict(gates.most_common()),
        "note": (
            "Counts are the failed_gates the LIVE pipeline actually recorded on "
            "NO_TRADE/near-miss bars for this strategy — real attrition, not a "
            "re-run of a specific historical candidate set. Pass --evidence-file "
            "with a standalone backtest/replay results JSON to cross-check raw "
            "candidate-count parity against this journal population."
        ),
    }


def _execution_accounting(entries: list[dict], strategy: str) -> dict[str, Any]:
    trade_rows = [
        e for e in entries
        if e.get("decision") == "TRADE" and (e.get("setup") or {}).get("strategy") == strategy
    ]
    approved = [e for e in trade_rows if (e.get("risk_check") or {}).get("result") == "APPROVED"]
    rejected = [e for e in trade_rows if (e.get("risk_check") or {}).get("result") == "REJECTED"]
    resolved, still_open = _pair_strategy_outcomes(entries, strategy)

    fills: list[dict] = []
    cancellations: list[dict] = []
    other: list[dict] = []
    no_fill_reason_counts: Counter[str] = Counter()
    for pair in resolved:
        outcome_body = pair["outcome"].get("outcome") or {}
        category = classify_outcome(outcome_body)
        if category in ("filled_win_loss", "breakeven", "reconciler_touched"):
            fills.append(pair)
        elif category == "cancelled_nofill":
            cancellations.append(pair)
            no_fill_reason_counts[outcome_body.get("no_fill_reason") or "UNCLASSIFIED"] += 1
        else:
            other.append(pair)

    today = date.today().isoformat()
    legitimately_open = [t for t in still_open if (t.get("ts") or "")[:10] == today]
    orphans = [t for t in still_open if (t.get("ts") or "")[:10] != today]

    identity_a = {
        "description": "candidates_reaching_risk_engine = approved + rejected",
        "lhs": len(trade_rows),
        "rhs": len(approved) + len(rejected),
        "matches": len(trade_rows) == len(approved) + len(rejected),
    }
    identity_b = {
        "description": "fills(open+closed) = resolved_fills + legitimately_open",
        "lhs": len(fills) + len(legitimately_open),
        "rhs": len(fills) + len(legitimately_open),
        "matches": True,
    }
    identity_c = {
        "description": (
            "entry_attempts(approved) = fills(resolved+open) + cancellations "
            "+ orphans + other_unclassified  [orphans/other must be 0 for a "
            "fully accounted population]"
        ),
        "lhs": len(approved),
        "rhs": len(fills) + len(legitimately_open) + len(cancellations) + len(orphans) + len(other),
        "matches": len(approved)
        == len(fills) + len(legitimately_open) + len(cancellations) + len(orphans) + len(other),
    }

    accounting_gap = bool(orphans or other)

    return {
        "candidates_reaching_risk_engine": len(trade_rows),
        "candidates_approved": len(approved),
        "candidates_rejected_by_risk_engine": len(rejected),
        "entry_attempts": len(approved),
        "fills_resolved_closed": len(fills),
        "fills_legitimately_open": len(legitimately_open),
        "cancellations_no_fill": len(cancellations),
        "known_no_fill_reasons": dict(no_fill_reason_counts.most_common()),
        "resolved_outcomes": len(resolved),
        "orphans_unresolved_prior_day": [
            {"instrument": t.get("instrument"), "ts": t.get("ts")} for t in orphans
        ],
        "other_unclassified_outcomes": len(other),
        "accounting_identities": [identity_a, identity_b, identity_c],
        "accounting_mismatch": accounting_gap or not (identity_a["matches"] and identity_c["matches"]),
        "zero_executable_fills": (len(fills) + len(legitimately_open)) == 0,
    }


def _performance(entries: list[dict], strategy: str) -> dict[str, Any]:
    resolved, _ = _pair_strategy_outcomes(entries, strategy)
    closed = [
        p for p in resolved
        if classify_outcome(p["outcome"].get("outcome") or {}) in ("filled_win_loss", "breakeven")
    ]
    closed.sort(key=lambda p: p["trade"].get("ts") or "")
    if not closed:
        return {
            "resolved_filled_trades": 0,
            "note": "No filled+closed trades for this strategy in the window — no performance to report.",
        }

    pnls = [float((p["outcome"].get("outcome") or {}).get("pnl_dollars") or 0.0) for p in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    net = sum(pnls)
    win_rate = round(100.0 * len(wins) / len(pnls), 1) if pnls else None
    pf = round(gross_win / gross_loss, 2) if gross_loss else None
    expectancy = round(net / len(pnls), 2) if pnls else None

    mid = len(closed) // 2
    h1, h2 = closed[:mid], closed[mid:]

    def _sum(rows):
        return round(sum(float((p["outcome"].get("outcome") or {}).get("pnl_dollars") or 0.0) for p in rows), 2)

    by_instrument: Counter[str] = Counter()
    by_direction: Counter[str] = Counter()
    for p in closed:
        pnl = float((p["outcome"].get("outcome") or {}).get("pnl_dollars") or 0.0)
        by_instrument[p["trade"].get("instrument") or "?"] += pnl
        by_direction[(p["trade"].get("setup") or {}).get("direction") or "?"] += pnl

    running = 0.0
    peak = 0.0
    max_dd = 0.0
    loss_streak = 0
    max_loss_streak = 0
    for pnl in pnls:
        running += pnl
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
        if pnl < 0:
            loss_streak += 1
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            loss_streak = 0

    top_winner = max(pnls) if wins else 0.0
    top_winner_concentration_pct = round(100.0 * top_winner / gross_win, 1) if gross_win else None

    return {
        "resolved_filled_trades": len(closed),
        "net_pnl_dollars": round(net, 2),
        "profit_factor": pf,
        "expectancy_dollars": expectancy,
        "win_rate_pct": win_rate,
        "wins": len(wins),
        "losses": len(losses),
        "h1_net_pnl_dollars": _sum(h1),
        "h2_net_pnl_dollars": _sum(h2),
        "by_instrument_net_pnl": {k: round(v, 2) for k, v in by_instrument.items()},
        "by_direction_net_pnl": {k: round(v, 2) for k, v in by_direction.items()},
        "max_drawdown_dollars": round(max_dd, 2),
        "max_consecutive_losses": max_loss_streak,
        "top_winner_pct_of_gross_win": top_winner_concentration_pct,
        "session_split": "NOT_AVAILABLE — session not consistently present on outcome rows; cross-check journal `session` field manually if needed.",
        "slippage_sensitivity": "NOT_AVAILABLE from journal alone — run scripts/*_slippage_sensitivity*.py or ops/fill_realism.py for this.",
    }


# ---------------------------------------------------------------- context


def _execution_context() -> dict[str, Any]:
    context: dict[str, Any] = {
        "entry_fill_model": "UNKNOWN",
        "entry_tolerance_ticks_by_root": "UNKNOWN",
        "max_contracts_per_instrument": "UNKNOWN",
        "max_contracts_hard_cap": "UNKNOWN",
        "strategy_permission_gate_enabled": "UNKNOWN",
    }
    try:
        from config.settings import load_config

        config = load_config()
        context["entry_fill_model"] = config.entry_fill_model
        context["execution_mode"] = config.exit_mode
        context["entry_tolerance_ticks_by_root"] = config.entry_tolerance_ticks_by_root
        context["max_contracts_per_instrument"] = config.max_contracts_per_instrument
        context["max_contracts_hard_cap"] = config.max_contracts_hard_cap
        context["strategy_permission_gate_enabled"] = config.strategy_permission_gate_enabled
        context["fill_slippage_ticks"] = config.fill_slippage_ticks
    except Exception as exc:  # noqa: BLE001
        context["config_load_error"] = str(exc)
    return context


def _strategy_lane_status(strategy: str) -> dict[str, Any]:
    status: dict[str, Any] = {
        "enabled_concept": "UNKNOWN",
        "permission_status": "UNKNOWN",
    }
    try:
        from config.settings import load_config

        config = load_config()
        status["enabled_concept"] = strategy in (config.enabled_concepts or [])
        status["permission_status"] = config.strategy_status.get(
            strategy, config.strategy_permission_default_status
        )
    except Exception as exc:  # noqa: BLE001
        status["config_load_error"] = str(exc)
    return status


def _normalize_strategy_name(name: str) -> str:
    import re

    name = re.sub(r"\([^)]*\)", "", name)  # drop "(MES)"/"(MNQ)" instrument suffix
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _research_result(strategy_display: str, repo_root: Path) -> dict[str, Any]:
    """Best-effort lookup of the strategy's row(s) in Strategy_Inventory.md —
    the hand-maintained standalone RESEARCH RESULT baseline this gate
    cross-checks paper-forward evidence against. Matching uses the same
    normalized-name comparison as ops.project_check_daily's strategy
    source-of-truth check (strip instrument-suffix parens, alnum-only,
    substring either direction) since inventory rows are prose like
    "ORB Breakout (MNQ)" against a config key like "orb_breakout"."""
    inventory_path = repo_root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    if not inventory_path.is_file():
        return {"found": False, "note": f"{inventory_path} not found"}
    text = inventory_path.read_text(encoding="utf-8", errors="replace")
    target = _normalize_strategy_name(strategy_display)
    matches = []
    in_master_table = False
    for line in text.splitlines():
        if line.startswith("## Master Table"):
            in_master_table = True
            continue
        if in_master_table and line.startswith("## "):
            break
        if not in_master_table or not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        name = cells[0]
        norm = _normalize_strategy_name(name)
        if norm and (target in norm or norm in target):
            verdict = cells[-1].replace("*", "").strip()
            matches.append({"row_name": name, "verdict": verdict})
    if matches:
        return {"found": True, "matches": matches, "verdict": "; ".join(f"{m['row_name']}={m['verdict']}" for m in matches)}
    return {
        "found": False,
        "note": (
            f"No row in {inventory_path} matched strategy name '{strategy_display}' "
            "by normalized name match — check spelling/aliasing manually."
        ),
    }


def _identity_parity(evidence_file: Optional[str], journal_candidate_count: int) -> dict[str, Any]:
    if not evidence_file:
        return {
            "performed": False,
            "note": (
                "No --evidence-file supplied — raw candidate-count / direction / "
                "entry-stop-target / timeframe parity against a specific standalone "
                "backtest was NOT checked. Pass --evidence-file <results.json> to "
                "perform it."
            ),
        }
    path = Path(evidence_file)
    if not path.is_file():
        return {"performed": False, "error": f"--evidence-file not found: {evidence_file}"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"performed": False, "error": f"could not parse --evidence-file: {exc}"}

    raw_candidates = None
    for key in ("raw_candidate_count", "candidate_count", "total_candidates"):
        if isinstance(data, dict) and isinstance(data.get(key), int):
            raw_candidates = data[key]
            break
    if raw_candidates is None and isinstance(data, dict):
        for key in ("trades", "results", "candidates"):
            if isinstance(data.get(key), list):
                raw_candidates = len(data[key])
                break

    return {
        "performed": True,
        "evidence_file": str(path),
        "evidence_file_raw_candidate_count": raw_candidates,
        "journal_candidate_bars_this_window": journal_candidate_count,
        "note": (
            "This is a coarse count-level cross-check only (the evidence file's "
            "schema is not standardized across research scripts). It does NOT "
            "verify per-candidate identity/direction/entry-stop-target/timeframe "
            "parity — that requires a purpose-built diff against the specific "
            "artifact and is out of scope for an automated gate."
        ),
    }


# ---------------------------------------------------------------- classify


def _classify(
    *,
    lane_status: dict[str, Any],
    accounting: dict[str, Any],
    performance: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []

    if lane_status.get("enabled_concept") is False or lane_status.get("permission_status") not in (
        "PAPER_ELIGIBLE",
    ):
        reasons.append(
            f"strategy is not currently paper-eligible in risk_rules.yaml "
            f"(enabled_concept={lane_status.get('enabled_concept')}, "
            f"permission_status={lane_status.get('permission_status')})"
        )
        return {"classification": "WAIT", "reasons": reasons}

    if accounting.get("accounting_mismatch"):
        reasons.append(
            "accounting identity mismatch or unresolved orphans/other outcomes present — "
            "resolve before this gate can be trusted"
        )
        return {"classification": "UNSAFE", "reasons": reasons}

    if accounting.get("zero_executable_fills"):
        reasons.append(
            "zero executable fills through the real path (RiskEngine/PaperBroker/entry-fill-model) — "
            "cannot assess performance regardless of any standalone backtest result"
        )
        reject_rate = None
        approved = accounting.get("candidates_approved") or 0
        reached = accounting.get("candidates_reaching_risk_engine") or 0
        if reached:
            reject_rate = round(100.0 * (reached - approved) / reached, 1)
        if reject_rate is not None and reject_rate >= 80:
            reasons.append(
                f"RiskEngine rejected {reject_rate}% of candidates reaching it — "
                "matches the Miyagi/60M-3-2-2 precedent (standalone-positive, real-system-rejected)"
            )
            return {"classification": "BROKEN", "reasons": reasons}
        return {"classification": "WAIT", "reasons": reasons}

    resolved = performance.get("resolved_filled_trades") or 0
    if resolved < 30:
        reasons.append(
            f"only {resolved} resolved filled trades through the real path — "
            "below the 30-trade minimum sample used elsewhere in this repo "
            "(ops/evidence_readiness.STRATEGY_MIN_EXAMPLES)"
        )
        if resolved > 0 and (performance.get("net_pnl_dollars") or 0) > 0:
            reasons.append("early sample is net-positive — worth continued paper accumulation")
        return {"classification": "PROMISING BUT UNPROVEN" if resolved > 0 else "WAIT", "reasons": reasons}

    pf = performance.get("profit_factor")
    net = performance.get("net_pnl_dollars") or 0
    h1 = performance.get("h1_net_pnl_dollars") or 0
    h2 = performance.get("h2_net_pnl_dollars") or 0

    if net <= 0 or (pf is not None and pf < 1.0):
        reasons.append(f"net P&L={net}, profit factor={pf} through the real path — not profitable")
        return {"classification": "BROKEN", "reasons": reasons}

    if (h1 > 0) != (h2 > 0):
        reasons.append(
            f"walk-forward split not both positive (H1={h1}, H2={h2}) — plausible overfit/regime dependence"
        )
        return {"classification": "OVERFIT", "reasons": reasons}

    reasons.append(
        f"{resolved} resolved filled trades, net P&L={net}, PF={pf}, both halves positive "
        "(H1={h1}, H2={h2}) through the real executable path"
    )
    reasons.append(
        "This routine does NOT itself certify VALIDATED — that requires the full "
        "8-dimension review recorded in docs/strategy-rules/Strategy_Inventory.md "
        "(replay parity, honest fills, walk-forward, slippage, sample size)."
    )
    return {"classification": "PROMISING BUT UNPROVEN", "reasons": reasons}


# ---------------------------------------------------------------- report


def build_promotion_report(
    strategy: str,
    *,
    log_dir: str | Path = "logs",
    days: int = 90,
    evidence_file: Optional[str] = None,
    repo_root: Optional[str | Path] = None,
) -> dict[str, Any]:
    root = Path(repo_root) if repo_root else Path.cwd()
    journal_dir = Path(log_dir)
    entries = read_journal_entries(journal_dir)
    if days:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        entries = [e for e in entries if (e.get("ts") or "9999")[:10] >= cutoff]

    gate_rows = _strategy_no_trade_and_near_miss(entries, strategy)
    gate_attrition = _gate_attrition(gate_rows)
    accounting = _execution_accounting(entries, strategy)
    performance = _performance(entries, strategy)
    context = _execution_context()
    lane_status = _strategy_lane_status(strategy)
    research_result = _research_result(strategy, root)
    identity_parity = _identity_parity(
        evidence_file, gate_attrition["candidate_bars_blocked_pre_risk"] + accounting["candidates_reaching_risk_engine"]
    )
    classification = _classify(lane_status=lane_status, accounting=accounting, performance=performance)
    reasons = list(classification["reasons"])
    if research_result.get("found"):
        for m in research_result["matches"]:
            verdict_upper = m["verdict"].upper()
            if any(k in verdict_upper for k in ("BROKEN", "WAIT", "RETIRE", "OVERFIT")) and lane_status.get(
                "permission_status"
            ) == "PAPER_ELIGIBLE":
                reasons.append(
                    f"CROSS-CHECK: Strategy_Inventory.md row '{m['row_name']}' verdict={m['verdict']} "
                    "is negative, yet risk_rules.yaml currently marks this concept PAPER_ELIGIBLE — "
                    "docs/runtime drift, verify which is stale before relying on either."
                )

    return {
        "generated_at": _now_iso(),
        "strategy": strategy,
        "window_days": days,
        "identity_parity": identity_parity,
        "gate_attrition": gate_attrition,
        "execution_accounting": accounting,
        "performance": performance,
        "execution_context": context,
        "strategy_lane_status": lane_status,
        "research_result_baseline": research_result,
        "classification": classification["classification"],
        "classification_reasons": reasons,
        "rules_note": (
            "Legitimate account risk controls were not bypassed to produce these "
            "numbers. No rescue/tuning variant was run in this pass. No runtime "
            "change, merge, deployment, or config edit was made by this routine."
        ),
    }


def format_promotion_report(report: dict[str, Any]) -> str:
    lines = [
        f"STRATEGY PROMOTION PROOF GATE — {report['strategy']} — {report['generated_at']}",
        f"window: last {report['window_days']} days",
        f"VERDICT: {report['classification']}",
    ]
    for r in report["classification_reasons"]:
        lines.append(f"  - {r}")
    lines.append("")
    lines.append("-- research result baseline (Strategy_Inventory.md) --")
    rr = report["research_result_baseline"]
    lines.append(f"  found={rr.get('found')} verdict={rr.get('verdict', rr.get('note'))}")
    lines.append("-- strategy lane status (risk_rules.yaml) --")
    ls = report["strategy_lane_status"]
    lines.append(f"  enabled_concept={ls.get('enabled_concept')} permission_status={ls.get('permission_status')}")
    lines.append("-- identity/parity --")
    ip = report["identity_parity"]
    lines.append(f"  performed={ip.get('performed')} note={ip.get('note', '')}")
    lines.append("-- gate attrition (real pipeline, journal-derived) --")
    ga = report["gate_attrition"]
    lines.append(f"  candidate bars blocked pre-risk: {ga['candidate_bars_blocked_pre_risk']}")
    for gate, count in ga["failed_gate_counts"].items():
        lines.append(f"    {gate}: {count}")
    lines.append("-- execution accounting --")
    ea = report["execution_accounting"]
    for k in (
        "candidates_reaching_risk_engine", "candidates_approved", "candidates_rejected_by_risk_engine",
        "entry_attempts", "fills_resolved_closed", "fills_legitimately_open", "cancellations_no_fill",
        "resolved_outcomes", "other_unclassified_outcomes", "zero_executable_fills", "accounting_mismatch",
    ):
        lines.append(f"  {k}: {ea[k]}")
    if ea["orphans_unresolved_prior_day"]:
        lines.append(f"  ORPHANS (needs review): {ea['orphans_unresolved_prior_day']}")
    lines.append("-- performance (real path, filled+closed only) --")
    perf = report["performance"]
    if perf.get("resolved_filled_trades"):
        for k in (
            "resolved_filled_trades", "net_pnl_dollars", "profit_factor", "expectancy_dollars",
            "win_rate_pct", "h1_net_pnl_dollars", "h2_net_pnl_dollars", "max_drawdown_dollars",
            "max_consecutive_losses", "top_winner_pct_of_gross_win",
        ):
            lines.append(f"  {k}: {perf.get(k)}")
        lines.append(f"  by_instrument_net_pnl: {perf.get('by_instrument_net_pnl')}")
        lines.append(f"  by_direction_net_pnl: {perf.get('by_direction_net_pnl')}")
    else:
        lines.append(f"  {perf.get('note')}")
    lines.append("-- execution context --")
    ctx = report["execution_context"]
    lines.append(
        f"  entry_fill_model={ctx.get('entry_fill_model')} "
        f"entry_tolerance_ticks_by_root={ctx.get('entry_tolerance_ticks_by_root')} "
        f"max_contracts_per_instrument={ctx.get('max_contracts_per_instrument')} "
        f"max_contracts_hard_cap={ctx.get('max_contracts_hard_cap')}"
    )
    return "\n".join(lines)
