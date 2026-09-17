#!/usr/bin/env python3
"""P-R — resolver equivalence on SYNTHETIC bars: shadow resolver vs observation-lane resolver.

Structural-level prereg v1.5 §14 (P-R) / C20. MNQ/MES replay outcomes come from
``strategy.shadow_setups.resolve_shadow_candidate``; a collection-only root's prospective
outcomes come from ``execution.cross_instrument_observation._resolve_one`` (the observation
lane's own implementation). Both claim the same rules — resting-entry fill, stop on the fill
bar = LOSS, target-only touch on the fill bar ignored, pessimistic both-hit = LOSS, same-day
window, else NO_FILL / unresolved. This script proves it on a large deterministic synthetic
population (seeded random brackets and random OHLC paths) so that **no real outcome is ever
read**. It is a code-equivalence proof, not evidence about any strategy.

Mapping of terminal states: shadow ``OPEN`` (filled, unresolved at window end) ≡ lane
``None`` (still pending) — both mean "not terminal inside the window".

Usage:
    python3 scripts/structural_level_resolver_equivalence.py --n 20000 --seed 17 --out <report.json>
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from execution.cross_instrument_observation import _resolve_one  # noqa: E402  (pure function)
from strategy.shadow_setups import ShadowSetupCandidate, resolve_shadow_candidate  # noqa: E402

TOOL_VERSION = "slr-equiv-v1.5"


def synth_case(rng: random.Random, instrument: str, tick: float) -> tuple[ShadowSetupCandidate, list[dict]]:
    """A random bracket around a random price with a random 1–40 bar OHLC path whose ranges
    are on the tick grid, so entry/stop/target touches happen at realistic frequencies."""
    px = round(rng.uniform(1000, 30000) / tick) * tick
    direction = rng.choice(("LONG", "SHORT"))
    risk = rng.randint(2, 40) * tick
    rr = rng.choice((1.0, 1.5, 2.0, 2.2, 3.0))
    entry = px
    stop = entry - risk if direction == "LONG" else entry + risk
    target = entry + risk * rr if direction == "LONG" else entry - risk * rr
    cand = ShadowSetupCandidate(strategy="synthetic", direction=direction, entry=entry, stop=round(stop, 6),
                                target=round(target, 6), rr_ratio=rr, risk_tier="B", size_multiplier=1.0, notes="")
    bars: list[dict] = []
    cur = entry + rng.randint(-30, 30) * tick
    for i in range(rng.randint(1, 40)):
        drift = rng.randint(-25, 25) * tick
        rng_w = rng.randint(0, 60) * tick
        o = cur
        c = cur + drift
        hi = max(o, c) + rng.randint(0, 1) * rng_w
        lo = min(o, c) - rng.randint(0, 1) * rng_w
        bars.append({"ts": f"2026-01-01T{i:02d}:00:00+00:00", "open": o, "high": hi, "low": lo, "close": c})
        cur = c
    return cand, bars


def compare(cand: ShadowSetupCandidate, bars: list[dict], instrument: str) -> dict:
    shadow = resolve_shadow_candidate(cand, [(b["high"], b["low"]) for b in bars], instrument=instrument)
    pending = {"record": {"direction": cand.direction, "entry": cand.entry, "stop": cand.stop,
                          "target": cand.target, "instrument": instrument},
               "filled": False, "fill_ts": None, "bars_seen": 0, "mae_points": 0.0, "mfe_points": 0.0}
    lane = _resolve_one(pending, bars)
    lane_result = lane["result"] if lane else ("OPEN" if pending["filled"] else "NO_FILL")
    lane_exit = lane["exit_price"] if lane else None
    lane_bars = pending["bars_seen"] if lane else None
    agree = (shadow.result == lane_result
             and (shadow.exit_price is None or abs(float(shadow.exit_price) - float(lane_exit)) < 1e-9)
             and (shadow.bars_to_exit is None or shadow.bars_to_exit == lane_bars))
    return {"agree": agree, "shadow": shadow.result, "lane": lane_result,
            "shadow_exit": shadow.exit_price, "lane_exit": lane_exit,
            "shadow_bars_to_exit": shadow.bars_to_exit, "lane_bars_seen": lane_bars,
            "ambiguous_fill_bar_target_ignored": shadow.fill_bar_target_ambiguous_ignored}


def run(n: int, seed: int, instruments: tuple[str, ...] = ("MNQ", "MES", "M2K")) -> dict:
    from config.futures_contracts import contract_economics
    rng = random.Random(seed)
    by_result: dict[str, int] = {}
    disagreements: list[dict] = []
    ambiguous = 0
    total = 0
    for k in range(n):
        inst = instruments[k % len(instruments)]
        tick = contract_economics(inst)[0]
        cand, bars = synth_case(rng, inst, tick)
        r = compare(cand, bars, inst)
        total += 1
        by_result[r["shadow"]] = by_result.get(r["shadow"], 0) + 1
        ambiguous += r["ambiguous_fill_bar_target_ignored"]
        if not r["agree"] and len(disagreements) < 20:
            disagreements.append({"instrument": inst, "candidate": cand.to_dict(), "bars": bars, **r})
    return {"tool": TOOL_VERSION, "n": total, "seed": seed, "instruments": list(instruments),
            "by_shadow_result": by_result, "ambiguous_fill_bar_target_cases": ambiguous,
            "disagreements": len(disagreements), "disagreement_examples": disagreements,
            "verdict": "EQUIVALENT" if not disagreements else "NOT_EQUIVALENT"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    rep = run(args.n, args.seed)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
        fh.write("\n")
    print(f"[req] n={rep['n']} results={rep['by_shadow_result']} ambiguous_fill_bar={rep['ambiguous_fill_bar_target_cases']} "
          f"disagreements={rep['disagreements']} → {rep['verdict']}")
    return 0 if rep["verdict"] == "EQUIVALENT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
