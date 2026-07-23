# Honest Fill Replay Results

**Research date:** 2026-07-23  
**Instrument:** MNQ, one contract  
**Scope:** reconciled 4HR Re-Trigger, 12HR Miyagi, and 60M 3-2-2 signals only

## Shared execution contract

- One-shot Limit-IOC; no retry or chase
- MNQ IOC adverse cap: 32 ticks (8 points) from the trigger
- Completed five-minute crossing/touch bar close is the market proxy at order arrival
- Two ticks adverse slippage on entry and exit in the base case
- $1.24 round-trip commission per fill
- Bracket evaluation begins on the next five-minute bar after a non-gap decision
- Stop wins when stop and target are both touched in the same eligible bar
- A fixed stop that is non-protective after the actual IOC fill fails closed
- Unresolved positions exit at the 15:55 ET bar close with adverse slippage
- Walk-forward halves split at the exact calendar midpoint of the full signal range

For 4HR Re-Trigger, the 15:55 exit is now the documented day-only rule: exit
unresolved positions at 3:55 PM ET and be flat by 4:00 PM ET. For Miyagi and
3-2-2 it remains a replay assumption unless their own rules are separately
resolved. The research engines are not wired into live execution.

## Base case

| Strategy | Signals | Fills | W/L | Net P&L | Exp/signal | Exp/fill | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 4HR Re-Trigger | 94 | 41 | 23/18 | $1,960.16 | $20.85 | $47.81 | 2.33 | $411.18 |
| 12HR Miyagi | 13 | 3 | 2/1 | $59.28 | $4.56 | $19.76 | 1.30 | $197.24 |
| 60M 3-2-2 | 32 | 20 | 17/3 | $1,537.70 | $48.05 | $76.88 | 8.00 | $167.24 |

## Stability

| Strategy | H1 net | H2 net | LONG net | SHORT net | Net at 1/2/3/4 ticks |
|---|---:|---:|---:|---:|---|
| 4HR Re-Trigger | $230.46 | $1,729.70 | $890.18 | $1,069.98 | $2,001.16 / $1,960.16 / $1,919.16 / $1,878.16 |
| 12HR Miyagi | -$115.48 | $174.76 | $0.00 (0 fills) | $59.28 | $62.28 / $59.28 / $56.28 / $53.28 |
| 60M 3-2-2 | $1,086.88 | $450.82 | $1,108.36 | $429.34 | $1,557.70 / $1,537.70 / $1,517.70 / $1,498.20 |

## Decision

- **4HR Re-Trigger — PROMISING BUT UNPROVEN.** Positive in both halves and
  directions, but H2 accounts for most of the profit and only 41 signals filled.
- **12HR Miyagi — WAIT.** Three fills cannot support an edge claim; H1 is negative
  and LONG has no filled observations.
- **60M 3-2-2 — PAPER PROOF.** Strongest replay of the three and positive in
  every requested split. Removing the three largest winners left 17 fills,
  +$965.92 net, $56.82 expectancy per fill, and PF 5.40. The paper-proof
  classification does not itself activate a runtime lane.

These results retire the prior performance figures built from superseded or
incomplete rule sets. They do not change configuration, execution, or deployment.

## Follow-up robustness diagnostics

### 60M 3-2-2 fat-tail removal

The three largest net winners were:

| Date | Direction | Net P&L |
|---|---|---:|
| 2025-09-05 | SHORT | $221.26 |
| 2026-05-13 | LONG | $180.76 |
| 2024-10-10 | LONG | $169.76 |

Together they contributed $571.78, or 37.2% of base-case net profit. Removing
them leaves 14 wins and 3 losses, +$965.92 net, $56.82 expectancy per remaining
fill, and PF 5.40. Expectancy is $30.19 when the residual profit is divided by
all 32 original signals.

### 4HR half regimes and non-fill anatomy

| Half | Market period | MNQ change | Max close DD | Mean intraday range | Filled net |
|---|---|---:|---:|---:|---:|
| H1 | 2024-07-02–2025-06-28 | +13.8% | -25.0% | 1.83% | $230.46 |
| H2 | 2025-06-29–2026-06-26 | +28.7% | -11.8% | 1.50% | $1,729.70 |

H1 contained a deeper, higher-range, less persistent regime. Its quarterly
filled P&L was -$411.18, +$356.82, +$319.78, and -$34.96. H2 quarterly filled
P&L was +$409.80, +$98.28, +$553.06, and +$668.56. This supports a regime
sensitivity concern; H2 is not explained by one isolated winning quarter.

The 53 non-fills split into:

| Reason | Count | Share of non-fills | Market-entry interpretation |
|---|---:|---:|---|
| No trigger crossing in window | 16 | 30.2% | No entry under IOC or market |
| IOC rejected crossing | 32 | 60.4% | A market order could fill, but at a materially displaced price |
| Non-protective fixed stop after fill | 5 | 9.4% | Fail closed; not a valid bracket under resolved rules |

