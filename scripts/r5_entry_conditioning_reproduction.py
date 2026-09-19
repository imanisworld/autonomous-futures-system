#!/usr/bin/env python3
"""Deterministic reproduction of the 2026-09-18 R5 entry-conditioning audit.

Research only. It consumes the sealed R5 candidate population and exact
replay_polygon_v2 15m corpus. It does not import runtime strategy/risk/broker
code and cannot place or simulate external orders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

HORIZON = 16
EXPECTED = {
    "MNQ": {
        "candidate_sha256": "148768cd9a7b9b6f48d003d91cdf73b757b3018dca1343dc9a56b7e4f91c1a7a",
        "candidate_rows": 40460,
        "manifest_sha256": "1f16b81b232f0275753c295bbca4686dcc675eec6f0b488cff3099693103d8ac",
        "corpus_rows": 40907,
        "corpus_files": 543,
    },
    "MES": {
        "candidate_sha256": "e50fa5525422690ceff69a4a115c78517eb9cdec51f8dee5ee004733045a7ebe",
        "candidate_rows": 39987,
        "manifest_sha256": "ca4481502b4f9be33de6bdb611c02ac462c36eab1b759a0e62dab45c10594de3",
        "corpus_rows": 40907,
        "corpus_files": 543,
    },
}

EXPECTED_HEADLINES = {
    "MNQ": {
        "mfe_r": 2.844,
        "mae_r": 2.778,
        "gross": {
            "impulse_first_pullback_observed": -0.174,
            "trend_consolidation_break_observed": -0.151,
            "orb_false_break_fade": 0.011,
            "strat_22_continuation_observed": -0.076,
            "strat_22_reversal_observed": -0.067,
            "ema_pullback_trend": -0.066,
            "strat_322_reversal_observed": -0.130,
            "strat_122_observed": -0.143,
            "strat_312_observed": -0.163,
            "transition_failed_breakdown_reclaim": -0.047,
            "strat_4hr_retrigger_observed": 0.159,
            "strat_122_pullback": -0.053,
        },
        "four_hr_halves": [0.259, 0.059],
    },
    "MES": {
        "mfe_r": 2.348,
        "mae_r": 2.442,
        "gross": {
            "impulse_first_pullback_observed": -0.208,
            "trend_consolidation_break_observed": -0.200,
            "orb_false_break_fade": -0.042,
            "strat_22_continuation_observed": -0.094,
            "strat_22_reversal_observed": -0.090,
            "ema_pullback_trend": -0.095,
            "strat_322_reversal_observed": -0.111,
            "strat_122_observed": -0.156,
            "strat_312_observed": -0.110,
            "transition_failed_breakdown_reclaim": -0.034,
            "strat_4hr_retrigger_observed": 0.026,
            "strat_122_pullback": 0.090,
        },
        "four_hr_halves": [-0.059, 0.109],
    },
}


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def verify_and_load_corpus(root: Path, instrument: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cfg = EXPECTED[instrument]
    directory = root / instrument
    manifest_path = directory / "MANIFEST.json"
    manifest_hash = sha256_path(manifest_path)
    if manifest_hash != cfg["manifest_sha256"]:
        raise RuntimeError(f"{instrument} manifest SHA mismatch: {manifest_hash}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["coverage"]["rows"] != cfg["corpus_rows"]:
        raise RuntimeError(f"{instrument} corpus row-count mismatch")
    if manifest["coverage"]["files"] != cfg["corpus_files"]:
        raise RuntimeError(f"{instrument} corpus file-count mismatch")

    bars: list[dict[str, Any]] = []
    for name, meta in manifest["files"].items():
        path = directory / name
        got = sha256_path(path)
        if got != meta["sha256"]:
            raise RuntimeError(f"{instrument} corpus file SHA mismatch: {name}")
        rows = load_jsonl(path)
        if len(rows) != meta["rows"]:
            raise RuntimeError(f"{instrument} corpus file row mismatch: {name}")
        bars.extend(rows)

    if len(bars) != cfg["corpus_rows"]:
        raise RuntimeError(f"{instrument} loaded corpus row-count mismatch")
    return bars, manifest


def verify_and_load_candidates(root: Path, instrument: str) -> list[dict[str, Any]]:
    cfg = EXPECTED[instrument]
    path = root / instrument / "candidates.jsonl"
    got = sha256_path(path)
    if got != cfg["candidate_sha256"]:
        raise RuntimeError(f"{instrument} candidate SHA mismatch: {got}")
    rows = load_jsonl(path)
    if len(rows) != cfg["candidate_rows"]:
        raise RuntimeError(f"{instrument} candidate row-count mismatch")
    return rows


def touches(bar: dict[str, Any], level: float) -> bool:
    return float(bar["low"]) <= level <= float(bar["high"])


def barrier_result(
    bars: list[dict[str, Any]],
    direction: str,
    good: float,
    bad: float,
) -> str:
    for bar in bars:
        if direction == "LONG":
            bad_hit = float(bar["low"]) <= bad
            good_hit = float(bar["high"]) >= good
        else:
            bad_hit = float(bar["high"]) >= bad
            good_hit = float(bar["low"]) <= good
        if bad_hit:
            return "bad"
        if good_hit:
            return "good"
    return "none"


def current_bracket(
    candidate: dict[str, Any],
    bars: list[dict[str, Any]],
    signal_index: int,
) -> tuple[float, int | None, str]:
    entry = float(candidate["entry"])
    stop = float(candidate["stop"])
    target = float(candidate["target"])
    risk = abs(entry - stop)
    if risk <= 0:
        raise RuntimeError(f"non-positive risk: {candidate['candidate_key']}")

    fill_index = None
    for idx in range(signal_index + 1, min(len(bars), signal_index + 1 + HORIZON)):
        if touches(bars[idx], entry):
            fill_index = idx
            break
    if fill_index is None:
        return 0.0, None, "no_fill"

    window = bars[fill_index : fill_index + HORIZON]
    outcome = barrier_result(window, candidate["direction"], target, stop)
    if outcome == "bad":
        return -1.0, fill_index, "stop"
    if outcome == "good":
        return abs(target - entry) / risk, fill_index, "target"
    return 0.0, fill_index, "unresolved"


def analyze_instrument(
    instrument: str,
    bars: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    bar_index = {bar["timestamp"]: idx for idx, bar in enumerate(bars)}
    family_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mfe_values: list[float] = []
    mae_values: list[float] = []
    raw_current = {"stop": 0, "target": 0, "unresolved": 0}

    for candidate in candidates:
        signal_index = bar_index.get(candidate["bar_ts"])
        if signal_index is None:
            raise RuntimeError(f"candidate timestamp missing from corpus: {candidate['candidate_key']}")
        future = bars[signal_index + 1 : signal_index + 1 + HORIZON]
        entry = float(candidate["entry"])
        stop = float(candidate["stop"])
        target = float(candidate["target"])
        risk = abs(entry - stop)
        if risk <= 0:
            raise RuntimeError(f"non-positive risk: {candidate['candidate_key']}")

        if future:
            if candidate["direction"] == "LONG":
                mfe_values.append((max(float(b["high"]) for b in future) - entry) / risk)
                mae_values.append((entry - min(float(b["low"]) for b in future)) / risk)
            else:
                mfe_values.append((entry - min(float(b["low"]) for b in future)) / risk)
                mae_values.append((max(float(b["high"]) for b in future) - entry) / risk)

        raw = barrier_result(future, candidate["direction"], target, stop)
        raw_current[{"bad": "stop", "good": "target", "none": "unresolved"}[raw]] += 1

        gross_r, fill_index, bracket_outcome = current_bracket(candidate, bars, signal_index)

        pre_good = entry + risk if candidate["direction"] == "LONG" else entry - risk
        pre_bad = entry - risk if candidate["direction"] == "LONG" else entry + risk
        symmetric_pre = barrier_result(future, candidate["direction"], pre_good, pre_bad)

        if fill_index is None:
            symmetric_post = "no_fill"
        else:
            symmetric_post = barrier_result(
                bars[fill_index : fill_index + HORIZON],
                candidate["direction"],
                pre_good,
                pre_bad,
            )

        family_rows[candidate["family"]].append(
            {
                "bar_ts": candidate["bar_ts"],
                "gross_r": gross_r,
                "filled": fill_index is not None,
                "bracket_outcome": bracket_outcome,
                "symmetric_pre": symmetric_pre,
                "symmetric_post": symmetric_post,
            }
        )

    families: dict[str, Any] = {}
    for family, rows in sorted(family_rows.items()):
        rows.sort(key=lambda row: row["bar_ts"])
        n = len(rows)
        fills = sum(row["filled"] for row in rows)
        gross_all = sum(float(row["gross_r"]) for row in rows) / n
        midpoint = n // 2
        halves = [rows[:midpoint], rows[midpoint:]]
        h_values = [
            sum(float(row["gross_r"]) for row in half) / len(half) if half else 0.0
            for half in halves
        ]

        bracket_counts = {key: sum(row["bracket_outcome"] == key for row in rows) for key in ("target", "stop", "unresolved", "no_fill")}
        pre_counts = {key: sum(row["symmetric_pre"] == key for row in rows) for key in ("good", "bad", "none")}
        post_counts = {key: sum(row["symmetric_post"] == key for row in rows) for key in ("good", "bad", "none", "no_fill")}
        pre_resolved = pre_counts["good"] + pre_counts["bad"]
        post_resolved = post_counts["good"] + post_counts["bad"]

        families[family] = {
            "n": n,
            "fill_count": fills,
            "fill_rate": fills / n,
            "no_fill_rate": 1.0 - fills / n,
            "current_bracket": {
                **bracket_counts,
                "gross_r_all": gross_all,
                "h1_gross_r_all": h_values[0],
                "h2_gross_r_all": h_values[1],
            },
            "symmetric_1r_pre_fill": {
                **pre_counts,
                "good_first_rate_of_resolved": pre_counts["good"] / pre_resolved if pre_resolved else None,
            },
            "symmetric_1r_post_fill": {
                **post_counts,
                "good_first_rate_of_fills": post_counts["good"] / fills if fills else None,
                "good_first_rate_of_resolved": post_counts["good"] / post_resolved if post_resolved else None,
            },
        }

    total = len(candidates)
    return {
        "population": total,
        "excursion_rows_with_future_bars": len(mfe_values),
        "mean_mfe_r": sum(mfe_values) / len(mfe_values),
        "mean_mae_r": sum(mae_values) / len(mae_values),
        "raw_current_bracket_next_16": {
            **raw_current,
            "stop_rate": raw_current["stop"] / total,
            "target_rate": raw_current["target"] / total,
            "unresolved_rate": raw_current["unresolved"] / total,
        },
        "families": families,
    }


def verify_headlines(results: dict[str, Any]) -> None:
    for instrument, expected in EXPECTED_HEADLINES.items():
        got = results[instrument]
        if round(got["mean_mfe_r"], 3) != expected["mfe_r"]:
            raise RuntimeError(f"{instrument} MFE reproduction mismatch")
        if round(got["mean_mae_r"], 3) != expected["mae_r"]:
            raise RuntimeError(f"{instrument} MAE reproduction mismatch")
        for family, value in expected["gross"].items():
            actual = round(got["families"][family]["current_bracket"]["gross_r_all"], 3)
            if actual != value:
                raise RuntimeError(f"{instrument} {family} gross-R reproduction mismatch: {actual} != {value}")
        four_hr = got["families"]["strat_4hr_retrigger_observed"]["current_bracket"]
        halves = [round(four_hr["h1_gross_r_all"], 3), round(four_hr["h2_gross_r_all"], 3)]
        if halves != expected["four_hr_halves"]:
            raise RuntimeError(f"{instrument} 4HR half-split reproduction mismatch: {halves}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-root", type=Path, default=Path("data/replay_polygon_v2"))
    parser.add_argument(
        "--candidate-root",
        type=Path,
        default=Path("logs/structural_level_r5_2026_09_17/structural_level_r5"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    results: dict[str, Any] = {}
    inputs: dict[str, Any] = {}
    for instrument in ("MNQ", "MES"):
        bars, manifest = verify_and_load_corpus(args.corpus_root, instrument)
        candidates = verify_and_load_candidates(args.candidate_root, instrument)
        results[instrument] = analyze_instrument(instrument, bars, candidates)
        inputs[instrument] = {
            **EXPECTED[instrument],
            "gap_runs": len(manifest["gap_ledger_cme_hours"]),
        }

    verify_headlines(results)
    payload = {
        "schema": "r5-entry-conditioning-reproduction-v1",
        "classification": "AUDIT_ONLY",
        "method": {
            "signal_bar_included": False,
            "entry_search_bars": HORIZON,
            "fill_requires_entry_inside_bar_range": True,
            "bracket_bars_from_fill_including_fill_bar": HORIZON,
            "same_bar_stop_target": "STOP_FIRST",
            "no_fill_gross_r": 0.0,
            "unresolved_gross_r": 0.0,
            "symmetric_barriers": "entry +/- 1R",
            "half_split": "chronological candidate order, floor(n/2) in H1",
        },
        "inputs": inputs,
        "results": results,
        "headline_reproduction_pass": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "sha256": sha256_path(args.output),
        "headline_reproduction_pass": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
