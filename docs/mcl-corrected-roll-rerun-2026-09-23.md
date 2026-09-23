# MCL corrected-roll rerun of the production-detector geometry test (2026-09-23)

**RESEARCH ONLY.** Read-only and offline. No collector, strategy, risk,
broker, deployment or runtime change. Nothing is promoted.

## Provenance: the earlier result stays BLOCKED

In `docs/production-detector-geometry-test-2026-09-23.md` (#946), MCL was
**BLOCKED**.

- The parity gate failed: 89.1% of live setups were reproduced and 81% had
  matching geometry.
- The cause: the Polygon corpus used a prior-day-volume roll, while the live
  TradingView feed (MCL1!) was on a different contract month.

That result is kept as recorded. For the record, its unscored 2026 numbers
were:

| Config | 2026 PF | 2026 net |
|---|---|---|
| 15m CONTROL | 0.777 | −$86,343 |
| 60m WIDE | 0.996 | −$631 |
| 4H WIDE | 1.044 | +$1,502 |

## What changed (data construction only)

**The roll rule.** The front MCL contract for a CME trade date switches on
the trade date **one business day before the expiring contract's listed
last trade date**.

- The listed last-trade dates come from the tranche-2 Polygon inventory,
  `docs/structural-level-tranche2-2026-09-17-probe-MCL.json`.
- Evidence for the rule:
  - The live MCL1! switched V6→X6 at 2026-09-17T22:00Z (trade date 09-18);
    V6's last trade was 09-21.
  - The rule matches every Polygon volume crossover since 2026-03.
  - It matches TradingView's documented method: a fixed per-symbol number
    of business days before expiration.
- It is causal: last-trade dates are known when a contract lists.

**Parity on the corrected corpus** (live CANDIDATE rows, 2026-09-16..22):
- 308/312 setups reproduced (98.7%);
- 307/308 with entry, stop and target within 1 tick (99.7%);
- all residuals are on the 09-17/18 roll seam.

**Corrected schedule** (contract, first trade date):

| Contract | From | Contract | From | Contract | From |
|---|---|---|---|---|---|
| X4 | 2024-09-23 | U5 | 2025-07-18 | M6 | 2026-04-17 |
| Z4 | 2024-10-18 | V5 | 2025-08-18 | N6 | 2026-05-15 |
| F5 | 2024-11-18 | X5 | 2025-09-18 | Q6 | 2026-06-17 |
| G5 | 2024-12-17 | Z5 | 2025-10-17 | U6 | 2026-07-17 |
| H5 | 2025-01-16 | F6 | 2025-11-18 | V6 | 2026-08-18 |
| J5 | 2025-02-18 | G6 | 2025-12-17 | X6 | 2026-09-18 |
| K5 | 2025-03-18 | H6 | 2026-01-15 | | |
| M5 | 2025-04-21 | J6 | 2026-02-18 | | |
| N5 | 2025-05-16 | K6 | 2026-03-18 | | |
| Q5 | 2025-06-17 | | | | |

**Roll seams.** This rule was pre-declared: every setup signalled on one of
the 24 roll-switch trade dates is excluded as `ROLL_SEAM_CONTAMINATED`, for
both real trades and random-null draws. Excluded candidates were 1,772
(15m), 441 (60m) and 122 (4H).

**Everything else is identical to #946:**
- the production detectors;
- the three configurations;
- the 2024-09 → 2025-12 historical window and the untouched 2026 window;
- the $4.00 cost;
- the random-direction null (500 draws) and random-time null (200 draws);
- the PASS rule, the bootstrap comparison and the verdict rules.

## Results

| Config | 2024–25 n / net / PF | 2026 n / days / net / PF / win | 2026 max DD | 2026 halves | Random-direction 95% | Random-time 95% | Result |
|---|---|---|---|---|---|---|---|
| 15m CONTROL | 19,439 / −$120,999 / 0.59 | 11,549 / 179 / −$87,101 / 0.77 / 32% | $87,845 | −/− | 0.79 | 0.85 | FAIL |
| 60m WIDE | 4,305 / −$18,322 / 0.85 | 2,524 / 178 / −$5,297 / 0.97 / 48% | $15,528 | −/− | 1.03 | 1.07 | FAIL |
| 4H WIDE | 759 / −$1,336 / 0.95 | 444 / 166 / +$877 / 1.03 / 49% | $5,069 | −/+ | 1.19 | 1.26 | FAIL |

**Concentration.** 4H WIDE's 2026 net is tiny (+$877), so its top-3 days
equal 656% of net. It is not a meaningful edge. The other two configurations
lose money.

**2026 PF differences** (bootstrap 95% interval):

| Comparison | Difference |
|---|---|
| 60m − 15m | +0.20 (0.09 to 0.33) |
| 4H − 15m | +0.26 (−0.01 to 0.63) |
| 4H − 60m | +0.06 (−0.23 to 0.42) |

## Classification

| Config | Verdict |
|---|---|
| 60m WIDE | **PROMISING BUT UNPROVEN**: beats the 15m control, but loses money and is below both nulls |
| 4H WIDE | **REJECT**: its improvement over the control is not significant, and it fails the PASS rule |

**No fresh MCL forward test is justified.** The corrected data confirms the
blocked result's picture: MCL loses under every tested geometry, and wider
geometry only loses less.
