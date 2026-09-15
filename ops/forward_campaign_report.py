"""Read-only all-arm and matched-pair report for forward_ab_2026_08_v1."""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from execution.forward_evidence_campaign import CAMPAIGN_ID, EVIDENCE_FILENAME
from execution.evidence_identity import (
    configured_populations, population_key, record_key, LEGACY_EPOCH, PopulationKey,
)
from config.futures_contracts import contract_economics

COST_TIERS = (1, 2, 3)
PAIR_VARIANTS = ("control", "modified")


def _dedupe_rows(
    rows: list[dict[str, Any]],
) -> tuple[dict[tuple[PopulationKey, str], dict[str, Any]], int, list[str]]:
    """First-wins dedupe that distinguishes identical retries from conflicts."""
    unique: dict[tuple[PopulationKey, str], dict[str, Any]] = {}
    identical_duplicates = 0
    conflicting_ids: set[str] = set()
    for row in rows:
        candidate_id = record_key(row)
        prior = unique.get(candidate_id)
        if prior is None:
            unique[candidate_id] = row
            continue
        if row == prior:
            identical_duplicates += 1
        else:
            conflicting_ids.add(str(row["candidate_id"]))
    return unique, identical_duplicates, sorted(conflicting_ids)


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(row, dict) and row.get("campaign_id") == CAMPAIGN_ID:
            result.append(row)
    return result


def _max_drawdown(values: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return round(drawdown, 2)


def _profit_factor(values: list[float]) -> float | str | None:
    profits = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    if losses:
        return round(profits / losses, 4)
    return "INF" if profits else None


def _cost_metrics(rows: Iterable[dict[str, Any]], ticks: int, population: PopulationKey | None = None) -> dict[str, Any]:
    ordered = sorted(
        (row for row in rows if row.get("gross_pnl_dollars") is not None),
        key=lambda row: str(row.get("exit_timestamp") or ""),
    )
    gross = [float(row["gross_pnl_dollars"]) for row in ordered]
    commissions = [row.get("commission_assumption_dollars") for row in ordered]
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in commissions):
        return {"available": False, "reason": "MISSING_COMMISSION_PROOF"}
    costs = [commission + ticks * contract_economics(population_key(row)[1])[1]
             for row, commission in zip(ordered, commissions)]
    net = [round(value - cost, 2) for value, cost in zip(gross, costs)]
    commission = commissions[0] if commissions and len(set(commissions)) == 1 else None
    cost = costs[0] if costs and len(set(costs)) == 1 else None
    if not ordered and population is not None and population[1] == "MNQ" and population[3] == LEGACY_EPOCH:
        # The frozen legacy campaign proves these costs even before any fill.
        commission, cost = 1.48, 1.48 + ticks * 0.50
    signs = Counter("W" if value > 0 else "L" if value < 0 else "BE" for value in net)
    return {
        "round_trip_slippage_ticks": ticks,
        "commission_dollars": commission,
        "per_trade_cost_dollars": round(cost, 2) if cost is not None else None,
        "trades_with_gross_pnl": len(gross),
        "gross_pnl_dollars": round(sum(gross), 2),
        "net_pnl_dollars": round(sum(net), 2),
        "profit_factor": _profit_factor(net),
        "max_drawdown_dollars": _max_drawdown(net),
        "average_pnl_dollars": round(sum(net) / len(net), 4) if net else None,
        "economic_after_cost": {
            "wins": signs["W"], "losses": signs["L"], "breakeven": signs["BE"],
        },
    }


def _cost_sensitivity(rows: Iterable[dict[str, Any]], population: PopulationKey | None = None) -> dict[str, Any]:
    retained = list(rows)
    return {f"{ticks}_rt_tick": _cost_metrics(retained, ticks, population) for ticks in COST_TIERS}


