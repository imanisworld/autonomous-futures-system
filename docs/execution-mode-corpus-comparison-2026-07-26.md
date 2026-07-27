# Execution-mode corpus comparison (implemented modes, PR #357)

**Verdict: NO CURRENTLY MODELED/IMPLEMENTED EXECUTION MODE MAKES THE FROZEN SYSTEM PROFITABLE ON THIS CORPUS**

Pinned code: `b86eec690b7917f067d2daabcc9477584da451f0` (PR #357 merged)
Corpus: `4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4` (626 files — byte-identical to PR #346's corpus)
Range: 2025-07-24 → 2026-07-23

## Posture

- One full-corpus frozen-system replay per arm on identical pinned code —
  the PR #346 pipeline and posture exactly (breaker preserved, 1-tick
  adverse slippage, pessimistic same-bar, $1.48 RT at analysis layer).
- Arms differ ONLY in the entry fill model (in-memory config; the
  `market` arm additionally applies the production force_market_entry
  fill branch via a scoped, documented wrapper).
- `stop_limit` is NOT modeled in replay (no PaperBroker StopLimit entry
  model) — explicit gap, not approximated.
- System-path evidence: each arm's own losses can trip its own breaker;
  halted arms are censored from their halt date (reported per arm).

## Comparison (net after commission)

| Arm | Attempts | Fills | Fill rate | WR | Net after $1.48 RT | Exp net | PF net | Max DD net | Breaker halts |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| ioc_limit | 165 | 97 | 58.8% | 26.8% | $-802.28 | $-8.27 | 0.753 | $1,073.61 | MES 2025-12-11, MNQ 2025-09-08 |
| market | 108 | 108 | 100.0% | 41.7% | $-778.00 | $-7.20 | 0.767 | $1,393.64 | MES 2025-10-17, MNQ 2025-09-08 |
| marketable_limit | 537 | 101 | 18.8% | 28.7% | $-386.75 | $-3.83 | 0.867 | $806.44 | MES 2025-10-16 |
| stop_market | 110 | 109 | 99.1% | 41.3% | $-798.31 | $-7.32 | 0.762 | $1,468.43 | MES 2025-10-17, MNQ 2025-09-08 |

- PR #346 reference (same posture, pre-#357 code): 165 attempts, PF 0.753, $-802.28.

## Per-arm drawdown-breaker audit

- **ioc_limit**: `{"MES": {"first_rejection_bar_ts": "2025-12-11T16:30:00+00:00", "first_rejection_date": "2025-12-11", "last_order_attempt_date": "2025-12-11", "reason": "Account drawdown 22.0% exceeds max 20.0% from peak $1,500.00."}, "MNQ": {"first_rejection_bar_ts": "2025-09-08T09:45:00+00:00", "first_rejection_date": "2025-09-08", "last_order_attempt_date": "2025-09-08", "reason": "Account drawdown 21.9% exceeds max 20.0% from peak $1,500.00."}}`
- **market**: `{"MES": {"first_rejection_bar_ts": "2025-10-17T15:45:00+00:00", "first_rejection_date": "2025-10-17", "last_order_attempt_date": "2025-10-17", "reason": "Account drawdown 21.0% exceeds max 20.0% from peak $1,500.00."}, "MNQ": {"first_rejection_bar_ts": "2025-09-08T03:00:00+00:00", "first_rejection_date": "2025-09-08", "last_order_attempt_date": "2025-09-05", "reason": "Account drawdown 20.2% exceeds max 20.0% from peak $1,500.00."}}`
- **marketable_limit**: `{"MES": {"first_rejection_bar_ts": "2025-10-16T14:30:00+00:00", "first_rejection_date": "2025-10-16", "last_order_attempt_date": "2025-10-16", "reason": "Account drawdown 20.3% exceeds max 20.0% from peak $1,500.00."}, "MNQ": {"last_order_attempt_date": "2026-07-22"}}`
- **stop_market**: `{"MES": {"first_rejection_bar_ts": "2025-10-17T15:45:00+00:00", "first_rejection_date": "2025-10-17", "last_order_attempt_date": "2025-10-17", "reason": "Account drawdown 21.4% exceeds max 20.0% from peak $1,500.00."}, "MNQ": {"first_rejection_bar_ts": "2025-09-08T09:45:00+00:00", "first_rejection_date": "2025-09-08", "last_order_attempt_date": "2025-09-08", "reason": "Account drawdown 21.0% exceeds max 20.0% from peak $1,500.00."}}`

## Per-arm H1/H2

| Arm | H1 net | H1 PF | H2 attempts | H2 net | H2 PF |
|---|---:|---:|---:|---:|---:|
| ioc_limit | $-802.28 | 0.753 | 0 | $0.00 | — |
| market | $-778.00 | 0.767 | 0 | $0.00 | — |
| marketable_limit | $-389.49 | 0.820 | 234 | $2.74 | 1.004 |
| stop_market | $-798.31 | 0.762 | 0 | $0.00 | — |

## Limitations

- This verdict is scoped to the MODELED modes: it does NOT claim
  execution is irrelevant. stop_limit has no replay model yet, and
  marketable_limit materially changes the loss profile (smallest loss,
  lowest drawdown, and the only never-halting instrument lane) even
  though it does not produce positive expectancy.
- Replay-scale dollars; historical evidence, not live-fill proof.
- The marketable_limit arm uses PR #357's default 8-tick caps — note
  these are TIGHTER than the canonical IOC caps (MES 16t / MNQ 32t);
  it is the same IOC mechanism at the marketable default width.
- Attempt sets differ across arms by construction (fills change
  position blocking, session budgets, and breaker paths) — this is
  the system-path comparison, complementing the attempt-matched
  counterfactuals (#354/#355) and the causal trigger study (#356).

## Reproduction

```bash
ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES=16 ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ=32 \
python scripts/execution_mode_corpus_comparison.py \
  --corpus data/replay_corpus_v1_market_condition_fixed \
  --logs /private/tmp/execution_mode_comparison_logs \
  --out scripts/execution_mode_corpus_comparison_results.json \
  --raw scripts/execution_mode_corpus_comparison_raw.jsonl \
  --report docs/execution-mode-corpus-comparison-2026-07-26.md
```
