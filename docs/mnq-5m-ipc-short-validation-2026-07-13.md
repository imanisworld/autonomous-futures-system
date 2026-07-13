# MNQ 5m impulse-pullback-continuation, short-only R=1.5 — narrow validation pass (2026-07-13)

Required validation pass on the one segment that cleared the initial replay
gate (see `docs/mnq-5m-impulse-pullback-continuation-study-2026-07-13.md`).
1,615 resolved trades, 2024-07-02..2026-06-26, 2-tick slippage baseline.

## Result: FAILS THE BUILD GATE. Close as research-only.

The disqualifying finding is unambiguous and stated in the operator's own
gate: **"proceed only if removing the best 10 trades does not make it
clearly negative."** It does:

| | n | net P&L | mean expectancy | PF |
|---|---|---|---|---|
| All trades | 1,615 | +$1,315.80 | +$0.815 | 1.019 |
| Without best 5 | 1,610 | **-$1,393.55** | **-$0.866** | 0.980 |
| Without best 10 | 1,605 | **-$3,461.65** | **-$2.157** | 0.950 |

Top 10 trades: $621.52, $607.27, $541.27, $486.52, $452.77, $426.52, $421.27,
$413.77, $410.77, $395.77 — i.e. the entire positive result is carried by
10 outlier trades out of 1,615 (0.6% of the sample). **Median trade
expectancy is -$29.98** — most trades lose money; the mean is positive only
because of a fat right tail. This is not a "many small wins" edge; it is a
"mostly small losses, occasionally a large trend-continuation win"
distribution, and 10 trades' worth of luck in exactly which large moves
occurred is not something to build a runtime lane around.

## Full required checks

### 1-2. Subgroup splits (2-tick, all n reported so no subgroup is silently small)

