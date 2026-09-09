#!/usr/bin/env python3
"""Full-engine proof for the MES strat_122 ENTRY_DETACHED fallback hypothesis.

Evidence only. This script does not modify runtime code or config files.

It reuses the exact #373 proof shape:
  1. isolated #337 reproduction to anchor the 33 canonical MES strat_122 rows;
  2. current production-config control through ReplayEngine -> DecisionEngine ->
     RiskEngine -> PaperBroker;
  3. treatment through the same stack, with strategy fallback enabled ONLY on
     four pre-registered bars where #373 proved the higher-ranked setup failed
     ENTRY_DETACHED_FROM_PRICE.

The treatment is implemented as an in-process DecisionEngine subclass injected
only into ReplayEngine for the treatment pass. No repository/runtime behavior is
changed. The script records all changed decision bars and separately flags any
change involving a non-strat_122 executable trade.

The 313-day corpus is gitignored. Set AFS_122_CORPUS to a checkout containing
`data/replay_corpus_v1_market_condition_fixed`, or place that directory at the
normal repository path.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import statistics
import sys
import tempfile
from datetime import timezone
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
import replay.replay_engine as replay_module  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402
from strategy.signal_engine import DecisionEngine as BaseDecisionEngine  # noqa: E402

INSTRUMENT = "MES"
STRATEGY = "strat_122"
CORPUS = Path(
    os.environ.get("AFS_122_CORPUS")
    or (REPO / "data" / "replay_corpus_v1_market_condition_fixed")
)
KNOWN_TRADES = REPO / "scripts" / "strat_212_122_canonical_evidence_raw_trades.jsonl"

# Frozen from PR #373. These are the four bars whose higher-ranked setup failed
# exactly ENTRY_DETACHED_FROM_PRICE and whose lower-ranked MES strat_122 row was
# a canonical winner. Nothing else is eligible for the treatment.
TARGETS: dict[str, dict[str, Any]] = {
    "2025-10-24T12:30:00+00:00": {"winner": "orb_reclaim", "known_pnl": 97.50},
    "2026-02-20T13:30:00+00:00": {"winner": "vwap_hold", "known_pnl": 80.00},
    "2026-03-13T16:45:00+00:00": {"winner": "vwap_hold", "known_pnl": 150.00},
    "2026-03-26T11:15:00+00:00": {"winner": "vwap_hold", "known_pnl": 75.00},
}

EXPECTED_CONTROL = {
    "trades": 16,
    "wins": 5,
    "losses": 11,
    "net": 120.00,
    "profit_factor": 1.421053,
    "h1": 11.25,
    "h2": 108.75,
    "max_drawdown": 121.25,
}
EXPECTED_TREATMENT = {
    "trades": 20,
    "wins": 9,
    "losses": 11,
    "net": 522.50,
    "profit_factor": 2.833333,
    "h1": 82.50,
    "h2": 440.00,
    "max_drawdown": 121.25,
}


def _json_lines(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _load_known_mes_122() -> list[dict]:
    rows = [
        row
        for row in _json_lines(KNOWN_TRADES)
        if row.get("instrument") == INSTRUMENT and row.get("strategy") == STRATEGY
    ]
    return sorted(rows, key=lambda r: (r["date"], r["direction"]))


def _utc_iso(dt) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class ScopedFallbackDecisionEngine(BaseDecisionEngine):
    """Treatment-only DecisionEngine used by this evidence script.

    The normal production config is retained on every bar except the four
    frozen target timestamps. On those bars only, the existing candidate-loop
    fallback switch is enabled for the duration of one evaluate() call.
    """

    def evaluate(self, state, daily_state):  # type: ignore[override]
        bar_ts = _utc_iso(state.timestamp)
        if state.instrument != INSTRUMENT or bar_ts not in TARGETS:
            return super().evaluate(state, daily_state)

        original = self.config
        if getattr(original, "strategy_fallback_enabled", False):
            raise RuntimeError("control config unexpectedly has global strategy fallback enabled")
        self.config = dataclasses.replace(original, strategy_fallback_enabled=True)
        try:
            return super().evaluate(state, daily_state)
        finally:
            self.config = original


def _run(config, log_dir: Path, *, treatment: bool) -> dict[str, dict]:
    candle_dir = CORPUS / INSTRUMENT
    files = sorted(candle_dir.glob(f"{INSTRUMENT}_*.jsonl"))
    if not files:
        raise RuntimeError(
            f"no corpus files found in {candle_dir}; set AFS_122_CORPUS to the "
            "checkout containing replay_corpus_v1_market_condition_fixed"
        )

    log_dir.mkdir(parents=True, exist_ok=True)
    original_cls = replay_module.DecisionEngine
    replay_module.DecisionEngine = ScopedFallbackDecisionEngine if treatment else BaseDecisionEngine
    try:
        engine = ReplayEngine(config=config, log_dir=str(log_dir))
        by_bar_ts: dict[str, dict] = {}
        for i, path in enumerate(files, 1):
            date_hint = path.stem.replace(f"{INSTRUMENT}_", "")
            engine.run(path, review_date=date_hint)
            journal_path = log_dir / f"journal_{date_hint}.jsonl"
            for entry in _json_lines(journal_path):
                bar_ts = entry.get("bar_ts") or entry.get("ts")
                if bar_ts:
                    by_bar_ts[str(bar_ts)] = entry
            if i % 50 == 0 or i == len(files):
                print(
                    f"[{'treatment' if treatment else 'control'}] {i}/{len(files)} days",
                    flush=True,
                )
        return by_bar_ts
    finally:
        replay_module.DecisionEngine = original_cls


def _classify(entry: Optional[dict]) -> dict[str, Any]:
    if entry is None:
        return {"classification": "NO_ENGINE_DECISION_AT_BAR"}
    setup = entry.get("setup") or {}
    decision = entry.get("decision")
    setup_strategy = setup.get("strategy")
    if setup_strategy == STRATEGY:
        if decision == "TRADE":
            return {
                "classification": "TRADE",
                "strategy": STRATEGY,
                "direction": setup.get("direction"),
                "entry": setup.get("entry"),
                "stop": setup.get("stop"),
                "target": setup.get("target"),
                "rr_ratio": setup.get("rr_ratio"),
            }
        return {
            "classification": f"STRAT_122_{decision}",
            "decision": decision,
            "failed_gates": entry.get("failed_gates") or [],
        }
    return {
        "classification": "PREEMPTED_BY_OTHER_STRATEGY" if setup_strategy else "NO_SETUP_AT_BAR",
        "winning_setup_strategy": setup_strategy,
        "decision": decision,
        "failed_gates": entry.get("failed_gates") or [],
    }


def _signature(entry: Optional[dict]) -> tuple:
    if entry is None:
        return (None, None, None, ())
    setup = entry.get("setup") or {}
    return (
        entry.get("decision"),
        setup.get("strategy"),
        setup.get("direction"),
        tuple(entry.get("failed_gates") or []),
    )


def _metrics(rows: list[dict], key: str) -> dict[str, Any]:
    trades = [row for row in rows if row[key]["classification"] == "TRADE"]
    pnls = [float(row["known_pnl"]) for row in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    mid = len(pnls) // 2
    eq = peak = max_dd = 0.0
    for pnl in pnls:
        eq += pnl
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "net": round(sum(pnls), 2),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 6) if wins and losses else None,
        "h1": round(sum(pnls[:mid]), 2),
        "h2": round(sum(pnls[mid:]), 2),
        "max_drawdown": round(max_dd, 2),
        "trade_dates": [row["date"] for row in trades],
    }


def _matches(actual: dict, expected: dict) -> bool:
    for key, value in expected.items():
        if isinstance(value, float):
            if abs(float(actual[key]) - value) > 1e-5:
                return False
        elif actual[key] != value:
            return False
    return True


def _anchor_rows(known: list[dict], isolated: dict[str, dict], control: dict[str, dict], treatment: dict[str, dict]) -> tuple[list[dict], int]:
    iso_by_date: dict[str, list[tuple[str, dict]]] = {}
    for bar_ts, entry in sorted(isolated.items()):
        setup = entry.get("setup") or {}
        if setup.get("strategy") == STRATEGY and entry.get("decision") == "TRADE":
            iso_by_date.setdefault(bar_ts[:10], []).append((bar_ts, entry))

    consumed: dict[str, set[str]] = {}
    rows: list[dict] = []
    mismatches = 0
    for known_trade in known:
        date = known_trade["date"]
        candidates = iso_by_date.get(date, [])
        used = consumed.setdefault(date, set())
        match = next(
            (
                (bar_ts, entry)
                for bar_ts, entry in candidates
                if bar_ts not in used
                and (entry.get("setup") or {}).get("direction") == known_trade["direction"]
            ),
            None,
        )
        if match is None:
            mismatches += 1
            rows.append({
                "date": date,
                "known_direction": known_trade["direction"],
                "known_result": known_trade["result"],
                "known_pnl": known_trade["pnl"],
                "reproduction": "MISSING_IN_ISOLATED_RERUN",
            })
            continue
        bar_ts, _ = match
        used.add(bar_ts)
        rows.append({
            "date": date,
            "bar_ts": bar_ts,
            "known_direction": known_trade["direction"],
            "known_result": known_trade["result"],
            "known_pnl": known_trade["pnl"],
            "control": _classify(control.get(bar_ts)),
            "treatment": _classify(treatment.get(bar_ts)),
        })
    return rows, mismatches


def run_proof(out_path: Path) -> dict[str, Any]:
    known = _load_known_mes_122()
    if len(known) != 33:
        raise RuntimeError(f"canonical MES strat_122 population drifted: expected 33, got {len(known)}")

    base = load_config()
    if getattr(base, "strategy_fallback_enabled", False):
        raise RuntimeError("production control unexpectedly has strategy_fallback_enabled=True")
    isolated_cfg = dataclasses.replace(
        base,
        enabled_concepts=["strat_212", "strat_122"],
        disabled_concepts_per_instrument={},
    )

    with tempfile.TemporaryDirectory(prefix="mes122_full_engine_") as tmp:
        root = Path(tmp)
        print("[pass 1] isolated #337 reproduction", flush=True)
        isolated = _run(isolated_cfg, root / "isolated", treatment=False)
        print("[pass 2] production control", flush=True)
        control = _run(base, root / "control", treatment=False)

        # Fail before treatment if the historical target premise no longer exists.
        target_control: dict[str, Any] = {}
        for bar_ts, expected in TARGETS.items():
            cls = _classify(control.get(bar_ts))
            target_control[bar_ts] = cls
            if cls.get("classification") != "PREEMPTED_BY_OTHER_STRATEGY":
                raise RuntimeError(f"target {bar_ts} no longer preempted in control: {cls}")
            if cls.get("winning_setup_strategy") != expected["winner"]:
                raise RuntimeError(f"target {bar_ts} winner drifted: {cls}")
            if "ENTRY_DETACHED_FROM_PRICE" not in cls.get("failed_gates", []):
                raise RuntimeError(f"target {bar_ts} is no longer an ENTRY_DETACHED failure: {cls}")

        print("[pass 3] scoped treatment", flush=True)
        treatment = _run(base, root / "treatment", treatment=True)

    rows, reproduction_mismatches = _anchor_rows(known, isolated, control, treatment)
    control_metrics = _metrics(rows, "control")
    treatment_metrics = _metrics(rows, "treatment")

    target_treatment: dict[str, Any] = {}
    for bar_ts in TARGETS:
        cls = _classify(treatment.get(bar_ts))
        target_treatment[bar_ts] = cls

    canonical_changes = [
        {
            "bar_ts": row.get("bar_ts"),
            "date": row["date"],
            "control": row.get("control"),
            "treatment": row.get("treatment"),
        }
        for row in rows
        if row.get("bar_ts")
        and row.get("control", {}).get("classification") != row.get("treatment", {}).get("classification")
    ]
    non_target_canonical_changes = [
        row for row in canonical_changes if row.get("bar_ts") not in TARGETS
    ]

    changed_bars = []
    collateral_trade_changes = []
    for bar_ts in sorted(set(control) | set(treatment)):
        if _signature(control.get(bar_ts)) == _signature(treatment.get(bar_ts)):
            continue
        c = control.get(bar_ts)
        t = treatment.get(bar_ts)
        row = {"bar_ts": bar_ts, "control": _signature(c), "treatment": _signature(t)}
        changed_bars.append(row)
        c_setup = (c or {}).get("setup") or {}
        t_setup = (t or {}).get("setup") or {}
        c_trade = (c or {}).get("decision") == "TRADE"
        t_trade = (t or {}).get("decision") == "TRADE"
        if (c_trade and c_setup.get("strategy") != STRATEGY) or (t_trade and t_setup.get("strategy") != STRATEGY):
            collateral_trade_changes.append(row)

    targets_recovered = all(
        cls.get("classification") == "TRADE" and cls.get("strategy") == STRATEGY
        for cls in target_treatment.values()
    )
    report = {
        "corpus": str(CORPUS),
        "canonical_candidates": len(known),
        "reproduction_mismatches": reproduction_mismatches,
        "targets": TARGETS,
        "target_control": target_control,
        "target_treatment": target_treatment,
        "control": control_metrics,
        "treatment": treatment_metrics,
        "expected_control": EXPECTED_CONTROL,
        "expected_treatment": EXPECTED_TREATMENT,
        "control_reproduced": _matches(control_metrics, EXPECTED_CONTROL),
        "treatment_matches_counterfactual": _matches(treatment_metrics, EXPECTED_TREATMENT),
        "all_four_targets_recovered": targets_recovered,
        "canonical_changes": canonical_changes,
        "non_target_canonical_changes": non_target_canonical_changes,
        "all_changed_bar_count": len(changed_bars),
        "changed_bars": changed_bars,
        "collateral_non_strat122_trade_changes": collateral_trade_changes,
    }
    report["proof_pass"] = bool(
        report["reproduction_mismatches"] == 0
        and report["control_reproduced"]
        and report["all_four_targets_recovered"]
        and report["treatment_matches_counterfactual"]
        and not report["non_target_canonical_changes"]
        and not report["collateral_non_strat122_trade_changes"]
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "control": control_metrics,
        "treatment": treatment_metrics,
        "proof_pass": report["proof_pass"],
        "non_target_canonical_changes": len(non_target_canonical_changes),
        "collateral_non_strat122_trade_changes": len(collateral_trade_changes),
    }, indent=2))
    print(f"wrote {out_path}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "scripts" / "mes_122_fallback_full_engine_proof_2026-09-08.json",
    )
    args = parser.parse_args()
    run_proof(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
