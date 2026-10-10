# Preregistration — MNQ 4HR re-trigger, 400-tick forward cell (2026-10-09)

<!-- trial_id: T-2026-10-09-prereg-4hr-mnq-400-forward-2026-10-09-01 -->

**Status: APPROVED FOR MEASUREMENT — NOT RUN.** Eligible session dates start **2026-10-12**. Approval does not enable the strategy, change a cap, move money, or submit an order.

**Trial ID:** `T-2026-10-09-prereg-4hr-mnq-400-forward-2026-10-09-01`

**Experiment ID:** `E-2026-10-09-4hr-mnq-400-forward-01`

This record freezes one measurement. It does not enable `strat_4hr_retrigger`, change the global 120-tick cap, change the wired 300-tick `wide_stop_4k` paper lane, move money, or submit an order.

## Question

On new MNQ 4HR re-trigger candidates admitted only when the stop is at most 400 ticks and reward-to-risk is at least 1.0, do one-contract IOC fills at 1 adverse tick finish with positive net P&L in both chronological halves and with less than 60% of net P&L in the top three months, once 40 fills exist?

## Population

MNQ strat_4hr_retrigger; one contract; stop cap 400 ticks ($200) and minimum reward-to-risk 1.0; isolated ledger starting balance $4,000; forward sessions strictly after this spec is operator-approved; the consumed historical cell 2024-07-09 through 2026-06-03 and every session through 2026-10-09 inclusive are ineligible and are never rescored as this trial's result

## Historical benchmark, already exposed

Reproduced 2026-10-09 from `scripts/edge_decomposition_audit_results_candidates.jsonl.gz`, lane `4hr_mnq`, `stop_ticks <= 400`, `rr >= 1.0`, resolved bracket only:

| Item | Value |
|---|---|
| Trades | 36 (20 wins / 16 losses) |
| Net | +$3,076.72 |
| Profit factor | 3.125 |
| Halves | +$1,433.86 / +$1,642.86 |
| Span | 2024-07-09 through 2026-06-03 |
| Top-3-month share | 66.8% (2025-05, 2026-01, 2025-07) |

That cell is the benchmark. It is not this trial's result. The wired 300-tick / R:R ≥ 1.0 cell on the same file is 32 trades, +$1,901.14, profit factor 2.313, and its top three months exceed 100% of net. This trial does not rescore either cell. Eligible dates start 2026-10-12.

## Pass and fail

Score only forward IOC fills after approval. One adverse tick. Static bracket. Round-turn cost stays the historical $1.48. No runner.

Pass only if all of these are true at the first look:

- at least 40 filled trades;
- net P&L positive;
- both chronological halves positive;
- top-three-month share of net under 60%.

Stop at 40 fills, or at 24 months from the approved start, whichever comes first. Fewer than 40 fills is `INSUFFICIENT SAMPLE`, not a pass and not a fail. A non-positive net, a non-positive half, or top-three-month share of 60% or more is a fail.

## What this approval does not do

The $1,500 book's 120-tick cap and 2.0 reward-to-risk floor stay. The 300-tick paper lane stays on its own contract. Strategy inventory verdict stays **PROMISING BUT UNPROVEN**. No deploy.
