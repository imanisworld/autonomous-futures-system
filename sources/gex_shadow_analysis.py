"""Read-only shadow analysis for journaled GEX observations.

This module turns the observe-only ``gex_observed`` snapshots into a compact
scorecard against resolved futures outcomes. It never fetches live data, writes
the journal, or participates in trade gating.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


RESULTS = {"WIN", "LOSS", "BREAKEVEN"}
_NEAR_FLIP_PCT = 0.0025
_MID_FLIP_PCT = 0.01
_NEAR_WALL_PCT = 0.003
_ENRICHMENT_DIMENSIONS = (
    "by_delta_bias",
    "by_delta_alignment",
    "by_spot_vs_flip",
    "by_flip_distance",
    "by_wall_rank_context",
)


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
        "by_delta_bias": _cohorts(measured, "delta_bias", min_sample=min_sample),
        "by_delta_alignment": _cohorts(
            measured, "delta_alignment", min_sample=min_sample
        ),
        "by_spot_vs_flip": _cohorts(measured, "spot_vs_flip", min_sample=min_sample),
        "by_flip_distance": _cohorts(measured, "flip_distance", min_sample=min_sample),
        "by_wall_rank_context": _cohorts(
            measured, "wall_rank_context", min_sample=min_sample
        ),
    }
    overall = _stats(measured)
    overall["sufficient_sample"] = overall["sample_size"] >= min_sample

    best = _best_cohort(cohorts["by_regime_wall"])
    worst = _worst_cohort(cohorts["by_regime_wall"])
    verdict = _verdict(overall, best, worst, min_sample)
    enrichment_evidence = _enrichment_evidence(measured, cohorts, min_sample)

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
        "enrichment_evidence": enrichment_evidence,
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
    direction = str(setup.get("direction") or "").upper()
    spot = _num(observed.get("spot"))
    if spot is None:
        spot = close
    flip_point = _num(observed.get("flip_point"))
    dist_to_flip = _num(observed.get("dist_to_flip"))
    if dist_to_flip is None and spot is not None and flip_point is not None:
        dist_to_flip = flip_point - spot
    spot_vs_flip = _choice(observed.get("spot_vs_flip"), {"above", "below"})
    if spot_vs_flip == "unknown" and dist_to_flip is not None:
        spot_vs_flip = "below" if dist_to_flip > 0 else "above"
    call_walls = _walls(observed.get("call_walls"), observed.get("call_wall"))
    put_walls = _walls(observed.get("put_walls"), observed.get("put_wall"))
    wall_context = _wall_context(
        close=close,
        call_wall=call_walls[0] if call_walls else None,
        put_wall=put_walls[0] if put_walls else None,
        flip_point=flip_point,
    )
    delta_bias = _choice(
        observed.get("delta_bias"), {"bullish", "bearish", "neutral"}
    )
    return {
        "ts": decision.get("ts"),
        "instrument": decision.get("instrument"),
        "strategy": setup.get("strategy"),
        "direction": direction,
        "regime": regime,
        "delta_bias": delta_bias,
        "delta_alignment": _delta_alignment(direction, delta_bias),
        "spot_vs_flip": spot_vs_flip,
        "flip_distance": _flip_distance_bucket(dist_to_flip, spot),
        "wall_context": wall_context,
        "wall_rank_context": _wall_rank_context(close, call_walls, put_walls),
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
        if distance <= _NEAR_WALL_PCT:
            return label
    if flip_point is not None:
        if close > flip_point:
            return "above_flip"
        if close < flip_point:
            return "below_flip"
        return "at_flip"
    return "between_walls"


def _flip_distance_bucket(distance: float | None, spot: float | None) -> str:
    if distance is None or spot is None or spot <= 0:
        return "unknown"
    distance_pct = abs(distance) / spot
    if distance_pct <= _NEAR_FLIP_PCT:
        return "near_0_0.25pct"
    if distance_pct <= _MID_FLIP_PCT:
        return "mid_0.25_1pct"
    return "far_over_1pct"


def _delta_alignment(direction: str, delta_bias: str) -> str:
    if delta_bias == "neutral":
        return "neutral"
    if direction not in {"LONG", "SHORT"} or delta_bias == "unknown":
        return "unknown"
    aligned = (direction == "LONG" and delta_bias == "bullish") or (
        direction == "SHORT" and delta_bias == "bearish"
    )
    return "aligned" if aligned else "conflicted"


def _wall_rank_context(
    close: float | None, call_walls: list[float], put_walls: list[float]
) -> str:
    """Compactly expose whether secondary walls add context beyond wall #1."""
    if close is None or close <= 0:
        return "unknown"
    candidates = [
        (f"near_call_{'primary' if rank == 1 else 'secondary'}", abs(close - wall) / close)
        for rank, wall in enumerate(call_walls, 1)
    ]
    candidates += [
        (f"near_put_{'primary' if rank == 1 else 'secondary'}", abs(close - wall) / close)
        for rank, wall in enumerate(put_walls, 1)
    ]
    if candidates:
        label, distance = min(candidates, key=lambda item: item[1])
        if distance <= _NEAR_WALL_PCT:
            return label
    if call_walls and close > max(call_walls):
        return "above_call_range"
    if put_walls and close < min(put_walls):
        return "below_put_range"
    if call_walls or put_walls:
        return "inside_wall_range"
    return "unknown"


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


