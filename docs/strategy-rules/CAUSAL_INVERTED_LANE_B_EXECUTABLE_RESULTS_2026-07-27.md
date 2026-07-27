# Causal inverted Lane B executable candidate — results

Preregistration commit:
`56981ecb58958b8269f8792c525ec027d0753ae9`

## VERDICT

**BROKEN**

## FINAL DECISION

**REJECT**

The single preregistered causal replay lost **$710.82** after baseline costs
over 509 resolved trades, with profit factor **0.9573** and expectancy
**-$1.3965 per trade**. H2, the recent 126 trades, SHORT, 2025, and 2026 were
all negative. The candidate also failed every frozen slippage tier.

No rescue variant, parameter change, filter, or alternate boundary was tested.
No paper implementation plan is produced.

## EXACT CAUSAL RULE

- One MNQ contract on five-minute bars in `America/New_York`.
- Prospective eligibility comes from the frozen NYSE cash-session calendar:
  only dates scheduled to remain open through 16:00 ET qualify.
- Previous comparison source is the immediately preceding scheduled full
  session's observed 15:55-bar close. Missing that close does not skip backward.
- At 15:30, compute
  `current_15:25_close / previous_scheduled_close - 1`.
- SHORT when positive; LONG otherwise. Exact zero maps LONG.
- Entry is the 15:35 bar open, one full five-minute boundary after signal
  availability.
- Exit is a pre-scheduled market exit modeled at the 15:55 bar open.
- Baseline costs are $1.48 round-trip commission and one adverse tick per side.
- No stop, target, trail, threshold, ranking, trend, market-condition, HTF,
  VWAP, ORB, confluence, permission, or variable-sizing gate.

The controlling specification is
`CAUSAL_INVERTED_LANE_B_EXECUTABLE_PREREGISTRATION_2026-07-27.md`.

## WHAT CHANGED FROM THE OLD 509-TRADE RULE

Only the three preregistered causal substitutions changed:

1. Session eligibility is fixed prospectively from the published calendar,
   rather than inferred from whether future required bars later exist.
2. Entry moved from the simultaneous 15:30 open to the 15:35 open.
3. Exit moved from the retrospectively observed 15:55 close to the
   pre-scheduled 15:55 open.

Signal formula, inverse direction, exact-zero mapping, instrument, size, costs,
and absence of filters/stops/targets remained unchanged.

## RESULTS

| Metric | Combined causal result |
|---|---:|
| Prospectively eligible sessions | 511 |
| Candidates | 509 |
| Resolved trades | 509 |
| Unresolved candidates | 0 |
| Gross P&L before friction | +$551.50 |
| Commission | $753.32 |
| Slippage cost | $509.00 |
| Net P&L | **-$710.82** |
| Expectancy/trade | **-$1.3965** |
| Profit factor | **0.9573** |
| Win rate | 48.53% |
| Wins / losses | 247 / 262 |
| Max drawdown | **$2,744.48** |
| Longest losing streak | 10 |

Coverage produced one initial `PRIOR_CLOSE_MISSING`, one
`SIGNAL_DATA_MISSING`, 509 resolved trades, and no unresolved entered trade.

The original viewed period contained 490 resolved trades and lost **$927.70**,
PF **0.9425**. The 19-trade prior extension gained **$216.88**, but that small
period does not overturn the negative combined result.

## OLD VS NEW PERFORMANCE

This is the frozen ordered bridge; costs remain one adverse tick per side plus
$1.48 commission at every stage.

| Stage | Trades | Net | PF | Incremental net change |
|---|---:|---:|---:|---:|
| Old non-causal result | 509 | +$4,288.68 | 1.2208 | — |
| Prospective calendar/state only | 509 | +$4,288.68 | 1.2208 | $0.00 |
| Plus causal 15:35 entry | 509 | +$1,840.68 | 1.0975 | **-$2,448.00** |
| Plus causal 15:55-open exit | 509 | **-$710.82** | **0.9573** | **-$2,551.50** |

Total degradation was **-$4,999.50**.

Prospective eligibility happened to select the same 509 resolved observations
in this corpus, so it contributed no realized P&L difference. The entire
degradation came from executable entry and exit timing. This attribution is
order-dependent exactly as preregistered.

