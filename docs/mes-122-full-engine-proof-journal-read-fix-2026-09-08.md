# MES 1-2-2 full-engine proof: journal-read defect fix and in-sample rerun (2026-09-08)

**Verdict: EVIDENCE ONLY. Harness fix only. No runtime, config, risk, or strategy change.**

## Defect

`scripts/mes_122_fallback_full_engine_proof.py::_run` read `journal_{date}.jsonl` immediately after
`engine.run(path)` for that day. `ReplayEngine` writes a carried trade's OUTCOME row into the journal of
its **signal** date (`replay/replay_engine.py`, `for_date=_carried["journal_date"]`) when the trade
resolves on a later day file. That row was appended after the read, so the trade was classified
`TRADE_UNRESOLVED`. First confirmed out of sample on 2026-08-18; this PR reproduces it in sample.

## Fix

Journals are read once, after all 313 days have been replayed. Nothing else in the harness changed.

## In-sample rerun (313 days x 3 passes, `data/replay_corpus_v1_market_condition_fixed`)

Baseline = HEAD `8372edd` script. Fixed = this PR. Results are byte-identical with and without a `.env`.

| metric | baseline control | fixed control | baseline treatment | fixed treatment | EXPECTED_CONTROL | EXPECTED_TREATMENT |
|---|---|---|---|---|---|---|
| trades | 9 | 9 | 10 | 10 | 16 | 20 |
| wins / losses | 2 / 7 | 2 / 7 | 3 / 7 | 3 / 7 | 5 / 11 | 9 / 11 |
| net | -81.25 | -81.25 | +158.75 | +158.75 | +120.00 | +522.50 |
| profit factor | 0.733 | 0.733 | 1.523 | 1.523 | 1.421 | 2.833 |
| max drawdown | 242.50 | 242.50 | 242.50 | 242.50 | 121.25 | 121.25 |
| reproduction mismatches | 14 | 14 | | | 0 | 0 |
| proof_pass | false | false | | | | |

**The fix does not change control or treatment metrics.** It changes exactly two bars:

- `2026-03-13T16:45Z` (pre-registered target): treatment `strat_122` SHORT goes `TRADE_UNRESOLVED` ->
  `TRADE_RESOLVED` WIN +$450.00. This is the in-sample instance of the defect. It does not move the
  metrics because the same bar is `MISSING_IN_ISOLATED_RERUN` (see below) and so is not an anchored row.
- `2026-04-27T17:15Z`: an `orb_reclaim` LONG in both control and treatment now has its late OUTCOME read
  (control -210.00 vs treatment -262.50). A collateral divergence that was masked as "unresolved" is now
  counted: collateral non-strat_122 trade changes 25 -> 26.

## Why the proof fails at HEAD (independent of this fix)

PR #514 states the full replay had **not** run; `EXPECTED_CONTROL` / `EXPECTED_TREATMENT` were never
reproduced by this harness. On current code:

- **Isolated pass:** 14 of 33 canonical rows, every row from 2026-03-13 onward, are `RISK_REJECTED`
  by the `max_drawdown` gate ("Account drawdown 22.0% exceeds max 20.0% from peak $1,500.00"). They
  never enter the anchored row set, so metrics are computed over 19 rows, not 33.
- **Sizing:** the isolated pass fills 1 contract (2026-02-20 = +$80.00, equal to canonical). The
  production-config control/treatment passes fill the same targets at 3x-4x (+240 / +450 / +300).

Both are current-code drift versus the #373-era control. The harness is designed to fail closed on that
rather than recreate old code, and it does. Resolving the drift is a separate decision; this PR does not
attempt it.

## Files

- `scripts/mes_122_fallback_full_engine_proof.py` (fix)
- `scripts/mes_122_fallback_full_engine_proof_2026-09-08.json` (fixed-run full report)
- `scripts/mes_122_fallback_full_engine_proof_journal_read_fix_2026-09-08.json` (baseline vs fixed summary)
