#!/usr/bin/env python3
"""4HR Re-Trigger -- executable-parity audit against the #334 canonical evidence.

#334's own evidence (docs/4hr-retrigger-batch1-evidence-2026-07-26.md,
scripts/four_hr_retrigger_stop_study.py) reuses the real
strategy/four_hr_retrigger.py::advance_4hr_retrigger state machine and the
real execution/day_only_exit.py + execution/paper_broker.py fill/exit
functions -- but it is still a standalone driver script, not the real
strategy/signal_engine.py::DecisionEngine -> risk/risk_engine.py::RiskEngine
path #335 wires strat_4hr_retrigger into for paper-forward trading. The
already-established parity-defect pattern this session (Miyagi PR #366,
3-2-2 PR #367) found MARKET_CONDITION_NOT_TRENDING/TREND_STRENGTH/
EMA_STACK/min_rr_ratio blocking 5-minute-native strategies with zero basis
in their own rules -- strat_4hr_retrigger is NOT on
strategy/signal_engine.py's _TRENDING_GATE_EXEMPT frozenset (grep-verified
before writing this script: only strat_322_first_live is exempt on
`main` as of this branch), so this strategy is exactly as exposed to that
defect class as Miyagi/3-2-2 were before their fixes -- unverified until
now because #334 never ran it through DecisionEngine/RiskEngine at all.

This script drives the SAME 81 MNQ / 76 MES candidate dates (from
scripts/four_hr_retrigger_stop_study_results.json, #334's own committed
output) through the real ReplayEngine -> DecisionEngine -> RiskEngine ->
PaperBroker path, isolated (enabled_concepts=["strat_4hr_retrigger"] only,
both instruments enabled here regardless of production's MES exclusion --
this is an evidence pass, not a production run), CONTINUOUS multi-day
replay per instrument (strat_4hr_retrigger is not day-only in the sense of
needing isolation between days -- its own reference-day logic spans day
boundaries, so a single persistent ReplayEngine instance must process all
days in order, matching the pattern already established for
scripts/orb_reclaim_v4r_runtime_audit.py).

Corpus: data/replay_corpus_v1_5m_4hr_audit/{MNQ,MES} -- regenerated OFFLINE
(no network) from data/replay_polygon_5m's already-fetched raw OHLCV via
scripts/polygon_to_replay.py::derive_candles(), covering the EXACT #334
date range (2024-07-02..2026-06-26, 621 daily files/instrument, byte-exact
bar count minus the ~54-bar EMA/ORB warmup skip at the very start). This
adds the reconstructed_market_condition/market_condition_status fields
data/replay_polygon_5m itself predates and lacks entirely -- without them
the real DecisionEngine's market_condition gate cannot evaluate correctly
for a 5-minute-native strategy. See
scripts/four_hr_retrigger_parity_audit_corpus_regen_log.txt for the
regeneration run's own output.

The pure state machine (advance_4hr_retrigger) is driven independently and
continuously alongside the real engine to find the EXACT trigger bar for
every day in the full 621-day corpus (not just the 81/76 known candidate
days) -- this both anchors classification to the correct bar (avoiding
ambient NO_TRADE noise from unrelated bars) and surfaces "missing
candidate" (#334 date with no independently-confirmed trigger here) or
"extra candidate" (a triggered day never in #334's list) discrepancies.

Usage:
    python3 scripts/four_hr_retrigger_parity_audit.py --out <path>
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

STRATEGY = "strat_4hr_retrigger"
INSTRUMENTS = ("MNQ", "MES")
CORPUS = REPO / "data" / "replay_corpus_v1_5m_4hr_audit"
KNOWN_RESULTS = REPO / "scripts" / "four_hr_retrigger_stop_study_results.json"
ROLLING_WINDOW_DAYS = 5  # covers the largest _prior_reference_day lookback (1 day, or 3 for QQQ, plus slack)

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


def _json_lines(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _find_trigger_bars(files: list[Path], instrument: str) -> dict[str, dict[str, Any]]:
    """Continuous pure-state-machine walk across the FULL corpus (not just
    known days) -- records, per trading_date, the trigger bar (if any) plus
    the terminal status for days that never trigger. Uses a rolling window
    of the last ROLLING_WINDOW_DAYS calendar days of bars (the function
    itself filters for bars strictly closed before current_close; extra
    bars beyond what it needs are harmless)."""
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
                per_day[day] = {"trigger_bar_ts": None, "status": None, "direction": None}
            if candidate is not None and day:
                per_day[day]["trigger_bar_ts"] = row["timestamp"]
                per_day[day]["direction"] = candidate.get("direction")
                per_day[day]["status"] = state.get("status")
            elif day:
                per_day[day]["status"] = state.get("status")
    return per_day


def _run_isolated(config, log_dir: Path) -> dict[str, dict[str, dict]]:
    """Continuous multi-day replay per instrument -- one persistent
    ReplayEngine instance processes every daily file in order, matching
    scripts/orb_reclaim_v4r_runtime_audit.py's established pattern (needed
    for any strategy whose own logic spans day boundaries)."""
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

    with tempfile.TemporaryDirectory(prefix="four_hr_parity_audit_") as tmp:
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

        # Known #334 candidate days (missing/changed detection).
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

        # Extra candidates: days the pure state machine triggers on in the
        # regenerated corpus that #334's own list never mentions at all.
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
