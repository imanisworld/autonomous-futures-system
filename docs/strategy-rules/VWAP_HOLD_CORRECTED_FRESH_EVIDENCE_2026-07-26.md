# VWAP Hold — fresh corrected-system evidence

**Classification: WAIT**

Git SHA: `69ec77fd33834a437fec77a51249fa1d66030a16`
Date range: 2025-07-24 → 2026-07-23

Fresh candidate generation through the current canonical `DecisionEngine._try_vwap_hold`, not the locked #345 arm population.

## Assumptions

- MNQ, New York session, SHORT only.
- Corrected post-#338 engine-facing market condition.
- IOC-limit marketability at the completed decision bar close; 32-tick cap.
- Runner exit: 1.0R activation / 0.5R trail.
- Pessimistic same-bar resolution; day-only flatten on the 15:55 ET 5m bar.
- Baseline: 2 ticks adverse PaperBroker slippage; $1.48 round-trip commission.
- $1.48 is sourced from current `execution.mnq_strat_evidence`; the older $1.24 #345 convention is not silently reused.

## Baseline

| Scope | Attempts | Fills | Fill rate | WR | Gross | Net | Exp/fill | PF | Max DD | Largest loss | Net after top-3 removal |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 26 | 6 | 23.1% | 16.7% | $-74.14 | $-83.02 | $-13.84 | 0.398 | $112.68 | $-30.96 | $-137.90 |
| H1 | 20 | 2 | 10.0% | 0.0% | $-55.84 | $-58.80 | $-29.40 | — | $58.80 | $-30.96 | $-58.80 |
| H2 | 6 | 4 | 66.7% | 25.0% | $-18.30 | $-24.22 | $-6.05 | 0.694 | $53.88 | $-29.74 | $-79.10 |
| SHORT | 26 | 6 | 23.1% | 16.7% | $-74.14 | $-83.02 | $-13.84 | 0.398 | $112.68 | $-30.96 | $-137.90 |

## Slippage sensitivity

| Adverse ticks | Attempts | Fills | Fill rate | WR | Gross | Net | Exp/fill | PF | Both halves positive |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 1 | 26 | 6 | 23.1% | 16.7% | $-67.89 | $-76.77 | $-12.79 | 0.422 | NO |
| 2 | 26 | 6 | 23.1% | 16.7% | $-74.14 | $-83.02 | $-13.84 | 0.398 | NO |
| 3 | 26 | 6 | 23.1% | 16.7% | $-80.39 | $-89.27 | $-14.88 | 0.375 | NO |
| 4 | 26 | 6 | 23.1% | 16.7% | $-86.64 | $-95.52 | $-15.92 | 0.354 | NO |

## #345 sanity comparison

#345 used a locked 107-arm NY population and reported 55 IOC-close fills (51.4%), runner net $828.77, PF 3.218 under its older $1.24/2-tick cost convention. This run regenerates candidates through current corrected code; differences are evidence, not forced reconciliation.

## Classification reasoning

- only 6 resolved fills
- **IOC starvation flag: fill rate is below 30%.**

## Scope

Evidence only. Strategy permission is bypassed in memory solely to measure the current SHADOW_ONLY strategy; no repository config/risk/demo/runtime state changed. RiskEngine/account breaker effects are outside this isolated per-strategy detector/fill study.
