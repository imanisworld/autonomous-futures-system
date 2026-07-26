# MNQ 4HR Re-Trigger — Corrected IOC Evidence (2026-07-26)

## Verdict

**WAIT — zero executable fills.**

The canonical detector still finds the same 81 historical MNQ setups as the
prior study, but the result previously described as **PROMISING BUT UNPROVEN**
does not survive as current executable evidence. On the corrected replay state:

- 81 canonical setups were detected;
- 43 occurred on a corrected `TRENDING` bar and 38 were blocked by the global
  market-condition gate;
- current downstream quality/risk gates left one approved order attempt;
- that one order self-cancelled under the current 32-tick MNQ Limit-IOC
  tolerance;
- **0 filled / 0 resolved / $0 gross / $0 net**.

There is therefore no honest WR, expectancy, PF, H1/H2 P&L, direction split,
drawdown, largest loss, or fat-tail statistic to report. Zero is not a negative
half: neither half contains a fill. The correct classification is `WAIT`, not
`BROKEN`, because no executable outcome sample exists.

ORB Breakout was explicitly held by the operator amendment and was not run.
MES was not run. No active observation, demo state, risk rule, broker
configuration, `.env`, runtime strategy, or Pine logic was touched.

## Preflight

| Check | Result |
|---|---|
| Exact base | `origin/main@69ec77fd33834a437fec77a51249fa1d66030a16` (post-#346) |
| Isolated branch/worktree | `codex/fresh-4hr-orb-evidence`, `/private/tmp/afs_fresh_4hr_orb` |
| Canonical detector | `strategy.four_hr_retrigger.advance_4hr_retrigger`, merged by #317 |
| Fixed stop | last completed 1H candle at entry; detector state fixes it for trade life |
| Target | prior 4PM reference level |
| Day-only exit | exact 15:55 ET bar through `execution.day_only_exit` |
| Engine | current `replay.replay_engine.ReplayEngine` |
| Market condition | current `scripts.pine_market_condition.reconstruct_bar` formula |
| Valid reconstructed buckets | only `DEAD`, `CHOPPY`, `TRENDING`, `RANGE_BOUND`; no `CONSOLIDATING`/`UNKNOWN` output |
| IOC | current `PaperBroker(entry_fill_model="ioc_limit")`, current MNQ tolerance = 32 ticks |
| Same-bar ambiguity | pessimistic stop-first |
| Slippage | 2-tick baseline; 1/2/3/4-tick sensitivity |
| Commission | **$1.48 round trip**, from `execution.mnq_strat_evidence.MNQ_COMMISSION_ROUND_TRIP` |

The directive's original $1.24 figure is an older study precedent. Current
repository execution-evidence constants are $1.48 for MNQ and MES, so this
run uses $1.48 and reports gross separately. With no fills, no commission is
charged.

The legacy `derive_market_condition()` calls remain in the converters only to
populate `legacy_market_condition` provenance. Current converter wiring assigns
the engine-facing `market_condition` from `reconstruct_bar`. The evidence
harness rematerialized the existing 5-minute source in `/private/tmp` and
verified that the resulting engine-facing field contains no invalid label.

## Corpus and reproducibility

- Source: existing local `data/replay_polygon_5m/MNQ`
- Coverage: 621 JSONL files, 140,115 five-minute bars
- Exact range: `2024-07-02T13:30:00+00:00` through
  `2026-06-26T20:55:00+00:00`
- Chronological midpoint: `2025-06-29T17:12:30+00:00`
- Source tree SHA-256:
  `177fcc9e79853c27eef8e3edfd3370f0b6d6a023b47e58f3f5c0c70773e3128b`
- Corrected temporary corpus tree SHA-256:
  `48bd4ac043d1225c860624aaf38e30e17e8f9f590ea1c5782fd6052dd526882e`

Market-condition distribution:

| State | Before (legacy field) | After (canonical engine field) |
|---|---:|---:|
| TRENDING | 121,672 | 44,696 |
| RANGE_BOUND | 0 | 66,456 |
| CHOPPY | 3,003 | 19,056 |
| DEAD | 0 | 9,888 |
| CONSOLIDATING | 15,440 | 0 |
| unavailable warmup | 0 | 19 |

The 140,096 available reconstructed bars carry
`RECONSTRUCTED_UNVALIDATED_INIT`, and the first 19 bars are
`UNAVAILABLE_WARMUP`. That is the current module's honest provenance:
the formula is canonical, but Polygon-derived recursive indicator
initialization has not been independently overlapped against a longer Pine
pre-roll. This limitation is disclosed rather than upgraded to proven
Pine-value parity.

For performance, ReplayEngine's retained 5-minute history was bounded in the
evidence harness to 600 bars (50 hours). This is the same bound documented by
the prior canonical detector study and exceeds the detector's maximum
reference need (<36 hours). It changes no candidate semantics: the fresh run
reproduced the prior study's 81 detected setups exactly.

Reproduction:

```text
python3 scripts/four_hr_retrigger_corrected_ioc_evidence.py \
  --source /absolute/path/to/data/replay_polygon_5m/MNQ \
  --corrected /private/tmp/afs_run5_corrected_5m \
  --logs /private/tmp/afs_run5_logs \
  --out scripts/four_hr_retrigger_corrected_ioc_results.json
```

The only in-memory study overrides were:

- isolate `enabled_concepts=[strat_4hr_retrigger]`;
- select `entry_fill_model=ioc_limit`;
- set the requested slippage point (1, 2, 3, or 4 ticks).

All other current gates, position sizing, survival controls, strategy
permission, IOC tolerance, and execution behavior came from the pinned
repository configuration.

## Baseline funnel (2 ticks)

| Stage | Count | Rate / note |
|---|---:|---|
| canonical setups detected | 81 | matches prior detector count |
| corrected `TRENDING` admission | 43 | 53.1% of setups |
| non-`TRENDING` blocked | 38 | 46.9% of setups |
| downstream decision rejected | 31 | among the 43 condition-admitted setups |
| risk rejected | 11 | `stop_too_wide` |
| order attempted | 1 | approved by decision + risk |
| IOC filled | 0 | 0.0% fill rate |
| IOC cancelled / no-fill | 1 | `ENTRY_NOT_FILLED` |
| resolved | 0 | no P&L sample |
| open | 0 | identity closes through cancellation |

The trigger-row gate counts below are diagnostic and non-exclusive because a
decision can carry more than one failed gate:

| Gate | Trigger rows |
|---|---:|
| `MARKET_CONDITION_NOT_TRENDING` | 38 |
| `RR_BELOW_MINIMUM` | 23 |
| `stop_too_wide` | 11 |
| `ENTRY_DETACHED_FROM_PRICE` | 8 |
| `WEAK_BAR_CLOSE` | 3 |
| admitted with no failed gate | 1 |

## Requested metrics (2-tick baseline)

| Cell | Attempts | Fills | Fill rate | Cancel/no-fill | Resolved/open | WR | Gross | Net after commission | Expectancy/fill | PF | Max DD | Largest loss | Top-3 removal |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Overall | 1 | 0 | 0.0% | 1 | 0 / 0 | N/A | $0.00 | $0.00 | N/A | N/A | N/A | N/A | N/A |
| H1 | — | 0 | — | — | 0 / 0 | N/A | $0.00 | $0.00 | N/A | N/A | N/A | N/A | N/A |
| H2 | — | 0 | — | — | 0 / 0 | N/A | $0.00 | $0.00 | N/A | N/A | N/A | N/A | N/A |
| LONG | — | 0 | — | — | 0 / 0 | N/A | $0.00 | $0.00 | N/A | N/A | N/A | N/A | N/A |
| SHORT | 1 | 0 | 0.0% | 1 | 0 / 0 | N/A | $0.00 | $0.00 | N/A | N/A | N/A | N/A | N/A |

## Slippage sensitivity

All four passes were executed independently. They are identical because the
only approved IOC attempt was unmarketable and never opened a position;
slippage applies only after a fill.

| Adverse slippage | Setups | Condition-admitted | Attempts | Fills | No-fills | Resolved | Gross | Net | PF |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 tick | 81 | 43 | 1 | 0 | 1 | 0 | $0.00 | $0.00 | N/A |
| **2 ticks** | **81** | **43** | **1** | **0** | **1** | **0** | **$0.00** | **$0.00** | **N/A** |
| 3 ticks | 81 | 43 | 1 | 0 | 1 | 0 | $0.00 | $0.00 | N/A |
| 4 ticks | 81 | 43 | 1 | 0 | 1 | 0 | $0.00 | $0.00 | N/A |

## Comparison with the prior +$3,069.60 result

The earlier result used:

- direct canonical detector candidates without the current full decision/risk
  gate chain;
- market fills;
- a 1-tick baseline.

The fresh result uses:

- corrected engine-facing market condition;
- current canonical ReplayEngine decision and risk gates;
- current 32-tick Limit-IOC entry;
- a 2-tick baseline.

The headline difference is `$0.00 - $3,069.60 = -$3,069.60`, but this is not a
like-for-like P&L degradation calculation. It is a change in evidentiary
question: the old study demonstrated favorable outcomes if every detected
trigger received a market fill, while the fresh study asks whether the
current executable system actually admits and fills those triggers. It does
not.

Accordingly, the old +$3,069.60 remains historical detector/market-fill
provenance but cannot support the current executable-edge claim. The present
classification is **WAIT — execution sample absent**. No tuning or rule change
was performed after observing the result.

## Artifacts and validation

- `scripts/four_hr_retrigger_corrected_ioc_evidence.py` — reproducible
  evidence-only driver and analyzer.
- `scripts/four_hr_retrigger_corrected_ioc_results.json` — complete
  machine-readable assumptions, corpus hashes, per-pass funnels, metrics,
  classifications, and zero-fill trade lists.
- `tests/test_four_hr_retrigger_corrected_ioc_evidence.py` — evidence summary
  and classification regression tests.

Validation includes current market-condition wiring, canonical detector,
ReplayEngine, and evidence-analyzer tests. The SHA-256 of `risk_rules.yaml`
was identical before and after the run:
`56677a0ab37bbf6277a895fd7ddb37351f8c2436c4e48debe9c9acfa3361d2e3`.

This PR is evidence-only. Never self-merge.
