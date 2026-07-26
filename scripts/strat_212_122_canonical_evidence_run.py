#!/usr/bin/env python3
"""
scripts/strat_212_122_canonical_evidence_run.py

STATUS (2026-07-26, updated after PR #338 merged main@0057bc23): the
market-condition parity blocker is FIXED and incorporated below -- this
script now reads data/replay_corpus_v1_market_condition_fixed, the output of
scripts/rematerialize_market_condition_corpus.py run against
data/replay_corpus_v1 (offline, no API pull needed --
reconstructed_market_condition was already present in every row per PR
#323). Reproduce with:
    python3 scripts/rematerialize_market_condition_corpus.py \
        --input data/replay_corpus_v1 \
        --output data/replay_corpus_v1_market_condition_fixed \
        --report /tmp/rematerialize_report.json

A SEPARATE shared-engine defect is still OUTSTANDING and NOT yet fixed:
replay_engine.py silently drops any position still OPEN at a daily-
candle-file boundary (proven by PR #333; fix dispatched on
claude/replay-engine-cross-day-position-carryforward, not yet merged as of
this note). Results from this corrected-corpus run are therefore still not
fully final -- they may still mishandle any strat_212/strat_122 position
that legitimately spans a day boundary (neither strategy is in
execution/day_only_exit.py's DAY_ONLY_STRATEGIES). See
memory/project_replay_engine_cross_day_position_carryforward.md. The
original data/replay_corpus_v1-based run (pre-#338, market-condition-tainted)
is preserved, not deleted, as
scripts/strat_212_122_canonical_evidence_results_pre_pr338_superseded.json
and the matching _raw_trades_pre_pr338_superseded.jsonl, for provenance.

Fresh canonical replay evidence for strat_212 (2-1-2) / strat_122 (1-2-2)
against the SAME post-#320-fix, directional, 313-day Polygon corpus already
used for the Corpus v1 clean baseline (data/replay_corpus_v1/{MNQ,MES},
2025-07-24 -> 2026-07-23, 15m bars, current_bar_type carries real "2U"/"2D").
See memory/project_corpus_v1_clean_baseline_scope.md for that corpus's own
provenance/reproduction notes.

This replaces the 2026-07-24 TradingView-CSV pass (see
memory/project_strat_212_122_fresh_replay_evidence.md), whose scratchpad-only
CSV prep (ORB-placeholder backfill + bar-direction reconstruction) was not
reproducible from a clean checkout -- the operator's blocker #1. Using
data/replay_corpus_v1 instead needs neither correction: it is already
directional (PR #320) and already the corpus this system treats as canonical
for every other strategy's evidence.

Reuses the real production pipeline unmodified -- no bespoke fill/detection
logic: ReplayEngine (DecisionEngine -> RiskEngine -> PaperBroker ->
JournalLogger) calls strategy.strat_212_122.advance_strat_212_122 directly,
the exact function live/replay both share (PR #319).

Isolation: enabled_concepts patched in-memory (dataclasses.replace) to
[strat_212, strat_122] ONLY, disabled_concepts_per_instrument cleared for
this run -- never written to risk_rules.yaml. Verified unchanged on disk
before and after. Journals land in a NEW, isolated log directory --
logs/replay_corpus_v1/ (the closed, separate, production-enabled_concepts
Corpus v1 lane) is never read or written by this script.

Usage:
    python3 scripts/strat_212_122_canonical_evidence_run.py [--fresh]
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_config  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402

INSTRUMENTS = ("MNQ", "MES")
CANDLE_BASE = Path("data/replay_corpus_v1_market_condition_fixed")
LOG_BASE = Path("logs/replay_strat212_122_canonical")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh", action="store_true",
                         help="Re-run all days even if a journal already exists")
    args = parser.parse_args()

    base_config = load_config()
    before_enabled = tuple(base_config.enabled_concepts)
    before_disabled = {k: tuple(v) for k, v in base_config.disabled_concepts_per_instrument.items()}

    iso_config = dataclasses.replace(
        base_config,
        enabled_concepts=["strat_212", "strat_122"],
        disabled_concepts_per_instrument={},
    )

    for instr in INSTRUMENTS:
        candle_dir = CANDLE_BASE / instr
        log_dir = LOG_BASE / instr
        log_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(candle_dir.glob(f"{instr}_*.jsonl"))
        if not files:
            print(f"[run] MISSING candle dir: {candle_dir}", file=sys.stderr)
            return 1

        engine = ReplayEngine(config=iso_config, log_dir=str(log_dir))
        print(f"[run] {instr}: {len(files)} candle days -> {log_dir}")

        ran = 0
        skipped = 0
        errors = 0
        for i, f in enumerate(files, 1):
            date_hint = f.stem.replace(f"{instr}_", "")
            if not args.fresh and (log_dir / f"journal_{date_hint}.jsonl").exists():
                skipped += 1
                continue
            try:
                engine.run(f, review_date=date_hint)
                ran += 1
            except Exception as exc:  # noqa: BLE001 - surface and keep going
                errors += 1
                print(f"[run] {instr} {date_hint} ERROR: {exc}", file=sys.stderr)
            if i % 50 == 0 or i == len(files):
                print(f"[run] {instr} {i}/{len(files)} days processed "
                      f"(ran={ran} skipped={skipped} errors={errors})")

        print(f"[run] {instr} DONE: ran={ran} skipped={skipped} errors={errors}")
        if errors:
            print(f"[run] {instr}: {errors} day(s) errored -- see stderr above", file=sys.stderr)

    # Prove this run never mutated the real config on disk.
    after_config = load_config()
    after_enabled = tuple(after_config.enabled_concepts)
    after_disabled = {k: tuple(v) for k, v in after_config.disabled_concepts_per_instrument.items()}
    if after_enabled != before_enabled or after_disabled != before_disabled:
        print("[run] FATAL: risk_rules.yaml enabled_concepts drifted on disk!", file=sys.stderr)
        return 1
    print("[run] risk_rules.yaml verified unchanged on disk (enabled_concepts + "
          "disabled_concepts_per_instrument identical before/after).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