For the 32 IOC rejections, the completed crossing-bar close was a median 135
ticks (33.75 points) beyond the trigger; the IOC cap is 32 ticks (8 points).
H1 and H2 median displacements were nearly identical at 34.0 and 33.5 points.
The rejection rate therefore is not the cause of the half-performance split,
and a market-entry replay must recalculate stop/target economics at the worse
actual entry rather than simply converting cancellations to fills.

## 4HR market-entry and TRENDING-gate counterfactual

The counterfactual replayed only the 32 audited IOC-rejected crossings. Entry
used the completed crossing-bar close plus two adverse ticks with no IOC cap.
The fixed 1H stop was recalculated at that entry timestamp and the structural
prior-4PM target remained unchanged.

Ten attempts were already beyond the target after entry slippage. They failed
the strict bracket predicate and were excluded as `TARGET_ALREADY_PASSED`.
None of these ten were non-protective-stop cases. This leaves 22 valid market
fills and a maximum honest combined population of 63, not 73.

### IOC-rejected market attempts in isolation

| Population | Attempts | Valid fills | W/L | Net | Exp/attempt | Exp/fill | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Market counterfactual | 32 | 22 | 16/6 | $793.72 | $24.80 | $36.08 | 1.60 | $482.74 |

| Split | Valid fills | Net | Exp/fill | PF |
|---|---:|---:|---:|---:|
| H1 | 12 | $859.62 | $71.64 | 2.54 |
| H2 | 10 | -$65.90 | -$6.59 | 0.91 |
| LONG | 7 | -$295.18 | -$42.17 | 0.66 |
| SHORT | 15 | $1,088.90 | $72.59 | 3.38 |

Displaced-price market entry preserves an overall edge, but it is not stable
enough to replace IOC wholesale: H2 and LONG are negative in isolation.

### Combined valid fills

The combined population retains the original 41 IOC fills unchanged and adds
the 22 valid market fills.

| Population | Fills | W/L | Net | Exp/fill | Exp/original signal | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| IOC + valid market | 63 | 39/24 | $2,753.88 | $43.71 | $29.30 | 1.98 | $640.68 |

| Half | Fills | Net | Exp/fill | PF |
|---|---:|---:|---:|---:|
| H1 | 33 | $1,090.08 | $33.03 | 1.86 |
| H2 | 30 | $1,663.80 | $55.46 | 2.09 |

### Combined fills gated to TRENDING

The cached historical replay label was joined at the completed crossing-bar
timestamp. Of 63 combined valid fills, 45 were `TRENDING`; the 18 excluded
non-trending fills lost $313.32 in aggregate.

| Population | Fills | W/L | Net | Exp/fill | Exp/original signal | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| TRENDING only | 45 | 32/13 | $3,067.20 | $68.16 | $32.63 | 2.71 | $591.96 |

| Half | Fills | Net | Exp/fill | PF |
|---|---:|---:|---:|---:|
| H1 | 23 | $1,077.98 | $46.87 | 2.32 |
| H2 | 22 | $1,989.22 | $90.42 | 3.03 |

TRENDING-only H1 quarters were:

| Quarter | Fills | Net | Exp/fill | PF |
|---|---:|---:|---:|---:|
| 2024 Q3 | 5 | -$37.20 | -$7.44 | 0.71 |
| 2024 Q4 | 8 | $394.08 | $49.26 | 69.66 |
| 2025 Q1 | 3 | $395.78 | $131.93 | No losses |
| 2025 Q2 | 8 | $177.58 | $22.20 | 1.21 |

The gate improves combined expectancy and profit factor, but it does not turn
all four H1 quarters positive. H1 non-trending fills were slightly positive
(+$12.10), so the market counterfactual—not the gate—is what materially raises
H1 from the original IOC result. Historical regime labels were generated by
the repository Polygon replay converter; they are the correct historical proxy
for the VP gate but do not prove exact TradingView/Pine label parity.

### Evidence boundaries — do not blend

The 4HR record contains three separate claims:

1. **Current executable-style IOC evidence:** 41 valid fills, +$1,960.16,
   PF 2.33.
2. **Research-only market counterfactual:** 22 additional valid fills,
   10 target-already-passed exclusions, +$793.72. This does not authorize a
   market-entry policy.
3. **Research-only combined population with the existing historical TRENDING
   proxy:** 45 gated fills, +$3,067.20, $68.16 per fill, PF 2.71.

The combined/gated result must never replace the ordinary 4HR IOC performance
number in inventory, runtime, execution, or operator reporting.

### Research stop

This performance tranche is closed. Do not add direction-specific market-entry
rules, additional historical filters, or further slice optimization from this
sample. Remaining work is limited to a narrow counterfactual delta audit and a
later Phase-1 runtime/specification reconciliation.
