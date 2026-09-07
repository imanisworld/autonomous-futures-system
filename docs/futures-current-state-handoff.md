# Futures — Current State Handoff

_As of 2026-09-07. This is the single current futures handoff. Do not recreate completed audits, restart repo cleanup, rerun settled strategy tests, or reopen closed evidence lanes unless new evidence proves a defect._

## Verdict

**PAPER ONLY / EVIDENCE WORK CONTINUES / 4HR MNQ GATE ABLATION IS THE NEXT TEST.**

The cleanup detour is closed. Transition-reclaim is settled as BROKEN. Inverse ORB has a fresh reproducible positive IOC audit but remains PROMISING BUT UNPROVEN. 4HR MNQ now has evidence that its underlying signal/bracket is profitable while the current production gates reject nearly the entire population; the next justified work is a one-gate-at-a-time ablation on that exact 81-candidate population.

No strategy promotion, deployment, risk widening, or live execution is authorized by this handoff.

## Do not redo

The following work is complete and should not be restarted:

- repository/branch/worktree cleanup audit;
- old futures defect audit items already fixed on current main;
- transition-reclaim long/short direction test;
- transition-reclaim passive matched control;
- transition-reclaim stop widening, time-exit, MAE/MFE, stop-first and session/half decomposition;
- transition-reclaim 2×2 entry/exit runner matrix;
- inverse-ORB canonical realistic-fill audit on the current reproducible population;
- 4HR MNQ gate-attribution pass;
- prior VWAP Hold IOC/runner historical evidence work.

## Sept. 7 strategy evidence

### 1. Transition-reclaim — BROKEN / CLOSED

The current executable bracket is **BROKEN**.

Final evidence:

- MES and MNQ both negative under the documented bracket;
- both chronological halves negative;
- every session negative in the costed decomposition;
- gross P&L already negative before costs;
- matched passive control outperformed the strategy on both instruments;
- exact mirrored short direction did not rescue the population;
- IOC versus market entry made essentially no difference;
- runner exit materially reduced losses, especially MNQ, but did not recover positive expectancy.

MNQ exact #475 2×2 runner matrix, 1.0R activation / 0.5R trail / 1-tick slippage / $1.48 commission / pessimistic same-bar / 1 contract:

| Entry | Exit | Net | PF | Expectancy | H1 | H2 |
|---|---|---:|---:|---:|---:|---:|
| IOC | Static | -$10,949.32 | 0.80 | -$3.33 | -$4,639.93 | -$6,309.39 |
| IOC | Runner | -$4,636.77 | 0.94 | -$1.42 | +$221.99 | -$4,858.76 |
| Market | Static | -$10,949.32 | 0.80 | -$3.33 | -$4,639.93 | -$6,309.39 |
| Market | Runner | -$4,636.77 | 0.94 | -$1.42 | +$221.99 | -$4,858.76 |

MES exact 415-row source cannot be fully replayed because the original `CME_MINI_MES1!, 5.csv` source is missing; the 405-candidate re-anchored canonical population reaches the same negative conclusion in every tested combination.

PR #475 was closed without merge. Final BROKEN verdict and the CI failure remain preserved in history. Do not tune transition stops, entries, horizons, or runner parameters. Stop testing this strategy unless genuinely new external evidence appears.

### 2. Inverse ORB — PROMISING BUT UNPROVEN / WAIT

Fresh current reproducible audit:

- population: 63 arms;
- fingerprint: `f32b1b1d...19e2da2`;
- IOC fills: 57/63;
- resolved: 57;
- net after costs: **+$1,026.64**;
- PF: **5.284**;
- expectancy/fill: **+$18.01**;
- max drawdown: **$55.90**;
- H1: **+$546.08**;
- H2: **+$480.56**;
- Asian: +$63.62;
- London: +$179.90;
- New York: +$783.12.

Disposition remains **WAIT / PROMISING BUT UNPROVEN** because the older documented n=111 inverse population is not reproducible from the current canonical journals. The stale n=111 result is context only and cannot substitute for reproducible proof.

Local evidence artifacts reported on 2026-09-07:

- `docs/inverse-orb-canonical-ioc-proof-2026-09-07.md`
- `scripts/inverse_orb_canonical_ioc_proof_2026-09-07.json`

Do not promote from this result alone. Continue natural forward evidence collection.

### 3. 4HR Re-Trigger MNQ — SIGNAL/BRACKET PROMISING; CURRENT GATING FAILS

The prior `BROKEN FOR CURRENT EXECUTABLE FORM` label is now too coarse.

Current gate-attribution evidence on the 81-candidate MNQ population:

