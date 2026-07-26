# Stop-market-at-the-level counterfactual on the 165 corrected-IOC attempts

**Verdict: LATENCY IS MATERIAL — AT-LEVEL ENTRY POSITIVE AT 1 AND 2 TICKS; ENTRY TIMING/EXECUTION ARCHITECTURE IS A LEGITIMATE RESEARCH LANE**

Pinned code: `bc03eaf015626b439333cec77f6afb3fc6762fbd`
Corpus: `4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4` (626 files — byte-identical to PR #346's corpus)
Attempt population: the 165 committed PR #346 attempts (same join contract as PR #354, 165/165 by `paper_order_id`).
Range: 2025-07-24 → 2026-07-23

## Question and posture

- #346: IOC-limit system loses (PF 0.753). #354: honest market entry at the 15m decision-bar close still loses overall (PF 0.861); the 68 missed attempts were the better trades but the ~61-tick chase leaves ~breakeven.
- Remaining question (operator-scoped): bad signal, or bad 15-minute reaction latency? Discriminator: enter AT the planned level, aggressive stop-market fill, realistic adverse slippage. Same frozen signals, stops, targets, costs, pessimistic exits. Nothing else changes.
- **LEVEL** (primary): zero-latency bound — fill at level ± slip, valid only if the decision bar traded the level; decision-bar range handled pessimistically (range touching the ordered stop books an immediate LOSS; a range touching the target is never awarded — the position must prove out on later bars via the production resolver).
- **ARMED** (secondary): the shipped `entry_fill_model="stop_market"` exactly as built — armed at decision close, one-next-bar causal activation (gap → next open ± slip; touch → level ± slip; else fails closed), production resolution. What today's architecture could do.
- $1.48 RT commission, analysis layer; slippage tiers 1/2/3/4 ticks (1 = primary). Isolated per-attempt brokers (no breaker — #346 owns the system path). All 165 attempts are H1.
- Evidence orchestration only: zero strategy/replay/broker/risk/config/deployment/Pine edits.

## Pre-registered decision rule (operator's)

- LEVEL all-165 net ≤ 0 or PF ≤ 1 at 1 tick → strategy logic is the problem; stop investigating execution.
- Positive at 1 tick only → marginal; not a research lane.
- Net > 0 AND PF > 1 at both 1 and 2 ticks → latency is material; entry timing/execution architecture becomes a legitimate research lane.

## LEVEL variant (zero-latency bound) — cohorts at 1 tick

| Scope | Attempts | Triggered | Resolved | Open | Imm. stop | WR | Net gross | Net after $1.48 RT | Exp net | PF net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ALL 165 | 165 | 151 | 151 | 0 | 33 | 32.5% | $943.75 | $720.27 | $4.77 | 1.175 |
| IOC_NO_FILL 68 | 68 | 56 | 56 | 0 | 17 | 46.4% | $1,419.38 | $1,336.50 | $23.87 | 2.075 |
| IOC_FILLED 97 | 97 | 95 | 95 | 0 | 16 | 24.2% | $-475.63 | $-616.23 | $-6.49 | 0.786 |

## ARMED variant (shipped stop_market) — cohorts at 1 tick

| Scope | Attempts | Triggered | Resolved | Open | Imm. stop | WR | Net gross | Net after $1.48 RT | Exp net | PF net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ALL 165 | 165 | 163 | 163 | 0 | 0 | 43.6% | $-371.62 | $-612.86 | $-3.76 | 0.874 |
| IOC_NO_FILL 68 | 68 | 67 | 67 | 0 | 0 | 67.2% | $276.10 | $176.94 | $2.64 | 1.107 |
| IOC_FILLED 97 | 97 | 96 | 96 | 0 | 0 | 27.1% | $-647.72 | $-789.80 | $-8.23 | 0.755 |

## LEVEL by strategy (1 tick)

| Scope | Attempts | Triggered | Resolved | Open | Imm. stop | WR | Net gross | Net after $1.48 RT | Exp net | PF net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb_breakout | 20 | 7 | 7 | 0 | 0 | 57.1% | $140.00 | $129.64 | $18.52 | 2.573 |
| orb_reclaim | 131 | 131 | 131 | 0 | 23 | 33.6% | $918.25 | $724.37 | $5.53 | 1.187 |
| orb_rejection | 3 | 3 | 3 | 0 | 3 | 0.0% | $-15.00 | $-19.44 | $-6.48 | 0.000 |
| pdl_reclaim | 1 | 0 | 0 | 0 | 0 | — | $0.00 | $0.00 | — | — |
| vwap_reclaim | 10 | 10 | 10 | 0 | 7 | 10.0% | $-99.50 | $-114.30 | $-11.43 | 0.273 |

## LEVEL by instrument (1 tick)

| Scope | Attempts | Triggered | Resolved | Open | Imm. stop | WR | Net gross | Net after $1.48 RT | Exp net | PF net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MNQ | 61 | 47 | 47 | 0 | 19 | 34.0% | $417.50 | $347.94 | $7.40 | 1.373 |
| MES | 104 | 104 | 104 | 0 | 14 | 31.7% | $526.25 | $372.33 | $3.58 | 1.117 |

## LEVEL no-fill cohort by strategy (1 tick)

| Scope | Attempts | Triggered | Resolved | Open | Imm. stop | WR | Net gross | Net after $1.48 RT | Exp net | PF net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb_breakout | 17 | 5 | 5 | 0 | 0 | 60.0% | $111.50 | $104.10 | $20.82 | 2.894 |
| orb_reclaim | 45 | 45 | 45 | 0 | 12 | 48.9% | $1,332.38 | $1,265.78 | $28.13 | 2.138 |
| orb_rejection | 1 | 1 | 1 | 0 | 1 | 0.0% | $-5.00 | $-6.48 | $-6.48 | 0.000 |
| vwap_reclaim | 5 | 5 | 5 | 0 | 4 | 20.0% | $-19.50 | $-26.90 | $-5.38 | 0.615 |

## Slippage sensitivity (net after commission / PF)

| Slippage | LEVEL all-165 | LEVEL no-fill-68 | ARMED all-165 |
|---|---:|---:|---:|
| 1 tick | $720.27 / 1.175 | $1,336.50 / 2.075 | $-612.86 / 0.874 |
| 2 tick | $462.52 / 1.107 | $1,260.49 / 1.973 | $-863.37 / 0.829 |
| 3 tick | $204.77 / 1.045 | $1,184.50 / 1.878 | $-1,114.88 / 0.788 |
| 4 tick | $-52.98 / 0.989 | $1,108.49 / 1.791 | $-1,281.41 / 0.760 |

## Comparison ladder (all matched populations, 1 tick, net after commission)

| Pass | Entry | Net | PF | WR |
|---|---|---:|---:|---:|
| #346 (97 fills, system) | IOC limit at level | $-802.28 | 0.753 | 26.8% |
| #354 all-165 | market at decision close | $-689.32 | 0.861 | 43.0% |
| LEVEL all-165 | at-level, zero latency | $720.27 | 1.175 | 32.5% |
| ARMED all-165 | shipped stop_market | $-612.86 | 0.874 | 43.6% |

## Audit and limitations

- LEVEL: 151/165 triggered intrabar (14 not triggered / bracket-invalid, reported not traded); 33 pessimistic decision-bar immediate stops; 0 unresolved at corpus end; 7 cross-day resolutions.
- LEVEL entry is a retroactive zero-latency BOUND, not an implementable order: it assumes reaction at the exact level during the decision bar. Its pessimistic decision-bar rule (stop-range → immediate loss; target-range never awarded same-bar) biases it conservative.
- ARMED is fully causal and implementable today, but arms only at the decision-bar close (one-next-bar model as shipped) — it still carries the 15m latency and gap-fills runaways at the next open.
- Every resolved row's P&L independently recomputed from raw prices; join, corpus hash, and cohort splits asserted as in PR #354.
- All 165 attempts are H1 (inherits #346's breaker censoring); replay-scale dollars; historical evidence, not live-fill proof.

## Reproduction

```bash
python scripts/stop_market_level_counterfactual_165.py \
  --corpus data/replay_corpus_v1_market_condition_fixed \
  --logs /private/tmp/corrected_ioc_corpus_logs \
  --raw-attempts scripts/corrected_ioc_corpus_raw_trades.jsonl \
  --out scripts/stop_market_level_counterfactual_165_results.json \
  --raw scripts/stop_market_level_counterfactual_165_raw.jsonl \
  --report docs/stop-market-level-counterfactual-165-2026-07-26.md
```
