#!/usr/bin/env python3
"""ORB Reclaim V4-R -- per-variant ISOLATED account audit (correction).

Preregistration: docs/strategy-rules/ORB_RECLAIM_V4R_PREREGISTRATION_2026-07-27.md

scripts/orb_reclaim_v4r_runtime_audit.py ran the UNRESTRICTED first_cross
population through one continuous account per instrument and then labeled
which bars happened to also be V4-R/V4-original-eligible -- this measures
today's deployed behavior correctly, but it does NOT isolate V4-R's own
standalone risk profile: first_cross's net-negative real trades (confirmed
by Pass 1, -$1,056.30) share the SAME account and drawdown-breaker state as
the V4-R subset, so a max_drawdown halt caused by unrelated first_cross
losses can block a V4-R candidate that would otherwise have traded --
exactly the same "own isolated filtered replay" requirement Pass 1's own
caveats section already states ("A passing variant is a CANDIDATE for its
own isolated filtered replay -- never directly promotable from this pass"),
and the same session-isolated-account precedent PR #352 itself used
(MNQ_new_york_ioc_1tick etc, each its own account).

This script runs V4-original and V4-R EACH in their own fresh, isolated,
continuous account (in-process monkeypatch of DecisionEngine._try_orb_reclaim
only -- zero committed-file changes) so a candidate can only ever open a
position if it belongs to that specific variant's population. No other
candidate ever reaches RiskEngine in that run, so its own drawdown-breaker
state reflects ONLY that variant's own trades.

The monkeypatch gates purely on a PRECOMPUTED (instrument, bar timestamp)
eligibility set built directly from scripts/orb_reclaim_v4r_detector.py's
already-causal orb_status-transition history -- it does not change what
_try_orb_reclaim's own bracket/gate logic does for an eligible bar, only
whether that bar is allowed to reach it at all. Every other real gate
(TRENDING, stop-cap, confluence, drawdown, entry-detachment, etc.) is
exercised completely unmodified for whichever bars ARE eligible.

Usage:
    python3 scripts/orb_reclaim_v4r_isolated_variant_audit.py \\
        --raw /tmp/orb_v4r_raw.json --variant v4_r --out <path>
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402
from strategy.signal_engine import DecisionEngine  # noqa: E402

STRATEGY = "orb_reclaim"
INSTRUMENTS = ("MNQ", "MES")
CORPUS = REPO / "data" / "replay_corpus_v1_market_condition_fixed"
CANONICAL_ENTRY_TOLERANCE = {"MNQ": 32.0, "MES": 16.0}

SIGNAL_LAYER_GATES_OF_INTEREST = {
    "MARKET_CONDITION_NOT_TRADABLE", "MARKET_CONDITION_NOT_TRENDING",
    "TREND_STRENGTH_BELOW_REQUIRED", "EMA_STACK_NOT_ALIGNED",
    "SIGNAL_BAR_VOLUME_TOO_LOW", "RR_BELOW_MINIMUM", "ENTRY_DETACHED_FROM_PRICE",
}
RISK_LAYER_RULES_OF_INTEREST = {
    "max_stop_ticks", "stop_too_wide", "min_confluence_grade",
    "target_too_close", "max_drawdown",
}


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _install_variant_gate(eligible: set[tuple[str, datetime]]) -> None:
    original = DecisionEngine._try_orb_reclaim

    def gated(self, state):  # noqa: ANN001
        key = (state.instrument, state.timestamp)
        if key not in eligible:
            return None
        return original(self, state)

    DecisionEngine._try_orb_reclaim = gated


def _json_lines(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _run_isolated(config, log_dir: Path) -> dict[str, dict[str, dict]]:
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
            if index % 100 == 0 or index == len(files):
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

    if decision == "RISK_REJECTED" and (risk.get("failed_rule") == "max_drawdown"):
        return {"classification": "max_drawdown", "layer": "risk", "risk_reason": risk.get("reason")}

    gates = list(entry.get("failed_gates") or [])
    known = [g for g in gates if g in SIGNAL_LAYER_GATES_OF_INTEREST]
    classification = known[0] if known else f"OTHER_SIGNAL_BLOCKED:{gates}:{decision}"
    return {"classification": classification, "layer": "signal", "gates_observed": gates,
            "reason": entry.get("reason"), "engine_decision": decision}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--variant", required=True, choices=["v4_original", "v4_r"])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    candidates = raw["candidates"]
    key_field = f"{args.variant}_eligible"
    eligible_candidates = [c for c in candidates if c[key_field]]
    eligible_set = {
        (c["instrument"], _parse_ts(c["bar_ts"])) for c in eligible_candidates
    }
    print(f"[setup] variant={args.variant} eligible_candidates={len(eligible_candidates)}")

    _install_variant_gate(eligible_set)

    base_config = load_config()
    config = dataclasses.replace(
        base_config,
        enabled_concepts=[STRATEGY],
        disabled_concepts_per_instrument={},
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root=dict(CANONICAL_ENTRY_TOLERANCE),
    )

    with tempfile.TemporaryDirectory(prefix=f"orb_reclaim_v4r_{args.variant}_") as tmp:
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
        for cand in eligible_candidates:
            instrument = cand["instrument"]
            bar_ts = cand["bar_ts"]
            entry = entries_by_instrument.get(instrument, {}).get(bar_ts)
            classification = _classify_candidate(entry)
            row = {
                "instrument": instrument, "date": cand["date"], "bar_ts": bar_ts,
                "attempt_index": cand["attempt_index"], "half": cand["half"],
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
        "variant": args.variant,
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
