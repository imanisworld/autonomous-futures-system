#!/usr/bin/env python3
"""MES 1-2-2 (strat_122) executable-parity audit.

Unlike the 4HR/3-2-2/Miyagi audits, strat_122's canonical evidence (#337,
scripts/strat_212_122_canonical_evidence_run.py) already ran through the
REAL ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker path,
calling strategy.strat_212_122.advance_strat_212_122 -- the exact function
live/replay both share (PR #319). Confirmed by direct read of that script,
not assumed. So there is no separate "research vs runtime" code-path split
to audit here, and no hidden gate-exemption set (strat_122/strat_212 are on
neither strategy/signal_engine.py's nor risk/risk_engine.py's _GATE_EXEMPT
set) -- a ceiling-pass-style exemption experiment does not apply.

The real open parity question is different and was independently confirmed
by reading risk_rules.yaml directly: #337's canonical run used
enabled_concepts=["strat_212","strat_122"] ONLY and cleared
disabled_concepts_per_instrument entirely (isolated). Current production
risk_rules.yaml has strat_122 genuinely enabled for MES (not in
disabled_concepts_per_instrument.MES) alongside orb_reclaim and
shadow-only vwap_hold -- a materially smaller set than the full 11-strategy
list, but still a DIFFERENT config than #337's 2-strategy isolation. If the
engine selects a single setup per bar, a bar where strat_122 would arm/
resolve in isolation could be silently preempted by a different strategy's
setup under the real production concept list -- something #337's isolated
run never tested.

Two passes:
  1. REPRODUCTION -- rerun #337's exact isolated config on today's code,
     compare against the committed MES strat_122 population in
     scripts/strat_212_122_canonical_evidence_raw_trades.jsonl. Confirms no
     drift since #337 merged (2026-07-26) before trusting it as the
     ground-truth candidate set for pass 2.
  2. PRODUCTION-CONFIG -- rerun the identical corpus/days under the ACTUAL
     current production config (load_config() unmodified -- real
     enabled_concepts + disabled_concepts_per_instrument from
     risk_rules.yaml), then look up what happened for MES/strat_122
     specifically at each of pass 1's known trigger bars. If the journal
     entry at that bar now shows a different strategy's setup (or no
     strat_122 involvement at all), that is real cross-strategy preemption;
     if identical, production matches the isolated evidence.

Corpus: data/replay_corpus_v1_market_condition_fixed, symlinked into this
worktree from the main worktree (gitignored, not duplicated) -- identical
to #337's own source.

Usage:
    python3 scripts/strat_122_executable_parity_audit.py --out <path>
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402

INSTRUMENT = "MES"
STRATEGY = "strat_122"
CORPUS = REPO / "data" / "replay_corpus_v1_market_condition_fixed"
KNOWN_TRADES = REPO / "scripts" / "strat_212_122_canonical_evidence_raw_trades.jsonl"


def _json_lines(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _load_known_mes_122() -> list[dict]:
    rows = []
    for row in _json_lines(KNOWN_TRADES):
        if row.get("instrument") == INSTRUMENT and row.get("strategy") == STRATEGY:
            rows.append(row)
    return sorted(rows, key=lambda r: r["date"])


def _run(config, log_dir: Path) -> dict[str, dict]:
    """Runs the full corpus for INSTRUMENT under `config`, returns bar_ts -> journal entry."""
    candle_dir = CORPUS / INSTRUMENT
    files = sorted(candle_dir.glob(f"{INSTRUMENT}_*.jsonl"))
    if not files:
        raise RuntimeError(f"no corpus files found in {candle_dir}")
    log_dir.mkdir(parents=True, exist_ok=True)
    engine = ReplayEngine(config=config, log_dir=str(log_dir))
    by_bar_ts: dict[str, dict] = {}
    for i, f in enumerate(files, 1):
        date_hint = f.stem.replace(f"{INSTRUMENT}_", "")
        engine.run(f, review_date=date_hint)
        journal_path = log_dir / f"journal_{date_hint}.jsonl"
        for entry in _json_lines(journal_path):
            bar_ts = entry.get("bar_ts") or entry.get("ts")
            if bar_ts:
                by_bar_ts[bar_ts] = entry
        if i % 50 == 0 or i == len(files):
            print(f"[run] {INSTRUMENT} {i}/{len(files)} days processed", flush=True)
    return by_bar_ts


def _classify_strat122(entry: Optional[dict]) -> dict[str, Any]:
    if entry is None:
        return {"classification": "NO_ENGINE_DECISION_AT_BAR"}
    setup = entry.get("setup") or {}
    decision = entry.get("decision")
    setup_strategy = setup.get("strategy")
    if setup_strategy == STRATEGY:
        if decision == "TRADE":
            return {"classification": "TRADE", "layer": "filled", "decision": decision,
                     "direction": setup.get("direction")}
        if decision == "RISK_REJECTED":
            risk = entry.get("risk_check") or {}
            return {"classification": f"RISK_REJECTED:{risk.get('failed_rule')}",
                     "layer": "risk", "decision": decision}
        return {"classification": f"OTHER_DECISION:{decision}", "decision": decision}
    # A different strategy's setup won this bar, or no setup at all.
    gates = entry.get("failed_gates") or []
    return {
        "classification": "PREEMPTED_BY_OTHER_STRATEGY" if setup_strategy else "NO_SETUP_AT_BAR",
        "winning_setup_strategy": setup_strategy,
        "decision": decision,
        "failed_gates": gates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    known = _load_known_mes_122()
    print(f"[setup] {len(known)} known MES strat_122 candidates from #337", flush=True)

    base_config = load_config()
    prod_enabled = list(base_config.enabled_concepts)
    prod_disabled = {k: list(v) for k, v in base_config.disabled_concepts_per_instrument.items()}
    print(f"[setup] production enabled_concepts ({len(prod_enabled)}): {prod_enabled}", flush=True)
    print(f"[setup] production disabled_concepts_per_instrument: {prod_disabled}", flush=True)

    iso_config = dataclasses.replace(
        base_config,
        enabled_concepts=["strat_212", "strat_122"],
        disabled_concepts_per_instrument={},
    )

    with tempfile.TemporaryDirectory(prefix="strat122_audit_") as tmp:
        tmp_path = Path(tmp)

        print("[pass 1] ISOLATED (reproducing #337's exact config) ...", flush=True)
        iso_entries = _run(iso_config, tmp_path / "isolated")

        print("[pass 2] PRODUCTION CONFIG (real risk_rules.yaml, unmodified) ...", flush=True)
        prod_entries = _run(base_config, tmp_path / "production")

    # Reconstruct per-known-candidate bar_ts from the isolated run: the
    # isolated journal entry classified TRADE for strat_122/MES on a given
    # date is our anchor bar. A single day CAN produce more than one
    # resolved strat_122 candidate (the state machine can re-arm after
    # resolving) -- keep every match per date, not just the last one, and
    # disambiguate known-vs-isolated by direction within that date.
    iso_by_date: dict[str, list[tuple[str, dict]]] = {}
    for bar_ts, entry in sorted(iso_entries.items()):
        setup = entry.get("setup") or {}
        if setup.get("strategy") == STRATEGY and entry.get("decision") == "TRADE":
            date = bar_ts[:10]
            iso_by_date.setdefault(date, []).append((bar_ts, entry))

    rows = []
    reproduction_mismatches = 0
    preemption_hits = 0
    consumed: dict[str, set[str]] = {}  # date -> set of bar_ts already matched to a known row
    for known_trade in known:
        date = known_trade["date"]
        candidates = iso_by_date.get(date, [])
        used = consumed.setdefault(date, set())
        # Prefer an unconsumed candidate whose direction matches the known trade.
        iso_match = next(
            ((bts, e) for bts, e in candidates
             if bts not in used and (e.get("setup") or {}).get("direction") == known_trade["direction"]),
            None,
        )
        row: dict[str, Any] = {
            "date": date,
            "known_direction": known_trade["direction"],
            "known_result": known_trade["result"],
            "known_pnl": known_trade["pnl"],
        }
        if iso_match is None:
            row["reproduction"] = "MISSING_IN_ISOLATED_RERUN"
            row["isolated_candidates_this_date"] = [
                {"bar_ts": bts, "direction": (e.get("setup") or {}).get("direction")}
                for bts, e in candidates
            ]
            reproduction_mismatches += 1
            rows.append(row)
            continue
        bar_ts, iso_entry = iso_match
        used.add(bar_ts)
        row["bar_ts"] = bar_ts
        row["reproduction_direction_match"] = True

        prod_entry = prod_entries.get(bar_ts)
        prod_classification = _classify_strat122(prod_entry)
        row["production"] = prod_classification
        if prod_classification["classification"] == "PREEMPTED_BY_OTHER_STRATEGY":
            preemption_hits += 1
        rows.append(row)

    extra_in_isolated = sorted(
        f"{date}@{bts}" for date, entries in iso_by_date.items()
        for bts, _ in entries if bts not in consumed.get(date, set())
    )

    out = {
        "known_candidate_count": len(known),
        "reproduction_mismatches": reproduction_mismatches,
        "extra_candidates_in_isolated_rerun_not_in_337": extra_in_isolated,
        "preemption_hits_under_production_config": preemption_hits,
        "production_config": {"enabled_concepts": prod_enabled, "disabled_concepts_per_instrument": prod_disabled},
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n[done] wrote {args.out}")
    print(f"reproduction_mismatches={reproduction_mismatches} "
          f"extra_in_isolated={len(extra_in_isolated)} "
          f"preemption_hits_under_production_config={preemption_hits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
