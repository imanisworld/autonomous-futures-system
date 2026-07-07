# MES orb_reclaim Deep-Dive — 2026-07-06

## Status

**Replay-robust research candidate. NOT a deployable edge and NOT a promotion.**
This doc records replay-side robustness only. Live promotion is a separate,
later decision gated on honest live evidence (see "Promotion gate" below). No
config change accompanies this study: runner exits remain pinned off
(`EXIT_MODE=static`) and no instrument/strategy isolation is enabled.

## Why this cell

The IOC-faithful 622-day baseline
(docs/ioc-faithful-baseline-622d-2026-07-06.md, PR #150) found the honest
whole-book edge is ~zero-to-negative, with exactly one walk-forward-robust
positive cell: MES `orb_reclaim` (+$12.67/trade under runner exits, 95% CI
[+$0.20, +$25.13], n=191, WR 55.5%, ~59% fill rate). This study stress-tests
that single cell against pre-agreed robustness criteria.

## Method

- Same harness and data as PR #150: 622 daily 15-minute Polygon files
  (2024-07-01 → 2026-06-26), `ENTRY_FILL_MODEL=ioc_limit`, live tolerances
  (MES=16 ticks), account-drawdown breaker disabled for full-period
  measurement, midpoint walk-forward split.
- **The 3×3 runner-parameter grid was fixed before any cell was run**:
  activation {0.5, 1.0, 1.5} R × trail {0.25, 0.5, 1.0} R. This is a
  robustness check on the shipped 1.0/0.5 config — asking whether it is a
  parameter island — not a parameter search. No cell was added, removed, or
  re-run after seeing results.
- Aggregation pairs approved TRADE decisions with type=OUTCOME rows
  (same convention as `scripts/ioc_baseline_622d_analysis.py`).

## Quarter stability (honest fills, runner 1.0/0.5)

| Quarter | n | WR | P&L |
|---|---:|---:|---:|
| 2024Q3 | 39 | 54% | +$243 |
| 2024Q4 | 25 | 56% | +$498 |
| 2025Q1 | 25 | 64% | +$332 |
| 2025Q2 | 13 | 69% | +$805 |
| 2025Q3 | 19 | 53% | +$83 |
| 2025Q4 | 26 | 38% | **-$287** |
| 2026Q1 | 16 | 50% | +$70 |
| 2026Q2 | 28 | 64% | +$676 |

**7 of 8 quarters positive.** Under static exits the same cell is 4/8 —
the runner exit is what makes this cell work, consistent with
[exit-structure findings] and the #143 control.

## Session breakdown (honest fills, runner 1.0/0.5)

| Session | n | WR | P&L |
|---|---:|---:|---:|
| new_york | 130 | 57% | +$1,688 |
| london | 41 | 54% | +$786 |
| asian | 20 | 50% | -$55 |

**Asian weakness is recorded as a FUTURE HYPOTHESIS only.** No session gate
was added or tested in this study; doing so post-hoc would be tuning on the
test set. If a session filter is ever proposed, it must be validated as its
own pre-registered study.

## Runner-parameter grid (predefined 3×3, honest fills)

| activation/trail | n | WR | Net | H1 | H2 | Both halves + |
|---|---:|---:|---:|---:|---:|---|
| 0.5 / 0.25 | 202 | 59.9% | +$2,258 | +$1,006 | +$1,253 | YES |
| 0.5 / 0.5 | 211 | 61.6% | +$1,308 | +$1,067 | +$241 | YES |
| 0.5 / 1.0 | 204 | 42.6% | +$124 | +$31 | +$93 | YES |
| 1.0 / 0.25 | 189 | 53.4% | +$2,133 | +$1,363 | +$771 | YES |
| **1.0 / 0.5 (shipped)** | 191 | 55.5% | +$2,419 | +$1,471 | +$949 | YES |
| 1.0 / 1.0 | 183 | 53.6% | +$638 | +$109 | +$529 | YES |
| 1.5 / 0.25 | 182 | 44.0% | +$2,706 | +$1,352 | +$1,355 | YES |
| 1.5 / 0.5 | 182 | 44.0% | +$1,826 | +$1,202 | +$623 | YES |
| 1.5 / 1.0 | 124 | 39.5% | **-$766** | -$383 | -$384 | no |

**8 of 9 cells positive in both walk-forward halves.** Only the extreme
corner (latest activation + widest trail — the config that waits longest and
gives back most) fails. The shipped 1.0/0.5 is not a parameter island; its
neighbors are comparable or better.

## Live-fill reconciliation

The apparent contradiction "replay fills ~59% of MES orb_reclaim attempts but
live filled 0/5" dissolved under reconstruction:

- Every `orb_reclaim` attempt in the 06-22 → 06-29 live window was **MNQ**,
  not MES. The full MNQ record since 06-22 is 3 fills / 9 attempts (33%),
  consistent with the honest replay's 27-28% MNQ fill rate. The "0/5" was a
  consecutive-no-fill stretch inside that record.
- MES `orb_reclaim` has only two live attempts ever (re-enabled 06-30). Both
  were verified genuine no-fills — the 07-02 attempt's decision bar closed
  7588.75 against an IOC cap of 7588.0 (three ticks unmarketable), and the
  07-06 attempt was the known Monday unmarketable case. 0-for-2 against a 59%
  expected fill rate is small-sample noise (P ≈ 17%), not a contradiction.

**Conclusion: the ioc_limit replay fill model is not contradicted by live
data.** The live constraint is throughput: MES generates ~2 attempts per 6
days, so live evidence accumulates slowly.

## Promotion gate (why replay robustness is not enough)

- The live runner-shadow evidence stream was found (same evening) to arm on
  journal positions that never filled at the broker — its single recorded
  armed event was such a phantom. **The honest armed-event count is zero.**
  A fill-gating fix (via the #146 `entry_order_filled` primitive) is in
  flight; the promotion count restarts from zero once it lands.
- At live throughput (~⅓ of a few attempts/day filling, of which only some
  reach +1R), a live-only ≥20-armed-event gate plausibly takes **months**.
  Whether replay-derived runner evidence may supplement live shadow rows is
  an explicitly deferred decision — logged here, not taken.

## Honest limits

- Expectancy ~$12.67/trade with a 95% CI barely excluding zero.
- One strategy on one instrument; the honest whole-book edge remains ~zero
  (#150), so this cell matters only if traded alone or in a re-weighted book.
- Replay magnitudes are 1 micro with 1-tick adverse slip; treat signs and
  splits as the finding, not the dollars.

## Reproduce

```bash
# grid cell (example): honest fills, runner exit at <ACT>/<TRAIL>
ENTRY_FILL_MODEL=ioc_limit EXIT_MODE=runner_live \
RUNNER_ACTIVATION_R=<ACT> RUNNER_TRAIL_R=<TRAIL> \
ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES=16 \
python3 scripts/run_replay_batch.py --candles data/replay_polygon/MES \
  --log-dir logs/replay_sens_a<ACT>_t<TRAIL>/MES --fresh
# run from a CWD whose risk_rules.yaml copy sets max_drawdown_percent: 0
# (breaker-off measurement pass; keep repo .env present for config parity)
```

Baseline harness and full-book results: PR #150. Related studies:
docs/orb-market-entry-study-2026-07-02.md (#143),
docs/orb-entry-fill-ab-2026-07-06.md (#152).
