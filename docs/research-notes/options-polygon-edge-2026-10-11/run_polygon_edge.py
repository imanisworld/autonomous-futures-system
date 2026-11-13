#!/usr/bin/env python3
"""Score the frozen underlying-edge families on the Polygon 5-minute tape.

The entry, invalidation, target, hold, and dedupe rules are the functions in
docs/research-notes/options-underlying-edge-2026-10-11/run_underlying_edge.py.
This file does not change those thresholds. It loads the verified local bars
and adds exit-R measurement.

Verdict rule:

The comparison baseline uses the same structural stop price as the strategy.
The registered baseline, which places a tighter stop at a fixed pre-entry
distance, is reported and is not the advantage test.

REJECT when the sample is under 30 executed trades, 10 sessions, or 5 symbols,
or when full-sample strategy expectancy per setup is not above both zero and
the same-stop baseline.

TEST FURTHER when that advantage exists, but a robustness check fails:
symbol-equal-weighted expectancy, single-name expectancy, the pre-2026-08-14
slice, gap-aware fills, or the 2026-08-14..2026-09-08 sessions. Those later
sessions overlap the scanner study. They are not validation. A loss there
still means the result is not stable across the available sessions.

STRUCTURALLY PROMISING only when the same-stop advantage and every check
above are positive. Positive here means the strategy expectancy is above zero
and above the same-stop baseline on each required slice.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from alert_ranker.causal_bars import Bar  # noqa: E402

NOTE = Path(__file__).resolve().parent
FROZEN = ROOT / "docs/research-notes/options-underlying-edge-2026-10-11/run_underlying_edge.py"
DATA = ROOT / "data/options_research_polygon_5m_20260401_20260908"
SCANNER_OVERLAP = date(2026, 8, 14)
ETFS = frozenset({
    "SPY", "QQQ", "IWM", "DIA", "SMH", "XLK", "XLF", "XLE", "XBI",
    "HYG", "EEM", "FXI", "KWEB", "TLT", "GLD", "SLV", "IBIT",
})


def load_frozen():
    spec = importlib.util.spec_from_file_location("underlying_edge_v1", FROZEN)
    if spec is None or spec.loader is None:
        raise RuntimeError("frozen runner could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fill(bars, entry_i, direction, structural_stop, decision_close, *, baseline):
    """Frozen walk. baseline=False keeps the structural stop price."""
    edge = walk_with_r.edge
    if entry_i >= len(bars):
        return {"class": "MISSED", "reason": "NO_NEXT_BAR"}
    raw = bars[entry_i].open
    planned = (decision_close - structural_stop) * direction
    if planned <= 0:
        return {"class": "MISSED", "reason": "INVALID_RISK"}
    beyond = decision_close + direction * planned
    if (raw - structural_stop) * direction <= 0:
        return {"class": "MISSED", "reason": "GAP_THROUGH_STOP"}
    if (raw - beyond) * direction >= 0:
        return {"class": "MISSED", "reason": "MOVED_BEYOND_ENTRY"}
    entry = raw + edge.SLIP * direction
    if baseline:
        risk = planned
        stop = entry - direction * planned
    else:
        stop = structural_stop
        risk = (entry - structural_stop) * direction
        if risk <= 0:
            return {"class": "MISSED", "reason": "GAP_THROUGH_STOP"}
    target1 = entry + direction * risk
    target2 = entry + direction * 2 * risk
    mae = 0.0
    mfe = 0.0
    for offset, probe in enumerate(bars[entry_i:], start=1):
        if direction > 0:
            mae = max(mae, (entry - probe.low) / risk)
            mfe = max(mfe, (probe.high - entry) / risk)
            stop_hit = probe.low <= stop
            hit1 = probe.high >= target1
            hit2 = probe.high >= target2
            opened_through_stop = probe.open <= stop
        else:
            mae = max(mae, (probe.high - entry) / risk)
            mfe = max(mfe, (entry - probe.low) / risk)
            stop_hit = probe.high >= stop
            hit1 = probe.low <= target1
            hit2 = probe.low <= target2
            opened_through_stop = probe.open >= stop
        outcome = None
        if stop_hit:
            outcome = "STOP"
        elif hit2:
            outcome = "R2"
        elif hit1:
            outcome = "R1"
        if outcome is None:
            continue
        if outcome == "STOP":
            exit_r = -1.0
            exit_r_gap = (probe.open - entry) * direction / risk if opened_through_stop else -1.0
        elif outcome == "R2":
            exit_r = 2.0
            exit_r_gap = 2.0
        else:
            exit_r = 1.0
            exit_r_gap = 1.0
        return {
            "class": "EXECUTED",
            "result": outcome,
            "bars": offset,
            "mae_r": mae,
            "mfe_r": mfe,
            "risk_price": risk,
            "exit_r": exit_r,
            "exit_r_gap": exit_r_gap,
        }
    last = bars[-1].close - edge.SLIP * direction
    exit_r = (last - entry) * direction / risk
    return {
        "class": "EXECUTED",
        "result": "EOD",
        "bars": len(bars) - entry_i,
        "mae_r": mae,
        "mfe_r": max(mfe, exit_r),
        "risk_price": risk,
        "exit_r": exit_r,
        "exit_r_gap": exit_r,
    }


def walk_with_r(bars, entry_i, direction, structural_stop, decision_close, *, baseline):
    """Registered result, plus the same-stop fill when this call is the baseline."""
    result = _fill(
        bars, entry_i, direction, structural_stop, decision_close, baseline=baseline,
    )
    if not baseline:
        return result
    same = _fill(
        bars, entry_i, direction, structural_stop, decision_close, baseline=False,
    )
    result = dict(result)
    result["same_stop_class"] = same["class"]
    result["same_stop_result"] = same.get("result")
    result["same_stop_exit_r"] = float(same["exit_r"]) if same.get("class") == "EXECUTED" else 0.0
    result["same_stop_exit_r_gap"] = float(same["exit_r_gap"]) if same.get("class") == "EXECUTED" else 0.0
    result["same_stop_risk_price"] = same.get("risk_price")
    return result


def verify_hashes() -> dict:
    manifest = json.loads((DATA / "manifest.json").read_text())
    mismatched = []
    for symbol, digest in manifest["sha256"].items():
        path = DATA / f"{symbol}_5min.csv"
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                hasher.update(chunk)
        if hasher.hexdigest() != digest:
            mismatched.append(symbol)
    if mismatched:
        raise SystemExit(f"hash mismatch: {mismatched}")
    if manifest["requested_date_range"]["end"] >= "2026-09-09":
        raise SystemExit("dataset includes the sealed September week")
    if manifest.get("adjusted") is not True:
        raise SystemExit("dataset is not the adjusted Polygon tape")
    return manifest


def load_bars(symbols: tuple[str, ...]) -> dict[str, list[Bar]]:
    found = {}
    for symbol in symbols:
        rows = []
        with (DATA / f"{symbol}_5min.csv").open(newline="") as handle:
            for record in csv.DictReader(handle):
                rows.append(Bar(
                    start=datetime.fromisoformat(record["timestamp"]),
                    open=float(record["open"]),
                    high=float(record["high"]),
                    low=float(record["low"]),
                    close=float(record["close"]),
                    volume=float(record["volume"]),
                ))
        found[symbol] = rows
    return found


def leg_r(leg: dict, field: str) -> float:
    if leg.get("class") != "EXECUTED":
        return 0.0
    return float(leg[field])


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.fmean(values), 4)


def executed_mean(rows: list[dict], side: str, field: str) -> float | None:
    values = [float(row[side][field]) for row in rows if row[side].get("class") == "EXECUTED"]
    return mean(values)


def setup_mean(rows: list[dict], side: str, field: str) -> float | None:
    if not rows:
        return None
    return mean([leg_r(row[side], field) for row in rows])


def symbol_equal(rows: list[dict], field: str) -> float | None:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row["strategy"].get("class") == "EXECUTED":
            grouped[row["symbol"]].append(float(row["strategy"][field]))
    per_symbol = [statistics.fmean(values) for values in grouped.values() if values]
    return mean(per_symbol)


def positive_symbol_fraction(rows: list[dict]) -> float | None:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row["strategy"].get("class") == "EXECUTED":
            grouped[row["symbol"]].append(float(row["strategy"]["exit_r"]))
    if not grouped:
        return None
    positive = sum(1 for values in grouped.values() if statistics.fmean(values) > 0)
    return round(positive / len(grouped), 3)


def same_stop_mean(rows: list[dict]) -> float | None:
    if not rows:
        return None
    return mean([float(row["baseline"].get("same_stop_exit_r", 0.0)) for row in rows])


def slice_block(rows: list[dict]) -> dict:
    executed = [row for row in rows if row["strategy"].get("class") == "EXECUTED"]
    dollars = [
        float(row["strategy"]["exit_r"]) * float(row["strategy"]["risk_price"])
        for row in executed
    ]
    return {
        "eligible": len(rows),
        "executed": len(executed),
        "symbols": len({row["symbol"] for row in executed}),
        "sessions": len({row["session"] for row in executed}),
        "strategy_expectancy_r": executed_mean(rows, "strategy", "exit_r"),
        "baseline_expectancy_r": executed_mean(rows, "baseline", "exit_r"),
        "strategy_per_setup_r": setup_mean(rows, "strategy", "exit_r"),
        "baseline_per_setup_r": setup_mean(rows, "baseline", "exit_r"),
        "same_stop_baseline_per_setup_r": same_stop_mean(rows),
        "strategy_gap_aware_r": executed_mean(rows, "strategy", "exit_r_gap"),
        "mean_dollar_per_share": mean(dollars),
        "symbol_equal_weight_r": symbol_equal(rows, "exit_r"),
        "symbols_with_positive_mean": positive_symbol_fraction(rows),
    }


def _beats(strategy: float | None, baseline: float | None) -> bool:
    return strategy is not None and baseline is not None and strategy > 0 and strategy > baseline


def decide(full: dict, early: dict, late: dict, singles: dict) -> str:
    enough = full["executed"] >= 30 and full["sessions"] >= 10 and full["symbols"] >= 5
    if not enough or not _beats(full["strategy_per_setup_r"], full["same_stop_baseline_per_setup_r"]):
        return "REJECT"
    checks = (
        full["symbol_equal_weight_r"],
        full["strategy_gap_aware_r"],
        early["strategy_per_setup_r"],
        early["same_stop_baseline_per_setup_r"],
        late["strategy_per_setup_r"],
        late["same_stop_baseline_per_setup_r"],
        singles["strategy_per_setup_r"],
        singles["same_stop_baseline_per_setup_r"],
    )
    if any(value is None for value in checks):
        return "TEST FURTHER"
    robust = (
        full["symbol_equal_weight_r"] > 0
        and full["strategy_gap_aware_r"] > 0
        and _beats(early["strategy_per_setup_r"], early["same_stop_baseline_per_setup_r"])
        and _beats(late["strategy_per_setup_r"], late["same_stop_baseline_per_setup_r"])
        and _beats(singles["strategy_per_setup_r"], singles["same_stop_baseline_per_setup_r"])
    )
    if robust:
        return "STRUCTURALLY PROMISING"
    return "TEST FURTHER"


def main() -> None:
    manifest = verify_hashes()
    edge = load_frozen()
    walk_with_r.edge = edge
    edge.walk = walk_with_r
    if len(edge.SYMBOLS) != 66:
        raise SystemExit(f"universe_count {len(edge.SYMBOLS)}")
    bars = load_bars(edge.SYMBOLS)
    prepared = {}
    incomplete = 0
    for symbol, symbol_bars in bars.items():
        symbol_days, missing = edge.sessions_for(symbol_bars)
        prepared[symbol] = symbol_days
        incomplete += missing
    families = {
        "htf_pullback": edge.htf_pullback,
        "failed_break": edge.failed_break,
        "compression_expansion": edge.compression,
    }
    summaries = {}
    episode_path = NOTE / "episodes.jsonl"
    with episode_path.open("w", encoding="utf-8") as sink:
        for name, function in families.items():
            rows = []
            excluded: Counter = Counter()
            for symbol in edge.SYMBOLS:
                symbol_rows, symbol_excluded = function(symbol, prepared[symbol])
                rows.extend(symbol_rows)
                excluded.update(symbol_excluded)
                for row in symbol_rows:
                    sink.write(json.dumps(row, separators=(",", ":")) + "\n")
            registered = edge.summarize(name, rows, excluded)
            early_rows = [row for row in rows if date.fromisoformat(row["session"]) < SCANNER_OVERLAP]
            late_rows = [row for row in rows if date.fromisoformat(row["session"]) >= SCANNER_OVERLAP]
            single_rows = [row for row in rows if row["symbol"] not in ETFS]
            etf_rows = [row for row in rows if row["symbol"] in ETFS]
            full = slice_block(rows)
            early = slice_block(early_rows)
            late = slice_block(late_rows)
            singles = slice_block(single_rows)
            etfs = slice_block(etf_rows)
            verdict = decide(full, early, late, singles)
            summaries[name] = {
                "verdict": verdict,
                "registered_mechanical_label": registered["verdict"],
                "eligible_episodes": registered["eligible_episodes"],
                "observed_trigger_pct": registered["observed_trigger_pct"],
                "actionable_trigger_pct": registered["actionable_trigger_pct"],
                "missed_entry_pct": registered["missed_entry_pct"],
                "missed_reasons": registered["missed_reasons"],
                "no_trigger": registered["no_trigger"],
                "no_trigger_reasons": registered["no_trigger_reasons"],
                "executed": registered["executed"],
                "r1_before_stop_pct": registered["r1_before_stop_pct"],
                "r2_before_stop_pct": registered["r2_before_stop_pct"],
                "stop_first_pct": registered["stop_first_pct"],
                "eod_pct": registered["eod_pct"],
                "median_mae_r": registered["median_mae_r"],
                "median_mfe_r": registered["median_mfe_r"],
                "median_hold_bars": registered["median_hold_bars"],
                "baseline_r1_before_stop_pct": registered["baseline_r1_before_stop_pct"],
                "baseline_r2_before_stop_pct": registered["baseline_r2_before_stop_pct"],
                "baseline_stop_first_pct": registered["baseline_stop_first_pct"],
                "r1_minus_baseline_pp": registered["r1_minus_baseline_pp"],
                "baseline_comparability": registered["baseline_comparability"],
                "exclusions": registered["exclusions"],
                "regimes": registered["regimes"],
                "by_level": registered["by_level"],
                "expectancy": {
                    "full": full,
                    "before_scanner_overlap": early,
                    "scanner_overlap_not_validation": late,
                    "single_names": singles,
                    "etf_cluster": etfs,
                },
            }
            print(name, verdict, full["strategy_per_setup_r"], full["baseline_per_setup_r"], flush=True)
    payload = {
        "definition": edge.RULES,
        "dataset": {
            "provider": "polygon_adjusted_5min",
            "path": str(DATA.relative_to(ROOT)),
            "window": [manifest["requested_date_range"]["start"], manifest["requested_date_range"]["end"]],
            "adjusted": True,
            "symbols": 66,
            "incomplete_regular_sessions": incomplete,
            "prior_session_note": "The file starts 2026-04-01, so families that need the prior day do not score that first session.",
            "scanner_overlap_not_validation": ["2026-08-14", "2026-09-08"],
            "sealed_week_excluded": ["2026-09-09", "2026-09-15"],
            "alpaca_screen_already_saw_this_window": True,
        },
        "families": summaries,
    }
    (NOTE / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("wrote", NOTE / "results.json", flush=True)


if __name__ == "__main__":
    main()
