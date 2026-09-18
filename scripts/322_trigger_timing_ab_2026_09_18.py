#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import statistics
from collections import Counter
from datetime import date
from pathlib import Path

from research.run_322_expanded_evidence import detect_candidates
from scripts.edge_decomposition_audit import (
    TICK_SIZE,
    LANES,
    Candidate,
    _boundary,
    bracket_summary,
    extract_state_machine,
    load_bars,
    run_bracket_stage,
)

PREREG_COMMIT = "97c87fddbfb98404ab0d87a1d61148ba3c6ad2ba"
START = date(2024, 7, 2)
END = date(2026, 6, 26)
EXPECTED_N = 34
TICK = 0.25
IOC_TOLERANCE = 32.0
EXPECTED_PLAN = {"filled": 33, "net": 2532.66, "h1_net": 1383.34, "h2_net": 1149.32}
EXPECTED_IOC = {"filled": 20, "net": 1859.40, "h1_net": 1068.68, "h2_net": 790.72}


def tree_sha(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.glob("MNQ_*.jsonl")):
        h.update(p.name.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()


def crosscheck(research_15m: Path, audit_5m: Path):
    research = detect_candidates(research_15m, "MNQ", START, END)
    bars = load_bars(audit_5m, "MNQ")
    state = extract_state_machine(LANES["322_mnq"], bars)
    state.sort(key=lambda c: c.bar_idx)
    if len(research) != EXPECTED_N or len(state) != EXPECTED_N:
        raise RuntimeError(f"candidate count mismatch research={len(research)} state={len(state)}")
    mismatches = []
    triggers = {}
    for r, s in zip(research, state):
        rt = (
            r["date"].isoformat(),
            r["direction"],
            round(float(r["entry_trigger"]), 8),
            round(float(r["stop"]), 8),
            round(float(r["target"]), 8),
        )
        st = (
            s.date,
            s.direction,
            round(float(s.entry), 8),
            round(float(s.stop), 8),
            round(float(s.target), 8),
        )
        if rt != st:
            mismatches.append({"research": rt, "state": st})
        triggers[s.date] = {"trigger": float(r["entry_trigger"]), "gap_open": bool(r["gap_open"])}
    if mismatches:
        raise RuntimeError(f"candidate parity failed: {mismatches[:5]}")
    if any(v["gap_open"] for v in triggers.values()):
        raise RuntimeError("accepted 34-candidate baseline should contain zero gap-open candidates")
    return state, triggers, bars


def check_repro(plan: dict, ioc: dict):
    for key, exp in EXPECTED_PLAN.items():
        got = plan[key]
        if key == "filled":
            if got != exp:
                raise RuntimeError(f"plan {key}: {got} != {exp}")
        elif abs(float(got) - float(exp)) > 0.02:
            raise RuntimeError(f"plan {key}: {got} != {exp}")
    for key, exp in EXPECTED_IOC.items():
        got = ioc[key]
        if key == "filled":
            if got != exp:
                raise RuntimeError(f"ioc {key}: {got} != {exp}")
        elif abs(float(got) - float(exp)) > 0.02:
            raise RuntimeError(f"ioc {key}: {got} != {exp}")


def make_prearmed(candidates, triggers, bars):
    out = []
    for c in candidates:
        prev_idx = c.bar_idx - 1
        if prev_idx < 0:
            raise RuntimeError(f"{c.date}: no prior bar to arm from")
        if bars.et(prev_idx).date().isoformat() != c.date:
            raise RuntimeError(f"{c.date}: prior bar crosses date")
        extra = dict(c.extra)
        extra.update(
            original_bar_idx=c.bar_idx,
            original_bar_ts=c.bar_ts,
            prearmed_from_idx=prev_idx,
            prearmed_from_ts=bars.rows[prev_idx]["timestamp"],
            trigger=triggers[c.date]["trigger"],
        )
        out.append(dataclasses.replace(
            c,
            bar_idx=prev_idx,
            bar_ts=bars.rows[prev_idx]["timestamp"],
            entry=triggers[c.date]["trigger"],
            extra=extra,
        ))
    return out


def direction_stats(rows):
    out = {}
    for direction in ("LONG", "SHORT"):
        vals = [
            float(r["net"]) for r in rows
            if r.get("status") == "RESOLVED" and r["cand"].direction == direction
        ]
        out[direction] = {
            "n": len(vals),
            "net": round(sum(vals), 2),
            "wins": sum(v > 0 for v in vals),
            "losses": sum(v < 0 for v in vals),
        }
    return out


def enrich(rows, summary, model, originals, bars):
    out = dict(summary)
    out["expectancy_per_candidate"] = round(float(out["net"]) / EXPECTED_N, 2)
    out["direction"] = direction_stats(rows)
    out["same_trigger_bar_resolutions"] = 0
    out["same_trigger_bar_both_stop_target"] = 0
    if model == "prearmed_touch":
        for r in rows:
            c = r["cand"]
            orig = originals[c.date]
            if r.get("status") == "RESOLVED" and r.get("exit_idx") == orig.bar_idx:
                out["same_trigger_bar_resolutions"] += 1
            if r.get("status") in {"RESOLVED", "UNRESOLVED", "OPEN"}:
                b = bars.rows[orig.bar_idx]
                stop_hit = (
                    float(b["low"]) <= orig.stop if orig.direction == "LONG"
                    else float(b["high"]) >= orig.stop
                )
                target_hit = (
                    float(b["high"]) >= orig.target if orig.direction == "LONG"
                    else float(b["low"]) <= orig.target
                )
                if stop_hit and target_hit:
                    out["same_trigger_bar_both_stop_target"] += 1
    return out


def detachment(candidates, triggers, bars):
    vals = []
    rows = []
    for c in candidates:
        trig = float(triggers[c.date]["trigger"])
        close = float(bars.rows[c.bar_idx]["close"])
        adverse = max(0.0, close - trig) / TICK if c.direction == "LONG" else max(0.0, trig - close) / TICK
        absolute = abs(close - trig) / TICK
        vals.append(adverse)
        rows.append({
            "date": c.date,
            "direction": c.direction,
            "trigger": trig,
            "decision_close": close,
            "adverse_detachment_ticks": round(adverse, 3),
            "absolute_detachment_ticks": round(absolute, 3),
        })
    s = sorted(vals)
    p90 = s[min(len(s)-1, math.ceil(0.90 * len(s)) - 1)]
    return {
        "n": len(vals),
        "median_adverse_ticks": round(statistics.median(vals), 3),
        "p90_adverse_ticks": round(p90, 3),
        "max_adverse_ticks": round(max(vals), 3),
        "count_over_32_ticks": sum(v > IOC_TOLERANCE for v in vals),
        "share_over_32_ticks": round(sum(v > IOC_TOLERANCE for v in vals) / len(vals), 4),
        "rows": rows,
    }


def strip_row(row):
    return {k: v for k, v in row.items() if k != "cand"}


def ledger(candidates, rows_by_model, triggers, bars):
    maps = {name: {r["cand"].date: r for r in rows} for name, rows in rows_by_model.items()}
    out = []
    for c in candidates:
        b = bars.rows[c.bar_idx]
        item = {
            "date": c.date,
            "direction": c.direction,
            "trigger": float(triggers[c.date]["trigger"]),
            "stop": c.stop,
            "target": c.target,
            "trigger_bar_ts": b["timestamp"],
            "trigger_bar_open": float(b["open"]),
            "trigger_bar_high": float(b["high"]),
            "trigger_bar_low": float(b["low"]),
            "trigger_bar_close": float(b["close"]),
            "models": {},
        }
        for name, mp in maps.items():
            item["models"][name] = strip_row(mp[c.date])
        out.append(item)
    return out


def classify(pre3):
    if pre3["net"] <= 0:
        return "TIMING EDGE DOES NOT SURVIVE"
    if pre3["h1_net"] > 0 and pre3["h2_net"] > 0:
        return "TIMING EDGE SURVIVES / PROMISING BUT UNPROVEN"
    return "TIMING RESULT UNSTABLE / WAIT"


def run(research_15m: Path, audit_5m: Path):
    candidates, triggers, bars = crosscheck(research_15m, audit_5m)
    boundary = _boundary(candidates)
    originals = {c.date: c for c in candidates}
    prearmed = make_prearmed(candidates, triggers, bars)

    models = {"plan": {}, "ioc_close": {}, "prearmed_touch": {}}
    ledgers = {}
    for slip in (1.0, 2.0, 3.0):
        skey = f"{int(slip)}tick"
        plan_rows = run_bracket_stage(
            LANES["322_mnq"], bars, candidates,
            fill_model="market", slippage_ticks=slip, tolerance_ticks=IOC_TOLERANCE
        )
        ioc_rows = run_bracket_stage(
            LANES["322_mnq"], bars, candidates,
            fill_model="ioc_limit", slippage_ticks=slip, tolerance_ticks=IOC_TOLERANCE
        )
        pre_rows = run_bracket_stage(
            LANES["322_mnq"], bars, prearmed,
            fill_model="stop_market", slippage_ticks=slip, tolerance_ticks=IOC_TOLERANCE
        )
        for name, rows in (
            ("plan", plan_rows),
            ("ioc_close", ioc_rows),
            ("prearmed_touch", pre_rows),
        ):
            summary = bracket_summary(rows, boundary)
            models[name][skey] = enrich(rows, summary, name, originals, bars)
        if slip == 1.0:
            check_repro(models["plan"][skey], models["ioc_close"][skey])
        ledgers[skey] = ledger(
            candidates,
            {"plan": plan_rows, "ioc_close": ioc_rows, "prearmed_touch": pre_rows},
            triggers,
            bars,
        )

    result = {
        "status": "AUDIT_ONLY",
        "prereg_commit": PREREG_COMMIT,
        "population": {
            "candidate_count": len(candidates),
            "first_date": candidates[0].date,
            "last_date": candidates[-1].date,
            "direction_counts": dict(Counter(c.direction for c in candidates)),
            "walk_forward_boundary": boundary,
            "research_15m_tree_sha256": tree_sha(research_15m / "MNQ"),
            "audit_5m_tree_sha256": tree_sha(audit_5m / "MNQ"),
        },
        "accounting": {
            "commission_round_trip": 1.48,
            "tick_size": TICK,
            "point_value": 2.0,
            "ioc_tolerance_ticks": IOC_TOLERANCE,
            "slippage_ticks": [1, 2, 3],
        },
        "detachment": detachment(candidates, triggers, bars),
        "models": models,
        "classification": classify(models["prearmed_touch"]["3tick"]),
        "ledger": ledgers,
    }
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--research-15m", type=Path, required=True)
    ap.add_argument("--audit-5m", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    args = ap.parse_args()
    result = run(args.research_15m, args.audit_5m)
    args.json_out.write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(json.dumps({
        "classification": result["classification"],
        "population": result["population"],
        "detachment": {k:v for k,v in result["detachment"].items() if k != "rows"},
        "plan_1tick": result["models"]["plan"]["1tick"],
        "ioc_1tick": result["models"]["ioc_close"]["1tick"],
        "prearmed_1tick": result["models"]["prearmed_touch"]["1tick"],
        "prearmed_3tick": result["models"]["prearmed_touch"]["3tick"],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
