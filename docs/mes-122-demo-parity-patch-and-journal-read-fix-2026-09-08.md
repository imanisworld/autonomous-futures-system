# MES 1-2-2: paper/replay parity patch + `_run_pass` journal-read fix, final in-sample rerun (2026-09-08/09)

**Verdict: EVIDENCE + PAPER-RUNTIME PARITY CHANGE ONLY.** This PR does change paper-mode
runtime behavior — `PaperBroker.resolve_position()` and `webhook/runner.py`'s stale-position
safety net both now behave differently for MES strat_122 in the live/forward-paper webhook
path, not just in offline replay scripts. What it does **not** do: enable any strategy for
paper collection, deploy anything, touch the live/Tradovate order-submission path, or change
config/risk defaults. (An earlier draft of this doc said "no runtime change," which
overstated the claim — corrected here.)

This is the "REQUIRED FINAL TEST BEFORE PAPER COLLECTION" pass from
`FUTURES_STRATEGY_REPAIR_HANDOFF_CURRENT_2026-09-08.md`, plus the two corrections requested
in review of the first version of this PR (2026-09-09): the reported $219.11 drawdown was
closed-trade/realized-equity drawdown mislabeled as mark-to-market, and the 8h-timeout
exemption was scoped to `strat_122` alone rather than MES `strat_122` specifically (the test
proving it actually used MNQ). Both are fixed below. All 16 handoff checklist items plus the
2 follow-up corrections pass.
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

## Fix 2: MES strat_122 exempt from the 8h paper stale-position timeout (`webhook/runner.py`)

`strat_122` is evaluated as a swing-capable paper strategy — replay lets its positions
carry across day/weekend boundaries, but the paper forward-runner's safety net force-closed
*any* strategy's paper position after 8 hours regardless of price. The exemption is scoped
to **MES strat_122 only**: `is_mes_strat_122_swing = _open_pos_strategy == STRAT_122 and
instrument_root == "MES"`; `is_timed_out = position_age_hours > 8.0 and not
is_mes_strat_122_swing`. MNQ strat_122 and every other instrument/strategy pair keep the
existing 8h timeout unchanged. The price-scale-mismatch safety close (`is_price_mismatch`)
is untouched and still applies unconditionally, MES strat_122 included.

