#!/usr/bin/env python3
"""Would the inverse ORB lane have an edge if the bracket were recomputed from
the ACTUAL fill instead of the stale nominal entry?

Research only. No strategy, risk, replay, broker, config, runtime or deployment
change. Nothing here imports or mutates runtime code.

CONTEXT. The canonical 63-arm baseline's whole profit sits in fills the
production post-fill validator rejects: the marketable tolerance bounds only the
ADVERSE side, so a stale ORB level fills arbitrarily far on the favourable side
while the static stop and target are still anchored to the nominal entry. The
stop then sits between the fill and the target and books a gain. See
docs/inverse-orb-baseline-post-fill-decomposition-2026-09-08.md.

There are two possible responses, and they are different systems:

  REFUSE     -- do not open a position whose fill is already past its own
                bracket. Shipped as the PaperBroker parity guard; it is what the
                live Tradovate leg already does via post-fill validation.
  RECOMPUTE  -- keep the trade, but translate the bracket onto the actual fill,
                preserving the stop and target DISTANCES (and therefore the
                R:R). No venue does this today. This script measures it.

METHOD. For each filled arm, take the same entry the frozen proof used
(fill_market + one adverse tick on the market leg), then walk the same 5-minute
bars forward under the same pessimistic same-bar rule (stop wins ties), the same
five-session forward window and the same $1.48 round-trip commission as
scripts/inverse_orb_ioc_tolerance_sweep.py -- so the three arms below are
numerically comparable.

CONTROL FIRST. The script re-resolves every filled arm against its ORIGINAL
bracket and checks that it reproduces the frozen proof's own recorded exit
reason and net. Treatment numbers are only reported if that control agrees; a
disagreement means the replay machinery -- not the recomputed bracket -- is what
changed, and the run fails loudly instead of publishing a number.

PREREQUISITE. Needs the local 5-minute bar corpus at data/replay_polygon_5m/MNQ,
which is gitignored and therefore absent from a fresh clone. The committed JSON
artifact is the reproducible record; regenerating it requires the corpus.

Run: PYTHONPATH=. python3 scripts/inverse_orb_recomputed_bracket_study.py
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from datetime import datetime

TICK = 0.25
MULT = 2.0          # MNQ: $2 per index point
COMMISSION = 1.48   # round trip
DATA = "data/replay_polygon_5m/MNQ"
PROOF = "scripts/inverse_orb_canonical_ioc_proof_2026-09-07.json"
OUT = "scripts/inverse_orb_recomputed_bracket_study_2026-09-08.json"

_bars_cache: dict[str, list] = {}


def bars_for(date: str) -> list:
    if date not in _bars_cache:
        path = f"{DATA}/MNQ_{date}.jsonl"
        rows = []
        if os.path.exists(path):
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        rows.sort(key=lambda b: b["timestamp"])
        _bars_cache[date] = rows
    return _bars_cache[date]


def ts(value) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))


def entry_price(row: dict) -> float:
    """The frozen proof's own entry: the fill plus one adverse tick (market leg)."""
    return row["fill_market"] + (TICK if row["inverse_direction"] == "LONG" else -TICK)


def original_bracket(row: dict) -> tuple[float, float]:
    return row["inverse_stop"], row["inverse_target"]


def recomputed_bracket(row: dict) -> tuple[float, float]:
    """Translate the bracket onto the actual fill, preserving both distances.

    The inverse order's planned entry is the source entry (the mirror keeps the
    entry and mirrors stop/target around it), so the stop and target offsets are
    measured from there and re-applied to the price actually filled. Distances --
    and therefore R:R and the max-stop-ticks posture -- are unchanged; only the
    anchor moves.
    """
    stop, target = original_bracket(row)
    anchor = row["source_entry"]
    fill = entry_price(row)
    return fill + (stop - anchor), fill + (target - anchor)