def _enrichment_evidence(
    samples: list[dict[str, Any]],
    cohorts: dict[str, list[dict[str, Any]]],
    min_sample: int,
) -> dict[str, Any]:
    """Say whether PR #91 fields separate outcomes, not merely fill journals."""
    dimensions = []
    for name in _ENRICHMENT_DIMENSIONS:
        rows = cohorts[name]
        known = sum(row["sample_size"] for row in rows if row["key"] != "unknown")
        sufficient = [
            row for row in rows
            if row["key"] != "unknown" and row["sample_size"] >= min_sample
        ]
        best = max(sufficient, key=lambda row: row["expectancy"]) if sufficient else None
        worst = min(sufficient, key=lambda row: row["expectancy"]) if sufficient else None
        spread = (
            round(best["expectancy"] - worst["expectancy"], 2)
            if best is not None and worst is not None and len(sufficient) >= 2
            else None
        )
        stable_across_time = _stable_separation(
            samples,
            key=name.removeprefix("by_"),
            best_key=best["key"] if best else None,
            worst_key=worst["key"] if worst else None,
            min_sample=min_sample,
        )
        dimensions.append({
            "dimension": name.removeprefix("by_"),
            "known_samples": known,
            "coverage_pct": round(known / len(samples) * 100, 1) if samples else 0.0,
            "sufficient_cohorts": len(sufficient),
            "best_key": best["key"] if best else None,
            "worst_key": worst["key"] if worst else None,
            "expectancy_spread": spread,
            "stable_across_time": stable_across_time,
            "separates_outcomes": bool(
                best and worst and best["expectancy"] > 0 > worst["expectancy"]
            ),
        })
    earned = [row for row in dimensions if row["separates_outcomes"]]
    stable_earned = [row for row in earned if row["stable_across_time"] is True]
    if stable_earned:
        status = "ENRICHMENT_PROMISING"
        reason = (
            "At least one new-field dimension has sufficient positive and negative "
            "expectancy cohorts in both chronological halves; validate it in replay."
        )
    elif earned:
        status = "ENRICHMENT_CANDIDATE_ONLY"
        reason = (
            "A new-field dimension separates aggregate outcomes, but has not repeated "
            "across both chronological halves."
        )
    elif any(row["sufficient_cohorts"] >= 2 for row in dimensions):
        status = "NO_ENRICHMENT_EDGE_YET"
        reason = "New fields are measurable, but sufficient cohorts do not separate outcomes."
    else:
        status = "JOURNAL_ONLY"
        reason = (
            f"New fields need at least two cohorts with {min_sample} resolved trades "
            "each before their incremental value can be judged."
        )
    return {
        "status": status,
        "reason": reason,
        "earned_dimensions": [row["dimension"] for row in earned],
        "stable_earned_dimensions": [row["dimension"] for row in stable_earned],
        "dimensions": dimensions,
    }


def _stable_separation(
    samples: list[dict[str, Any]],
    *,
    key: str,
    best_key: str | None,
    worst_key: str | None,
    min_sample: int,
) -> bool | None:
    if not best_key or not worst_key or len(samples) < 2:
        return None
    ordered = sorted(samples, key=lambda sample: str(sample.get("ts") or ""))
    midpoint = len(ordered) // 2
    if midpoint == 0 or midpoint == len(ordered):
        return None
    half_min = max(1, (min_sample + 1) // 2)
    for period in (ordered[:midpoint], ordered[midpoint:]):
        grouped = {
            row["key"]: row
            for row in _cohorts(period, key, min_sample=half_min)
        }
        best = grouped.get(best_key)
        worst = grouped.get(worst_key)
        if not best or not worst:
            return None
        if not best["sufficient_sample"] or not worst["sufficient_sample"]:
            return None
        if not (best["expectancy"] > 0 > worst["expectancy"]):
            return False
    return True


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


def _choice(value: Any, allowed: set[str]) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else "unknown"


def _walls(value: Any, fallback: Any) -> list[float]:
    raw = value if isinstance(value, (list, tuple)) else []
    walls = [number for item in raw if (number := _num(item)) is not None]
    fallback_number = _num(fallback)
    if fallback_number is not None and fallback_number not in walls:
        walls.insert(0, fallback_number)
    return walls
