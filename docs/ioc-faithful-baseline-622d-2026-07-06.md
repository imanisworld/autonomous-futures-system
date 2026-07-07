# IOC-Faithful 622-Day Baseline — 2026-07-06

## Decision

The headline replay edge (six figures per instrument over 622 days) is an
artifact of the legacy fill model, not tradable edge. Under the IOC-faithful
entry fill model (#145) the same system over the same 622 days is
zero-to-negative on both instruments, in both walk-forward halves, under both
exit modes. Go-live gate criterion A ("edge proven in replay") is hereby
**un-stamped**: edge is NOT currently proven under fillable-only accounting.

Recommended actions:

1. Treat the live 30-fill proof as an **operational** gate only (reconcile,
   brackets, no manual touch) until an entry mechanism with honest positive
   expectancy exists; it is currently sampling a ~zero-edge universe.
2. Prioritize Workstream A Phase 1 (stop-market entry with slippage budget).
3. `MES orb_reclaim` is the single walk-forward-robust honest cell (details
   below) — the natural first candidate for a re-weighted, fillable book.
4. `pdh_reclaim` is honest-negative everywhere it trades — candidate to
   disable pending redesign (consistent with the entry-staleness incident
   analysis of 2026-06-30).

## Method

- 622 daily 15-minute Polygon files per instrument (2024-07-01 → 2026-06-26),
  replayed with `scripts/run_replay_batch.py` on main @ c2188c7.
- Full 2×2 matrix, matched pairs — identical config except the two factors:
  `ENTRY_FILL_MODEL` ∈ {market (legacy), ioc_limit} ×
  `EXIT_MODE` ∈ {static, runner_live (1.0R activation / 0.5R trail)}.
- Live entry tolerances: `ENTRY_SLIPPAGE_TOLERANCE_TICKS` MES=16 / MNQ=32.
- Walk-forward: midpoint day split (same convention as the #142 scorecard).
- Aggregation: `scripts/ioc_baseline_622d_analysis.py` (decision/outcome
  pairing identical to `run_replay_batch._strategy_breakdown`).
- Fill-model verification: 6/6 sampled `ENTRY_NOT_FILLED` bookings hand-checked
  against candle data — in every case the IOC limit was genuinely unreachable
  at the decision bar (the live "level already passed" no-fill pattern).

## Pass 1 — as configured: the honest system halts itself in month one

With production risk rules, the `ioc_limit`+static legs tripped the 20%
account-drawdown breaker in **July 2024** (first month of the window) and
never traded again: "Account drawdown 20.9% exceeds max 20.0% from peak
$1,500.00". The legacy-fill legs never trip it because they win from day one.

Caveat: the replay account starts at $1,500, so 20% = $300 of drawdown; a
$50k live account would absorb the same dollar losses without tripping. The
halt is replay-scale-dependent — but the *direction* of the equity curve that
caused it is not. Pass 2 disables only this breaker to measure the
unconditional edge (all other rules, including max_daily_loss, unchanged).

## Pass 2 — drawdown breaker disabled: the honest full-period numbers

Legacy-fill legs reproduced byte-identical to pass 1 (config-drift check ✅).

| Leg | Instr | Attempts | Fill% | Resolved | WR | Net P&L | H1 P&L | H2 P&L |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| market_static | MES | 1456 | 100% | 1375 | 50.2% | $241,242 | $95,461 | $145,781 |
| market_static | MNQ | 1005 | 100% | 999 | 52.2% | $106,208 | $53,141 | $53,066 |
| market_runner | MES | 1457 | 100% | 1380 | 50.7% | $244,462 | $98,680 | $145,781 |
| market_runner | MNQ | 1004 | 100% | 998 | 52.2% | $105,406 | $52,340 | $53,066 |
| ioc_limit_static | MES | 1654 | 37.2% | 570 | 32.3% | **-$1,550** | -$1,219 | -$330 |
| ioc_limit_static | MNQ | 823 | 36.3% | 296 | 34.1% | **-$1,523** | -$467 | -$1,056 |
| ioc_limit_runner | MES | 1946 | 36.5% | 678 | 46.9% | $224 | $392 | -$167 |
| ioc_limit_runner | MNQ | 1073 | 36.2% | 384 | 39.6% | $228 | $598 | -$370 |

Per-trade expectancy with 95% confidence intervals (honest legs):

| Leg | Instr | n | Expectancy | 95% CI |
|---|---|---:|---:|---|
| ioc_limit_static | MES | 570 | -$2.72 | [-$8.50, +$3.06] |
| ioc_limit_static | MNQ | 296 | -$5.15 | [-$9.24, -$1.06] (significantly negative) |
| ioc_limit_runner | MES | 678 | +$0.33 | [-$5.63, +$6.30] |
| ioc_limit_runner | MNQ | 384 | +$0.59 | [-$5.05, +$6.24] |

Reading per the pre-agreed framework:

- **Leg A (IOC + static = honest production config): uniformly negative.**
  Both instruments, both halves. MNQ significantly so.
- **Leg B (IOC + runner): does not rescue.** Aggregate ~$0 with second-half
  sign flips on both instruments — fails the walk-forward rule.
- ~63% of approved entries never fill (fill rate 36-37%), consistent in
  direction with the live no-fill experience and the #145 2025-03 sanity
  check. The legacy model's edge lived almost entirely in trades that
  cannot be filled with a Limit-IOC at the planned level.

## Per-strategy (honest legs, breaker off)

MES, ioc_limit_static:

| Strategy | Attempts | Fill% | Resolved | WR | Net P&L | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| orb_breakout | 281 | 21.0% | 57 | 35.1% | -$81 | -$38 | -$44 |
| orb_reclaim | 302 | 60.6% | 154 | 37.7% | +$1,014 | +$432 | +$582 |
| pdh_reclaim | 154 | 51.3% | 78 | 24.4% | -$1,404 | -$1,036 | -$368 |
| vwap_hold | 917 | 32.2% | 281 | 31.0% | -$1,078 | -$574 | -$503 |

MES, ioc_limit_runner:

| Strategy | Attempts | Fill% | Resolved | WR | Net P&L | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| orb_breakout | 339 | 22.1% | 72 | 48.6% | +$279 | -$51 | +$329 |
| orb_reclaim | 357 | 59.1% | 191 | 55.5% | +$2,419 | +$1,877 | +$542 |
| pdh_reclaim | 185 | 46.0% | 84 | 30.9% | -$1,672 | -$1,132 | -$540 |
| vwap_hold | 1065 | 31.9% | 331 | 45.6% | -$802 | -$15 | -$787 |

MNQ, ioc_limit_static:

| Strategy | Attempts | Fill% | Resolved | WR | Net P&L | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| orb_breakout | 75 | 18.7% | 14 | 42.9% | +$11 | +$24 | -$13 |
| orb_reclaim | 189 | 27.0% | 48 | 25.0% | -$762 | -$136 | -$626 |
| pdh_reclaim | 47 | 53.2% | 25 | 36.0% | -$154 | -$119 | -$36 |
| pdl_reclaim | 7 | 57.1% | 4 | 75.0% | +$41 | +$4 | +$37 |
| vwap_hold | 299 | 30.1% | 90 | 34.4% | -$246 | +$84 | -$330 |
| vwap_reclaim | 206 | 55.8% | 115 | 34.8% | -$413 | -$287 | -$127 |

MNQ, ioc_limit_runner:

| Strategy | Attempts | Fill% | Resolved | WR | Net P&L | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|---:|
| orb_breakout | 89 | 18.0% | 16 | 37.5% | +$200 | +$200 | $0 |
| orb_reclaim | 265 | 28.3% | 72 | 40.3% | -$379 | +$139 | -$518 |
| pdh_reclaim | 74 | 50.0% | 37 | 32.4% | -$442 | -$234 | -$208 |
| pdl_reclaim | 12 | 58.3% | 7 | 42.9% | +$168 | -$5 | +$174 |
| vwap_hold | 373 | 30.3% | 113 | 38.0% | +$256 | +$406 | -$150 |
| vwap_reclaim | 260 | 53.8% | 139 | 42.4% | +$424 | +$112 | +$312 |

Notable cells:

- **MES orb_reclaim is the only walk-forward-robust honest performer**:
  positive in both halves under BOTH exits; under runner,
  +$12.67/trade, 95% CI [+$0.20, +$25.13] (n=191, WR 55.5%, 59% fill rate).
  Barely clears zero — a candidate, not a proof.
- MNQ vwap_reclaim under runner is positive both halves (+$112/+$312) but its
  CI includes zero ([-$5.26, +$11.36], n=139) and it fails under static.
  Watch, don't promote. (MES has vwap_reclaim disabled by config.)
- pdh_reclaim loses in every honest cell — 8 of 8 half-period cells negative.
- vwap_hold, the largest single source of the legacy $147k MES "edge", is
  honest-negative on MES under both exits.

## Caveats

- Entry attempts differ across legs (e.g. 1654 vs 1456 MES) because no-fills
  do not consume the session budget, freeing later slots. Matched-pair
  comparisons are per-leg-internal (halves, strategies), unaffected.
- IOC marketability is evaluated at the decision bar's close (order arrival
  proxy), per #145. Live no-fill rate (~40-86% depending on window) brackets
  the replay's ~63%.
- Magnitudes are 1-2 micros with 1-tick adverse slippage on fills that occur;
  absolute dollars remain replay-scale ("magnitude = fiction" still applies) —
  the signs and splits are the finding.

## Artifacts

- Analysis: `scripts/ioc_baseline_622d_analysis.py`
- Machine-readable results: `scripts/ioc_baseline_622d_results_as_configured.json`
  (pass 1) and `scripts/ioc_baseline_622d_results_breaker_off.json` (pass 2)
- Raw journals: `logs/replay_622d_*` and `logs/replay_622d_nodd_*` (local,
  uncommitted; regenerate with the commands in this doc's Method section)