def resolve(row: dict, stop: float, target: float, forward_days: int = 5):
    """Walk bars from fill_ts; pessimistic same-bar (stop wins ties)."""
    direction = row["inverse_direction"]
    entry = entry_price(row)
    start = ts(row["fill_ts"])
    dates = [row["date"]]
    if forward_days > 1:
        all_dates = sorted(os.path.basename(p)[4:-6] for p in glob.glob(f"{DATA}/MNQ_*.jsonl"))
        if not all_dates:
            raise RuntimeError(
                f"no bar files under {DATA} -- that path is gitignored and is not in a "
                "fresh clone. Restore the local replay corpus to re-run this study.")
        if row["date"] not in all_dates:
            raise RuntimeError(f"no bar file for {row['date']} under {DATA}")
        index = all_dates.index(row["date"])
        dates = all_dates[index:index + forward_days]
    for date in dates:
        for bar in bars_for(date):
            if ts(bar["timestamp"]) < start:
                continue
            high, low = bar["high"], bar["low"]
            hit_stop = (low <= stop) if direction == "LONG" else (high >= stop)
            hit_target = (high >= target) if direction == "LONG" else (low <= target)
            if hit_stop:
                px = stop - TICK if direction == "LONG" else stop + TICK  # market leg slippage
                gross = (px - entry) * MULT if direction == "LONG" else (entry - px) * MULT
                return "STOP_HIT", bar["timestamp"], round(gross, 2)
            if hit_target:  # limit exit, no slippage
                gross = (target - entry) * MULT if direction == "LONG" else (entry - target) * MULT
                return "TARGET_HIT", bar["timestamp"], round(gross, 2)
    return None, None, None


def bracket_valid_at_fill(row: dict) -> bool:
    """The production rule: the fill must sit inside its own bracket."""
    stop, target = original_bracket(row)
    fill = entry_price(row)
    if row["inverse_direction"] == "LONG":
        return stop < fill < target
    return target < fill < stop


def metrics(nets: list[float]) -> dict:
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    return {
        "n": len(nets),
        "net": round(sum(nets), 2),
        "wins": len(wins),
        "losses": len(losses),
        "profit_factor": (round(gross_win / gross_loss, 2) if gross_loss else None),
    }


