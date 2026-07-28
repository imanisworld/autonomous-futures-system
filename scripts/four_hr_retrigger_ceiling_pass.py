#!/usr/bin/env python3
"""4HR Re-Trigger -- HYPOTHETICAL ceiling pass (research only, in-process
monkeypatch, zero committed file changes, zero deployment).

Operator-authorized scope (2026-07-27, following the same audit shown by
scripts/four_hr_retrigger_parity_audit.py's real baseline): exempt strat_
4hr_retrigger from EXACTLY three signal-layer gates --
MARKET_CONDITION_NOT_TRENDING, RR_BELOW_MINIMUM, EMA_STACK_NOT_ALIGNED --
mirroring PR #365's already-built, already-tested exemption pattern
(currently scoped to strat_322_first_live/strat_12hr_miyagi only). Preserve
EVERYTHING else exactly: max_stop_ticks/stop_too_wide, ENTRY_DETACHED_FROM_
PRICE, target_too_close, min_confluence_grade, session filters, detector/
state-machine logic, entry_fill_model=market, commission/slippage,
pessimistic same-bar stop-before-target handling. STRONG-trend
(_STRONG_TREND_GATE_EXEMPT) is deliberately NOT touched -- it had zero
hits in the real baseline audit, and the operator's exact spec named only
three gates.

Run from a worktree of claude/paper-execution-parity-fixes (PR #365) so the
REAL, already-tested exemption machinery (_trending_gate_exempt_candidate,
_ema_stack_gate_exempt_candidate, _sole_five_minute_native_candidate,
RiskEngine._check_rr_ratio's exemption clause) is used verbatim -- not a
hand-rolled reimplementation that could subtly diverge. The ONLY
monkeypatch applied is widening three existing frozenset class attributes
to also include "strat_4hr_retrigger" -- no method bodies are touched.
_FIVE_MINUTE_NATIVE_CANDIDATE_ATTRS already maps strat_4hr_retrigger ->
four_hr_retrigger_candidate on this branch (needed for collision-safety
regardless of exemption status), so it needs no patching.

Corpus/candidates: identical to scripts/four_hr_retrigger_parity_audit.py
(absolute paths into the main worktree -- this branch has neither the
regenerated corpus nor the #334 results file checked out).

Usage:
    python3 scripts/four_hr_retrigger_ceiling_pass.py --out <path>
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402
from strategy.four_hr_retrigger import advance_4hr_retrigger  # noqa: E402
from strategy.signal_engine import DecisionEngine  # noqa: E402
from risk.risk_engine import RiskEngine  # noqa: E402

STRATEGY = "strat_4hr_retrigger"
INSTRUMENTS = ("MNQ", "MES")
MAIN_REPO = Path("/Users/djb.a.e/MAINVSCODE/autonomous-futures-system")
CORPUS = MAIN_REPO / "data" / "replay_corpus_v1_5m_4hr_audit"
KNOWN_RESULTS = MAIN_REPO / "scripts" / "four_hr_retrigger_stop_study_results.json"
ROLLING_WINDOW_DAYS = 5

SIGNAL_LAYER_GATES_OF_INTEREST = {
    "MARKET_CONDITION_NOT_TRENDING",
    "MARKET_CONDITION_NOT_TRADABLE",
    "TREND_STRENGTH_BELOW_REQUIRED",
    "EMA_STACK_NOT_ALIGNED",
    "RR_BELOW_MINIMUM",
    "ENTRY_DETACHED_FROM_PRICE",
}
RISK_LAYER_RULES_OF_INTEREST = {
    "max_stop_ticks", "stop_too_wide", "min_confluence_grade", "target_too_close",
}

# ---------------------------------------------------------------------------
# THE MONKEYPATCH: widen exactly three existing exemption sets. No method
# bodies touched. Verify pre/post membership so a silent no-op is impossible.
# ---------------------------------------------------------------------------
assert STRATEGY not in DecisionEngine._TRENDING_GATE_EXEMPT
assert STRATEGY not in DecisionEngine._EMA_STACK_GATE_EXEMPT
assert STRATEGY not in DecisionEngine._MIN_RR_GATE_EXEMPT  # signal-layer RR enforcement (fires FIRST)
assert STRATEGY not in RiskEngine._MIN_RR_GATE_EXEMPT      # risk-layer RR enforcement (fires SECOND, independent set)
assert STRATEGY not in DecisionEngine._STRONG_TREND_GATE_EXEMPT  # left untouched, confirm still absent after patch below

DecisionEngine._TRENDING_GATE_EXEMPT = frozenset(DecisionEngine._TRENDING_GATE_EXEMPT | {STRATEGY})
DecisionEngine._EMA_STACK_GATE_EXEMPT = frozenset(DecisionEngine._EMA_STACK_GATE_EXEMPT | {STRATEGY})
DecisionEngine._MIN_RR_GATE_EXEMPT = frozenset(DecisionEngine._MIN_RR_GATE_EXEMPT | {STRATEGY})
RiskEngine._MIN_RR_GATE_EXEMPT = frozenset(RiskEngine._MIN_RR_GATE_EXEMPT | {STRATEGY})

assert STRATEGY in DecisionEngine._TRENDING_GATE_EXEMPT
assert STRATEGY in DecisionEngine._EMA_STACK_GATE_EXEMPT
assert STRATEGY in DecisionEngine._MIN_RR_GATE_EXEMPT
assert STRATEGY in RiskEngine._MIN_RR_GATE_EXEMPT
assert STRATEGY not in DecisionEngine._STRONG_TREND_GATE_EXEMPT  # STRONG-trend deliberately NOT exempted
print("[monkeypatch] TRENDING/EMA_STACK/MIN_RR (BOTH signal-layer DecisionEngine "
      "and risk-layer RiskEngine enforcement points) exempt sets widened to include "
      f"{STRATEGY!r}; STRONG_TREND left untouched.", flush=True)
# ---------------------------------------------------------------------------


def _json_lines(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _find_trigger_bars(files: list[Path], instrument: str) -> dict[str, dict[str, Any]]:
    state: dict = {}
    window: list[dict] = []
    per_day: dict[str, dict[str, Any]] = {}
    for f in files:
        for row in _json_lines(f):
            ts = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            window.append(row)
            cutoff = ts - timedelta(days=ROLLING_WINDOW_DAYS)
            while window and datetime.fromisoformat(
                str(window[0]["timestamp"]).replace("Z", "+00:00")
            ) < cutoff:
                window.pop(0)
            state, candidate = advance_4hr_retrigger(
                bars_5m=window, current_bar_ts=ts, instrument=instrument,
                persisted_state=state,
            )
            day = state.get("trading_date")
            if day and day not in per_day:
                per_day[day] = {
                    "trigger_bar_ts": None, "status": None, "direction": None,
                    "entry": None, "stop": None, "target": None,
                }
            if candidate is not None and day:
                per_day[day].update({
                    "trigger_bar_ts": row["timestamp"],
                    "direction": candidate.get("direction"),
                    "status": state.get("status"),
                    "entry": candidate.get("entry"),
                    "stop": candidate.get("stop"),
                    "target": candidate.get("target"),
                })
            elif day:
                per_day[day]["status"] = state.get("status")
    return per_day


def _run_isolated(config, log_dir: Path) -> dict[str, dict[str, dict]]:
    per_instrument_entries: dict[str, dict[str, dict]] = {}
    for instrument in INSTRUMENTS:
        candle_dir = CORPUS / instrument
        files = sorted(candle_dir.glob(f"{instrument}_*.jsonl"))
        if not files:
            raise RuntimeError(f"{instrument}: no corpus files found in {candle_dir}")
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


def _classify(entry: Optional[dict]) -> dict[str, Any]:
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
                "direction": setup.get("direction"),
                "entry": setup.get("entry"), "stop": setup.get("stop"), "target": setup.get("target"),
                "rr_ratio": setup.get("rr_ratio"), "confluence_grade": confluence.get("grade"),
                "risk_failed_rule": failed_rule, "risk_reason": risk.get("reason"),
            }
        return {
            "classification": "REACHED_RISK_APPROVED", "layer": "risk",
            "direction": setup.get("direction"),
            "entry": setup.get("entry"), "stop": setup.get("stop"), "target": setup.get("target"),
            "rr_ratio": setup.get("rr_ratio"), "confluence_grade": confluence.get("grade"),
            "paper_order_id": entry.get("paper_order_id"),
        }

    gates = list(entry.get("failed_gates") or [])
    known = [g for g in gates if g in SIGNAL_LAYER_GATES_OF_INTEREST]
    classification = known[0] if known else f"OTHER_SIGNAL_BLOCKED:{gates}"
    return {"classification": classification, "layer": "signal", "gates_observed": gates,
            "reason": entry.get("reason"), "engine_decision": decision}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    known = json.loads(KNOWN_RESULTS.read_text(encoding="utf-8"))
    known_trades_by_instrument = {
        inst: {t["day"]: t for t in known["instruments"][inst]["trades_baseline"]}
        for inst in INSTRUMENTS
    }

    base_config = load_config()
    config = dataclasses.replace(
        base_config,
        enabled_concepts=[STRATEGY],
        disabled_concepts_per_instrument={},
        expected_timeframe_minutes=5,
    )
    assert config.entry_fill_model == "market", (
        f"expected production default entry_fill_model=market, got {config.entry_fill_model}"
    )

    with tempfile.TemporaryDirectory(prefix="four_hr_ceiling_") as tmp:
        tmp_path = Path(tmp)

        trigger_bars_by_instrument = {}
        for instrument in INSTRUMENTS:
            files = sorted((CORPUS / instrument).glob(f"{instrument}_*.jsonl"))
            print(f"[pure-sm] walking {instrument} ({len(files)} files)...", flush=True)
            trigger_bars_by_instrument[instrument] = _find_trigger_bars(files, instrument)

        entries_by_instrument = _run_isolated(config, tmp_path)

        outcomes_by_instrument: dict[str, dict[str, dict]] = {}
        for instrument in INSTRUMENTS:
            inst_log_dir = tmp_path / instrument
            outcomes: dict[str, dict] = {}
            for path in sorted(inst_log_dir.glob("journal_*.jsonl")):
                for entry in _json_lines(path):
                    if entry.get("type") == "OUTCOME":
                        oid = (entry.get("outcome") or {}).get("paper_order_id")
                        if oid:
                            outcomes[oid] = entry.get("outcome") or {}
            outcomes_by_instrument[instrument] = outcomes

    results = []
    for instrument in INSTRUMENTS:
        known_trades = known_trades_by_instrument[instrument]
        trigger_bars = trigger_bars_by_instrument[instrument]
        by_bar_ts = entries_by_instrument[instrument]

        for day, known_trade in sorted(known_trades.items()):
            pure_sm = trigger_bars.get(day, {})
            trigger_bar_ts = pure_sm.get("trigger_bar_ts")
            row: dict[str, Any] = {
                "instrument": instrument, "date": day,
                "known_direction": known_trade["direction"],
                "known_excluded": known_trade.get("excluded", False),
                "known_exclusion_reason": known_trade.get("exclusion_reason"),
                "known_result": known_trade.get("result"),
                "known_net_pnl": known_trade.get("net_pnl"),
                "pure_sm_status": pure_sm.get("status"),
                "pure_sm_direction": pure_sm.get("direction"),
                "pure_sm_trigger_bar_ts": trigger_bar_ts,
                "pure_sm_entry": pure_sm.get("entry"),
                "pure_sm_stop": pure_sm.get("stop"),
                "pure_sm_target": pure_sm.get("target"),
            }
            if trigger_bar_ts is None:
                row["classification"] = "MISSING_CANDIDATE_IN_REGENERATED_CORPUS"
                results.append(row)
                continue
            if pure_sm.get("direction") != known_trade["direction"]:
                row["direction_mismatch"] = True
            entry = by_bar_ts.get(trigger_bar_ts)
            classification = _classify(entry)
            row.update(classification)
            if classification.get("classification") == "REACHED_RISK_APPROVED":
                order_id = classification.get("paper_order_id")
                outcome = outcomes_by_instrument.get(instrument, {}).get(order_id, {})
                result = outcome.get("result")
                row["engine_filled"] = bool(result and result != "CANCELLED")
                row["engine_result"] = result
                row["engine_exit_reason"] = outcome.get("exit_reason")
                row["engine_pnl_gross"] = outcome.get("pnl_dollars")
            results.append(row)

        extra_days = sorted(
            d for d, v in trigger_bars.items()
            if v.get("trigger_bar_ts") is not None and d not in known_trades
        )
        for day in extra_days:
            pure_sm = trigger_bars[day]
            trigger_bar_ts = pure_sm["trigger_bar_ts"]
            entry = by_bar_ts.get(trigger_bar_ts)
            classification = _classify(entry)
            results.append({
                "instrument": instrument, "date": day,
                "known_direction": None, "known_result": None, "known_net_pnl": None,
                "pure_sm_status": pure_sm.get("status"),
                "pure_sm_direction": pure_sm.get("direction"),
                "pure_sm_trigger_bar_ts": trigger_bar_ts,
                "extra_candidate_not_in_334": True,
                **classification,
            })

    summary: dict[str, int] = {}
    for r in results:
        summary[r["classification"]] = summary.get(r["classification"], 0) + 1
    extra_count = sum(1 for r in results if r.get("extra_candidate_not_in_334"))
    missing_count = sum(
        1 for r in results if r["classification"] == "MISSING_CANDIDATE_IN_REGENERATED_CORPUS"
    )
    direction_mismatch_count = sum(1 for r in results if r.get("direction_mismatch"))

    out = {
        "ceiling_pass": True,
        "exempted_gates": ["MARKET_CONDITION_NOT_TRENDING", "RR_BELOW_MINIMUM", "EMA_STACK_NOT_ALIGNED"],
        "preserved_gates": ["stop_too_wide/max_stop_ticks", "ENTRY_DETACHED_FROM_PRICE",
                             "target_too_close", "min_confluence_grade", "TREND_STRENGTH_BELOW_REQUIRED"],
        "config": {
            "enabled_concepts": config.enabled_concepts,
            "expected_timeframe_minutes": config.expected_timeframe_minutes,
            "entry_fill_model": config.entry_fill_model,
            "fill_slippage_ticks": config.fill_slippage_ticks,
            "require_trending_condition": config.require_trending_condition,
            "require_strong_trend": dict(config.require_strong_trend),
            "min_rr_ratio": config.min_rr_ratio,
            "min_confluence_grade": getattr(config, "min_confluence_grade", None),
            "max_stop_ticks": dict(getattr(config, "max_stop_ticks", {}) or {}),
        },
        "known_candidate_count": {
            inst: len(known_trades_by_instrument[inst]) for inst in INSTRUMENTS
        },
        "extra_candidate_count_not_in_334": extra_count,
        "missing_candidate_count": missing_count,
        "direction_mismatch_count": direction_mismatch_count,
        "summary": summary,
        "trades": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n[done] wrote {args.out}")
    print(json.dumps(summary, indent=2))
    print(f"extra_candidates_not_in_334={extra_count} missing={missing_count} "
          f"direction_mismatches={direction_mismatch_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
