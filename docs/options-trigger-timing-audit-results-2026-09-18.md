# Options trigger-time audit results — 2026-09-18

## Status

**PARTIALLY PROVEN.** The timing defect is now measured on a hashed historical
underlying dataset. This is strategy-structure evidence only; it is not option
profitability or promotion evidence.

Audit identity: `OPTIONS_STRAT_TRIGGER_TIMING_AUDIT / trigger-audit-v0.1`

Window: 2026-09-09 through 2026-09-15 (5 NYSE sessions)

Universe: frozen primary 20 symbols:
AAPL, MSFT, NVDA, TSLA, SPY, QQQ, AMZN, GOOGL, PLTR, INTC, IWM, TLT, JPM, BAC,
COIN, XOM, MRK, WMT, NFLX, GE.

Feed: consolidated SIP historical 30m + 5m. Per-session canonical bar hashes are
stored in the committed summary JSON.

## What changed

The old `cov-v0.1` observer names a setup only after the breakout 30m bar is
complete, then defines first sight as that close + 960 seconds rounded to the
scanner grid.

The trigger-time model instead:

1. freezes the precursor bar before the watched 30m bar begins;
2. resolves the first high/low break from 5m bars;
3. refuses to invent ordering when both boundaries break in the same 5m bar;
4. preserves the first break even if the 30m candle later becomes a 3;
5. re-anchors the logical next 30m watch window across a regular-session
   overnight gap rather than expiring the setup in non-trading clock time;
6. samples market context no later than the start of the 5m trigger bar.

No runtime, alerting, broker, DEMO, or live-trading path changed.

## Measured timing result

Across 1,268 armed watch bars under the current fail-closed trigger policy:

- 560 first-break triggers
- 20 ambiguous first-break bars
- 447 cancelled signals
  - 331 same-direction 2-2-2 run-context breaks
  - 27 same-direction 3-2-2 continuations not separately approved
  - 89 1-2-2 reversals cancelled because the same-direction side broke first
- 241 no-trigger watch bars

Among the 506 trigger-time events that also existed in the old directional-bar
observer, the old first-sight delay was:

- median: **47.95 minutes**
- minimum: **22.95 minutes**
- maximum: **47.95 minutes**

This is the measured delay from the start of the 5m bar that first proves the
break to the old observer's first-sight timestamp. The exact intrabar break can
occur up to 5 minutes after that start, so this is not a tick-precise latency
measurement.

## The most important population change

**54 currently approved trigger-time events were absent from the old directional-bar population.**

All 54 are cases where a valid first boundary break occurred, but the same 30m
bar later broke the opposite side and finished as an outside bar. The old
observer therefore discarded the earlier actionable 2 because it only labels
the final 30m scenario. Same-direction 2-2-2 and 3-2-2 continuation breaks are
excluded here because current main now fails those entry interpretations closed.

Comparable events changed direction **0 times**. The main problem is therefore
not that 2U/2D direction was usually misread; it is that the observer waited
long enough for some triggered 2s to become 3s and disappear from the setup
population.

## Family counts

| Trigger-time family | Old comparable count | Trigger-time count | Newly visible |
|---|---:|---:|---:|
| 2-1-2 continuation | 59 | 64 | 5 |
| 2-1-2 reversal | 81 | 89 | 8 |
| 2-2-2 reversal | 176 | 198 | 22 |
| 3-1-2 | 21 | 22 | 1 |
| 3-2-2 reversal | 29 | 30 | 1 |
| 1-2-2 reversal | 60 | 73 | 13 |
| direct 3-2 continuation | 67 | 70 | 3 |
| direct 3-2 reversal | 13 | 14 | 1 |

Same-direction 2-2-2 continuation (331 first breaks) and same-direction
3-2-2 continuation (27 first breaks) are now retained as **CANCELLED/context**,
not trigger-time trade families.

For 2-1-2 reversal specifically, all 81 comparable old events retained the same
family and direction. The trigger-time clock adds 8 additional first-break
reversals that later became outside bars. That means the original 212R family
definition was stable on comparable bars; the observation clock was incomplete.

## 3-2 was mislabeled as generic context

The old observer recorded 80 rows as `OTHER:strat_outside_continuation`.
Trigger-time classification resolves those into:

- 67 direct 3-2 continuations
- 13 direct 3-2 reversals

Four additional 3-2 first breaks later became outside and were absent from the
old directional-bar set, producing 84 trigger-time 3-2 events total
(70 continuation, 14 reversal).

External rule review supports treating 3-2 as a direct actionable family:
the trigger is the break of the outside bar's high/low, with continuation or
reversal determined by which side breaks relative to the outside bar's
direction.

## Market-context timing also changes the gate

On the same 506 comparable triggered events under the current fail-closed
family policy:

- old 30m-close alignment passed 28
- completed-only trigger-time alignment passed 22
- causal developing-HTF trigger-time alignment passed 8

Completed-only trigger-time vs old close:

- 16 passed both
- 12 passed only after waiting until the 30m close
- 6 failed at the old close but passed at trigger time
- 472 failed both

Developing-HTF trigger-time vs old close:

- 3 passed both
- 25 passed only at the old close
- 5 failed at the old close but passed at trigger time
- 473 failed both

Therefore the timing correction does **not** simply make more trades qualify.
It changes what market context was actually knowable at entry. Some old passes
were only true later; some trigger-time passes disappeared by the close.

For 212R, 1 of 81 comparable events passed the old close alignment and the
completed trigger-time alignment; none passed the stricter developing-HTF
variant in this five-session window. That is not a strategy verdict. It only
shows that the current full-alignment requirement is a major independent
filter and must be frozen separately from trigger detection.

## External rule mismatches that still require correction/audit

### 3-2

The reviewed TheStrat rule says the 3-2 should trigger immediately through the
outside bar's boundary, use a tight stop near the entry line, and has no
magnitude of its own. The current generic coverage geometry would instead be
capable of using the far side of the outside bar as invalidation and generic
nearest structural targets. **Do not promote 3-2 using the generic geometry.**

### 2-1-2 / 3-1-2 reversal

The reviewed rule agrees with the trigger-time design: enter on the break of
the inside bar, stop at the other side of the inside bar, and use the parent
2/3 extreme as the pattern's magnitude. Our generic structural target finder
contains that parent extreme, but can choose another nearer structural level.
That difference needs to be measured instead of assumed equivalent.

### 2-2 reversals / 3-2-2

The reviewed reversal rule uses the prior bar break as trigger and the
bar-before-trigger extreme as magnitude. The current generic target selection
can choose other structural levels first. Canonical magnitude vs generic
target needs a controlled comparison.

### 2-2-2 continuation

The reviewed material describes same-direction 2-2-2 primarily as a confirmed
run/trend state, not a standalone setup to chase at the third bar's close. The
trade entry is preferably a lower-timeframe reversal in the run's direction
that puts the next higher-timeframe 2 in force. This family therefore needs an
entry-architecture audit, not only a timing patch.

## Still unproven

- option P&L under trigger-time entries;
- decision-time historical option quotes/Greeks/OI at the new trigger clock;
- whether the running options scanner can observe the trigger in real time
  without the historical SIP delay;
- which market-alignment variant is the approved rule;
- family-specific stop/target/magnitude parity;
- DEMO eligibility or any strategy promotion.

## Next ordered work

1. audit canonical magnitude/stop geometry against the generic target finder;
2. freeze one trigger-time market-context rule;
3. determine the real-time read-only source for boundary monitoring;
4. only then freeze historical option quote timestamps and rerun the options
   qualification gate.

212R remains **WAIT/BLOCKED** while this proof is completed.
