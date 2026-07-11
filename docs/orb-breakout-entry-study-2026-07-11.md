# ORB Breakout Entry Study — 2026-07-11

## Question

A live audit of the last 10 trading days found MNQ `orb_breakout` NO_TRADE 18/18
times on `ENTRY_DETACHED_FROM_PRICE` — the strategy's static entry anchor
(`orb.high + 2 ticks`) goes stale before the 15-minute decision engine
evaluates it. The market-entry study from 2026-07-02
(`docs/orb-market-entry-study-2026-07-02.md`, PR #143) already showed unbounded
market entry is robust for MNQ ORB overall (breakout + reclaim combined,
n=300, both halves positive) but only under the **runner** exit — its
"decisive control" paragraph noted static exit was "worthless" without
isolating that by strategy. This study reuses the exact same arms
(`logs/retest_baseline_off`), 5-minute bars (`data/replay_polygon_5m`), fill
mechanism, and resolve harness — zero new assumptions — to break the result
out by **strategy** (`orb_breakout` vs `orb_reclaim` separately) and by
**exit mode** (runner vs static), answering: does market entry fix
`orb_breakout` specifically, under the box's actual currently-pinned exit
mode (`EXIT_MODE=static`)?

Reproduce: `python3 scripts/orb_breakout_entry_study.py`.

## Results — MNQ `orb_breakout`, unbounded market entry

| Exit mode | n (resolved) | Net P&L (622d) | Expectancy/trade | WR | PF | Halves |
|---|---:|---:|---:|---:|---:|---:|
| runner (1.0R activation / 0.5R trail) | 60 | +$1,043.75 | $17.40 | 58.3% | 1.77 | +26.64 / +8.15 |
| static (current live exit) | 63 | +$56.50 | $0.90 | 71.4% | 1.06 | +8.68 / **−6.64** |

**Runner exit: both walk-forward halves positive, real sample.** Static exit
(the box's actual current setting): net effect is noise-level and fails
walk-forward — the second half is negative. Market entry alone, without the
runner exit, is not a deployable fix for `orb_breakout`.

## No-chase distance cap {2, 4, 8 ticks} — MNQ `orb_breakout`

| Cap | n (runner) | n (static) |
|---|---:|---:|
| 2 ticks | 1 | 1 |
| 4 ticks | 4 | 4 |
| 8 ticks | 4 | 4 |

Sample sizes are too small at every cap level to draw a conclusion in either
direction — **INCONCLUSIVE**, not "caps don't help." Only the unbounded leg
(n=60-63) reaches a statistically usable sample for `orb_breakout`.

## MES `orb_breakout` — stays dead regardless of cap or exit mode

Unbounded market entry, runner exit: n=200, net **−$2,273.75**, expectancy
−$11.37/trade, WR 42.5%, PF 0.70, **both halves negative** (−10.0 / −12.71).
Consistent across every cap and both exit modes tested. Confirms the existing
posture: `orb_reclaim`/`pdh_reclaim` carry MES, not `orb_breakout`.

## Open evidence gaps (not resolved by this study)

- **Re-anchored entry near evaluation time** (`momentum_entry_reanchor`,
  #94/#112): historically tested on **MES only**, combined across 3
  strategies (`vwap_hold` + `pdh_reclaim` + `orb_breakout`), and rejected
  (18 trades, 15L/3W, −$199.75, tripped the drawdown breaker) — see
  `docs/` history / memory `project_entry_staleness_fallback_gap`. No MNQ
  result exists, and no `orb_breakout`-only breakdown exists; the combined
  MES number could be driven entirely by `vwap_hold`. Untested for MNQ
  `orb_breakout` specifically.
- **Faster (5-minute) confirmation feed**: a prior study found only ~10% of
  stale-entry cases resolve within the same 15-minute bar; the other 90% is
  setup-confirmation lag (mostly `SIGNAL_BAR_VOLUME_TOO_LOW`), upstream of
  entry timing. That study was built around `pdh_reclaim`'s failure shape.
  This audit's `orb_breakout` incidents all show `market_condition: TRENDING`
  already at evaluation time — confirmation had already landed; the
  staleness is the static anchor going stale during a fast trend, not a
  confirmation-lag problem. Whether the 90%/10% split transfers to
  `orb_breakout` is untested, not assumed.

## Recommendation (operator decision, nothing enabled by this study)

1. **Sequencing dependency confirmed**: runner-exit promotion is the
   prerequisite for a viable MNQ `orb_breakout` market-entry fix, exactly as
   it already was for the broader ORB market-entry finding in PR #143. Not a
   parallel, independent option — market entry without the runner exit is
   not worth shipping for `orb_breakout` (noise-level, sign-flipping).
2. Once runner-exit promotion is proven and safely promoted (per
   `project_live_trailing_scope`'s phased plan), a scoped MNQ
   `orb_breakout` proof lane — modeled on PR #259's MNQ `orb_reclaim` proof
   mode (MNQ-only, `orb_breakout`-only, forced market entry, forced runner
   exit, campaign dedupe, existing risk gates retained, demo/paper only, no
   MES) — is the natural next build.
3. Do not build the `orb_breakout` proof lane before the runner exit is
   live — it would ship the same "worthless without the runner" gap this
   study just measured directly.
4. MES `orb_breakout` stays disabled; no evidence at any cap or exit mode
   supports revisiting it.
