"""Read-only shadow analysis for journaled GEX observations.

This module turns the observe-only ``gex_observed`` snapshots into a compact
scorecard against resolved futures outcomes. It never fetches live data, writes
the journal, or participates in trade gating.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


RESULTS = {"WIN", "LOSS", "BREAKEVEN"}


def summarize_gex_shadow(entries: list[dict[str, Any]], *, min_sample: int = 20) -> dict[str, Any]:
    """Summarize resolved trades by observed GEX regime and wall context.

    Standalone OUTCOME entries are paired FIFO with approved TRADE decisions,
    matching the journal reader's current convention. Entries without an
    ``ok=True`` GEX snapshot are ignored for measured cohorts but counted.
    """
    min_sample = max(1, int(min_sample or 20))
    pairs = _resolved_trade_pairs(entries)
    measured: list[dict[str, Any]] = []
    skipped_missing_gex = 0

    for decision, outcome in pairs:
        observed = decision.get("gex_observed") or {}
        if not isinstance(observed, dict) or observed.get("ok") is not True:
            skipped_missing_gex += 1
            continue
        measured.append(_sample(decision, observed, outcome))

    cohorts = {
        "by_regime": _cohorts(measured, "regime", min_sample=min_sample),
        "by_wall_context": _cohorts(measured, "wall_context", min_sample=min_sample),
        "by_regime_wall": _cohorts(measured, "regime_wall", min_sample=min_sample),
    }
    overall = _stats(measured)
    overall["sufficient_sample"] = overall["sample_size"] >= min_sample

    best = _best_cohort(cohorts["by_regime_wall"])
    worst = _worst_cohort(cohorts["by_regime_wall"])
    verdict = _verdict(overall, best, worst, min_sample)

    return {
        "enabled": True,
        "mode": "observe_only",
        "trade_gating_changed": False,
        "min_sample": min_sample,
        "resolved_trades": len(pairs),
        "measured_trades": len(measured),
        "skipped_missing_gex": skipped_missing_gex,
        "overall": overall,
        "cohorts": cohorts,
        "best_cohort": best,
        "worst_cohort": worst,
        "verdict": verdict,
        "promotion_checklist": [
            f"Collect at least {min_sample} resolved trades with ok=true gex_observed.",
            "Look for stable positive expectancy in one or more GEX cohorts.",
            "Look for clearly negative cohorts that could justify future blocking.",
            "Replay or shadow-run any proposed gate before enforcing it live.",
        ],
    }


def disabled_summary() -> dict[str, Any]:
    return {
        "enabled": False,
        "mode": "observe_only",
        "trade_gating_changed": False,
        "reason": "Set GEX_SHADOW_ANALYSIS_ENABLED=true to summarize journaled GEX observations.",
    }


def _resolved_trade_pairs(entries: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    decisions: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("type") == "OUTCOME":
            outcome = entry.get("outcome") or {}
            if outcome.get("result") in RESULTS:
                outcomes.append(outcome)
            continue
        if (
            entry.get("decision") == "TRADE"
            and (entry.get("risk_check") or {}).get("result") == "APPROVED"
        ):
            decisions.append(entry)

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    outcome_iter = iter(outcomes)
    for decision in decisions:
        inline = decision.get("outcome") or {}
        if inline.get("result") in RESULTS:
            pairs.append((decision, inline))
            continue
        try:
            pairs.append((decision, next(outcome_iter)))
        except StopIteration:
            break
    return pairs


def _sample(decision: dict[str, Any], observed: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    setup = decision.get("setup") or {}
    context = decision.get("context") or {}
    close = _num(context.get("close"))
    if close is None:
        close = _num(setup.get("entry"))

    regime = str(observed.get("regime") or "unknown").lower()
    wall_context = _wall_context(
        close=close,
        call_wall=_num(observed.get("call_wall")),
        put_wall=_num(observed.get("put_wall")),
        flip_point=_num(observed.get("flip_point")),
    )
    return {
        "instrument": decision.get("instrument"),
        "strategy": setup.get("strategy"),
        "direction": setup.get("direction"),
        "regime": regime,
        "wall_context": wall_context,
        "regime_wall": f"{regime}|{wall_context}",
        "result": outcome.get("result"),
        "pnl_dollars": float(outcome.get("pnl_dollars") or 0.0),
    }


def _wall_context(
    *,
    close: float | None,
    call_wall: float | None,
    put_wall: float | None,
    flip_point: float | None,
) -> str:
    if close is None:
        return "unknown"
    nearest = []
    if call_wall is not None:
        nearest.append(("near_call_wall", abs(close - call_wall) / close))
    if put_wall is not None:
        nearest.append(("near_put_wall", abs(close - put_wall) / close))
    if nearest:
        label, distance = min(nearest, key=lambda item: item[1])
        if distance <= 0.003:
            return label
    if flip_point is not None:
        if close > flip_point:
            return "above_flip"
        if close < flip_point:
            return "below_flip"
        return "at_flip"
    return "between_walls"


def _cohorts(samples: list[dict[str, Any]], key: str, *, min_sample: int) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        buckets[str(sample.get(key) or "unknown")].append(sample)
    rows = []
    for name, bucket in buckets.items():
        row = {"key": name, **_stats(bucket)}
        row["sufficient_sample"] = row["sample_size"] >= min_sample
        rows.append(row)
    return sorted(rows, key=lambda row: (-row["sample_size"], row["key"]))


def _stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    sample_size = len(samples)
    wins = sum(1 for s in samples if s.get("result") == "WIN")
    losses = sum(1 for s in samples if s.get("result") == "LOSS")
    breakeven = sum(1 for s in samples if s.get("result") == "BREAKEVEN")
    pnl = round(sum(float(s.get("pnl_dollars") or 0.0) for s in samples), 2)
    return {
        "sample_size": sample_size,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": round((wins / (wins + losses)) * 100, 1) if wins + losses else 0.0,
        "pnl_dollars": pnl,
        "expectancy": round(pnl / sample_size, 2) if sample_size else 0.0,
    }


def _best_cohort(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    sufficient = [row for row in rows if row.get("sufficient_sample")]
    if not sufficient:
        return None
    return max(sufficient, key=lambda row: (row["expectancy"], row["sample_size"]))


def _worst_cohort(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    sufficient = [row for row in rows if row.get("sufficient_sample")]
    if not sufficient:
        return None
    return min(sufficient, key=lambda row: (row["expectancy"], -row["sample_size"]))


def _verdict(
    overall: dict[str, Any],
    best: dict[str, Any] | None,
    worst: dict[str, Any] | None,
    min_sample: int,
) -> dict[str, Any]:
    if overall["sample_size"] < min_sample:
        return {
            "status": "JOURNAL_ONLY",
            "reason": (
                f"Only {overall['sample_size']} measured resolved trade(s); "
                f"need {min_sample} before judging GEX cohorts."
            ),
        }
    if best and best["expectancy"] > 0 and worst and worst["expectancy"] < 0:
        return {
            "status": "PROMISING_SHADOW_EDGE",
            "reason": (
                "Observed cohorts show both positive and negative expectancy; "
                "candidate filters deserve replay/shadow validation."
            ),
        }
    return {
        "status": "NO_PROMOTION_YET",
        "reason": "Sample is large enough to inspect, but no clear GEX cohort separation is visible yet.",
    }


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
