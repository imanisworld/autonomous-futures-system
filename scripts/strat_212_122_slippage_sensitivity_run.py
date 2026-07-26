#!/usr/bin/env python3
"""
scripts/strat_212_122_slippage_sensitivity_run.py

PR #337 evidence-completeness pass: 1/2/3-tick adverse-slippage sensitivity
for strat_212/strat_122, requested because commission sensitivity (the
existing $1.48/round-trip comm-adjusted column) is not a substitute for fill
slippage -- especially for strat_122, whose comm-adjusted edge is thin.

Uses PaperBroker's OWN adverse-slippage mechanism (execution/paper_broker.py
`slippage_ticks` -- applied to MARKET fills, i.e. the entry and any stop
exit; limit exits (target) fill clean per the broker's own docstring), wired
through unmodified via ReplayEngine.run()'s existing
`slippage_ticks=float(getattr(self.config, "fill_slippage_ticks", 0.0))`
read. This is the REAL fill path, not a flat-dollar approximation -- and a
flat per-trade approximation would in fact be WRONG here: a WIN (target,
limit fill) only eats slippage on its entry, while a LOSS (stop, market
fill) eats slippage on both entry and exit, so slippage's dollar impact is
NOT uniform across WIN/LOSS as a naive post-hoc subtraction would assume.

`config.fill_slippage_ticks` DEFAULTS TO 1.0 (config/settings.py), unmodified
by risk_rules.yaml or env in this repo -- so PR #337's existing FINAL
baseline (scripts/strat_212_122_canonical_evidence_results.json) ALREADY
reflects 1-tick adverse slippage through this exact mechanism. This script
does NOT rerun the 1-tick case (reuses the existing baseline journals/
results as the "1-tick" sensitivity point) and adds 2-tick and 3-tick
variants by overriding fill_slippage_ticks via the same in-memory
`dataclasses.replace()` isolation already used for `enabled_concepts` --
never written to risk_rules.yaml, verified unchanged on disk before/after,
exactly like scripts/strat_212_122_canonical_evidence_run.py.

No changes to strategy/replay-engine/risk/broker/config/deployment/corpus
logic -- this script only supplies a different in-memory config value to
the SAME unmodified ReplayEngine.

Usage:
    python3 scripts/strat_212_122_slippage_sensitivity_run.py [--fresh]
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
LOG_BASE = Path("logs/replay_strat212_122_slippage")

# 1-tick is the existing PR #337 FINAL baseline (config.fill_slippage_ticks
# default) -- not rerun here, reused from
# scripts/strat_212_122_canonical_evidence_raw_trades.jsonl. Only the
# ADDITIONAL sensitivity points are run by this script.
ADDITIONAL_SLIPPAGE_TICKS = (2, 3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fresh", action="store_true",
                         help="Re-run all days even if a journal already exists")
    args = parser.parse_args()

    base_config = load_config()
    before_enabled = tuple(base_config.enabled_concepts)
    before_disabled = {k: tuple(v) for k, v in base_config.disabled_concepts_per_instrument.items()}
    baseline_slippage = float(getattr(base_config, "fill_slippage_ticks", 0.0) or 0.0)
    if baseline_slippage != 1.0:
        print(f"[run] FATAL: expected config.fill_slippage_ticks == 1.0 (the "
              f"PR #337 baseline this script assumes), got {baseline_slippage}. "
              f"risk_rules.yaml or env may have changed -- do not silently "
              f"proceed with a mismatched baseline.", file=sys.stderr)
        return 1

    for slip in ADDITIONAL_SLIPPAGE_TICKS:
        iso_config = dataclasses.replace(
            base_config,
            enabled_concepts=["strat_212", "strat_122"],
            disabled_concepts_per_instrument={},
            fill_slippage_ticks=float(slip),
        )
        for instr in INSTRUMENTS:
            candle_dir = CANDLE_BASE / instr
            log_dir = LOG_BASE / f"slip_{slip}" / instr
            log_dir.mkdir(parents=True, exist_ok=True)
            files = sorted(candle_dir.glob(f"{instr}_*.jsonl"))
            if not files:
                print(f"[run] MISSING candle dir: {candle_dir}", file=sys.stderr)
                return 1

            engine = ReplayEngine(config=iso_config, log_dir=str(log_dir))
            print(f"[run] slippage={slip} {instr}: {len(files)} candle days -> {log_dir}")

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
                    print(f"[run] slippage={slip} {instr} {date_hint} ERROR: {exc}", file=sys.stderr)
                if i % 100 == 0 or i == len(files):
                    print(f"[run] slippage={slip} {instr} {i}/{len(files)} days processed "
                          f"(ran={ran} skipped={skipped} errors={errors})")

            print(f"[run] slippage={slip} {instr} DONE: ran={ran} skipped={skipped} errors={errors}")
            if errors:
                print(f"[run] slippage={slip} {instr}: {errors} day(s) errored -- see stderr above",
                      file=sys.stderr)

    # Prove this run never mutated the real config on disk.
    after_config = load_config()
    after_enabled = tuple(after_config.enabled_concepts)
    after_disabled = {k: tuple(v) for k, v in after_config.disabled_concepts_per_instrument.items()}
    after_slippage = float(getattr(after_config, "fill_slippage_ticks", 0.0) or 0.0)
    if (after_enabled != before_enabled or after_disabled != before_disabled
            or after_slippage != baseline_slippage):
        print("[run] FATAL: risk_rules.yaml drifted on disk (enabled_concepts, "
              "disabled_concepts_per_instrument, or fill_slippage_ticks)!", file=sys.stderr)
        return 1
    print("[run] risk_rules.yaml verified unchanged on disk (enabled_concepts, "
          "disabled_concepts_per_instrument, fill_slippage_ticks all identical "
          "before/after).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
