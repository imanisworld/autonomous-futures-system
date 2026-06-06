# Range-Fade Backtest Experiment

Date: 2026-06-05  
Branch: `codex/range-fade-backtest`  
Status: local replay experiment only; not connected to live decisions or execution.

## Hypothesis

After a tradeable range is confirmed, freeze its support and resistance and fade
edge rejections toward the midpoint. Pause after one buffered close outside the
range. Invalidate after two buffered closes outside, or one high-volume break.

## Initial Rules

- New York session only
- One contract
- One tick adverse slippage on market entry and stop
- Worst-case stop result when stop and target occur in the same candle
- Completed candles only; no future data used to establish a range
- Six prior candles required by the default model
- At least two touches near support and resistance
- Strong-trend and high-volume entries blocked
- Target at range midpoint
- Maximum one range trade and one range loss per day in the conservative test

## Data

- MNQ: 74 New York-session replay days
- MES: 72 New York-session replay days
- Expanded MES snapshot set: 62 dates, 13,998 deduplicated five-minute bars,
  including 4,216 unique New York-session bars
- Development: through 2026-04-17
- Validation: 2026-04-20 through 2026-05-15
- Holdout: after 2026-05-15

## Key Results

### Conservative Default: 6 Bars, 20% Entry Zone, 1 Trade/Day

| Instrument | Trades | Win Rate | P&L | Expectancy | Profit Factor |
|---|---:|---:|---:|---:|---:|
| MNQ | 13 | 84.6% | $253.61 | $19.51 | 3.783 |
| MES | 26 | 57.7% | -$178.33 | -$6.86 | 0.781 |

MNQ validation was slightly negative on only three trades. The holdout contained
three wins and no losses. This is encouraging but far below the required sample.

### Up To 3 Trades/Day, Stop After 2 Losses

| Instrument | Trades | Win Rate | P&L | Expectancy | Profit Factor |
|---|---:|---:|---:|---:|---:|
| MNQ | 16 | 81.3% | $236.62 | $14.79 | 2.280 |
| MES | 46 | 53.5% | -$430.12 | -$10.00 | 0.709 |

Additional trades reduced MNQ quality and materially worsened MES. The proposed
three-trade range budget is not supported by this first test.

### Robustness Grid

- MNQ six-bar confirmation was positive across 10%, 15%, and 20% entry zones,
  but produced only 10-13 trades.
- MNQ four-bar confirmation produced more trades but development and validation
  were approximately flat/negative; its overall profit came from the small holdout.
- MES four- and six-bar confirmation lost money across every tested entry zone.
- A stricter MES eight-bar setup appeared positive on the smaller clean dataset,
  but failed on the larger independent MES snapshot dataset.

## Expanded-Data Findings

The cumulative `MES_5m_YYYY-MM-DD.jsonl` snapshots contained 64,506 rows. After
deduplicating repeated snapshots, they provided 13,998 unique bars with no OHLC
conflicts. This is a separately constructed MES price series and was tested as
an independent validation set rather than blended with the clean MES dataset.

### Expanded MES, New York Session

| Setup | Trades | Win Rate | P&L | Expectancy | Profit Factor |
|---|---:|---:|---:|---:|---:|
| 6 bars, 20% zone, 1/day | 42 | 64.1% | -$178.24 | -$4.57 | 0.698 |
| 8 bars, 15% zone, 1/day | 33 | 46.2% | -$307.32 | -$11.82 | 0.483 |
| 6 bars, 20% zone, up to 3/day | 96 | 59.3% | -$603.01 | -$6.63 | 0.572 |

Every expanded-MES New York grid combination was flat-to-negative. This
invalidates the earlier small-sample suggestion that stricter MES ranges might
work.

### Additional Session Tests

- MNQ off-hours six-bar rules were positive overall across all entry zones,
  producing 20 trades at the 15% setting. The holdout contained only two trades
  and was negative, so this is not ready for activation.
- Expanded MES London was consistently negative.
- Expanded MES Asian was approximately breakeven.
- Expanded MES off-hours was positive overall but materially negative in the
  holdout.

## Decision

Do not deploy and do not merge into the live strategy path.

The hypothesis is worth continuing, but the evidence currently supports:

1. Continue only the MNQ New York candidate.
2. One range trade per day, not three.
3. Keep MES and overnight range execution disabled.
4. Collect more historical MNQ New York data before paper activation.
5. Require at least 50 resolved trades for the selected rule set.
6. Run a combined-system replay to measure interactions with regular trades.

## Commands

```bash
python3 scripts/range_fade_backtest.py \
  --candles data/replay/mnq5m_full \
  --output /tmp/range-mnq.json \
  --max-trades 1 \
  --max-losses 1

python3 scripts/range_fade_grid.py \
  --candles data/replay/mnq5m_full \
  --output /tmp/range-grid-mnq.json
```

## Limitations

- The replay data covers less than four months.
- MES and MNQ historical datasets differ in construction and price behavior.
- The range strategy has not yet been combined with regular strategy decisions.
- Midpoint-only targets and fixed stop buffers need further sensitivity testing.
- No live-paper shadow observations have been collected.

