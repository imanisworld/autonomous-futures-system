# 12HR Miyagi causal stop-reference correction — 2026-09-18

**Verdict: PROMISING BUT UNPROVEN / research only.**

The July canonical evidence contained a lookahead defect in Step 7. The 60-minute loader labels each bucket by its **start** timestamp. The detector accepted every hourly bar with `bar["ts"] < 09:30 ET`, so the 09:00–10:00 bucket was treated as completed at the 09:30 decision. That leaks its 09:30–10:00 high/low into the stop reference.

The written rule requires the **most recently completed 60-minute candle before entry**. At 09:30 ET that is the 08:00–09:00 candle. The corrected detector therefore requires:

`bar["ts"] + 1 hour <= 09:30 ET`

Direct cached-data proof before the fix selected 09:00 ET stop bars on MNQ 2024-08-22, MNQ 2024-10-11, and MES 2024-07-12. The corrected detector selects 08:00 ET on all three.

## Corrected full-study evidence

Study range remains 2024-07-02 through 2026-06-26. Honest-fill replay, 2-tick base slippage, commission, day-only exit, and every other rule are unchanged.

| Instrument | Candidates | Fills | W-L | Net | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| MNQ | 15 | 8 | 7-1 | +$552.83 | 3.22 | $248.74 |
| MES | 19 | 10 | 7-3 | +$138.85 | 1.59 | $133.12 |

MNQ remains positive but the sample is extremely thin: H1 +$419.32 (6W-1L), H2 +$133.51 with only one fill. MES is weaker after correction: H1 +$140.69 (5W-1L), H2 -$1.84 (2W-2L).

MES was previously reported as 8W-2L / +$198.85 / PF 1.98. Those performance claims are superseded by the corrected artifact. The original JSON files remain in place as historical audit artifacts; do not use them for promotion or expectancy claims.

Corrected versioned artifacts:
- `evidence_12hr_miyagi/mnq_results_corrected_2026-09-18.json`
- `evidence_12hr_miyagi/mes_results_corrected_2026-09-18.json`

Slippage sensitivity remains positive from 1 through 4 adverse ticks in both corrected samples, but sample size and MES second-half weakness prevent validation.

## Safety / scope

This is a research-detector correction only. Miyagi is not an active broker-execution lane. No risk rules, strategy parameters, broker routing, account settings, or deployed runtime were changed.

Regression proof: detector/reconciliation/honest-fill Miyagi suite = **39 passed, 2 skipped**.
