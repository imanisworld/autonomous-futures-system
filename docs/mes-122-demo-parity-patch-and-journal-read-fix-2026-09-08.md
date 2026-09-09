# MES 1-2-2: paper/replay parity patch + `_run_pass` journal-read fix, final in-sample rerun (2026-09-08)

**Verdict: EVIDENCE ONLY. Harness + paper-mode parity fix. No runtime, config, risk,
strategy, or deployment change. No live/Tradovate code touched.**

This is the "REQUIRED FINAL TEST BEFORE PAPER COLLECTION" pass from
`FUTURES_STRATEGY_REPAIR_HANDOFF_CURRENT_2026-09-08.md`. All 16 checklist items pass.
Do not treat this PR as paper-collection enablement — that is a separate, later PR.

## Fix 1: stop-gap pricing (`execution/paper_broker.py`)

`PaperBroker.resolve_position()` priced an already-resting stop at the stale stop level
even when the next bar's `open` had gapped through it (overnight/weekend). Now, if the
next bar's `open` is on or beyond the stop, the exit is priced from that open plus the
same adverse slippage used for ordinary stop fills (`exit_reason="STOP_GAP"`), checked
ahead of same-bar target/breakeven logic since the opening gap is causally first.
Ordinary intrabar same-bar pessimistic handling is unchanged when `open` is absent or the
bar didn't gap through the stop.

`webhook/runner.py` now passes `payload.open` into the `NextBarOHLC` built for the
forward-paper resolution call (previously only `high`/`low` were passed, so paper
demo runs could never see gap opens at all).

## Fix 2: strat_122 exempt from the 8h paper stale-position timeout (`webhook/runner.py`)

`strat_122` is evaluated as a swing-capable paper strategy — replay lets its positions
carry across day/weekend boundaries, but the paper forward-runner's safety net force-closed
*any* strategy's paper position after 8 hours regardless of price. `is_timed_out` is now
`position_age_hours > 8.0 and _open_pos_strategy != STRAT_122`. Every other strategy keeps
the existing 8h timeout unchanged. The price-scale-mismatch safety close (`is_price_mismatch`)
is untouched and still applies unconditionally, `strat_122` included.

## Fix 3: `_run_pass` journal-read-inside-the-loop defect (`scripts/mes_122_controlled_one_variable_tests.py`)

Same defect PR #537 fixed in `scripts/mes_122_fallback_full_engine_proof.py::_run`, but
`_run_pass` (the shared replay loop used by `mes_122_controlled_one_variable_tests.py`,
`mes_122_h1h2_context_decomposition.py`, `mes_122_slippage_path_decomposition.py`, and
`mes_122_current_baseline_slippage_stress.py`) had the identical bug and had not been
fixed: it read `journal_{date}.jsonl` immediately after `engine.run()` for that day.
`ReplayEngine` writes a carried trade's OUTCOME row into its **signal** date's journal
when the trade resolves on a later day file, so that row landed after the read and the
trade was classified `TRADE_UNRESOLVED`/dropped from the resolved-trade set. Fix: read all
`journal_*.jsonl` files once, after the full day loop (identical pattern to #537). Nothing
else in the harness changed; `carry_history` bookkeeping (unrelated to this bug) stays
inside the loop.

This means **the previously reported "corrected 40-trade" headline in the handoff had
never actually been reproduced by any harness run** — it was the target this PR set out
to reproduce for real, not a number already sitting in a script output.

## Regression suite

- New: `tests/test_paper_broker_gap_stop.py` (long/short gap-through-stop pricing +
  same-bar-without-open legacy behavior unchanged) — 3/3 pass.
- New: `tests/test_webhook.py::test_runner_does_not_timeout_strat_122_swing_position` —
  pass. Existing `test_runner_force_closes_stale_previous_day_position` (non-strat_122,
  8h timeout) and `test_runner_force_closes_friday_position_on_monday` unchanged and
  still pass — confirms every other strategy keeps its 8h timeout.
- Price-mismatch-still-applies-to-strat_122: confirmed by inspection, not a new test —
  the edit only touches `is_timed_out`; `is_stale = is_price_mismatch or is_timed_out`
  and `is_price_mismatch` itself are untouched.
- `tests/test_mes_122_controlled_one_variable_tests.py` and its 3 downstream-script test
  files: 11/11 pass, unchanged.
- Full existing PaperBroker regression suite (`test_paper_broker.py`,
  `test_paper_broker_bracket_guard_parity.py`, `test_paper_broker_entry_fill.py`) and full
  `test_webhook.py`: 188/188 pass.
- Full repo suite: **4932/4932 pass** (`pytest tests/ -q`, 107s).

## Corrected 40-trade in-sample rerun (`mes_122_h1h2_context_decomposition.py`, 1-tick slippage)

Rerun with Fix 1 + Fix 3 both applied, `data/replay_corpus_v1_market_condition_fixed`,
313 days.

| metric | old (pre-fix, `mes_122_h1h2_context_decomposition_2026-09-08.json` as found) | corrected (this rerun) |
|---|---|---|
| trades | 38 | **40** |
| wins / losses | 10 / 28 | **11 / 29** |
| commission-adjusted net | +$27.51 | **+$90.80** |
| commission-adjusted PF | — | **1.104** |
| H1 (commission-adjusted) | — | **-$80.85** |
| H2 (commission-adjusted) | — | **+$171.65** |
| max drawdown (commission-adjusted, mark-to-market) | — | **$219.11** |

Row-level diff (old vs corrected): **exactly two rows added, zero removed, zero existing
rows changed** —

- `2026-03-13T16:45:00+00:00` SHORT: added, **WIN +$150.00** (matches handoff's missing
  cross-day outcome exactly).
- `2026-06-19T14:15:00+00:00` LONG: added, **LOSS -$83.75** before commission, `exit_reason`
  now the gap-aware price (matches handoff's "approximately -$83.75" exactly — this is
  Fix 1 pricing the Sunday-reopen gap from the actual bar open, not the stale stop).

Every number the handoff pre-registered for the "with the known June weekend stop-gap
corrected" scenario (40 trades, 11W/29L, ~+$90.80, PF ~1.10, H1 negative, H2 positive,
DD ~$219.11) is now reproduced exactly by a real harness run, not estimated.

