#!/usr/bin/env python3
"""Preregistered ORB false-break entry-architecture A/B.

Research only. No runtime strategy/risk/broker imports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from scripts.r5_entry_conditioning_reproduction import (
        HORIZON,
        barrier_result,
        current_bracket,
        verify_and_load_candidates,
        verify_and_load_corpus,
    )
except ModuleNotFoundError:
    from r5_entry_conditioning_reproduction import (
        HORIZON,
        barrier_result,
        current_bracket,
        verify_and_load_candidates,
        verify_and_load_corpus,
    )

FAMILY = "orb_false_break_fade"
TICK = {"MNQ": 0.25, "MES": 0.25}
EXPECTED = {
    "MNQ": {"n": 1491, "baseline_gross_r_all": 0.011066398390342052},
    "MES": {"n": 1550, "baseline_gross_r_all": -0.04161290322580645},
}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()
def _valid_bracket(direction: str, fill: float, stop: float, target: float) -> bool:
    if direction == "LONG":
        return stop < fill < target
    return target < fill < stop


def _signal_close_fill(direction: str, close: float, tick: float, adverse_ticks: int) -> float:
    delta = tick * adverse_ticks
    return close + delta if direction == "LONG" else close - delta


def _signal_close_result(
    candidate: dict[str, Any],
    bars: list[dict[str, Any]],
    signal_index: int,
    tick: float,
    adverse_ticks: int,
) -> tuple[float, bool, str, float]:
    direction = candidate["direction"]
    stop = float(candidate["stop"])
    target = float(candidate["target"])
    close = float(bars[signal_index]["close"])
    fill = _signal_close_fill(direction, close, tick, adverse_ticks)

    if not _valid_bracket(direction, fill, stop, target):
        return 0.0, False, "invalid_at_entry", fill

    risk = abs(fill - stop)
    if risk <= 0:
        return 0.0, False, "invalid_at_entry", fill

    window = bars[signal_index + 1 : signal_index + 1 + HORIZON]
    outcome = barrier_result(window, direction, target, stop)
    if outcome == "bad":
        return -1.0, True, "stop", fill
    if outcome == "good":
        return abs(target - fill) / risk, True, "target", fill
    return 0.0, True, "unresolved", fill
def _summarize(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    n = len(rows)
    fills = sum(bool(r[f"{arm}_filled"]) for r in rows)
    gross = [float(r[f"{arm}_gross_r"]) for r in rows]
    midpoint = n // 2

    def mean_for(sub: list[dict[str, Any]]) -> float:
        return sum(float(r[f"{arm}_gross_r"]) for r in sub) / len(sub) if sub else 0.0

    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[str(r[f"{arm}_outcome"])] += 1

    by_direction = {}
    for direction in ("LONG", "SHORT"):
        subset = [r for r in rows if r["direction"] == direction]
        by_direction[direction] = {"n": len(subset), "gross_r_all": mean_for(subset)}

    by_session = {}
    for session in ("london", "new_york"):
        subset = [r for r in rows if r["session"] == session]
        by_session[session] = {"n": len(subset), "gross_r_all": mean_for(subset)}

    return {
        "n": n,
        "filled_or_admissible": fills,
        "fill_rate": fills / n,
        "outcomes": dict(sorted(counts.items())),
        "gross_r_all": sum(gross) / n,
        "gross_r_filled": sum(gross) / fills if fills else 0.0,
        "h1_gross_r_all": mean_for(rows[:midpoint]),
        "h2_gross_r_all": mean_for(rows[midpoint:]),
        "by_direction": by_direction,
        "by_session": by_session,
    }
def analyze(
    instrument: str,
    bars: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    idx = {bar["timestamp"]: i for i, bar in enumerate(bars)}
    selected = [c for c in candidates if c.get("family") == FAMILY]
    selected.sort(key=lambda c: c["bar_ts"])
    if len(selected) != EXPECTED[instrument]["n"]:
        raise RuntimeError(f"{instrument} ORB population drift: {len(selected)}")

    rows: list[dict[str, Any]] = []
    for candidate in selected:
        signal_index = idx.get(candidate["bar_ts"])
        if signal_index is None:
            raise RuntimeError(f"missing signal bar: {candidate['candidate_key']}")

        a_gross, a_fill_idx, a_outcome = current_bracket(candidate, bars, signal_index)
        row = {
            "candidate_key": candidate["candidate_key"],
            "bar_ts": candidate["bar_ts"],
            "direction": candidate["direction"],
            "session": candidate["session"],
            "baseline_gross_r": a_gross,
            "baseline_filled": a_fill_idx is not None,
            "baseline_outcome": a_outcome,
        }

        for slip in (0, 1, 2, 3):
            gross, filled, outcome, fill = _signal_close_result(
                candidate, bars, signal_index, TICK[instrument], slip
            )
            prefix = "signal_close" if slip == 0 else f"signal_close_{slip}t"
            row[f"{prefix}_gross_r"] = gross
            row[f"{prefix}_filled"] = filled
            row[f"{prefix}_outcome"] = outcome
            row[f"{prefix}_fill"] = fill
        rows.append(row)
    baseline = _summarize(rows, "baseline")
    if abs(baseline["gross_r_all"] - EXPECTED[instrument]["baseline_gross_r_all"]) > 1e-12:
        raise RuntimeError(f"{instrument} baseline reproduction drift")

    signal = _summarize(rows, "signal_close")
    stress = {str(s): _summarize(rows, f"signal_close_{s}t") for s in (1, 2, 3)}
    return {
        "baseline_resting_retouch": baseline,
        "signal_close": signal,
        "signal_close_adverse_tick_stress": stress,
        "delta_gross_r_all": signal["gross_r_all"] - baseline["gross_r_all"],
    }


def classify(results: dict[str, Any]) -> tuple[str, dict[str, bool]]:
    mnq = results["MNQ"]
    mes = results["MES"]
    checks = {
        "signal_close_positive_both": (
            mnq["signal_close"]["gross_r_all"] > 0
            and mes["signal_close"]["gross_r_all"] > 0
        ),
        "improves_both": (
            mnq["delta_gross_r_all"] > 0 and mes["delta_gross_r_all"] > 0
        ),
        "mnq_halves_positive": (
            mnq["signal_close"]["h1_gross_r_all"] > 0
            and mnq["signal_close"]["h2_gross_r_all"] > 0
        ),
        "mes_halves_positive": (
            mes["signal_close"]["h1_gross_r_all"] > 0
            and mes["signal_close"]["h2_gross_r_all"] > 0
        ),
        "one_tick_positive_both": (
            mnq["signal_close_adverse_tick_stress"]["1"]["gross_r_all"] > 0
            and mes["signal_close_adverse_tick_stress"]["1"]["gross_r_all"] > 0
        ),
    }
    verdict = (
        "ENTRY_ARCHITECTURE_SIGNAL_SUPPORTED"
        if all(checks.values())
        else "NO_RUNTIME_CHANGE / MIXED_OR_UNSUPPORTED"
    )
    return verdict, checks
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    results = {}
    for instrument in ("MNQ", "MES"):
        bars, _ = verify_and_load_corpus(args.corpus_root, instrument)
        candidates = verify_and_load_candidates(args.candidate_root, instrument)
        results[instrument] = analyze(instrument, bars, candidates)

    verdict, checks = classify(results)
    payload = {
        "schema": "orb-false-break-entry-architecture-ab-v1",
        "classification": verdict,
        "pre_registered_checks": checks,
        "population": {"family": FAMILY, "MNQ": 1491, "MES": 1550},
        "arm_a": "current resting retouch at original entry; fixed stop/target",
        "arm_b": "completed signal-bar close; fixed original stop/target",
        "signal_bar_reused_for_resolution": False,
        "same_bar_ambiguity": "STOP_FIRST",
        "horizon_bars": HORIZON,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    args.output.write_text(text, encoding="utf-8")
    print(json.dumps({
        "classification": verdict,
        "checks": checks,
        "output": str(args.output),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
    }, indent=2))


if __name__ == "__main__":
    main()