## TEMPORAL STABILITY

### Chronological halves

| Half | Dates | Trades | Net | PF |
|---|---|---:|---:|---:|
| H1 | 2024-07-05–2025-07-16 | 254 | +$692.58 | 1.0848 |
| H2 | 2025-07-17–2026-07-24 | 255 | **-$1,403.40** | **0.8348** |

### Calendar years

| Year | Trades | Net | PF | Win rate |
|---|---:|---:|---:|---:|
| 2024 | 123 | +$764.96 | 1.2649 | 57.72% |
| 2025 | 246 | **-$1,033.08** | **0.8840** | 47.56% |
| 2026 through 07-24 | 140 | **-$442.70** | **0.9091** | 42.14% |

### Calendar quarters

| Quarter | Trades | Net | PF |
|---|---:|---:|---:|
| 2024-Q3 | 61 | +$469.22 | 1.2566 |
| 2024-Q4 | 62 | +$295.74 | 1.2794 |
| 2025-Q1 | 59 | +$114.68 | 1.0512 |
| 2025-Q2 | 62 | -$10.76 | 0.9962 |
| 2025-Q3 | 63 | -$670.74 | 0.5541 |
| 2025-Q4 | 62 | -$466.26 | 0.7993 |
| 2026-Q1 | 61 | -$587.28 | 0.7293 |
| 2026-Q2 | 62 | -$49.76 | 0.9771 |
| 2026-Q3 through 07-24 | 17 | +$194.34 | 1.3682 |

### Equal-count chronological quarters

| Period | Dates | Trades | Net | PF |
|---|---|---:|---:|---:|
| P1 | 2024-07-05–2025-01-07 | 127 | +$860.04 | 1.2954 |
| P2 | 2025-01-08–2025-07-16 | 127 | -$167.46 | 0.9681 |
| P3 | 2025-07-17–2026-01-20 | 127 | -$896.96 | 0.7659 |
| P4 | 2026-01-21–2026-07-24 | 128 | -$506.44 | 0.8914 |

The latest 126 trades, 2026-01-23 through 2026-07-24, lost **$462.48**,
PF **0.8988**, with a 42.06% win rate.

### Rolling windows

Rolling three-month net/PF by ending month:

| End | Net | PF | End | Net | PF |
|---|---:|---:|---|---:|---:|
| 2024-09 | +$469.22 | 1.2566 | 2025-09 | -$670.74 | 0.5541 |
| 2024-10 | +$558.30 | 1.3912 | 2025-10 | -$559.20 | 0.7183 |
| 2024-11 | +$763.74 | 1.8341 | 2025-11 | -$615.76 | 0.7473 |
| 2024-12 | +$295.74 | 1.2794 | 2025-12 | -$466.26 | 0.7993 |
| 2025-01 | +$873.16 | 1.9815 | 2026-01 | -$144.82 | 0.9205 |
| 2025-02 | -$151.34 | 0.9190 | 2026-02 | -$662.30 | 0.6420 |
| 2025-03 | +$114.68 | 1.0512 | 2026-03 | -$587.28 | 0.7293 |
| 2025-04 | -$1,183.28 | 0.6968 | 2026-04 | -$600.76 | 0.7355 |
| 2025-05 | +$227.76 | 1.0684 | 2026-05 | +$291.76 | 1.1463 |
| 2025-06 | -$10.76 | 0.9962 | 2026-06 | -$49.76 | 0.9771 |
| 2025-07 | +$261.74 | 1.1769 | 2026-07 | -$129.84 | 0.9410 |
| 2025-08 | -$431.26 | 0.6742 |  |  |  |

Rolling six-month net/PF by ending month:

