# 60M 3-2-2 MNQ Gate Attribution

Status: **AUDIT ONLY — no strategy, configuration, risk, permission, or deployment changes.**

## Reproduction boundary

This audit uses the frozen canonical 34-candidate `strat_322_first_live`
population, dated 2024-08-02 through 2026-06-11. The replay uses the existing
5-minute historical corpus and current engine formulas. The engine was run in
two diagnostic modes: risk floors disabled, and the current frozen account
configuration. Strategy permission was bypassed only diagnostically; no
permission or risk rule was changed.

Engine assumptions were: one MNQ contract, IOC limit entry, 32-tick entry
tolerance, one adverse fill-slippage tick, $1.48 round-trip commission,
pessimistic same-bar handling, current 120-tick MNQ stop cap, and current 2.0
minimum R:R gate.

## Signal and documented bracket

The raw population contains 17 LONG and 17 SHORT candidates. The no-stop
time-exit controls remain positive at the predeclared 30-, 60-, and 120-minute
horizons:

| Horizon | Mean net | PF | Hit rate | Mean MFE | Mean MAE |
|---|---:|---:|---:|---:|---:|
| 30m | +$52.96 | 2.555 | 55.9% | 72.24 pts | 44.12 pts |
| 60m | +$67.74 | 2.388 | 58.8% | 96.74 pts | 53.75 pts |
| 120m | +$94.46 | 2.769 | 58.8% | 125.83 pts | 71.88 pts |
| EOD | -$9.65 | 0.947 | 48.5% | 170.91 pts | 146.50 pts |

The documented plan-fill bracket resolved 33 of 34 candidates: 32 wins, one
loss, and one day-only flatten. Net was **+$2,532.66**, expectancy **+$76.75**,
PF **13.57**, maximum drawdown **$201.48**, with H1 **+$1,383.34** and H2
**+$1,149.32**. Both chronological halves were positive. The one non-fill was
`ENTRY_BRACKET_INVALID_AT_FILL`.

## Overlapping gate attribution

Every candidate failed both the documented minimum R:R and the current stop
width cap. Other overlapping failures were:

| Gate | Candidates |
|---|---:|
| `rr_below_minimum` | 34 |
| `stop_too_wide` | 34 |
| `target_too_close` | 11 |
| `min_confluence_grade` | 6 |
| `ENTRY_DETACHED_FROM_PRICE` | 9 |
| `MARKET_CONDITION_NOT_TRADABLE` | 1 |

The structural survivor count is **0/34**. The bracket P&L of the structurally
rejected population is therefore the full documented bracket result above, not
a weak residual subset. Stop widths had median **486 ticks**, 90th percentile
**878 ticks**, maximum **1,471 ticks**, and **100%** exceeded the 120-tick cap.

## Ordered full-engine blockers

With permissions neutralized, the current engine rejected all 34 candidates
before an order attempt. The first blocker for each candidate was:

| First engine blocker | Candidates | Bracket wins | Bracket losses | Resolved bracket net |
|---|---:|---:|---:|---:|
| `TREND_STRENGTH_BELOW_REQUIRED` | 26 | 25 | 1 | +$1,998.52 |
| `ENTRY_DETACHED_FROM_PRICE` | 5 | 4 | 0 | +$125.08 |
| `MARKET_CONDITION_NOT_TRADABLE` | 1 | 1 | 0 | +$302.02 |
| `WEAK_BAR_CLOSE` | 1 | 1 | 0 | +$99.02 |
| `RR_BELOW_MINIMUM` | 1 | 1 | 0 | +$8.02 |
| **Total** | **34** | **32** | **1** | **+$2,532.66** |

The first-blocker groups sum to the full candidate bracket result. The one
remaining candidate is the documented day-only flatten, so the win/loss columns
exclude that non-win/non-loss resolution.

Both the floors-off and current frozen engine runs produced zero approved
attempts, zero IOC fills, and zero realized engine P&L. The current account
configuration did not get as far as daily-loss, drawdown, open-position, or
trade-count enforcement because every candidate was rejected earlier by signal
or structural gates.

## IOC and cost ceiling

For diagnostic context only, the all-candidate IOC ceiling was replayed outside
the structural survivor funnel with one contract, $1.48 commission, pessimistic
same-bar handling, and adverse slippage:

| Adverse slippage | Fills | No-fills | Wins / losses | Net | PF | Max DD | H1 / H2 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 tick | 20 | 14 | 19 / 1 | +$1,859.40 | 12.169 | $166.48 | +$1,068.68 / +$790.72 |
| 2 ticks | 20 | 14 | 19 / 1 | +$1,848.90 | 12.040 | $167.48 | +$1,064.18 / +$784.72 |
| 3 ticks | 20 | 14 | 19 / 1 | +$1,838.40 | 11.912 | $168.48 | +$1,059.68 / +$778.72 |

This is explicitly **not** a production result: zero candidates survive the
current risk gates, so there is no valid production survivor population to which
IOC/cost performance can be attributed.

## Verdict

**3-2-2: BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS / HOLD.**

The signal and documented bracket are historically strong, but the current
engine makes the strategy non-executable: all 34 candidates violate the R:R and
stop-width architecture, and the ordered full-engine replay approves 0/34
candidates. This does not justify raising the stop cap, weakening trend gates,
or inventing a separate execution path. No code or configuration changes were
made.
