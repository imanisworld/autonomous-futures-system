#!/usr/bin/env python3
"""Observer signal grading (A/B/C/D) — forward evaluator.

Preregistered in docs/prereg-observer-signal-grading-2026-09-23.md (#942).

Default mode prints COUNTS ONLY (how many graded outcomes exist and whether the
single look is due). It never computes or prints per-grade performance until
``--final-look`` is passed, and ``--final-look`` refuses unless the prereg's
look condition holds or the 2027-03-31 deadline has passed. Read-only: reads
the observation evidence file, optionally writes one JSON result, touches
nothing else.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

FTFC_DEFINITION = "strat_ftfc_opens_v1"
GRADES = ("A", "B", "C", "D")
TERMINAL = {"WIN", "LOSS", "EXPIRED"}
PRIMARY, REPLICATION = "MNQ", "MES"
COMMISSION = 1.48
COST_TICKS = 2.0
LOOK_MIN_A, LOOK_MIN_A_DAYS, LOOK_MIN_OTHER = 60, 30, 30
DEADLINE = date(2027, 3, 31)
BOOTSTRAP_RESAMPLES = 10_000
SEED = 20260923
ROLL_MONTHS = (3, 6, 9, 12)
ROLL_WINDOW_DAYS = 14


# --------------------------------------------------------------------------- #
# grading (prereg §2) and inclusion (§3)
# --------------------------------------------------------------------------- #


def grade(row: dict[str, Any]) -> Optional[str]:
    label = row.get("strat_ftfc")
    alignment = label.get("alignment") if isinstance(label, dict) else None
    if alignment == "aligned":
        a_plus = row.get("session") == "new_york" and row.get("market_condition") != "DEAD"
        return "A" if a_plus else "B"
    return {"conflict": "C", "against": "D"}.get(alignment)  # unknown / missing -> None


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def in_roll_window(trading_date: date) -> bool:
    if trading_date.month not in ROLL_MONTHS:
        return False
    return trading_date >= third_friday(trading_date.year, trading_date.month) - timedelta(days=ROLL_WINDOW_DAYS)


def cost_r(row: dict[str, Any]) -> Optional[float]:
    try:
        risk_ticks = float(row["risk_ticks"])
        tick_value = float(row["tick_value_dollars"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(risk_ticks) and math.isfinite(tick_value)) or risk_ticks <= 0 or tick_value <= 0:
        return None
    return (COST_TICKS * tick_value + COMMISSION) / (risk_ticks * tick_value)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def load_outcomes(evidence: Path, *, truncate_at: Optional[date] = None) -> tuple[list[dict[str, Any]], Counter]:
    """Graded, cost-adjusted terminal outcomes of structural populations."""
    seen: set[tuple[str, str]] = set()
    excluded: Counter = Counter()
    out: list[dict[str, Any]] = []
    for line in evidence.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            excluded["malformed_json"] += 1
            continue
        if row.get("record_type") != "OUTCOME" or row.get("collection_mode") != "structural_outcome":
            continue
        identity = (str(row.get("record_type")), str(row.get("candidate_id")))
        if identity in seen:
            excluded["duplicate"] += 1
            continue
        seen.add(identity)
        if row.get("result") not in TERMINAL or not _finite(row.get("pnl_r")):
            excluded["not_terminal"] += 1
            continue
        label = row.get("strat_ftfc")
        if not isinstance(label, dict) or label.get("definition") != FTFC_DEFINITION:
            excluded["unlabeled"] += 1
            continue
        g = grade(row)
        if g is None:
            excluded["ftfc_unknown"] += 1
            continue
        try:
            td = date.fromisoformat(str(row.get("trading_date") or row.get("observation_date")))
        except ValueError:
            excluded["no_trading_date"] += 1
            continue
        if truncate_at is not None and td > truncate_at:
            excluded["after_truncation"] += 1
            continue
        if label.get("roll_adjusted") is False and in_roll_window(td):
            excluded["roll_window"] += 1
            continue
        c = cost_r(row)
        if c is None:
            excluded["no_cost_basis"] += 1
            continue
        out.append({
            "grade": g, "instrument": row.get("instrument"), "strategy": row.get("strategy"),
            "trading_date": td.isoformat(), "r_net": float(row["pnl_r"]) - c,
        })
    return out, excluded


# --------------------------------------------------------------------------- #
# counts-only mode (§4: nothing about performance before the look)
# --------------------------------------------------------------------------- #


def counts(outcomes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    table: dict[str, dict[str, dict[str, int]]] = {}
    for inst in sorted({o["instrument"] for o in outcomes}):
        rows = [o for o in outcomes if o["instrument"] == inst]
        table[inst] = {g: {"terminal": sum(o["grade"] == g for o in rows),
                           "days": len({o["trading_date"] for o in rows if o["grade"] == g})} for g in GRADES}
    return table


def look_due(table: dict[str, Any], as_of: date) -> dict[str, Any]:
    mnq = table.get(PRIMARY) or {g: {"terminal": 0, "days": 0} for g in GRADES}
    minimums_met = (mnq["A"]["terminal"] >= LOOK_MIN_A and mnq["A"]["days"] >= LOOK_MIN_A_DAYS
                    and all(mnq[g]["terminal"] >= LOOK_MIN_OTHER for g in ("B", "C", "D")))
    return {"minimums_met": minimums_met, "deadline_passed": as_of >= DEADLINE,
            "due": minimums_met or as_of >= DEADLINE}


# --------------------------------------------------------------------------- #
# final look (§5)
# --------------------------------------------------------------------------- #


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _by_day(rows: Iterable[dict[str, Any]]) -> dict[str, list[float]]:
    days: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        days[r["trading_date"]].append(r["r_net"])
    return days


def bootstrap_mean_ci(rows: Sequence[dict[str, Any]], rng: random.Random) -> Optional[tuple[float, float]]:
    days = list(_by_day(rows).values())
    if not days:
        return None
    stats = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        pick = [x for _ in days for x in days[rng.randrange(len(days))]]
        stats.append(sum(pick) / len(pick))
    stats.sort()
    return stats[int(0.025 * BOOTSTRAP_RESAMPLES)], stats[int(0.975 * BOOTSTRAP_RESAMPLES) - 1]


def bootstrap_diff_ci(a: Sequence[dict[str, Any]], c: Sequence[dict[str, Any]], rng: random.Random) -> Optional[tuple[float, float]]:
    a_days, c_days = _by_day(a), _by_day(c)
    days = sorted(set(a_days) | set(c_days))
    if not a_days or not c_days:
        return None
    stats = []
    attempts = 0
    while len(stats) < BOOTSTRAP_RESAMPLES and attempts < 20 * BOOTSTRAP_RESAMPLES:
        attempts += 1
        pick = [days[rng.randrange(len(days))] for _ in days]
        av = [x for d in pick for x in a_days.get(d, ())]
        cv = [x for d in pick for x in c_days.get(d, ())]
        if av and cv:
            stats.append(sum(av) / len(av) - sum(cv) / len(cv))
    if len(stats) < BOOTSTRAP_RESAMPLES:
        return None
    stats.sort()
    return stats[int(0.025 * BOOTSTRAP_RESAMPLES)], stats[int(0.975 * BOOTSTRAP_RESAMPLES) - 1]


def evaluate_instrument(rows: Sequence[dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    by = {g: [r for r in rows if r["grade"] == g] for g in GRADES}
    means = {g: _mean([r["r_net"] for r in by[g]]) for g in GRADES}
    a = by["A"]
    ci_a = bootstrap_mean_ci(a, rng)
    ci_ac = bootstrap_diff_ci(a, by["C"], rng)
    # G4: halves split at the median trading date of A's outcomes.
    dates = sorted(r["trading_date"] for r in a)
    median = dates[len(dates) // 2] if dates else None
    h1 = [r["r_net"] for r in a if median is not None and r["trading_date"] < median]
    h2 = [r["r_net"] for r in a if median is not None and r["trading_date"] >= median]
    day_net = {d: sum(v) for d, v in _by_day(a).items()}
    a_net = sum(day_net.values())
    best_day = max(day_net.values()) if day_net else 0.0
    fam = Counter()
    for r in a:
        fam[r["strategy"]] += r["r_net"]
    top_family_share = (max(fam.values()) / a_net) if (fam and a_net > 0) else None
    ordered = all(means[x] is not None for x in ("A", "B", "C")) and means["A"] >= means["B"] >= means["C"]
    gates = {
        "G1_A_positive_ci": bool(means["A"] is not None and means["A"] > 0 and ci_a and ci_a[0] > 0),
        "G2_A_beats_C_ci": bool(ci_ac and ci_ac[0] > 0),
        "G3_ordered_A_ge_B_ge_C": bool(ordered),
        "G4_halves_and_leave_best_day": bool(h1 and h2 and _mean(h1) > 0 and _mean(h2) > 0 and a_net - best_day > 0),
        "G5_no_family_over_half": bool(top_family_share is not None and top_family_share <= 0.5),
    }
    return {
        "per_grade": {g: {"terminal": len(by[g]), "days": len({r["trading_date"] for r in by[g]}),
                          "mean_r_net": None if means[g] is None else round(means[g], 4),
                          "win_rate_net": (round(sum(r["r_net"] > 0 for r in by[g]) / len(by[g]), 4) if by[g] else None)}
                      for g in GRADES},
        "A_mean_ci95": None if ci_a is None else [round(x, 4) for x in ci_a],
        "A_minus_C_ci95": None if ci_ac is None else [round(x, 4) for x in ci_ac],
        "A_halves_mean": [None if not h1 else round(_mean(h1), 4), None if not h2 else round(_mean(h2), 4)],
        "A_median_split_date": median,
        "A_net_r": round(a_net, 4), "A_net_r_without_best_day": round(a_net - best_day, 4),
        "A_family_net_r": {k: round(v, 4) for k, v in sorted(fam.items())},
        "A_top_family_share": None if top_family_share is None else round(top_family_share, 4),
        "gates": gates,
    }


def verdict(mnq: dict[str, Any], minimums_met: bool, truncated: bool) -> str:
    g = mnq["gates"]
    if not minimums_met:
        label = "INSUFFICIENT"
    elif all(g.values()):
        label = "GRADE VALIDATED (MNQ)"
    elif g["G2_A_beats_C_ci"] and g["G3_ordered_A_ge_B_ge_C"] and not g["G1_A_positive_ci"]:
        label = "GRADE ORDERS SIGNALS, A NOT PROFITABLE AFTER COSTS"
    else:
        label = "GRADE NOT SUPPORTED"
    return label + (" · TRUNCATED" if truncated else "")


def replication(mes: Optional[dict[str, Any]]) -> str:
    if not mes:
        return "NO DATA"
    mean_a = mes["per_grade"]["A"]["mean_r_net"]
    return "REPLICATED" if (mean_a is not None and mean_a > 0 and mes["gates"]["G3_ordered_A_ge_B_ge_C"]) else "NOT REPLICATED"


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evidence", type=Path, required=True, help="cross_instrument_observation_v1.jsonl")
    parser.add_argument("--as-of", type=date.fromisoformat, default=datetime.now(timezone.utc).date())
    parser.add_argument("--truncate-at", type=date.fromisoformat, default=None,
                        help="last trading date before a detector/bracket change (prereg §3)")
    parser.add_argument("--final-look", action="store_true", help="the single preregistered look")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    outcomes, excluded = load_outcomes(args.evidence, truncate_at=args.truncate_at)
    table = counts(outcomes)
    due = look_due(table, args.as_of)
    report: dict[str, Any] = {
        "study": "observer_signal_grading_v1",
        "prereg": "docs/prereg-observer-signal-grading-2026-09-23.md",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "as_of": args.as_of.isoformat(),
        "truncate_at": None if args.truncate_at is None else args.truncate_at.isoformat(),
        "counts": table, "excluded": dict(sorted(excluded.items())), "look": due,
    }
    if args.final_look:
        if not due["due"]:
            print(json.dumps({**report, "refused": "look not due: minimums unmet and deadline not reached"},
                             indent=2, sort_keys=True))
            return 2
        rng = random.Random(SEED)
        results = {inst: evaluate_instrument([o for o in outcomes if o["instrument"] == inst], rng)
                   for inst in (PRIMARY, REPLICATION) if any(o["instrument"] == inst for o in outcomes)}
        mnq = results.get(PRIMARY)
        report["final_look"] = {
            "results": results,
            "verdict": verdict(mnq, due["minimums_met"], args.truncate_at is not None) if mnq else "INSUFFICIENT",
            "mes_replication": replication(results.get(REPLICATION)),
            "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "seed": SEED, "cluster": "trading_date"},
        }
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