## OOS rerun, 2026-07-24 → 2026-09-08 (`scripts/mes_122_out_of_sample_run.py`)

Same script already used out of sample (imports the now-#537-fixed
`mes_122_fallback_full_engine_proof._run`, unaffected by Fix 3 since it doesn't go through
`_run_pass`). Rerun against the Fix-1/Fix-2-patched engine, 40-day Polygon corpus
(`data/replay_polygon/MES_oos_2026-07-24_2026-09-08/`):

| | isolated 1-2-2 | frozen #373 production-control |
|---|---|---|
| trades | 3 | 1 |
| W/L | 1W / 2L | 0W / 1L |
| net | +$35.00 | -$7.50 |
| preempted in control by shadow-only `vwap_hold` | 2 | — |

**Zero trades changed** versus the previously stored OOS result (identical `bar_ts` sets,
identical outcomes/classifications in both isolated and control passes) — this 40-day
window contains no overnight/weekend gap-through-stop event and no strat_122 position held
past 8h in the paper-forward path (this is a replay harness; it never exercises the
`webhook/runner.py` age-timeout code at all), so neither parity fix had anything to change
here. OOS did not invalidate the strategy; n=3/n=1 remains far too small to validate it.

## Checklist against the handoff (`FUTURES_STRATEGY_REPAIR_HANDOFF_CURRENT_2026-09-08.md`)

1. New long gap-through-stop test passes — ✅
2. New short gap-through-stop test passes — ✅
3. Ordinary intrabar stop behavior unchanged — ✅ (`test_static_stop_without_open_keeps_legacy_intrabar_pricing`)
4. Existing PaperBroker regression tests pass — ✅ (188/188)
5. Existing webhook position-resolution tests pass — ✅
6. Non-strat_122 stale paper position still gets the 8h timeout — ✅ (existing tests unchanged, pass)
7. strat_122 position not force-closed solely for being >8h old — ✅ (new test)
8. Price-mismatch protection still applies to strat_122 — ✅ (by inspection; logic untouched)
9. Corrected isolated MES 15m 1-2-2 rerun resolves 40 trades — ✅
10. Corrected population is 11W / 29L — ✅
11. June 19 gap-through loss ≈ -$83.75 before commission — ✅ (exact)
12. Total after $1.48 RT commission ≈ +$90.80 — ✅ (exact, $90.80)
13. Full mark-to-market max drawdown recomputed from the exact final run — ✅ ($219.11)
14. No unrelated historical trade changes — ✅ (row-level diff: only the two expected rows added)
15. OOS rerun 2026-07-24→2026-09-08, list any changed trade — ✅ (rerun; zero changed)
16. No runtime/deployment/config enablement in this PR — ✅

## Files touched

- `execution/paper_broker.py` — Fix 1
- `webhook/runner.py` — Fix 1 (payload wiring) + Fix 2
- `scripts/mes_122_controlled_one_variable_tests.py` — Fix 3
- `tests/test_paper_broker_gap_stop.py` — new
- `tests/test_webhook.py` — new `test_runner_does_not_timeout_strat_122_swing_position`
- `scripts/mes_122_h1h2_context_decomposition_2026-09-08.json` — corrected rerun output
- `scripts/mes_122_out_of_sample_2026-07-24_2026-09-08.json` — rerun output (unchanged vs prior)
- `scripts/mes_122_out_of_sample_run.py` — OOS runner (copied in verbatim from
  `claude/mes-out-of-sample-test-3465f9` for reproducibility; not modified)
- this doc

## Not in this PR

Per the handoff: no paper-collection enablement change. That is a separate, minimal PR
for a later, explicit decision.
