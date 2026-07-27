#!/usr/bin/env python3
"""ORB Reclaim V4-R -- final aggregation across all prior passes.

Preregistration: docs/strategy-rules/ORB_RECLAIM_V4R_PREREGISTRATION_2026-07-27.md

Combines:
  - scripts/orb_reclaim_v4r_detector.py's raw population (all 3 variant tags)
  - scripts/orb_reclaim_v4r_runtime_audit.py's unrestricted first_cross
    full-engine run (today's deployed behavior, one shared continuous account
    -- this IS what production actually does, no isolation correction needed)
  - scripts/orb_reclaim_v4r_isolated_variant_audit.py's two isolated runs
    (v4_original, v4_r -- each its own dedicated account, uncontaminated by
    the other's or first_cross's trades)

Produces the three-population comparison table the preregistration requires
(raw detector / runtime-filtered / filled), full performance metrics on
each filled population (W/L, net P&L, PF, expectancy, max drawdown, H1/H2,
month concentration), and reconciles NO_ENGINE_DECISION_AT_BAR as expected
"position already open" skips (verified, not a data gap) rather than errors.

Usage:
    python3 scripts/orb_reclaim_v4r_aggregate_report.py \\
        --raw /tmp/orb_v4r_raw.json \\
        --first-cross-audit /tmp/orb_v4r_runtime_audit.json \\
        --v4-original-audit /tmp/orb_v4r_isolated_v4orig.json \\
        --v4-r-audit /tmp/orb_v4r_isolated_v4r.json \\
        --out <path>
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

HALVES = {"H1": ("2025-07-24", "2026-01-23"), "H2": ("2026-01-24", "2026-07-23")}
COMMISSION_ROUND_TRIP = 1.48


def _pf(values: list[float]) -> Optional[float]:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses:
        return round(wins / losses, 4)
    return None if not wins else float("inf")


def _max_drawdown(rows: list[dict], pnl_key: str) -> float:
    equity = peak = max_dd = 0.0
    for r in sorted(rows, key=lambda x: (x["date"], x["bar_ts"])):
        equity += r[pnl_key]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def _bucket(
    rows: list[dict], pnl_key: str, result_key: str = "engine_result",
    subtract_commission: bool = True,
) -> dict:
    resolved = [r for r in rows if r.get(result_key) in ("WIN", "LOSS", "BREAKEVEN")]
    wins = [r for r in resolved if r[result_key] == "WIN"]
    losses = [r for r in resolved if r[result_key] == "LOSS"]
    commission = COMMISSION_ROUND_TRIP if subtract_commission else 0.0
    vals = [float(r[pnl_key]) - commission for r in resolved]
    return {
        "n": len(resolved), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(resolved), 4) if resolved else None,
        "net_pnl": round(sum(vals), 2) if vals else 0.0,
        "pf": _pf(vals) if vals else None,
        "expectancy_per_fill": round(statistics.mean(vals), 2) if vals else None,
        "max_drawdown": _max_drawdown(
            [{**r, "_net": float(r[pnl_key]) - commission} for r in resolved], "_net"
        ) if resolved else 0.0,
    }


def _half(date_str: str) -> str:
    for label, (start, end) in HALVES.items():
        if start <= date_str <= end:
            return label
    return "OUT_OF_RANGE"


def _summarize_population(candidates: list[dict], label: str) -> dict:
    n = len(candidates)
    reached_risk = [c for c in candidates if c.get("classification") == "REACHED_RISK_APPROVED"
                     or c.get("layer") == "risk"]
    filled = [c for c in candidates if c.get("engine_filled")]

    overall = _bucket(filled, "engine_pnl_gross")
    by_half = {h: _bucket([f for f in filled if _half(f["date"]) == h], "engine_pnl_gross") for h in HALVES}
    by_instrument = {
        inst: _bucket([f for f in filled if f["instrument"] == inst], "engine_pnl_gross")
        for inst in ("MNQ", "MES")
    }
    by_month: dict[str, list[dict]] = {}
    for f in filled:
        if f.get("engine_result") in ("WIN", "LOSS", "BREAKEVEN"):
            by_month.setdefault(f["date"][:7], []).append(f)
    month_stats = {m: _bucket(rows, "engine_pnl_gross") for m, rows in sorted(by_month.items())}
    top_month = max(month_stats.items(), key=lambda kv: kv[1]["net_pnl"], default=(None, {}))
    total_net = overall["net_pnl"] or 0.0
    top_month_share = (
        round(top_month[1]["net_pnl"] / total_net, 4)
        if top_month[0] and total_net else None
    )

    classification_counts: dict[str, int] = {}
    for c in candidates:
        classification_counts[c.get("classification", "UNKNOWN")] = (
            classification_counts.get(c.get("classification", "UNKNOWN"), 0) + 1
        )
    skipped_position_open = classification_counts.pop("NO_ENGINE_DECISION_AT_BAR", 0)
    if skipped_position_open:
        classification_counts["SKIPPED_POSITION_ALREADY_OPEN"] = skipped_position_open

    return {
        "label": label,
        "raw_candidate_count": n,
        "reached_risk_layer_count": len(reached_risk),
        "filled_count": len(filled),
        "gate_classification_breakdown": classification_counts,
        "filled_population_performance": {
            "overall": overall,
            "by_half": by_half,
            "by_instrument": by_instrument,
            "by_month": month_stats,
            "top_month": {"month": top_month[0], "net_pnl": top_month[1].get("net_pnl"),
                          "share_of_total_net": top_month_share} if top_month[0] else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--first-cross-audit", type=Path, required=True)
    parser.add_argument("--v4-original-audit", type=Path, required=True)
    parser.add_argument("--v4-r-audit", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    first_cross_audit = json.loads(args.first_cross_audit.read_text(encoding="utf-8"))
    v4_original_audit = json.loads(args.v4_original_audit.read_text(encoding="utf-8"))
    v4_r_audit = json.loads(args.v4_r_audit.read_text(encoding="utf-8"))

    raw_by_variant = {
        "first_cross": raw["candidates"],
        "v4_original": [c for c in raw["candidates"] if c["v4_original_eligible"]],
        "v4_r": [c for c in raw["candidates"] if c["v4_r_eligible"]],
    }

    report = {
        "raw_population": {
            variant: {
                "candidate_count": len(cands),
                "filled_count": sum(1 for c in cands if c.get("filled")),
                "resolved_performance": _bucket(
                    [c for c in cands if c.get("filled") and c.get("result") in ("WIN", "LOSS", "BREAKEVEN")],
                    "net_pnl", result_key="result", subtract_commission=False,
                ) if any(c.get("filled") for c in cands) else None,
            }
            for variant, cands in raw_by_variant.items()
        },
        "first_cross": _summarize_population(
            first_cross_audit["candidates"], "first_cross (today's deployed behavior, shared account)"
        ),
        "v4_original": _summarize_population(
            v4_original_audit["candidates"], "v4_original (NY + true_reclaim, isolated account)"
        ),
        "v4_r": _summarize_population(
            v4_r_audit["candidates"], "v4_r (NY + prior_rejected_high, isolated account, PREREGISTERED)"
        ),
        "note_raw_pnl_uses_own_fill_sim": (
            "raw_population's resolved_performance uses scripts/orb_reclaim_v4r_detector.py's "
            "own independent fill/exit simulation (ioc_limit single-bar-close + pessimistic "
            "stop/target walk-forward, no risk/quality gates applied at all) -- NOT the real "
            "engine. first_cross/v4_original/v4_r's filled_population_performance uses the REAL "
            "ReplayEngine->DecisionEngine->RiskEngine->PaperBroker outcome. The two were "
            "cross-checked on overlapping trades during this study and matched closely "
            "(same entry/exit logic, independently implemented)."
        ),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[done] wrote {args.out}")
    print(json.dumps({
        k: v.get("filled_population_performance", {}).get("overall")
        if isinstance(v, dict) and "filled_population_performance" in v else v
        for k, v in report.items() if k in ("first_cross", "v4_original", "v4_r")
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