def halves(rows: list[dict], key="net") -> dict:
    if not rows:
        return {"h1": None, "h2": None}
    dates = sorted(r["date"] for r in rows)
    median = dates[len(dates) // 2]
    h1 = [r[key] for r in rows if r["date"] < median]
    h2 = [r[key] for r in rows if r["date"] >= median]
    return {"h1": round(sum(h1), 2), "h2": round(sum(h2), 2),
            "median_date": median, "h1_n": len(h1), "h2_n": len(h2)}


def sessions(rows: list[dict], key="net") -> dict:
    out: dict[str, float] = {}
    for row in rows:
        out[row["session"]] = round(out.get(row["session"], 0.0) + row[key], 2)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=OUT)
    args = parser.parse_args()

    proof = json.load(open(PROOF))
    filled = [r for r in proof["rows"] if r["status"] == "FILLED"]

    # ── Control: reproduce the frozen result with the ORIGINAL bracket ────────
    control_rows, disagreements = [], []
    for row in filled:
        stop, target = original_bracket(row)
        reason, _exit_ts, gross = resolve(row, stop, target)
        net = None if gross is None else round(gross - COMMISSION, 2)
        control_rows.append({**row, "replayed_reason": reason, "replayed_net": net})
        if reason != row["exit_reason"] or net is None or abs(net - row["net"]) > 0.01:
            disagreements.append({
                "date": row["date"], "recorded_reason": row["exit_reason"],
                "replayed_reason": reason, "recorded_net": row["net"], "replayed_net": net,
            })

    if disagreements:
        print(f"CONTROL FAILED: {len(disagreements)} of {len(filled)} arms do not reproduce.")
        for item in disagreements[:5]:
            print(" ", item)
        print("Refusing to report treatment numbers -- the replay machinery is what changed.")
        return 2

    # ── Treatment: the SAME arms with the bracket translated onto the fill ────
    treatment_rows = []
    for row in filled:
        stop, target = recomputed_bracket(row)
        reason, _exit_ts, gross = resolve(row, stop, target)
        treatment_rows.append({
            "date": row["date"], "session": row["session"],
            "direction": row["inverse_direction"],
            "source_entry": row["source_entry"], "fill": entry_price(row),
            "original_stop": row["inverse_stop"], "original_target": row["inverse_target"],
            "recomputed_stop": round(stop, 4), "recomputed_target": round(target, 4),
            "valid_at_fill_originally": bracket_valid_at_fill(row),
            "baseline_reason": row["exit_reason"], "baseline_net": row["net"],
            "reason": reason if reason else "UNRESOLVED",
            "net": None if gross is None else round(gross - COMMISSION, 2),
        })

    unresolved = [r for r in treatment_rows if r["net"] is None]
    resolved = [r for r in treatment_rows if r["net"] is not None]
    admissible = [r for r in treatment_rows if r["valid_at_fill_originally"]]
    rejected = [r for r in treatment_rows if not r["valid_at_fill_originally"]]

    report = {
        "manifest": {
            "question": "Does translating the bracket onto the actual fill give the lane an edge?",
            "population": f"{PROOF} -- the frozen 63-arm canonical proof",
            "filled_arms": len(filled),
            "method": "same entry, same bars, same pessimistic same-bar rule, same 5-session "
                      "forward window, same $1.48 round-trip commission as the IOC tolerance sweep",
            "recompute_rule": "stop/target distances from the nominal entry, re-anchored on the fill "
                              "(R:R preserved, only the anchor moves)",
            "control": f"all {len(filled)} filled arms reproduce their recorded exit reason and net "
                       "under the ORIGINAL bracket -- 0 disagreements",
            "data": DATA,
            "reproducibility": "requires the gitignored local bar corpus; absent from a fresh clone",
            "not_a_venue_behavior": "no broker recomputes a bracket from the fill today; this is a "
                                    "hypothetical, not a description of any deployed path",
        },
        "baseline_as_recorded": {
            **metrics([r["net"] for r in filled]),
            "halves": halves(filled), "sessions": sessions(filled),
        },
        "refuse_admissible_only": {
            **metrics([r["baseline_net"] for r in admissible]),
            "halves": halves([{"date": r["date"], "net": r["baseline_net"]} for r in admissible]),
            "sessions": sessions([{"session": r["session"], "net": r["baseline_net"]} for r in admissible]),
            "note": "what the shipped PaperBroker guard leaves: the arms whose fill was inside its bracket",
        },
        "recomputed_bracket": {
            **metrics([r["net"] for r in resolved]),
            "unresolved": len(unresolved),
            "halves": halves(resolved), "sessions": sessions(resolved),
            "exit_reasons": {reason: sum(1 for r in resolved if r["reason"] == reason)
                             for reason in sorted({r["reason"] for r in resolved})},
        },
        "recomputed_split_by_original_admissibility": {
            "originally_rejected": metrics([r["net"] for r in rejected if r["net"] is not None]),
            "originally_admitted": metrics([r["net"] for r in admissible if r["net"] is not None]),
        },
        "detachment_ticks": {
            "median": round(statistics.median(
                abs(r["fill"] - r["source_entry"]) / TICK for r in treatment_rows), 1),
            "max": round(max(abs(r["fill"] - r["source_entry"]) / TICK for r in treatment_rows), 1),
        },
        "rows": treatment_rows,
    }

    with open(args.output, "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")

    print(f"control: {len(filled)}/{len(filled)} arms reproduce the frozen result (0 disagreements)")
    for label in ("baseline_as_recorded", "refuse_admissible_only", "recomputed_bracket"):
        block = report[label]
        print(f"{label:26} n={block['n']:>3}  net={block['net']:>10}  "
              f"PF={block['profit_factor']}  W/L={block['wins']}/{block['losses']}  "
              f"H1={block['halves']['h1']} H2={block['halves']['h2']}")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