## June 5 Expanded 15-Minute Export

TradingView was asked for two years, but the delivered 15-minute exports cover
June 30, 2025 through June 5, 2026: 22,019 bars across 293 calendar dates. The
delivered 60-minute files cover only February/March 2026 through June 5, 2026.

All range tests below use one contract, one tick of adverse slippage,
pessimistic same-bar stop/target resolution, and a maximum of one range trade
per day.

### MNQ New York

The best tested New York variation was six confirmation bars and a 15% entry
zone:

| Trades | Win Rate | P&L | Expectancy | Profit Factor | Max Drawdown |
|---:|---:|---:|---:|---:|---:|
| 36 | 60.6% | $99.86 | $3.03 | 1.108 | $369.20 |

Nearby settings were mostly flat or negative. This edge is too small to survive
normal commissions reliably and is not supported for implementation.

### MNQ Asian

Six confirmation bars and a 10% entry zone produced:

| Trades | Win Rate | P&L | Expectancy | Profit Factor | Max Drawdown |
|---:|---:|---:|---:|---:|---:|
| 33 | 72.4% | $214.84 | $7.41 | 1.834 | $159.44 |

Only one trade appeared in validation and one in holdout. The result is
interesting but not validated and remains shadow-research only.

### MES and Other Sessions

- MES New York's best-looking result had only five trades.
- MNQ London was negative across the tested grid.
- MES London and MES Asian were negative across meaningful samples.
- Off-hours samples were too small to support activation.

### Regular MNQ Baseline From The Same Export

The regular system replay used the historical NY ORB plots and available MNQ
indicator context. It covers a partial New York window, so it is not a complete
all-session replay. One-hour context was delayed until the HTF bar closed to
prevent lookahead.

| Split | Resolved Trades | Win Rate | Expectancy | Profit Factor | Max Drawdown |
|---|---:|---:|---:|---:|---:|
| Development | 253 | 75.5% | $105.42 | 6.49 | $342.00 |
| Validation | 74 | 70.3% | $143.95 | 4.95 | $318.00 |
| Holdout | 86 | 82.6% | $202.68 | 10.02 | $522.00 |
| Overall | 413 | 76.0% | $132.58 | 6.77 | $522.00 |

These regular-system results are encouraging but exclude commissions and
unresolved trades. MES could not receive an equivalent regular-system test
because its new export omitted historical VWAP, EMA, and NY ORB columns.

### Updated Decision

1. Keep the regular system unchanged while collecting more complete evidence.
2. Do not implement a live or paper range lane yet.
3. Do not add separate range-trade capacity yet.
4. Continue researching MNQ Asian and MNQ New York in shadow mode only.
5. Require at least 50 resolved validation/holdout range trades after costs.

## Regular-System Always-On Schedule Test

A separate full-session MNQ feed was constructed from all 22,019 available
15-minute bars. The NY ORB is formed causally from the completed 09:30 ET bar
and carried forward until the next NY open. All other decision and risk rules
were held constant, including the daily trade cap, one-open-position rule,
one-tick adverse slippage, and pessimistic same-bar fills.

The always-on schedule:

- allows Asian, London, New York, the 08:30-09:30 gap, and off-hours;
- removes session allow windows and time cutoffs;
- removes the internal NY lunch and late-day blocks;
- retains all non-session strategy and risk gates.

| Schedule | Resolved | Win Rate | P&L | Expectancy | Profit Factor | Max Drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Current schedule | 621 | 80.0% | $92,275.10 | $148.59 | 7.03 | $618.00 |
| NY full session only | 535 | 78.3% | $80,586.50 | $150.63 | 7.90 | $618.00 |
| Always on | 776 | 78.0% | $117,211.10 | $151.05 | 6.40 | $528.00 |

At a stress cost of $5 round-trip per contract, current-schedule P&L becomes
$76,300.10 and always-on P&L becomes $96,631.10. Always-on remains ahead by
$20,331.00.

### Always-On Session Breakdown

| Entry Session | Resolved | Win Rate | Expectancy | Profit Factor | Holdout Expectancy |
|---|---:|---:|---:|---:|---:|
| Asian | 199 | 83.4% | $145.35 | 7.20 | $172.89 |
| London | 126 | 77.0% | $136.30 | 5.00 | $148.60 |
| New York | 415 | 77.3% | $160.99 | 7.10 | $194.60 |
| Session gap | 31 | 58.1% | $122.75 | 3.29 | $262.29 |
| Off-hours | 5 | 60.0% | $99.56 | 4.89 | $87.60 |

### Always-On Decision

Always-on is supported as the next paper/shadow schedule candidate. It is not
approved for live activation yet because:

1. The history is approximately eleven months, not the requested two years.
2. Some overnight ORB strategies reference the prior New York opening range.
3. Only five off-hours trades occurred.
4. The combined result still excludes unresolved trades and full broker fees.
5. Live alert delivery must be verified across all sessions before widening
   backend session gates.