def _terminal(candidate: dict[str, Any], outcomes: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    outcome = outcomes.get(record_key(candidate))
    if outcome is not None:
        return outcome
    if str(candidate.get("terminal_state") or "OPEN") != "OPEN":
        return candidate
    return None


def _is_filled(row: dict[str, Any]) -> bool:
    return row.get("fillable_state") == "FILLED"


def _average(rows: Iterable[dict[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if row.get(field) is not None]
    return round(sum(values) / len(values), 4) if values else None


def _is_resolved_filled_economic_outcome(row: dict[str, Any]) -> bool:
    """True only for terminal filled rows with independently retained P&L."""
    return (
        row.get("fillable_state") == "FILLED"
        and str(row.get("terminal_state") or "OPEN") != "OPEN"
        and isinstance(row.get("gross_pnl_dollars"), (int, float))
        and math.isfinite(row["gross_pnl_dollars"])
        and all(isinstance(row.get(field), (int, float))
                and math.isfinite(row[field]) and row[field] >= 0
                for field in ("commission_assumption_dollars", "slippage_assumption_ticks"))
    )


def _all_arm_population(
    strategy: str,
    variant: str,
    cells: list[dict[str, Any]],
    outcomes: dict[str, dict[str, Any]],
    population: PopulationKey | None = None,
) -> dict[str, Any]:
    cell_outcomes = [outcomes[record_key(row)] for row in cells if record_key(row) in outcomes]
    terminal = Counter(str(row.get("terminal_state") or "UNKNOWN") for row in cell_outcomes)
    rejected = sum(row.get("terminal_state") == "REJECTED" for row in cells)
    no_fill = sum(row.get("fillable_state") == "NO_FILL" for row in cell_outcomes)
    open_count = sum(
        row.get("terminal_state") == "OPEN" and record_key(row) not in outcomes
        for row in cells
    )
    wins, losses, breakevens = terminal["WIN"], terminal["LOSS"], terminal["BREAKEVEN"]
    decided = wins + losses + breakevens
    pnl_rows = sorted(
        (row for row in cell_outcomes if row.get("net_pnl_dollars") is not None),
        key=lambda row: str(row.get("exit_timestamp") or ""),
    )
    net_values = [float(row["net_pnl_dollars"]) for row in pnl_rows]
    gross_values = [
        float(row["gross_pnl_dollars"])
        for row in cell_outcomes if row.get("gross_pnl_dollars") is not None
    ]
    resolved_filled_outcomes = [
        row for row in cell_outcomes if _is_resolved_filled_economic_outcome(row)
    ]
    days = {str(row.get("signal_timestamp", ""))[:10] for row in cells if row.get("signal_timestamp")}
    review_eligible = len(days) >= 20 and len(resolved_filled_outcomes) >= 30
    return {
        "strategy": strategy,
        "variant": variant,
        "candidates": len(cells),
        "fills": sum(_is_filled(_terminal(row, outcomes) or row) for row in cells),
        "rejected": rejected,
        "no_fill": no_fill,
        "open": open_count,
        "resolved": len(cell_outcomes),
        "resolved_filled_outcomes": len(resolved_filled_outcomes),
        "wins": wins,
        "losses": losses,
        "breakeven": breakevens,
        "price_path_outcomes": {"wins": wins, "losses": losses, "breakeven": breakevens},
        "expired": terminal["EXPIRED"],
        "win_rate": round(wins / decided, 4) if decided else None,
        "gross_pnl_dollars": round(sum(gross_values), 2),
        "net_pnl_dollars": round(sum(net_values), 2),
        "profit_factor": _profit_factor(net_values),
        "max_drawdown_dollars": _max_drawdown(net_values),
        "average_mfe_points": _average(cell_outcomes, "mfe_points"),
        "average_mae_points": _average(cell_outcomes, "mae_points"),
        "cost_sensitivity": _cost_sensitivity(cell_outcomes, population),
        "reject_reasons": dict(Counter(str(row.get("reject_reason")) for row in cells if row.get("reject_reason"))),
        "sessions": dict(Counter(str(row.get("session")) for row in cells)),
        "regimes": dict(Counter(str(row.get("regime")) for row in cells)),
        "code_shas": sorted({str(row.get("generating_git_sha")) for row in cells}),
        "trading_days": len(days),
        "review_eligible": review_eligible,
        "classification_if_not_eligible": (
            None if review_eligible
            else "WAIT / PROMISING BUT UNPROVEN"
        ),
    }


def _pair_status(control: dict | None, modified: dict | None, control_end: dict | None, modified_end: dict | None) -> str:
    if control is None:
        return "MODIFIED_ONLY"
    if modified is None:
        return "CONTROL_ONLY"
    if control_end is not None and modified_end is not None:
        return "PAIR_COMPLETE_RESOLVED"
    if control_end is None and modified_end is None:
        return "PAIR_COMPLETE_OPEN"
    if control_end is not None:
        return "CONTROL_RESOLVED_MODIFIED_OPEN"
    return "MODIFIED_RESOLVED_CONTROL_OPEN"


def _matched_pair_report(
    strategy: str,
    candidates: list[dict[str, Any]],
    outcomes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    # One event/arm is one trial. Deterministic first-wins prevents malformed
    # duplicate candidate IDs from inflating matched-pair sample size.
    arms: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_arm_candidates = 0
    for candidate in sorted(candidates, key=lambda row: (str(row.get("signal_timestamp")), str(row.get("candidate_id")))):
        variant = str(candidate.get("variant"))
        if variant not in PAIR_VARIANTS:
            continue
        key = (str(candidate.get("event_id")), variant)
        if key in arms:
            duplicate_arm_candidates += 1
            continue
        arms[key] = candidate

    event_ids = sorted({event_id for event_id, _variant in arms})
    events = []
    resolved_pairs: list[tuple[dict, dict]] = []
    for event_id in event_ids:
        control = arms.get((event_id, "control"))
        modified = arms.get((event_id, "modified"))
        control_end = _terminal(control, outcomes) if control else None
        modified_end = _terminal(modified, outcomes) if modified else None
        status = _pair_status(control, modified, control_end, modified_end)
        events.append({
            "event_id": event_id,
            "status": status,
            "control_candidate_id": control.get("candidate_id") if control else None,
            "modified_candidate_id": modified.get("candidate_id") if modified else None,
            "control_terminal_state": control_end.get("terminal_state") if control_end else "OPEN",
            "modified_terminal_state": modified_end.get("terminal_state") if modified_end else "OPEN",
        })
        if status == "PAIR_COMPLETE_RESOLVED":
            resolved_pairs.append((control_end, modified_end))

    statuses = Counter(event["status"] for event in events)
    complete_events = len(events) - statuses["CONTROL_ONLY"] - statuses["MODIFIED_ONLY"]
    control_ends = [pair[0] for pair in resolved_pairs]
    modified_ends = [pair[1] for pair in resolved_pairs]
    pnl_pairs = [
        pair for pair in resolved_pairs
        if pair[0].get("gross_pnl_dollars") is not None and pair[1].get("gross_pnl_dollars") is not None
    ]
    control_gross = [float(pair[0]["gross_pnl_dollars"]) for pair in pnl_pairs]
    modified_gross = [float(pair[1]["gross_pnl_dollars"]) for pair in pnl_pairs]
    stored_net_pairs = [
        pair for pair in pnl_pairs
        if pair[0].get("net_pnl_dollars") is not None and pair[1].get("net_pnl_dollars") is not None
    ]
    control_net = [float(pair[0]["net_pnl_dollars"]) for pair in stored_net_pairs]
    modified_net = [float(pair[1]["net_pnl_dollars"]) for pair in stored_net_pairs]
    deltas = [round(modified - control, 2) for control, modified in zip(control_net, modified_net)]
    tier_metrics = {}
    population = population_key(candidates[0]) if candidates else None
    for ticks in COST_TIERS:
        control_tier = _cost_metrics((pair[0] for pair in pnl_pairs), ticks, population)
        modified_tier = _cost_metrics((pair[1] for pair in pnl_pairs), ticks, population)
        tier_metrics[f"{ticks}_rt_tick"] = {
            "control": control_tier,
            "modified": modified_tier,
            "modified_minus_control_net_pnl_dollars": (round(
                modified_tier["net_pnl_dollars"] - control_tier["net_pnl_dollars"], 2
            ) if "net_pnl_dollars" in modified_tier and "net_pnl_dollars" in control_tier else None),
        }
    return {
        "strategy": strategy,
        "unit": "shared event_id",
        "total_unique_event_ids": len(events),
        "paired_candidates": complete_events * 2,
        "control_only_events": statuses["CONTROL_ONLY"],
        "modified_only_events": statuses["MODIFIED_ONLY"],
        "pair_complete_candidates": complete_events,
        "pair_complete_resolved": statuses["PAIR_COMPLETE_RESOLVED"],
        "pair_complete_unresolved": complete_events - statuses["PAIR_COMPLETE_RESOLVED"],
        "pairing_rate": round(complete_events / len(events), 4) if events else None,
        "status_counts": dict(statuses),
        "duplicate_arm_candidates_ignored": duplicate_arm_candidates,
        "resolved_pair_metrics": {
            "resolved_pairs": len(resolved_pairs),
            "pnl_comparable_pairs": len(pnl_pairs),
            "stored_net_comparable_pairs": len(stored_net_pairs),
            "control_gross_pnl_dollars": round(sum(control_gross), 2),
            "modified_gross_pnl_dollars": round(sum(modified_gross), 2),
            "control_net_pnl_dollars": round(sum(control_net), 2),
            "modified_net_pnl_dollars": round(sum(modified_net), 2),
            "modified_minus_control_delta_dollars": round(sum(deltas), 2),
            "modified_better": sum(delta > 0 for delta in deltas),
            "control_better": sum(delta < 0 for delta in deltas),
            "equal": sum(delta == 0 for delta in deltas),
            "control_fill_rate": round(sum(_is_filled(row) for row in control_ends) / len(control_ends), 4) if control_ends else None,
            "modified_fill_rate": round(sum(_is_filled(row) for row in modified_ends) / len(modified_ends), 4) if modified_ends else None,
            "control_average_mfe_points": _average(control_ends, "mfe_points"),
            "control_average_mae_points": _average(control_ends, "mae_points"),
            "modified_average_mfe_points": _average(modified_ends, "mfe_points"),
            "modified_average_mae_points": _average(modified_ends, "mae_points"),
            "cost_sensitivity": tier_metrics,
        },
        "events": events,
    }


def build_report(path: str | Path, *, populations=None) -> dict[str, Any]:
    raw_rows = _rows(Path(path))
    rows = []
    invalid_identity_rows = 0
    for row in raw_rows:
        try:
            record_key(row)
        except ValueError:
            invalid_identity_rows += 1
            continue
        rows.append(row)
    candidate_rows = [row for row in rows if row.get("record_type") == "CANDIDATE"]
    outcome_rows = [row for row in rows if row.get("record_type") == "OUTCOME"]
    candidates, identical_candidate_duplicates, conflicting_candidate_ids = _dedupe_rows(candidate_rows)
    outcomes, identical_outcome_duplicates, conflicting_outcome_ids = _dedupe_rows(outcome_rows)

    configured_keys = list(configured_populations() if populations is None else populations)
    configured_set = set(configured_keys)
    observed_keys = {population_key(row) for row in candidates.values()}
    unexpected_keys = sorted(observed_keys - configured_set)

    def population_report(key):
        strategy, instrument, variant, epoch = key
        cells = [row for row in candidates.values() if population_key(row) == key]
        cell_outcomes = [row for row in outcomes.values() if population_key(row) == key]
        result = _all_arm_population(strategy, variant, cells, outcomes, key)
        result.update(
            instrument=instrument, evidence_epoch=epoch,
            newest_evidence_timestamp=max((str(row.get("observed_at")) for row in rows
                                       if population_key(row) == key and row.get("observed_at")), default=None),
            newest_data_timestamp=max((str(row["data_timestamp"]) for row in rows
                                      if population_key(row) == key and row.get("data_timestamp")), default=None),
            newest_candidate_timestamp=max((str(row["signal_timestamp"]) for row in cells
                                            if row.get("signal_timestamp")), default=None),
            newest_outcome_timestamp=max((str(row["exit_timestamp"]) for row in cell_outcomes
                                          if row.get("exit_timestamp")), default=None),
        )
        if key not in configured_set:
            result["review_eligible"] = False
            result["review_blockers"] = ["UNCONFIGURED_POPULATION"]
        return result

    populations = [population_report(key) for key in configured_keys]
    unexpected_populations = [population_report(key) for key in unexpected_keys]

    evidence_integrity_ok = not conflicting_candidate_ids and not conflicting_outcome_ids and not invalid_identity_rows
    if not evidence_integrity_ok:
        for population in populations + unexpected_populations:
            population["review_eligible"] = False
            population["review_blockers"] = ["EVIDENCE_INTEGRITY_CONFLICT"]
            population["classification_if_not_eligible"] = "WAIT / PROMISING BUT UNPROVEN"

    pair_strategies = sorted({
        (population_key(row)[0], population_key(row)[1], population_key(row)[3]) for row in candidates.values()
        if row.get("variant") in PAIR_VARIANTS
    })
    timestamps = [str(row.get("observed_at")) for row in rows if row.get("observed_at")]
    return {
        "campaign_id": CAMPAIGN_ID,
        "source_path": str(path),
        "campaign_start_timestamp": min(timestamps) if timestamps else None,
        "campaign_end_timestamp": max(timestamps) if timestamps else None,
        "raw_candidate_rows": len(candidate_rows),
        "raw_outcome_rows": len(outcome_rows),
        "candidate_rows": len(candidates),
        "outcome_rows": len(outcomes),
        "duplicate_candidate_rows_ignored": identical_candidate_duplicates,
        "duplicate_outcome_rows_ignored": identical_outcome_duplicates,
        "conflicting_candidate_rows": len(conflicting_candidate_ids),
        "conflicting_outcome_rows": len(conflicting_outcome_ids),
        "evidence_integrity": {
            "ok": evidence_integrity_ok,
            "conflicting_candidate_ids": conflicting_candidate_ids,
            "conflicting_outcome_ids": conflicting_outcome_ids,
        },
        "configured_population_count": len(configured_keys),
        "populations": populations,
        "unexpected_populations": unexpected_populations,
        "invalid_identity_rows": invalid_identity_rows,
        "matched_pairs": [
            {
                **_matched_pair_report(
                    strategy,
                    [row for row in candidates.values()
                     if (population_key(row)[0], population_key(row)[1], population_key(row)[3])
                     == (strategy, instrument, epoch)],
                    outcomes,
                ),
                "instrument": instrument,
                "evidence_epoch": epoch,
            }
            for strategy, instrument, epoch in pair_strategies
        ],
        "review_gate": {
            "minimum_trading_days": 20,
            "minimum_resolved_filled_outcomes_per_variant": 30,
            "automatic_promotion": False,
            "evidence_integrity_ok": evidence_integrity_ok,
            "blocked_by_evidence_integrity": not evidence_integrity_ok,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--path")
    args = parser.parse_args()
    path = Path(args.path) if args.path else Path(args.log_dir) / EVIDENCE_FILENAME
    print(json.dumps(build_report(path), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
