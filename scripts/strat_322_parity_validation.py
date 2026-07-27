#!/usr/bin/env python3
"""3-2-2 First Live parity validation — real full-engine population audit.

PR #359's canonical evidence (docs/strategy-rules/60M_322_EXPANDED_EVIDENCE_
2026-07-26.md: 34 candidates / 21 fills / 20 resolved / net $1,595.70 /
PF 10.36) was produced by `research/replay_322_honest_fill.py`, a standalone
function that never touches `replay/replay_engine.py` or `strategy/
signal_engine.py::DecisionEngine` -- it has no TRENDING/STRONG-trend/
EMA-stack/min_rr_ratio/min_confluence_grade/max_stop_ticks dependency at
all, by design (verified in that evidence doc's own %0.3 section). That
means the "34 candidates" figure was never actually run through the real
runtime gates PR #359 wires the strategy into. This script closes that gap:
it drives the SAME 34 known candidate dates through the real `ReplayEngine
-> DecisionEngine -> RiskEngine -> PaperBroker` path, isolated
(enabled_concepts=["strat_322_first_live"] only), and classifies each date
by exactly which gate stops it (or whether it reaches a real fill).

Must be run twice, once per code checkout, and the two result files
compared:

  1. On `origin/main` (current production code) -- shows the population
     BEFORE the four parity-defect fixes in PR #365 (TRENDING/STRONG-trend/
     EMA-stack/min_rr_ratio all unexempted for this strategy).
  2. On `claude/paper-execution-parity-fixes` (PR #365) -- shows the
     population AFTER those four fixes, with `min_confluence_grade` and
     `max_stop_ticks` still fully enforced (preserved, not exempted).

The delta between the two runs is exactly "parity-filter removals fixed by
#365." Within run 2, further rejections split into "legitimate confluence
rejections" and "legitimate stop-cap rejections" (both real risk_check
rejections, not signal-layer NO_TRADE). Whatever survives run 2 in full is
the actual executable population -- a genuine full-engine result including
real fills, not a number borrowed from the honest-fill research script.

Requires `expected_timeframe_minutes=5` explicitly set in the isolation
config -- this is the switch `replay/replay_engine.py`'s
`canonical_4hr_only` fix (#365) keys off to treat this run's bars as the
authoritative 5-minute-native cadence. Without it, none of #365's
exemptions can ever activate regardless of which code checkout is running
(a gap found and corrected while writing this script, after noticing the
Miyagi full-engine harness in PR #366 omitted it too -- see that PR's own
follow-up note).

Corpus: data/replay_corpus_v1_5m/MNQ (5-minute-native, #338-corrected
fields, gitignored -- regenerate via `scripts/polygon_to_replay.py
--timeframe 5`, requires POLYGON_API_KEY).

Usage:
    python3 scripts/strat_322_parity_validation.py \\
        --label main_baseline \\
        --out scripts/strat_322_parity_validation_main_baseline.json

    python3 scripts/strat_322_parity_validation.py \\
        --label corrected \\
        --out scripts/strat_322_parity_validation_corrected.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402
from strategy.strat_322_first_live import advance_strat_322_first_live  # noqa: E402

STRATEGY = "strat_322_first_live"
INSTRUMENT = "MNQ"
CORPUS_DIR = REPO / "data" / "replay_corpus_v1_5m" / INSTRUMENT
CANONICAL_ENTRY_TOLERANCE = {"MNQ": 32.0, "MES": 16.0}

# The 34 known candidate dates from docs/strategy-rules/
# 60M_322_EXPANDED_EVIDENCE_2026-07-26.md's base_case (group1_corrected_
# baseline.json), with the honest-fill research model's own result for
# cross-reference/comparison only -- this script does not trust or reuse
# those numbers, it independently re-derives everything through the real
# engine.
KNOWN_CANDIDATES = [
    {"date": "2024-08-02", "direction": "SHORT", "research_result": "CANCELLED", "research_net": 0.0},
    {"date": "2024-08-14", "direction": "SHORT", "research_result": "CANCELLED", "research_net": 0.0},
    {"date": "2024-08-22", "direction": "SHORT", "research_result": "CANCELLED", "research_net": 0.0},
    {"date": "2024-08-30", "direction": "SHORT", "research_result": "CANCELLED", "research_net": 0.0},
    {"date": "2024-09-06", "direction": "SHORT", "research_result": "WIN", "research_net": 58.76},
    {"date": "2024-09-11", "direction": "SHORT", "research_result": "WIN", "research_net": 135.26},
    {"date": "2024-09-12", "direction": "LONG", "research_result": "CANCELLED", "research_net": 0.0},
    {"date": "2024-10-10", "direction": "LONG", "research_result": "WIN", "research_net": 169.76},
    {"date": "2024-10-11", "direction": "LONG", "research_result": "WIN", "research_net": 106.26},
    {"date": "2024-11-26", "direction": "SHORT", "research_result": "WIN", "research_net": 5.76},
    {"date": "2024-12-23", "direction": "SHORT", "research_result": "CANCELLED", "research_net": 0.0},
    {"date": "2025-01-20", "direction": "SHORT", "research_result": "UNRESOLVED", "research_net": None},
    {"date": "2025-02-07", "direction": "SHORT", "research_result": "CANCELLED", "research_net": 0.0},
    {"date": "2025-02-12", "direction": "LONG", "research_result": "CANCELLED", "research_net": 0.0},
    {"date": "2025-02-26", "direction": "LONG", "research_result": "WIN", "research_net": 55.76},
    {"date": "2025-03-17", "direction": "SHORT", "research_result": "WIN", "research_net": 152.76},
    {"date": "2025-03-21", "direction": "LONG", "research_result": "WIN", "research_net": 137.26},
    {"date": "2025-05-01", "direction": "LONG", "research_result": "WIN", "research_net": 68.76},
    {"date": "2025-06-04", "direction": "SHORT", "research_result": "WIN", "research_net": 75.26},
    {"date": "2025-06-05", "direction": "LONG", "research_result": "WIN", "research_net": 131.76},
    {"date": "2025-06-26", "direction": "LONG", "research_result": "WIN", "research_net": 38.76},
    {"date": "2025-06-27", "direction": "LONG", "research_result": "CANCELLED", "research_net": 0.0},
    {"date": "2025-08-11", "direction": "LONG", "research_result": "WIN", "research_net": 56.76},
    {"date": "2025-08-27", "direction": "LONG", "research_result": "CANCELLED", "research_net": 0.0},
    {"date": "2025-09-05", "direction": "SHORT", "research_result": "WIN", "research_net": 221.26},
    {"date": "2025-09-18", "direction": "LONG", "research_result": "CANCELLED", "research_net": 0.0},
    {"date": "2025-09-30", "direction": "LONG", "research_result": "WIN", "research_net": 29.26},
    {"date": "2025-10-10", "direction": "SHORT", "research_result": "CANCELLED", "research_net": 0.0},
    {"date": "2025-10-16", "direction": "SHORT", "research_result": "LOSS", "research_net": -167.24},
    {"date": "2026-03-06", "direction": "LONG", "research_result": "CANCELLED", "research_net": 0.0},
    {"date": "2026-04-01", "direction": "LONG", "research_result": "WIN", "research_net": 133.26},
    {"date": "2026-05-12", "direction": "SHORT", "research_result": "LOSS", "research_net": -3.24},
    {"date": "2026-05-13", "direction": "LONG", "research_result": "WIN", "research_net": 180.76},
    {"date": "2026-06-11", "direction": "SHORT", "research_result": "WIN", "research_net": 8.76},
]

SIGNAL_LAYER_PARITY_GATES = {
    "MARKET_CONDITION_NOT_TRENDING",
    "TREND_STRENGTH_BELOW_REQUIRED",
    "EMA_STACK_NOT_ALIGNED",
    "RR_BELOW_MINIMUM",
}
PRESERVED_RISK_GATES = {
    "min_confluence_grade",
    "stop_too_wide",
    "max_stop_ticks",
}


def _json_lines(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _load_corpus_rows(candle_path: Path) -> list[dict]:
    return list(_json_lines(candle_path))


def _find_trigger_bar_ts(rows: list[dict], day: str) -> tuple[Optional[str], Optional[dict], str]:
    """Drive the pure state machine bar-by-bar (same pattern as the Miyagi
    causal-stop distribution study) to find the EXACT bar timestamp where
    strat_322_first_live transitions to TRIGGERED. This is a same-day-only
    pattern (7AM-11AM ET, all within one ET trading day; unlike Miyagi's
    12-hour span there is no UTC-midnight day-boundary ambiguity here), so
    a straightforward single-day cumulative drive is sufficient.

    Returns (trigger_bar_ts_iso_or_None, candidate_dict_or_None, terminal_status).
    trigger_bar_ts is the corpus row's OWN "timestamp" value (bar open) for
    the bar where advance_strat_322_first_live first returns a non-None
    candidate -- this is the same value ReplayEngine stamps onto
    journal_entry["bar_ts"] for that candle, so it can be matched directly
    against the full-engine journal without any timezone/format conversion.
    """
    state: dict = {}
    cumulative: list[dict] = []
    for row in rows:
        cumulative.append(row)
        ts = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
        state, candidate = advance_strat_322_first_live(
            bars_5m=cumulative, current_bar_ts=ts, instrument=INSTRUMENT,
            persisted_state=state,
        )
        if candidate is not None and state.get("trading_date") == day:
            return row["timestamp"], candidate, state.get("status", "")
    return None, None, state.get("status", "")


def _classify_day(entries: list[dict], trigger_bar_ts: Optional[str]) -> dict[str, Any]:
    """Classify one day's journal into exactly one outcome for
    strat_322_first_live, anchored on the SPECIFIC bar the pure state
    machine independently confirms is the trigger bar -- not on any
    MARKET_CONDITION_NOT_TRENDING/etc. NO_TRADE elsewhere in the day, which
    fires as ambient bar-level noise on every non-trending bar regardless
    of whether this strategy has an active candidate at all (that gate runs
    unconditionally before any candidate-presence check). Trusting ambient
    noise here would over-count PARITY_GATE_BLOCKED for bars this strategy
    was never actually contesting.
    """
    outcomes_by_order = {}
    for e in entries:
        if e.get("type") == "OUTCOME":
            oid = (e.get("outcome") or {}).get("paper_order_id") or e.get("paper_order_id")
            if oid:
                outcomes_by_order[oid] = e.get("outcome") or e

    if trigger_bar_ts is None:
        return {"classification": "NO_TRIGGER_IN_PURE_STATE_MACHINE", "bar_ts": None}

    at_trigger = [e for e in entries if e.get("bar_ts") == trigger_bar_ts]
    if not at_trigger:
        return {"classification": "TRIGGER_BAR_MISSING_FROM_JOURNAL", "bar_ts": trigger_bar_ts}

    entry = at_trigger[-1]
    setup = entry.get("setup") or {}
    risk = entry.get("risk_check") or {}
    confluence = entry.get("confluence") or {}

    if entry.get("decision") in ("TRADE", "RISK_REJECTED") and setup.get("strategy") == STRATEGY:
        if entry.get("decision") == "RISK_REJECTED":
            failed_rule = risk.get("failed_rule")
            if failed_rule == "min_confluence_grade":
                classification = "CONFLUENCE_REJECTED"
            elif failed_rule in ("stop_too_wide", "max_stop_ticks"):
                classification = "STOP_CAP_REJECTED"
            else:
                classification = f"OTHER_RISK_REJECTED:{failed_rule}"
            return {
                "classification": classification,
                "bar_ts": entry.get("bar_ts"),
                "direction": setup.get("direction"),
                "entry": setup.get("entry"),
                "stop": setup.get("stop"),
                "target": setup.get("target"),
                "rr_ratio": setup.get("rr_ratio"),
                "confluence_grade": confluence.get("grade"),
                "confluence_score": confluence.get("score"),
                "risk_failed_rule": failed_rule,
                "risk_reason": risk.get("reason"),
            }
        # decision == TRADE and risk approved
        order_id = entry.get("paper_order_id")
        outcome = outcomes_by_order.get(order_id, {})
        result = outcome.get("result")
        return {
            "classification": "FILLED" if result and result != "CANCELLED" else "ENTRY_NOT_FILLED",
            "bar_ts": entry.get("bar_ts"),
            "direction": setup.get("direction"),
            "entry": setup.get("entry"),
            "stop": setup.get("stop"),
            "target": setup.get("target"),
            "rr_ratio": setup.get("rr_ratio"),
            "confluence_grade": confluence.get("grade"),
            "confluence_score": confluence.get("score"),
            "result": result,
            "exit_reason": outcome.get("exit_reason"),
            "pnl_gross": outcome.get("pnl_dollars"),
            "entry_price_filled": outcome.get("entry_price"),
            "exit_price": outcome.get("exit_price"),
        }

    # decision is NO_TRADE at the exact trigger bar -- this candidate was
    # blocked before signal confirmation. Report every gate present.
    gates = list(entry.get("failed_gates") or [])
    parity_gates = [g for g in gates if g in SIGNAL_LAYER_PARITY_GATES]
    return {
        "classification": "PARITY_GATE_BLOCKED" if parity_gates else f"OTHER_SIGNAL_BLOCKED:{gates}",
        "bar_ts": entry.get("bar_ts"),
        "gates_observed": gates,
        "reason": entry.get("reason"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="e.g. main_baseline or corrected")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    base_config = load_config()
    if base_config.runner_mode is not False:
        raise RuntimeError("expected production default runner_mode=False")

    config = dataclasses.replace(
        base_config,
        enabled_concepts=[STRATEGY],
        disabled_concepts_per_instrument={},
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root=dict(CANONICAL_ENTRY_TOLERANCE),
        expected_timeframe_minutes=5,
    )

    results = []
    with tempfile.TemporaryDirectory(prefix="strat_322_parity_") as tmp:
        log_root = Path(tmp)
        for cand in KNOWN_CANDIDATES:
            day = cand["date"]
            candle_path = CORPUS_DIR / f"{INSTRUMENT}_{day}.jsonl"
            if not candle_path.exists():
                results.append({
                    "date": day,
                    "known_direction": cand["direction"],
                    "research_result": cand["research_result"],
                    "research_net": cand["research_net"],
                    "classification": "CORPUS_DATE_MISSING",
                })
                continue
            rows = _load_corpus_rows(candle_path)
            trigger_bar_ts, pure_candidate, terminal_status = _find_trigger_bar_ts(rows, day)

            inst_log_dir = log_root / day
            inst_log_dir.mkdir(parents=True, exist_ok=True)
            engine = ReplayEngine(config=config, log_dir=str(inst_log_dir))
            engine.run(candle_path, review_date=day)
            journal_path = inst_log_dir / f"journal_{day}.jsonl"
            entries = list(_json_lines(journal_path))
            classification = _classify_day(entries, trigger_bar_ts)
            results.append({
                "date": day,
                "known_direction": cand["direction"],
                "research_result": cand["research_result"],
                "research_net": cand["research_net"],
                "pure_state_machine_status": terminal_status,
                "pure_state_machine_direction": (pure_candidate or {}).get("direction"),
                **classification,
            })
            print(f"[run] {day}: {classification['classification']} "
                  f"(pure_sm={terminal_status})", flush=True)

    summary = {}
    for r in results:
        summary[r["classification"]] = summary.get(r["classification"], 0) + 1

    out = {
        "label": args.label,
        "config": {
            "enabled_concepts": config.enabled_concepts,
            "expected_timeframe_minutes": config.expected_timeframe_minutes,
            "entry_tolerance_ticks_by_root": config.entry_tolerance_ticks_by_root,
            "entry_fill_model": config.entry_fill_model,
            "require_trending_condition": config.require_trending_condition,
            "require_strong_trend": dict(config.require_strong_trend),
            "min_rr_ratio": config.min_rr_ratio,
            "min_confluence_grade": getattr(config, "min_confluence_grade", None),
            "max_stop_ticks": dict(getattr(config, "max_stop_ticks", {}) or {}),
        },
        "candidate_count": len(KNOWN_CANDIDATES),
        "summary": summary,
        "trades": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n[done] wrote {args.out}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
