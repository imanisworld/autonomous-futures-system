#!/usr/bin/env python3
"""3-2-2 First Live -- hypothetical "all proven parity gates removed" ceiling.

Research-only. Makes ZERO changes to any committed file. The two additional
candidate parity defects found in scripts/strat_322_parity_validation.py
(ENTRY_DETACHED_FROM_PRICE, target_too_close) are bypassed via in-process
monkeypatch only, for the lifetime of this script's own Python process --
never written to disk, never touching strategy/signal_engine.py or
risk/risk_engine.py on this branch or any other.

Verified before writing this script (see report):
  - ENTRY_DETACHED_FROM_PRICE: for all 9 real historical candidates this
    gate stopped, the bracket at the actual IOC-limit trigger price is
    structurally sound (LONG: stop < entry < target; SHORT:
    target < entry < stop) -- the guard is checking the wrong fill model
    (bar-close market fill) for an IOC-limit strategy, not a real geometry
    defect.
  - target_too_close: for all 4 real historical candidates this gate
    stopped, the target matches the canonical 8AM-boundary detector/replay
    geometry exactly (verified against docs/strategy-rules/evidence_322/
    group1_corrected_baseline.json) -- the risk-layer min_target_points
    check is a second, independent enforcement of a rule the signal layer
    already carves this strategy out of (_enforce_min_target_distance,
    matching the strat_4hr_retrigger/STRAT_212/STRAT_122 bypass), same
    double-enforcement shape as the already-fixed min_rr_ratio gate.

Both monkeypatches are SAFE in this isolated single-strategy
(enabled_concepts=["strat_322_first_live"]) run: no other strategy's
candidate can ever reach either check here, so there is no risk of
silently affecting a different strategy's evidence.

Explicitly PRESERVED, not patched (per operator instruction):
  - max_stop_ticks / stop_too_wide (RiskEngine)
  - min_confluence_grade (RiskEngine)
  - MARKET_CONDITION_NOT_TRADABLE (CHOPPY/DEAD, signal layer)
  - SIGNAL_BAR_VOLUME_TOO_LOW (signal layer)
  - every other real RiskEngine control (drawdown, daily loss, position
    sizing, session, contracts -- untouched, exercised normally)

Must be run against the `claude/paper-execution-parity-fixes` (#365) code
checkout, same as scripts/strat_322_parity_validation.py's "corrected"
pass, since it also needs #365's four already-authorized exemptions
(TRENDING/STRONG-trend/EMA-stack/min_rr_ratio) active.

Usage:
    python3 scripts/strat_322_parity_ceiling_pass.py --out <path>
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402
from strategy.signal_engine import DecisionEngine  # noqa: E402
from risk.risk_engine import RiskEngine  # noqa: E402
from strategy.strat_322_first_live import advance_strat_322_first_live  # noqa: E402

STRATEGY = "strat_322_first_live"
INSTRUMENT = "MNQ"
CORPUS_DIR = REPO / "data" / "replay_corpus_v1_5m" / INSTRUMENT
CANONICAL_ENTRY_TOLERANCE = {"MNQ": 32.0, "MES": 16.0}
COMMISSION_ROUND_TRIP = 1.48
HALVES = {"H1": ("2024-07-02", "2025-06-29"), "H2": ("2025-06-30", "2026-06-26")}

KNOWN_CANDIDATES = [
    "2024-08-02", "2024-08-14", "2024-08-22", "2024-08-30", "2024-09-06",
    "2024-09-11", "2024-09-12", "2024-10-10", "2024-10-11", "2024-11-26",
    "2024-12-23", "2025-01-20", "2025-02-07", "2025-02-12", "2025-02-26",
    "2025-03-17", "2025-03-21", "2025-05-01", "2025-06-04", "2025-06-05",
    "2025-06-26", "2025-06-27", "2025-08-11", "2025-08-27", "2025-09-05",
    "2025-09-18", "2025-09-30", "2025-10-10", "2025-10-16", "2026-03-06",
    "2026-04-01", "2026-05-12", "2026-05-13", "2026-06-11",
]


def _apply_hypothetical_monkeypatches() -> None:
    DecisionEngine._entry_bracket_straddles_price = staticmethod(
        lambda direction, entry, stop, target, price: True
    )

    def _no_min_target_check(self, setup, daily_state):  # noqa: ANN001, ARG001
        return None

    RiskEngine._check_min_target_distance = _no_min_target_check


def _json_lines(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _find_trigger_bar_ts(rows: list[dict], day: str):
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
    outcomes_by_order = {}
    for e in entries:
        if e.get("type") == "OUTCOME":
            oid = (e.get("outcome") or {}).get("paper_order_id") or e.get("paper_order_id")
            if oid:
                outcomes_by_order[oid] = e.get("outcome") or e

    if trigger_bar_ts is None:
        return {"classification": "NO_TRIGGER_IN_PURE_STATE_MACHINE"}

    at_trigger = [e for e in entries if e.get("bar_ts") == trigger_bar_ts]
    if not at_trigger:
        return {"classification": "TRIGGER_BAR_MISSING_FROM_JOURNAL"}

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
                "direction": setup.get("direction"), "entry": setup.get("entry"),
                "stop": setup.get("stop"), "target": setup.get("target"),
                "rr_ratio": setup.get("rr_ratio"),
                "confluence_grade": confluence.get("grade"),
                "risk_failed_rule": failed_rule, "risk_reason": risk.get("reason"),
            }
        order_id = entry.get("paper_order_id")
        outcome = outcomes_by_order.get(order_id, {})
        result = outcome.get("result")
        return {
            "classification": "FILLED" if result and result != "CANCELLED" else "ENTRY_NOT_FILLED",
            "direction": setup.get("direction"), "entry": setup.get("entry"),
            "stop": setup.get("stop"), "target": setup.get("target"),
            "rr_ratio": setup.get("rr_ratio"), "confluence_grade": confluence.get("grade"),
            "result": result, "exit_reason": outcome.get("exit_reason"),
            "pnl_gross": outcome.get("pnl_dollars"),
            "entry_price_filled": outcome.get("entry_price"),
            "exit_price": outcome.get("exit_price"),
        }

    gates = list(entry.get("failed_gates") or [])
    if "MARKET_CONDITION_NOT_TRADABLE" in gates:
        classification = "CHOPPY_DEAD_REJECTED"
    elif "SIGNAL_BAR_VOLUME_TOO_LOW" in gates:
        classification = "VOLUME_REJECTED"
    else:
        classification = f"OTHER_SIGNAL_BLOCKED:{gates}"
    return {"classification": classification, "gates_observed": gates, "reason": entry.get("reason")}


def _period_label(value: str) -> str:
    for label, (start, end) in HALVES.items():
        if start <= value <= end:
            return label
    return "OUT_OF_RANGE"


def _pf(values: list[float]) -> Optional[float]:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses:
        return round(wins / losses, 4)
    return None if not wins else float("inf")


def _max_drawdown(rows: list[dict]) -> float:
    equity = peak = max_dd = 0.0
    for r in sorted(rows, key=lambda x: x["date"]):
        equity += r["net_pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    _apply_hypothetical_monkeypatches()

    base_config = load_config()
    config = dataclasses.replace(
        base_config,
        enabled_concepts=[STRATEGY],
        disabled_concepts_per_instrument={},
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root=dict(CANONICAL_ENTRY_TOLERANCE),
        expected_timeframe_minutes=5,
    )

    results = []
    with tempfile.TemporaryDirectory(prefix="strat_322_ceiling_") as tmp:
        log_root = Path(tmp)
        for day in KNOWN_CANDIDATES:
            candle_path = CORPUS_DIR / f"{INSTRUMENT}_{day}.jsonl"
            if not candle_path.exists():
                results.append({"date": day, "classification": "CORPUS_DATE_MISSING"})
                continue
            rows = list(_json_lines(candle_path))
            trigger_bar_ts, _, terminal_status = _find_trigger_bar_ts(rows, day)

            inst_log_dir = log_root / day
            inst_log_dir.mkdir(parents=True, exist_ok=True)
            engine = ReplayEngine(config=config, log_dir=str(inst_log_dir))
            engine.run(candle_path, review_date=day)
            entries = list(_json_lines(inst_log_dir / f"journal_{day}.jsonl"))
            classification = _classify_day(entries, trigger_bar_ts)
            results.append({
                "date": day, "pure_state_machine_status": terminal_status,
                "half": _period_label(day), **classification,
            })
            print(f"[run] {day}: {classification['classification']}", flush=True)

    summary = {}
    for r in results:
        summary[r["classification"]] = summary.get(r["classification"], 0) + 1

    filled = [r for r in results if r["classification"] == "FILLED"]
    for r in filled:
        gross = float(r.get("pnl_gross") or 0.0)
        r["net_pnl"] = round(gross - COMMISSION_ROUND_TRIP, 2)

    resolved = [r for r in filled if r.get("result") in ("WIN", "LOSS", "BREAKEVEN")]
    wins = [r for r in resolved if r["result"] == "WIN"]
    losses = [r for r in resolved if r["result"] == "LOSS"]
    net_values = [r["net_pnl"] for r in resolved]

    def _bucket_stats(rows: list[dict]) -> dict:
        rw = [r for r in rows if r.get("result") == "WIN"]
        rl = [r for r in rows if r.get("result") == "LOSS"]
        nv = [r["net_pnl"] for r in rows]
        return {
            "n": len(rows), "wins": len(rw), "losses": len(rl),
            "win_rate": round(len(rw) / len(rows), 4) if rows else None,
            "net_pnl": round(sum(nv), 2) if nv else 0.0,
            "pf": _pf(nv) if nv else None,
            "expectancy": round(statistics.mean(nv), 2) if nv else None,
        }

    performance = {
        "resolved_fills": len(resolved),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(resolved), 4) if resolved else None,
        "net_pnl": round(sum(net_values), 2) if net_values else 0.0,
        "gross_pnl": round(sum(float(r.get("pnl_gross") or 0) for r in resolved), 2),
        "profit_factor": _pf(net_values) if net_values else None,
        "expectancy_per_fill": round(statistics.mean(net_values), 2) if net_values else None,
        "max_drawdown": _max_drawdown(resolved) if resolved else 0.0,
        "by_direction": {
            d: _bucket_stats([r for r in resolved if r.get("direction") == d])
            for d in ("LONG", "SHORT")
        },
        "by_half": {
            h: _bucket_stats([r for r in resolved if r.get("half") == h])
            for h in HALVES
        },
        "by_month": {},
    }
    by_month: dict[str, list[dict]] = {}
    for r in resolved:
        month = r["date"][:7]
        by_month.setdefault(month, []).append(r)
    performance["by_month"] = {m: _bucket_stats(rows) for m, rows in sorted(by_month.items())}

    out = {
        "candidate_count": len(KNOWN_CANDIDATES),
        "summary": summary,
        "performance_of_filled_population": performance,
        "trades": results,
        "hypothetical_exemptions_applied": [
            "ENTRY_DETACHED_FROM_PRICE (signal layer, DecisionEngine._entry_bracket_straddles_price)",
            "target_too_close (risk layer, RiskEngine._check_min_target_distance)",
        ],
        "gates_preserved": [
            "max_stop_ticks / stop_too_wide", "min_confluence_grade",
            "MARKET_CONDITION_NOT_TRADABLE (CHOPPY/DEAD)", "SIGNAL_BAR_VOLUME_TOO_LOW",
            "all other RiskEngine controls (drawdown, daily loss, position sizing, session, contracts)",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n[done] wrote {args.out}")
    print(json.dumps(summary, indent=2))
    print(json.dumps(performance, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