| End | Net | PF | End | Net | PF |
|---|---:|---:|---|---:|---:|
| 2024-12 | +$764.96 | 1.2649 | 2025-10 | -$297.46 | 0.9141 |
| 2025-01 | +$1,431.46 | 1.6179 | 2025-11 | -$1,047.02 | 0.7216 |
| 2025-02 | +$612.40 | 1.2199 | 2025-12 | -$1,137.00 | 0.7029 |
| 2025-03 | +$410.42 | 1.1245 | 2026-01 | -$704.02 | 0.8151 |
| 2025-04 | -$310.12 | 0.9353 | 2026-02 | -$1,278.06 | 0.7018 |
| 2025-05 | +$76.42 | 1.0147 | 2026-03 | -$1,053.54 | 0.7655 |
| 2025-06 | +$103.92 | 1.0205 | 2026-04 | -$745.58 | 0.8179 |
| 2025-07 | -$921.54 | 0.8288 | 2026-05 | -$370.54 | 0.9036 |
| 2025-08 | -$203.50 | 0.9563 | 2026-06 | -$637.04 | 0.8534 |
| 2025-09 | -$681.50 | 0.8430 | 2026-07 | -$730.60 | 0.8366 |

The latest rolling three- and six-month windows were both negative.

## LONG / SHORT

| Direction | Trades | Net | Expectancy | PF | Win rate | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| LONG | 232 | +$352.14 | +$1.5178 | 1.0422 | 52.16% | $1,167.72 |
| SHORT | 277 | **-$1,062.96** | **-$3.8374** | **0.8721** | 45.49% | $2,312.20 |

The result is direction-dependent and fails on the larger SHORT population.

## COST SENSITIVITY

| Adverse ticks per side | Net | Expectancy | PF | Max DD |
|---:|---:|---:|---:|---:|
| 1 | -$710.82 | -$1.3965 | 0.9573 | $2,744.48 |
| 2 | -$1,219.82 | -$2.3965 | 0.9279 | $3,095.48 |
| 3 | -$1,728.82 | -$3.3965 | 0.8994 | $3,446.48 |
| 4 | -$2,237.82 | -$4.3965 | 0.8718 | $3,797.48 |

The rule fails at baseline friction and degrades monotonically.

## CONCENTRATION

| Winners removed | Winner contribution | Net after removal |
|---:|---:|---:|
| Top 1 | $387.02 | -$1,097.84 |
| Top 5 | $1,782.60 | -$2,493.42 |
| Top 10 | $3,065.70 | -$3,776.52 |

The top ten account for 19.22% of all winner dollars. Concentration is not the
primary failure: the unmodified result is already negative.

## DRAWDOWN

- Maximum drawdown: **$2,744.48**.
- Peak before maximum drawdown: 2025-01-31.
- Trough: 2026-07-01.
- Largest single loss: **-$470.98**.
- Longest losing streak: 10.
- Longest recovery/underwater interval: 367 trading observations and
  539 calendar days.
- Terminal drawdown: **unrecovered** as of 2026-07-24.

## OOS RESULT

The period previously untouched for the old candidate, 2026-06-29 through
2026-07-24, contained 19 trades:

- gross +$264.00;
- net **+$216.88**;
- expectancy +$11.4147;
- PF 1.4040;
- win rate 52.63%;
- max drawdown $202.96;
- LONG +$101.20 and SHORT +$115.68.

This is not claimed as untouched OOS for the new candidate because the data
was available before its preregistration. N=19 is also too small to overcome
the combined failure, negative H2, negative recent-126 result, and extended
unrecovered drawdown.

## LOOKAHEAD / CAUSALITY CHECK

All frozen automated invariants passed:

- calendar eligibility is fixed independently of future bar presence;
- the current 15:55 bar is not used to decide entry eligibility;
- a missing immediately prior close does not skip backward;
- every entry timestamp is strictly later than signal availability;
- every exit timestamp is later than entry;
- exact zero maps LONG;
- unresolved candidates cannot enter the resolved-trade metrics.

The state-machine tests also verified prospective early-close exclusion,
strict entry ordering, retention of a later-missing exit candidate, correct
prior-close reseeding, and the zero-return mapping.

The candidate is causal and mechanically testable. It is rejected because its
economic result fails, not because of lookahead.

## DECISION BASIS

The preregistered promotion contract required positive realistic-cost results,
positive H1/H2, no catastrophic recent decay, acceptable drawdown, reasonable
slippage survival, and direct implementability.

This candidate fails profitability, H2, recent stability, SHORT stability,
drawdown/recovery, and every cost tier. Classification is therefore
**BROKEN** and the only allowed final decision is **REJECT**.

No runtime code, paper lane, implementation PR, deployment, box, #359,
strategy setting, or risk configuration was changed.