**Correction (2026-09-09):** the first version of this fix checked `_open_pos_strategy !=
STRAT_122` with no instrument condition — it exempted strat_122 on *any* instrument, and the
regression test that was supposed to prove the exemption actually seeded an MNQ position
(`_base_payload`'s default ticker), so it never caught this. Only MES strat_122 has earned
the exemption; MNQ strat_122 has no such evidence and must keep the ordinary timeout.

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
- New (2026-09-09, replaces the earlier MNQ-seeded test):
  `tests/test_webhook.py::test_runner_does_not_timeout_mes_strat_122_swing_position` (MES
  strat_122 swings past 8h), `test_runner_still_times_out_mnq_strat_122` (MNQ strat_122
  keeps the ordinary timeout), `test_runner_price_mismatch_still_force_closes_mes_strat_122`
  (price-scale-mismatch safety close still fires for MES strat_122, isolated from ordinary
  stop/target logic by using a deliberately wide stop/target bracket). Existing
  `test_runner_force_closes_stale_previous_day_position` (non-strat_122, MNQ, 8h timeout)
  and `test_runner_force_closes_friday_position_on_monday` unchanged and still pass —
  confirms every other instrument/strategy pair keeps its 8h timeout.
- `tests/test_mes_122_controlled_one_variable_tests.py` and its 3 downstream-script test
  files: 11/11 pass, unchanged.
- Full existing PaperBroker regression suite (`test_paper_broker.py`,
  `test_paper_broker_bracket_guard_parity.py`, `test_paper_broker_entry_fill.py`) and full
  `test_webhook.py`: 201/201 pass.
- Full repo suite: **4930/4930 pass, 4 skipped** (`pytest tests/ -q`, 105s; skips are
  pre-existing and unrelated to this change).

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
| max drawdown (commission-adjusted, closed-trade / realized-equity) | — | **$219.11** |

**Correction (2026-09-09): the $219.11 figure above is closed-trade/realized-equity
drawdown, not mark-to-market.** `scripts/mes_122_fallback_full_engine_proof.py::_metrics`
(reused by every script in this chain) computes it by running a peak/trough tracker over
the *list of resolved trade P&Ls only* — it never marks an open position against the bars
between entry and exit, so a deep intra-trade drawdown that recovered before the trade
closed would be invisible to it. The first version of this doc called $219.11
"mark-to-market," which was wrong. See "True mark-to-market drawdown" below for the actual
bar-by-bar MTM figure.

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

## True mark-to-market drawdown (`scripts/mes_122_mtm_drawdown.py`, new, 2026-09-09)

Bar-by-bar swing-risk reconstruction across the corrected 40 trades, as requested in review.
Starting balance $1,500, fixed 1 MES, 15m bars, `data/replay_corpus_v1_market_condition_fixed`.

Method: reproduces the identical 40-trade population (same isolated-baseline `_run_pass`
config as the h1h2 script), then for each trade either (a) walks forward bar-by-bar using
the *same* `PaperBroker.resolve_position()` call the engine itself uses — 1-tick slippage,
`pessimistic_both_hit=True`, no breakeven, no runner, static exit, Fix-1 gap-aware stop
fills — marking realized+unrealized equity at every bar close while the position stays
open and separately tracking the bar's intrabar high/low adverse extreme, or (b) for the 7
of 40 trades that are "same-bar pre-resolved" (armed entry boundary and its opposite/stop
boundary both crossed on the single watched bar — `replay_engine.py`'s
`decision.setup.pre_resolved` branch, `force_resolve()` at the exact boundary price, no
resting position ever exists to walk forward) books the already-verified outcome directly,
using that one watched bar's own OHLC for the adverse-excursion figure. Every trade's
rebuilt result/exit_reason/pnl is cross-checked against the already-verified population
(script raises loudly on any mismatch — none occurred). `raw_net` reproduces $150.00 and
`commission_adjusted_net` reproduces +$90.80 exactly, confirming this is the same 40 trades.

| metric | value |
|---|---|
| **true max MTM drawdown** | **$219.11** — independently reproduces the closed-trade figure exactly |
| max MTM drawdown point | 2026-02-16, trade #21 (LONG, entry 6877.25), a 45-minute `STOP_HIT` LOSS — a normal trade **close**, not a mid-swing mark |
| worst per-trade MAE | **-$196.25**, 2026-07-10 LONG (trade #39) — realized close was only -$26.25 (`STOP_HIT`); price moved much further against the position intrabar before the ordinary pessimistic-stop exit than the final booked loss shows |
| worst unrealized dollar loss (any bar) | **-$196.25** (same trade/bar as above); account equity at that instant was $1,422.28 — well above the eventual $1,369.94 trough, so this is *not* the account's worst moment, just the single worst intrabar excursion |
| longest hold | **55.75 hours**, 2026-06-19 LONG → 2026-06-21 22:00 UTC (trade #37, the `STOP_GAP` weekend trade) |
| overnight holds | **2** (2026-03-13 SHORT WIN, 2026-06-19 LONG LOSS — the same two cross-day trades Fix 3 recovered) |
| weekend holds | **2** (same two trades) |

The true MTM drawdown genuinely reproduces $219.11 — this is not a coincidence of rounding;
the point of maximum drawdown is a normal same-day trade close (0.75h hold), and no
intervening open position (including the two overnight/weekend swings) ever produced an
unrealized dip deeper than what the closed-trade-only calculation already captured. The
worst single-trade tail event ($196.25 intrabar on a trade that only closed -$26.25) is real
and separate from the account-level drawdown record — exactly the swing/tail-risk
distinction the handoff's own "Swing / tail risk" section flagged as a known unknown.
Full per-trade detail and the complete equity curve: `scripts/mes_122_mtm_drawdown_2026-09-09.json`.

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
4. Existing PaperBroker regression tests pass — ✅ (201/201)
5. Existing webhook position-resolution tests pass — ✅
6. Non-strat_122 stale paper position still gets the 8h timeout — ✅ (existing tests unchanged, pass)
7. MES strat_122 position not force-closed solely for being >8h old — ✅ (new test; MNQ strat_122 explicitly proven to still time out, see follow-up #2 below)
8. Price-mismatch protection still applies to MES strat_122 — ✅ (new dedicated test, not just inspection — see follow-up #2)
9. Corrected isolated MES 15m 1-2-2 rerun resolves 40 trades — ✅
10. Corrected population is 11W / 29L — ✅
11. June 19 gap-through loss ≈ -$83.75 before commission — ✅ (exact)
12. Total after $1.48 RT commission ≈ +$90.80 — ✅ (exact, $90.80)
13. Full drawdown recomputed from the exact final run — ✅ closed-trade $219.11; **true mark-to-market drawdown independently reproduced at $219.11** — see follow-up #1 below
14. No unrelated historical trade changes — ✅ (row-level diff: only the two expected rows added)
15. OOS rerun 2026-07-24→2026-09-08, list any changed trade — ✅ (rerun; zero changed)
16. No runtime/deployment/config enablement in this PR — ✅ (paper-runtime *behavior* does change — see corrected verdict wording above)

### Follow-up corrections requested in review (2026-09-09), both resolved

1. **True mark-to-market drawdown** — computed independently via
   `scripts/mes_122_mtm_drawdown.py`; reproduces $219.11 exactly, plus worst per-trade MAE
   (-$196.25), worst unrealized dollar loss (-$196.25), longest hold (55.75h), and
   overnight/weekend hold counts (2/2). See "True mark-to-market drawdown" section above.
2. **8h-timeout exemption scoped to MES only** — `is_mes_strat_122_swing` now requires both
   `strategy == strat_122` and `instrument_root == "MES"`. Proven by 3 tests: MES strat_122
   swings past 8h, MNQ strat_122 still times out, price-mismatch still force-closes MES
   strat_122 (isolated from ordinary stop/target logic).

## Files touched

- `execution/paper_broker.py` — Fix 1
- `webhook/runner.py` — Fix 1 (payload wiring) + Fix 2 (MES-scoped)
- `scripts/mes_122_controlled_one_variable_tests.py` — Fix 3
- `scripts/mes_122_mtm_drawdown.py` — new, true bar-by-bar MTM drawdown reconstruction
- `tests/test_paper_broker_gap_stop.py` — new
- `tests/test_webhook.py` — replaces the earlier (MNQ-seeded) exemption test with
  `test_runner_does_not_timeout_mes_strat_122_swing_position`,
  `test_runner_still_times_out_mnq_strat_122`,
  `test_runner_price_mismatch_still_force_closes_mes_strat_122`
- `scripts/mes_122_h1h2_context_decomposition_2026-09-08.json` — corrected rerun output
- `scripts/mes_122_mtm_drawdown_2026-09-09.json` — new, full per-trade + equity-curve detail
- `scripts/mes_122_out_of_sample_2026-07-24_2026-09-08.json` — rerun output (unchanged vs prior)
- `scripts/mes_122_out_of_sample_run.py` — OOS runner (copied in verbatim from
  `claude/mes-out-of-sample-test-3465f9` for reproducibility; not modified)
- this doc

## Not in this PR

Per the handoff: no paper-collection enablement change. That is a separate, minimal PR
for a later, explicit decision.
