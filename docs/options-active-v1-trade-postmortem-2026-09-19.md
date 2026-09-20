# OPTIONS_PAPER_V1 active-row postmortem — 2026-09-19

## Scope

Read-only review of the five **closed ACTIVE** options-shadow rows created during the Sep 16-18 V1 evidence window. Three additional ACTIVE rows were still `OPEN` at the weekend cutoff and are intentionally not scored here.

This document is descriptive evidence only. It does **not** change V1, entry logic, selector rules, target geometry, stop policy, risk limits, or deployment state.

## Closed ACTIVE rows

| Row | Ticker | Setup | Contract | DTE | Delta | OI | Entry spread | Planned risk | Underlying first-sight / trigger | Invalidation | T1 | Remaining R:R | Resolution | Option P&L |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 9197 | SPY | Daily 2-2-2 reversal LONG | `SPY261120C00775000` | 65 | 0.4099 | 8,753 | 0.59% | $296.00 | 758.97 / 760.35 | 756.15 | 765.14 | 2.19R | stop hit at 755.99 | **-$134** |
| 9209 | IWM | Daily 2-2-2 reversal LONG | `IWM261120C00295000` | 65 | 0.3996 | 8,946 | 0.50% | $150.00 | 286.445 / 286.93 | 284.08 | 289.83 | 1.43R | stop hit at 283.29 | **-$127** |
| 9212 | MRK | Daily 2-2-2 reversal LONG | `MRK261120C00150000` | 65 | 0.4151 | 11,339 | 3.57% | $142.50 | 144.03 / 144.15 | 142.32 | 146.281 | 1.32R | target hit at 146.82 | **+$30** |
| 9318 | NVDA | 30m 2-1-2 continuation LONG | `NVDA261120C00230000` | 64 | 0.4230 | 100,585 | 0.53% | $235.00 | 219.445 / 219.66 | 219.31 | 219.83 | 2.85R | target hit at 219.875 | **+$15** |
| 9320 | MRK | 30m 2-1-2 continuation LONG | `MRK261120C00150000` | 64 | 0.4773 | 11,346 | 3.63% | $175.00 | 147.16 / 147.26 | 146.95 | 147.40 | 1.14R | target hit at 147.49 | **-$15** |

Closed-row totals:

- structural status: **3 WIN / 2 LOSS**;
- realized shadow option P&L: **-$231**;
- all five selected contracts were 64-65 DTE, had material OI, and were inside the V1 10% spread ceiling;
- every row respected the frozen $300 per-trade planned-risk ceiling and aggregate-risk gate when opened.

## What went right

### 1. The two losses were genuine underlying invalidations

SPY and IWM were not merely option-premium noise. Both underlying prices crossed their stored invalidation levels:

- SPY: invalidation 756.15, resolved underlying 755.99;
- IWM: invalidation 284.08, resolved underlying 283.29.

The system therefore exited broken theses rather than converting them into hope holds. SPY lost 11.32% of premium and IWM lost 21.17%; neither required the full 25% premium stop because the underlying invalidation resolved first.

### 2. Contract quality was not obviously defective

All five contracts were 64-65 DTE with Delta roughly 0.40-0.48 and substantial open interest. Entry spreads ranged from 0.50% to 3.63%, all beneath the V1 10% hard ceiling. The two SPY/IWM losers actually had the tightest spreads in the set, so the losses cannot reasonably be blamed on illiquidity alone.

### 3. Risk controls behaved as designed

Planned risk was $142.50-$296.00 per row, always at or below the $300 trade cap. Aggregate planned risk stayed below the $1,000 cap at entry. No evidence in these rows shows averaging down or duplicate ACTIVE re-entry after an invalidated episode.

### 4. Two target hits translated to positive option P&L

- MRK Daily: underlying target hit and option produced +$30 (+5.26%).
- NVDA 30m 2-1-2: underlying target hit and option produced +$15 (+1.60%).

This proves the ASK-entry/BID-exit translation can produce a positive result when the underlying path and option movement are sufficient.

## What went wrong or remains questionable

### 1. Every closed ACTIVE row entered after the long trigger had already been crossed and price had retraced below it

At first sight, all five LONG rows were below their stored mechanical trigger:

- SPY: 758.97 vs 760.35 trigger;
- IWM: 286.445 vs 286.93;
- MRK Daily: 144.03 vs 144.15;
- NVDA: 219.445 vs 219.66;
- MRK 30m: 147.16 vs 147.26.

This was allowed by the frozen policy because price was still ahead of the stop/target and at least 1.0 remaining R:R. It is **not proven defective** from five rows, but it exposes a specific unresolved question: whether a trigger should remain actionable after the first break has failed back through the trigger before the scanner's first actionable observation.

Do not change this rule from this sample. It is a candidate for a separately preregistered persistence/reclaim-quality study.

### 2. The two 30m 2-1-2 targets were mechanically shallow

From the stored mechanical trigger:

- NVDA T1 was about **0.49R**;
- MRK T1 was about **0.45R**.