- raw signal positive at all tested horizons;
- documented bracket: **+$3,069.60, PF 1.774**;
- structurally rejected candidates alone: **+$3,076.02, PF 1.797**;
- 80/81 candidates rejected before execution;
- 1 approved candidate, IOC no-fill;
- IOC over all candidates: 44 fills, **+$1,731.86, PF 2.001**;
- IOC over structural survivors: 2 fills, both losses, **-$37.46**.

First engine disposition:

| Blocker | Count |
|---|---:|
| `MARKET_CONDITION_NOT_TRENDING` | 38 |
| `RR_BELOW_MINIMUM` | 19 |
| `stop_too_wide` | 11 |
| `ENTRY_DETACHED_FROM_PRICE` | 8 |
| `WEAK_BAR_CLOSE` | 4 |
| IOC no-fill | 1 |

Overlapping diagnostics:

- `stop_too_wide`: 75 candidates, +$3,180.98, PF 1.847;
- `rr_below_minimum`: 61 candidates, +$1,253.70, PF 1.409;
- `min_confluence_grade`: 12 candidates, -$573.76, PF 0.423;
- `target_too_close`: 8 candidates, -$51.34, PF 0.609.

Current interpretation:

- the 4HR signal and documented bracket have evidence of edge;
- the current production gating removes nearly the entire profitable population;
- this does **not** authorize loosening risk controls yet;
- the next justified test is a one-gate-at-a-time ablation on the exact same 81 candidates.

Local evidence artifacts reported on 2026-09-07:

- `docs/4hr-mnq-gate-attribution-2026-09-07.md`
- `scripts/four_hr_mnq_gate_attribution_2026-09-07.json`

### 4. VWAP Hold MNQ NY — PROMISING BUT UNPROVEN

No new historical rebuild is needed.

Retained canonical context:

- NY-only canonical population: n=107 armed, about 55 fills per exit mode;
- IOC-close is canonical;
- both chronological halves positive across the retained 1/2/3-tick exit matrix;
- runner historically stronger than static in retained studies;
- winner concentration and thin sample remain material concerns;
- prior modified forward result (+$430.51 on 16 fills) and control result remain context, not causal proof.

Continue forward collection. Do not recreate the historical runner study.

## Immediate next test

**4HR MNQ one-gate-at-a-time ablation.**

Use the exact same 81 candidates and unchanged fills/cost assumptions. Compare current gates against removing only one blocker at a time:

1. market-condition/trending gate;
2. R:R gate;
3. stop-width gate;
4. detached-entry gate;
5. weak-close gate.

No parameter sweep. No combinations yet. No strategy changes. No runtime changes. No deployment.

Report for each ablation:

- candidates surviving;
- IOC fills;
- net P&L;
- PF;
- expectancy;
- H1/H2;
- session split;
- drawdown;
- difference versus full current-gate baseline and all-candidate IOC baseline.

The purpose is to identify whether one specific gate is destroying the edge or whether several gates interact. Only after this attribution should any rule change be considered.

## Risk semantic fix — local/uncommitted

The `$150` futures daily-loss semantic issue has been corrected locally:

- `max_daily_loss` is account-level and no longer multiplied by proposed contracts;
- a 2-contract setup is rejected once realized daily loss reaches -$150;
- contract sizing remains governed separately.

Reported local files changed:

- `risk/risk_engine.py`
- `risk_rules.yaml`
- `tests/test_risk_engine.py`

Validation reported so far: 3 targeted tests passed. General pytest suite has not yet been run. These edits remain uncommitted on the already-dirty primary worktree. Therefore **do not describe this fix as merged/current-main until it is isolated, fully tested, and committed.**

## Repo / PR status that still matters

- #475: closed without merge; transition verdict BROKEN.
- #463: deliberate HOLD until 2026-09-30. Do not retarget the existing stacked branch again. Rebuild only the intended runtime-memory-gate delta on then-current main after the hold.
- #446: options-data-health proof, separate lane; do not mix it into futures work.
- Repo cleanup: closed. Do not restart branch/worktree deletion without new provenance evidence.

## Safety posture

- Paper/demo only.
- No live broker execution authorized.
- No strategy promotion from historical results alone.
- No widening stops or weakening gates merely to make a strategy pass.
- No averaging down.
- No deployment solely because evidence docs changed.
- Missing/reproducibility gaps remain blockers to validation claims.

## Source-of-truth note

`docs/strategy-rules/Strategy_Inventory.md` predates the Sept. 7 transition, inverse-ORB, and 4HR evidence above. Until that inventory is explicitly reconciled, **this handoff controls for the Sept. 7 evidence delta**. Historical strategy docs remain useful for provenance but must not override this current evidence state.

## Smallest safe next step

Run the **4HR MNQ one-gate-at-a-time ablation** only. Then stop and classify the result before moving to Miyagi, 3-2-2, or any new strategy work.
