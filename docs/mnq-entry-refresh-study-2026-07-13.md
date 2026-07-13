# MNQ Entry-Refresh Study — 2026-07-13

## Trigger

On 2026-07-13T02:15 UTC a live, natural MNQ `vwap_hold` SHORT candidate
(A+ confluence 10/10, R:R 3.0, `market_condition: TRENDING`) was rejected
`ENTRY_DETACHED_FROM_PRICE`: the planned entry was ~125 points above the
live price by evaluation time. The rejection was correct — this study
quantifies how often that gate fires, and whether any entry-refresh policy
recovers the missed participation without converting misses into chases.

## How often the gate fires (box journal history, 2026-06-01 → 2026-07-13)

- **302** `ENTRY_DETACHED_FROM_PRICE` NO_TRADE rows across 37 journal days
- occurred on **28 of 37 days (76%)** — near-daily, not an edge case
- **6.7%** of all NO_TRADE rows, but **30.3% of TRENDING-condition NO_TRADE
  rows** — the only market state where the gate is reachable. When
  conditions are actually favorable, roughly 1 in 3 otherwise-qualified
  decisions dies to a stale entry.
- MNQ 200 vs MES 102 (MNQ ~2x, consistent with the faster tape)
- Strategy attribution exists for only 39/302 rows (`vwap_hold` 23,
  `orb_breakout` 15, `orb_reclaim` 1) — the remaining 263 predate
  candidate-audit coverage at this gate, so no historical per-strategy
  distribution is claimed from that 13% sample.

## Method

Reuses the validated harness from PR #143 / PR #261 with zero new fill
assumptions: arms = MNQ `TRADE`+`APPROVED` rows from
`logs/retest_baseline_off` (622-day dataset; 63 `orb_breakout`,
348 `vwap_hold`, 253 `orb_reclaim`), 5-minute bars from
`data/replay_polygon_5m`, `PaperBroker` resolve with pessimistic same-bar
both-hit, runner = 1.0R activation / 0.5R trail. Predeclared costs: 1 tick
adverse slip on every market fill + $1.48 round-trip commission per
resolved trade. Walk-forward = per-strategy midpoint halves. **Predeclared
rule: any cell with resolved n<30 is INCONCLUSIVE** — no directional claims.

Policies (all causal): `static_reject` (live baseline — detached ⇒
NO_TRADE), `translate_capN` (market fill, stop/target translated with the
fill so original R geometry is preserved; chase caps 8/16/32 ticks,
0.25/0.5/1.0R, unbounded), `structural_minrr` (market fill, original
structural stop/target kept, reject if new R:R < 1.5 / risk > 1.5x /
target already passed), `confirm5m_16t` (first 5m close beyond the level,
enter next 5m open, 16-tick cap).

Reproduce: `python3 scripts/mnq_entry_refresh_study.py`. Full grid in
`scripts/mnq_entry_refresh_results.json`.

## Result 1 — runner exit is the prerequisite (third independent confirmation)

With the **static** exit, every refresh policy with n≥30 is negative for
`orb_reclaim` (e.g. 1R cap: −$4.84/trade, n=149) and negative-to-noise for
`vwap_hold` (best static cell +$3.44 with n=51; the rest ≤ +$1.48 or
negative, several failing walk-forward). With the **runner** exit,
essentially every adequately-sampled cell is walk-forward positive. Same
conclusion as PR #143 and PR #261, from a third angle: **no entry-refresh
mechanism is worth shipping before runner-exit promotion.**

## Result 2 — moderate detachment is recoverable (runner exit, net of costs)

Best cells passing all predeclared rules (n≥30, both halves positive):

| Strategy | Policy | n | Exp/trade | Halves | PF |
|---|---|---:|---:|---|---:|
| `orb_reclaim` | translate + 1.0R cap | 149 | +$12.98 | +14.32 / +11.82 | 1.69 |
| `vwap_hold` | 5m confirm + 16t cap | 51 | +$13.20 | +15.77 / +11.10 | 2.63 |
| `orb_breakout` | translate unbounded | 63 | +$11.13 | +16.54 / +5.89 | 1.83 |

Sample-size limitations, stated explicitly: every capped `orb_breakout`
cell is n<30 (INCONCLUSIVE — only its unbounded leg reaches sample);
`orb_reclaim` 5m-confirm looks exceptional (+$39.24/trade, PF 4.78) but
n=16 (INCONCLUSIVE, worth watching in shadow).

## Result 3 — the live incidents are a different failure class (the decisive finding)

Replay detachments are small: p50 ≈ 0.8–1.7R, **maximum 3.6R** across all
664 arms. The 35 strategy-attributed live incidents run **2.36R to 44.9R**
(the named 2026-07-13T02:15 incident: **16.6R**). Consequences:

1. **Every evidence-supported cap would have rejected all 35 live
   incidents** (0 of 35 are ≤1R; 0 of 35 are ≤2R). The capped policies
   validated above recover the *moderate* class only — they do not trade
   the incidents that motivated this study, and that is correct behavior.
2. **The replay "unbounded" result cannot be extrapolated to the live
   class.** The replay arm population physically contains no 4R+ gaps, so
   its positive unbounded cell is evidence about ≤3.6R chases only.
3. **All 35 live incidents return `REJECTED_TARGET_PASSED` under the
   structural rebuild** — price had blown through the original *target*,
   not merely the entry, by evaluation time. These were not late entries;
   the planned move was already complete. The 02:15 incident was
   **correctly rejected**, as was every other incident in the fixture set.

Fixture set (no outcome resolution — local 5m data ends 2026-06-26):
`scripts/fixtures/mnq_detached_incidents_2026-07-13.json` — REDACTED to
derived metrics only (timestamp, strategy, direction, detachment in ticks
and R, precomputed structural verdict). The live system's absolute
entry/stop/target levels are strategy internals and are deliberately not
published; every analytic claim above derives from the redacted metrics.

## Classification

- Detachment safety gate (`ENTRY_DETACHED_FROM_PRICE`): **VALIDATED** — do
  not disable.
- **Moderate-detachment lane** (≤~1R): `orb_reclaim` translate+1R-cap+runner
  and `vwap_hold` 5m-confirm+runner are **PROMISING BUT UNPROVEN** — replay
  evidence is positive and walk-forward-stable, but no shadow/live evidence
  exists, and both depend on runner-exit promotion completing first.
- **Extreme-latency lane** (2.4R–45R, the live incident class):
  **BROKEN UPSTREAM TIMING / RESEARCH ONLY** — not recoverable by any entry
  policy tested; the setup fires only after the move is complete. The fix,
  if any, is earlier signal generation (upstream), not entry refresh.
  Replay contains no data for this class; only a shadow lane logging live
  incidents can produce evidence about it.

## Recommendation (operator decision, nothing enabled by this study)

1. Sequencing unchanged: runner-exit promotion (in progress via the
   runner-shadow evidence gate) remains the prerequisite for any refresh
   build.
2. After runner promotion is proven: a scoped Phase-B **entry-refresh
   shadow lane** (observation-only rows logging original entry, live price,
   detachment in R, refreshed geometry, would-trade decision and rejection
   reason) is the next evidence step — it covers both lanes at once and is
   the only way to accumulate data on the extreme class.
3. Do not implement a global detached-entry fallback; the two failure
   classes need different mechanisms, and the extreme class needs none of
   them until upstream timing is understood.
