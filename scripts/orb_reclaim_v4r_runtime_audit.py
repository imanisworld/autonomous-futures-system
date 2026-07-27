#!/usr/bin/env python3
"""ORB Reclaim V4-R -- full-engine runtime audit (populations 2 and 3 of 3).

Preregistration: docs/strategy-rules/ORB_RECLAIM_V4R_PREREGISTRATION_2026-07-27.md

Runs the raw candidate population (scripts/orb_reclaim_v4r_detector.py's
output) through the real ReplayEngine -> DecisionEngine -> RiskEngine ->
PaperBroker path, isolated (enabled_concepts=["orb_reclaim"] only, MNQ+MES,
continuous multi-day replay per instrument -- orb_reclaim is NOT day-only,
confirmed against execution/day_only_exit.py's DAY_ONLY_STRATEGIES before
writing this script, so positions may legitimately carry across day
boundaries and the engine must run continuously, not per-day-isolated like
the 3-2-2/Miyagi day-only studies).

For every raw candidate bar (instrument, bar_ts), reads the real engine's
decision at that EXACT bar -- not ambient NO_TRADE noise elsewhere in the
day, which fires regardless of whether orb_reclaim has an active candidate
on that specific bar (same anchoring discipline as
scripts/strat_322_parity_validation.py).

No engine, config, signal, or risk file is modified. No gate is
hypothetically exempted in this pass -- this is the actual, current,
unmodified runtime, exactly as deployed.

Corpus: data/replay_corpus_v1_market_condition_fixed (MNQ+MES, 313 days
each, 2025-07-24..2026-07-23).

Usage:
    python3 scripts/orb_reclaim_v4r_runtime_audit.py \\
        --raw /tmp/orb_v4r_raw.json --out <path>
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402

STRATEGY = "orb_reclaim"
INSTRUMENTS = ("MNQ", "MES")
CORPUS = REPO / "data" / "replay_corpus_v1_market_condition_fixed"
CANONICAL_ENTRY_TOLERANCE = {"MNQ": 32.0, "MES": 16.0}

SIGNAL_LAYER_GATES_OF_INTEREST = {
    "MARKET_CONDITION_NOT_TRADABLE",
    "MARKET_CONDITION_NOT_TRENDING",
    "TREND_STRENGTH_BELOW_REQUIRED",
    "EMA_STACK_NOT_ALIGNED",
    "SIGNAL_BAR_VOLUME_TOO_LOW",
    "RR_BELOW_MINIMUM",
    "ENTRY_DETACHED_FROM_PRICE",
}
RISK_LAYER_RULES_OF_INTEREST = {
    "max_stop_ticks", "stop_too_wide", "min_confluence_grade", "target_too_close",
}


def _json_lines(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _run_isolated(config, log_dir: Path) -> dict[str, list[dict]]:
    """Continuous multi-day replay per instrument. Returns bar_ts -> journal
    entry map per instrument (last entry wins if a bar_ts repeats, which it
    should not for a single-candle-per-timestamp corpus)."""
    per_instrument_entries: dict[str, dict[str, dict]] = {}
    for instrument in INSTRUMENTS:
        candle_dir = CORPUS / instrument
        files = sorted(candle_dir.glob(f"{instrument}_*.jsonl"))
        if len(files) != 313:
            raise RuntimeError(f"{instrument}: expected 313 daily files, found {len(files)}")
        inst_log_dir = log_dir / instrument
        inst_log_dir.mkdir(parents=True, exist_ok=True)
        engine = ReplayEngine(config=config, log_dir=str(inst_log_dir))
        by_bar_ts: dict[str, dict] = {}
        for index, candle_path in enumerate(files, 1):
            day = candle_path.stem.rsplit("_", 1)[-1]
            engine.run(candle_path, review_date=day)
            journal_path = inst_log_dir / f"journal_{day}.jsonl"
            for entry in _json_lines(journal_path):
                bar_ts = entry.get("bar_ts")
                if bar_ts:
                    by_bar_ts[bar_ts] = entry
            if index % 50 == 0 or index == len(files):
                print(f"[run] {instrument} {index}/{len(files)}", flush=True)
        per_instrument_entries[instrument] = by_bar_ts
    return per_instrument_entries


def _classify_candidate(entry: dict | None) -> dict[str, Any]:
    if entry is None:
        return {"classification": "NO_ENGINE_DECISION_AT_BAR"}

    setup = entry.get("setup") or {}
    risk = entry.get("risk_check") or {}
    confluence = entry.get("confluence") or {}
    decision = entry.get("decision")

    if decision in ("TRADE", "RISK_REJECTED") and setup.get("strategy") == STRATEGY:
        if decision == "RISK_REJECTED":
            failed_rule = risk.get("failed_rule")
            classification = (
                failed_rule if failed_rule in RISK_LAYER_RULES_OF_INTEREST
                else f"OTHER_RISK_REJECTED:{failed_rule}"
            )
            return {
                "classification": classification, "layer": "risk",
                "entry": setup.get("entry"), "stop": setup.get("stop"), "target": setup.get("target"),
                "rr_ratio": setup.get("rr_ratio"), "confluence_grade": confluence.get("grade"),
                "risk_failed_rule": failed_rule, "risk_reason": risk.get("reason"),
            }
        return {
            "classification": "REACHED_RISK_APPROVED", "layer": "risk",
            "entry": setup.get("entry"), "stop": setup.get("stop"), "target": setup.get("target"),
            "rr_ratio": setup.get("rr_ratio"), "confluence_grade": confluence.get("grade"),
            "paper_order_id": entry.get("paper_order_id"),
        }

    gates = list(entry.get("failed_gates") or [])
    known = [g for g in gates if g in SIGNAL_LAYER_GATES_OF_INTEREST]
    classification = known[0] if known else f"OTHER_SIGNAL_BLOCKED:{gates}"
    return {"classification": classification, "layer": "signal", "gates_observed": gates,
            "reason": entry.get("reason"), "engine_decision": decision}


def _resolve_fill(instrument: str, paper_order_id: str, entries_by_ts: dict[str, dict]) -> dict:
    for entry in entries_by_ts.values():
        if entry.get("type") == "OUTCOME":
            outcome = entry.get("outcome") or {}
            if outcome.get("paper_order_id") == paper_order_id:
                return outcome
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    candidates = raw["candidates"]

    base_config = load_config()
    config = dataclasses.replace(
        base_config,
        enabled_concepts=[STRATEGY],
        disabled_concepts_per_instrument={},
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root=dict(CANONICAL_ENTRY_TOLERANCE),
    )

    with tempfile.TemporaryDirectory(prefix="orb_reclaim_v4r_audit_") as tmp:
        entries_by_instrument = _run_isolated(config, Path(tmp))

        outcomes_by_instrument: dict[str, dict[str, dict]] = {}
        for instrument in INSTRUMENTS:
            inst_log_dir = Path(tmp) / instrument
            outcomes: dict[str, dict] = {}
            for path in sorted(inst_log_dir.glob("journal_*.jsonl")):
                for entry in _json_lines(path):
                    if entry.get("type") == "OUTCOME":
                        oid = (entry.get("outcome") or {}).get("paper_order_id")
                        if oid:
                            outcomes[oid] = entry.get("outcome") or {}
            outcomes_by_instrument[instrument] = outcomes

        results = []
        for cand in candidates:
            instrument = cand["instrument"]
            bar_ts = cand["bar_ts"]
            entry = entries_by_instrument.get(instrument, {}).get(bar_ts)
            classification = _classify_candidate(entry)
            row = {
                "instrument": instrument, "date": cand["date"], "bar_ts": bar_ts,
                "attempt_index": cand["attempt_index"], "half": cand["half"],
                "true_reclaim": cand["true_reclaim"], "prior_rejected_high": cand["prior_rejected_high"],
                "v4_original_eligible": cand["v4_original_eligible"],
                "v4_r_eligible": cand["v4_r_eligible"],
                "raw_result": cand["result"], "raw_net_pnl": cand["net_pnl"],
                **classification,
            }
            if classification.get("classification") == "REACHED_RISK_APPROVED":
                order_id = classification.get("paper_order_id")
                outcome = outcomes_by_instrument.get(instrument, {}).get(order_id, {})
                result = outcome.get("result")
                row["engine_filled"] = bool(result and result != "CANCELLED")
                row["engine_result"] = result
                row["engine_exit_reason"] = outcome.get("exit_reason")
                row["engine_pnl_gross"] = outcome.get("pnl_dollars")
            results.append(row)

    summary = {}
    for r in results:
        summary[r["classification"]] = summary.get(r["classification"], 0) + 1

    out = {
        "candidate_count": len(results),
        "summary": summary,
        "candidates": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n[done] wrote {args.out}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