The actual first-sight remaining R:R was better because both entries occurred on a retrace, but the absolute underlying move to T1 was still small. That makes option translation friction proportionally important.

NVDA overcame it and made +$15. MRK did not.

### 3. MRK 30m demonstrates the structural-win / option-loss problem directly

MRK 30m reached T1 (147.49 vs 147.40) and was therefore structurally labelled `WIN`, yet the option moved from 7.00 ASK entry to 6.85 BID exit, producing **-$15 (-2.14%)**.

The entry spread was 3.63%, meaning the option began with meaningful execution friction. This does **not** prove the contract was bad—the contract had Delta 0.4773 and OI 11,346—but it proves an underlying target hit cannot be treated as option expectancy.

This row is consistent with the separate clean-shadow finding that underlying target hits can still lose money after ASK/BID translation.

### 4. The Daily setup failures occurred despite acceptable contract/risk geometry

SPY entered with 2.19R remaining and IWM with 1.43R remaining. Both had tight option spreads and acceptable contract quality, yet both invalidated structurally.

That points away from contract-selection failure as the primary explanation for those two rows. The cleaner explanation supported by the evidence is simply **setup failure after first-sight entry**.

### 5. Context proof was incomplete on every row

All five rows carried `trade_proof_status=INCOMPLETE`.

- Daily rows lacked weekly/monthly context, event-risk context, and flip context.
- 30m rows lacked event-risk and flip context.

This does not mean those missing fields caused the losses. It means the sample cannot answer whether complete higher-timeframe / event / flip context would have filtered or improved these entries. That remains an evidence gap rather than a reason to retrofit filters after the fact.

## Trade-by-trade assessment

### SPY Daily 2-2-2 reversal — LOSS / -$134

What worked:
- liquid 65-DTE contract;
- 0.59% spread;
- 0.4099 Delta;
- 8,753 OI;
- 2.19R remained from first sight;
- underlying stop was respected.

What failed:
- first actionable observation was already 1.38 points back below the long trigger;
- the reversal never re-established control and eventually broke invalidation.

Evidence-based classification: **valid under frozen V1 rules, then structurally failed.** No evidence supports blaming the option contract.

### IWM Daily 2-2-2 reversal — LOSS / -$127

What worked:
- 65 DTE;
- 0.50% spread;
- 0.3996 Delta;
- 8,946 OI;
- planned risk only $150;
- underlying invalidation exited the thesis before the 25% premium stop.

What failed:
- first sight was below trigger;
- only 1.43R remained;
- the underlying then broke invalidation materially;
- premium was already down 21.17% at resolution.

Evidence-based classification: **clean failed setup / correct cut.**

### MRK Daily 2-2-2 reversal — WIN / +$30

What worked:
- 65 DTE;
- 0.4151 Delta;
- 11,339 OI;
- acceptable 3.57% spread;
- target hit cleanly and option translated positively.

Limitations:
- first sight was slightly below trigger;
- only 1.32R remained;
- context proof was incomplete.

Evidence-based classification: **successful underlying setup with positive option translation.**

### NVDA 30m 2-1-2 continuation — WIN / +$15

What worked:
- very liquid contract: 100,585 OI, 4,546 option volume;
- 0.53% spread;
- 0.423 Delta;
- 2.85R remaining from first sight;
- target hit and option remained positive after ASK/BID friction.

Limitation:
- mechanical T1 was only ~0.49R from the trigger, so the structural target itself was shallow.

Evidence-based classification: **successful translation, but the profit was small and does not resolve the broader 212R expectancy question.**

### MRK 30m 2-1-2 continuation — structural WIN / option -$15

What worked:
- target was reached;
- 64 DTE;
- 0.4773 Delta;
- 11,346 OI;
- setup remained inside risk limits.

What failed:
- mechanical T1 only ~0.45R from trigger;
- first-sight remaining R:R only 1.14R;
- 3.63% entry spread was large relative to the small underlying target move;
- target hit did not produce enough option appreciation to overcome ASK/BID translation.

Evidence-based classification: **underlying thesis succeeded; option expression did not.**

## Ruling

The five closed ACTIVE rows do **not** support a broad V1 rule change.

They do support three narrower conclusions:

1. **The -$231 is not one failure mode.** Two rows were genuine structural losses; one was a structural win that lost at the option layer; two translated positively.
2. **Contract liquidity was not the obvious root cause.** Every selected contract passed V1 liquidity/spread rules, and the two largest losses had tight spreads.
3. **The most specific new question is trigger persistence at first sight.** All five long entries were opened after price had fallen back below the mechanical trigger. That pattern deserves a preregistered study only if additional independent rows accumulate; it is not enough to justify a policy change now.

The already-completed target-width study also remains controlling evidence against a simple "make targets wider" fix: widening targets alone did not rescue the clean shadow cohort.

## Monday boundary

Do not tune V1 from this five-row review. Continue collecting untouched ACTIVE V1 rows and the separate `122-IEX-E1` forward cohort. Revisit entry-trigger persistence, option translation, and context completeness only with a larger independent sample or a separately preregistered controlled test.

**No proof, no trade.**