**By session** — no single session carries it exclusively (this criterion
passes on its own, but doesn't rescue the outlier problem):

| Session | n | win rate | net P&L | mean exp | PF |
|---|---|---|---|---|---|
| asian (overnight) | 606 | 43.6% | +$549 | +$0.91 | 1.031 |
| london (premarket) | 453 | 40.8% | +$215 | +$0.48 | 1.012 |
| new_york (rth) | 556 | 39.6% | +$552 | +$0.99 | 1.016 |

**By year** — the most recent year is negative:

| Year | n | net P&L | mean exp | PF |
|---|---|---|---|---|
| 2024 (partial, from Jul) | 405 | +$856 | +$2.11 | 1.061 |
| 2025 | 813 | +$2,136 | +$2.63 | 1.065 |
| **2026 (partial, through Jun)** | 397 | **-$1,676** | **-$4.22** | 0.927 |

**By time-ordered quartile** — the most recent quarter of the sample is
negative:

| Quartile | n | net P&L | mean exp | PF |
|---|---|---|---|---|
| Q1 (earliest) | 403 | +$721 | +$1.79 | 1.051 |
| Q2 | 404 | +$773 | +$1.91 | 1.042 |
| Q3 | 404 | +$1,325 | +$3.28 | 1.093 |
| **Q4 (most recent)** | 404 | **-$1,503** | **-$3.72** | 0.934 |

**By month** — 24 months, roughly split: 13 positive, 11 negative, no
single month drives the aggregate (the full table is in the companion
script output; magnitudes range from -$22.30/trade in 2026-01 to
+$31.13/trade in 2025-10 — noisy, not a hidden single-month artifact, but
consistent with the quartile/year finding that the *recent* data trends
negative).

**By regime** — 1,611 of 1,615 trades (99.8%) occur in
`TRENDING/DOWN/STRONG`, as expected by construction (the detector requires
this regime for its impulse condition). The 4 trades in other regimes are
not a meaningful subsample.

### 3. Required metrics

- Median trade expectancy: **-$29.98** (most trades lose)
- Max drawdown: **-$3,636.10**
- Longest losing streak: **13 consecutive losses**
- Slippage sweep: 2-tick +$0.815/trade -> 3-tick +$0.315/trade -> 4-tick
  **-$0.185/trade** (flips negative)
- Commission included throughout ($1.48 round-trip)
- Sample counts: all reported above; no subgroup below n=46 (2026-06)

### 4. Manual validation of real events

**This morning's continuation after the bounce (2026-07-13)**: pulled real
5-minute bars from the box (`logs/tf5m/bars_MNQ_2026-07-13.jsonl`) plus the
real 15-minute journal's trend classification (DOWN/STRONG/TRENDING held
12:30-13:15 UTC, the closest available ground truth for the live 5-minute
payload's own trend fields, which are not separately logged anywhere). Fed
through the actual detector: **it fires** a real SHORT continuation at
13:30 UTC — entry 29645.50, stop 29723.00, target 29529.25 — exactly at the
high-volume breakdown bar (74,855 contracts vs a normal ~5-7k) following the
12:45-13:00 bounce and 13:05-13:25 chop. **This trade is still open** as of
the last available bar (13:35 UTC, price 29622.75, well past entry,
stop not yet hit) — it cannot be scored as a win or loss yet, and I did not
wait/poll for its resolution (no forcing evidence). It does concretely
confirm the detector catches the exact real-world pattern the operator
described.

**Two historical winners, two historical losers** (median-magnitude, not
outliers, manually read bar-by-bar against the raw replay files):

| Case | Entry ts | Setup | Stop/target math | Resolution |
|---|---|---|---|---|
| Winner | 2025-01-24T18:05 | 1-bar pullback (18:00) after DOWN/STRONG/TRENDING impulse; close 21925.75 < pullback low 21934.00 | stop 21954.75 = pullback high 21952.75+2 (matches); target 21882.25 = entry-1.5R (matches) | +$84.52, target eventually hit on later bars |
| Winner | 2025-04-02T10:20 | 1-bar pullback (10:15); close 19486.75 < pullback low 19489.00 | stop 19515.75 = pullback high 19513.75+2 (matches) | +$84.52 |
| Loser | 2024-10-08T01:30 | 2-bar pullback (01:20, 01:25) after impulse bar 01:15 (DOWN/STRONG/TRENDING); close 19963.25 < pullback low 19976.50 | stop 19991.50 = pullback high 19989.50+2 (matches); target 19920.88 = entry-1.5R (matches) | -$58.98, stopped out at 01:45 when price whipsawed back up through 19991.50 |
| Loser | 2025-02-28T03:30 | 1-bar pullback (03:25) after impulse bar 03:20; close 20542.75 < pullback low 20546.25 | stop 20571.00 = pullback high 20569.00+2 (matches); target 20500.38 = entry-1.5R (matches) | -$58.98, stopped out at 04:05 when price reversed back up through 20571.00 |

All four cases confirm: **only strictly-prior bars were used to build the
pullback and confirm the impulse** (no lookahead), stop/target arithmetic
matches the detector's own formulas exactly, and resolution matches the raw
subsequent OHLC. The detector's *mechanics* are sound — this is not a
lookahead bug or an arithmetic error. The problem is purely that the
aggregate edge does not survive removing its own best 10 trades.

## Build gate checklist (operator's explicit criteria)

| Criterion | Result |
|---|---|
| RTH or a defined session independently positive | **PASS** — all three sessions individually positive |
| Both walk-forward halves positive | **PASS** — both halves positive at 2-tick |
| No single month or handful of trades carries the result | **FAIL** — 10 trades (0.6% of sample) carry the entire result |
| Removing best 10 trades does not make it clearly negative | **FAIL** — flips to -$2.157/trade, PF 0.950 |
| 3-tick slippage remains positive | PASS (+$0.315/trade) — moot given the above |
| Manual cases match intended behavior | **PASS** — mechanically sound, no lookahead |

**Two of six explicit criteria fail, including the one the operator
specifically flagged as most likely to be disqualifying.** Per the stated
rule ("if those checks fail, close it as research"): **close it.**

## Verdict

**REJECT for runtime purposes. Research-only, alongside the level-fade
study.** No shadow lane will be built from this design. The setup mechanics
are correct and the manual validation is clean, which is worth preserving
for any future attempt, but the aggregate result is an artifact of a small
number of large trend-continuation days, not a repeatable edge — the same
failure family (thin, top-heavy expectancy that a handful of large moves
prop up) already seen, in a different form, in the rejected level-fade
study's >8R bucket.
