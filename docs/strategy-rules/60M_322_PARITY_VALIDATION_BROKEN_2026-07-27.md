# 60M 3-2-2 First Live — parity validation closure

## Verdict: BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS

**0 of 34 real historical candidates reach an actual fill** through the real
`ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker` path — even in
the most favorable hypothetical, with every proven parity defect removed.
This is the same classification as [12HR Miyagi](12HR_MIYAGI_CAUSAL_STOP_EVIDENCE_2026-07-27.md)
(BROKEN, not WAIT): a structural incompatibility between this strategy's
canonical trade geometry and the account's own, independently-validated risk
controls, not a sample-size or parity problem that more fixing could solve.

**The old canonical evidence (PR #340/#341, `60M_322_EXPANDED_EVIDENCE_2026-07-26.md`:
34 candidates, 21 fills, 20 resolved, 18W-2L, net $1,595.70, PF 10.36) is not
valid evidence for the deployable strategy.** It was produced by
`research/replay_322_honest_fill.py`, a standalone function with zero
dependency on `market_condition`/trend/EMA/R:R/confluence/stop-cap/entry-
sanity/target-distance — none of the account's real runtime controls were
ever exercised. Running the identical 34 candidates through the strategy's
actual wired-in runtime path produces zero fills.

## Why this effort exists

PR #359 wired `strategy/strat_322_first_live.py` into `DecisionEngine`'s
canonical 5-minute-native path (matching the earlier #362/Miyagi wiring
pattern) and enabled it MNQ-only, `PAPER_ELIGIBLE`, forward-demo-active.
That wiring never itself constituted new evidence — the strategy's
classification remained PROMISING BUT UNPROVEN, carried over unchanged from
the standalone research replay. This effort determines what the strategy's
real canonical historical population actually looks like once it is run
through the runtime path it is actually wired into.

## Method — three passes, in order

### Pass 1 — `main` baseline (`scripts/strat_322_parity_validation.py --label main_baseline`)

Isolated single-strategy replay (`enabled_concepts=["strat_322_first_live"]`
only, MNQ), driven through `ReplayEngine` on unmodified `origin/main`, one
day at a time for each of the 34 known candidate dates
(`data/replay_corpus_v1_5m/MNQ`, this session's 5-minute-native, #338-corrected
corpus). For each date, the strategy's own pure state machine
(`advance_strat_322_first_live`) is driven independently first to find the
exact bar the candidate triggers on — the full-engine journal is then read
at that specific bar only, never at ambient non-candidate bars elsewhere in
the day (`MARKET_CONDITION_NOT_TRENDING` fires on every non-trending bar
regardless of whether any candidate is active; anchoring to the confirmed
trigger bar is required to avoid over-counting parity-gate blocks against
bars this strategy was never actually contesting).

Result: **34/34 blocked.** All 34 confirmed TRIGGERED by the pure state
machine (matches the canonical evidence's population exactly, zero
discrepancy). 28 blocked by one of the four already-known parity gates
(`MARKET_CONDITION_NOT_TRENDING` / `TREND_STRENGTH_BELOW_REQUIRED` /
`EMA_STACK_NOT_ALIGNED` / `RR_BELOW_MINIMUM`), 5 by `ENTRY_DETACHED_FROM_PRICE`,
1 by `MARKET_CONDITION_NOT_TRADABLE` (CHOPPY).
Full detail: `scripts/strat_322_parity_validation_main_baseline_results.json`.

### Pass 2 — `#365`-corrected (`scripts/strat_322_parity_validation.py --label corrected`)

Identical method, run against [PR #365](https://github.com/imanisworld/autonomous-futures-system/pull/365)'s
code (`claude/paper-execution-parity-fixes`), which carries the four
already-authorized signal/risk-layer exemptions for `strat_322_first_live`
(TRENDING/STRONG-trend/EMA-stack/`min_rr_ratio`, both enforcement points).

Result: **still 0/34 reach fill.** The four fixed gates stop blocking (as
designed), but every candidate is caught by something else:

| Classification | Count | % of 34 |
|---|---:|---:|
| `STOP_CAP_REJECTED` (`max_stop_ticks`) | 14 | 41.2% |
| `ENTRY_DETACHED_FROM_PRICE` | 9 | 26.5% |
| `CONFLUENCE_REJECTED` (`min_confluence_grade`) | 5 | 14.7% |
| `target_too_close` (`min_target_points`, risk layer) | 4 | 11.8% |
| `MARKET_CONDITION_NOT_TRADABLE` (CHOPPY/DEAD) | 1 | 2.9% |
| `SIGNAL_BAR_VOLUME_TOO_LOW` | 1 | 2.9% |
| **FILLED** | **0** | **0%** |

This surfaced two previously-undiscovered candidate parity defects
(`ENTRY_DETACHED_FROM_PRICE`, `target_too_close`) and two low-materiality
findings (`MARKET_CONDITION_NOT_TRADABLE`, `SIGNAL_BAR_VOLUME_TOO_LOW`,
1/34 = 2.9% each). Full detail:
`scripts/strat_322_parity_validation_corrected_results.json`.

#### `ENTRY_DETACHED_FROM_PRICE` — audited, CONFIRMED real defect, DEFERRED

Zero mentions of "detach"/"straddle"/entry-sanity anywhere in
`docs/strategy-rules/60M_322_FirstLive_Rules.md`,
`research/detector_322_first_live.py`, `research/replay_322_honest_fill.py`,
or `strategy/strat_322_first_live.py`. The guard's own comment
(`strategy/signal_engine.py:1892`) states "Entries are placed as MARKET
orders, so the fill happens at the current price" — this is categorically
false for `strat_322_first_live`, which fills via
`entry_fill_model="ioc_limit"` at the trigger price, never at the bar's
close. Verified for all 9 real historical candidates this gate stopped: the
bracket is structurally sound at the actual IOC-limit trigger price in
every case (LONG: `stop < entry < target`; SHORT: `target < entry < stop`):

| Date | Dir | Entry | Stop | Target | Bracket valid at trigger price |
|---|---|---:|---:|---:|:---:|
| 2024-08-02 | SHORT | 18541.75 | 18761.25 | 18508.00 | ✓ |
| 2024-08-14 | SHORT | 19011.00 | 19175.00 | 19004.25 | ✓ |
| 2024-08-22 | SHORT | 19944.50 | 20025.75 | 19938.00 | ✓ |
| 2024-08-30 | SHORT | 19513.00 | 19603.00 | 19508.25 | ✓ |
| 2024-12-23 | SHORT | 21552.50 | 21675.50 | 21543.75 | ✓ |
| 2025-02-07 | SHORT | 21864.25 | 21967.75 | 21790.00 | ✓ |
| 2025-06-27 | LONG | 22770.25 | 22682.00 | 22787.50 | ✓ |
| 2025-10-10 | SHORT | 25293.75 | 25388.00 | 25288.50 | ✓ |
| 2026-05-12 | SHORT | 29113.50 | 29295.75 | 29113.25 | ✓ (margin 0.25) |

The bar's own close in each case moved decisively past the trigger — often
past the target too (e.g. 2024-08-02: entry 18541.75, target 18508.0, bar
close 18476.25, already through target) — which is the strategy's intended
clean-breakout signature, not a stale/gapped feed. **Confirmed real: the
guard is checking the wrong fill model for this strategy.**

#### `target_too_close` — audited, CONFIRMED real defect, DEFERRED

`RiskEngine._check_min_target_distance` (`risk/risk_engine.py:879`) is a
**second, independent enforcement** of `min_target_points` — the signal
layer's own `_enforce_min_target_distance` already carves this strategy out
(confirmed by pre-existing test
`test_generic_transforms_do_not_mutate_the_canonical_bracket`, "matches the
strat_4hr_retrigger / STRAT_212 / STRAT_122 bypass"). Same double-
enforcement shape as the already-fixed `min_rr_ratio` gate. Verified all 4
real historical candidates this gate stopped against the canonical
evidence's own trigger/stop/target (`docs/strategy-rules/evidence_322/
group1_corrected_baseline.json`) — **exact match, zero discrepancy**:

| Date | Dir | Entry | Stop | Target | Distance | Floor | Matches canonical geometry |
|---|---|---:|---:|---:|---:|---:|:---:|
| 2025-08-11 | LONG | 23745.00 | 23681.00 | 23756.25 | 11.25pt | 15pt | ✓ |
| 2025-09-30 | LONG | 24837.00 | 24722.50 | 24845.00 | 8.00pt | 15pt | ✓ |
| 2025-10-16 | SHORT | 25026.00 | 25125.50 | 25020.75 | 5.25pt | 15pt | ✓ |
| 2026-06-11 | SHORT | 28927.75 | 29295.50 | 28922.75 | 5.00pt | 15pt | ✓ |

Target is always the opposite 8AM-boundary — a structural level, not a
fixed-distance target — and this strategy's whole edge runs on tight, quick
T1 hits (real R:R values as low as 0.01-0.4 across the full population).
**Confirmed real: the risk-layer duplicate is suppressing genuine canonical
geometry, not catching a bad setup.**

### Pass 3 — hypothetical ceiling (`scripts/strat_322_parity_ceiling_pass.py`)

Research-only, zero committed-file changes: in-process monkeypatch of
`DecisionEngine._entry_bracket_straddles_price` (always True) and
`RiskEngine._check_min_target_distance` (always None), for the lifetime of
the script's own process only, against #365's code. Safe in this isolated
single-strategy run — no other strategy's candidate can ever reach either
check here. Everything else preserved unchanged: `max_stop_ticks`,
`min_confluence_grade`, `MARKET_CONDITION_NOT_TRADABLE`,
`SIGNAL_BAR_VOLUME_TOO_LOW`, and every other real `RiskEngine` control
(drawdown, daily loss, position sizing, session, contracts).

This answers the decisive question: after removing every gate proven to
have zero basis in this strategy's own canonical rules, how many real
historical trades survive the account's legitimate, preserved controls?

| Outcome | Count |
|---|---:|
| Historical candidates | 34 |
| Triggered (pure state machine) | 34 |
| Survive parity-only removals (reach RiskEngine) | 32 |
| Fail `max_stop_ticks` | **27** |
| Fail `min_confluence_grade` | 5 |
| Fail `MARKET_CONDITION_NOT_TRADABLE` | 1 |
| Fail `SIGNAL_BAR_VOLUME_TOO_LOW` | 1 |
| **Reach fill** | **0** |

Full detail: `scripts/strat_322_parity_ceiling_pass_results.json`.

#### Stop-width sanity check

Sampled the 27 `max_stop_ticks` rejections directly against real
entry/stop prices — stops range 325-924 ticks against the account's
120-tick MNQ cap:

| Date | Dir | Entry | Stop | Ticks | Cap |
|---|---|---:|---:|---:|---:|
| 2024-08-02 | SHORT | 18541.75 | 18761.25 | 878.0 | 120 |
| 2024-08-14 | SHORT | 19011.00 | 19175.00 | 656.0 | 120 |
| 2024-08-22 | SHORT | 19944.50 | 20025.75 | 325.0 | 120 |
| 2024-08-30 | SHORT | 19513.00 | 19603.00 | 360.0 | 120 |
| 2024-09-06 | SHORT | 18741.50 | 18972.50 | 924.0 | 120 |
| 2024-09-11 | SHORT | 18773.25 | 18929.25 | 624.0 | 120 |
| 2024-09-12 | LONG | 19523.00 | 19416.25 | 427.0 | 120 |
| 2024-12-23 | SHORT | 21552.50 | 21675.50 | 492.0 | 120 |
| 2025-01-20 | SHORT | 21658.50 | 21780.00 | 486.0 | 120 |
| 2025-02-07 | SHORT | 21864.25 | 21967.75 | 414.0 | 120 |

Not a harness artifact — the 8AM-outside-bar-derived stop (opposite 9AM
boundary) is structurally wide by construction, the same pattern already
established for Miyagi's causal stop.

#### `max_stop_ticks` and `min_confluence_grade` — already audited, PRESERVED

Both independently confirmed legitimate, pre-existing account-wide risk
controls (not parity defects) in the Miyagi effort and the earlier 3-2-2
confluence-grade audit respectively. Neither is touched by this study.
`min_confluence_grade`'s 5/34 (14.7%) rejection rate here is consistent
with the earlier 12-date sub-sample finding (only 1/12 WEAK) — a small
minority, not a systematic block.

## Decision

Per the operator's own decision rule: removing every proven parity defect
(the four #365 fixes plus the two newly-confirmed defects above) still
leaves **zero executable historical trades**, because `max_stop_ticks`
(79.4% of the reaching-RiskEngine population) and `min_confluence_grade`
eliminate everything that survives the signal layer. Fixing
`ENTRY_DETACHED_FROM_PRICE` or `target_too_close` would change runtime
code while producing zero additional executable historical trades.

- **`ENTRY_DETACHED_FROM_PRICE`**: CONFIRMED real defect / **DEFERRED** — no
  current operational benefit. May be revisited if another live IOC-limit-
  fill strategy is actually harmed by it.
- **`target_too_close`**: CONFIRMED real defect / **DEFERRED** — same
  reasoning.
- **`max_stop_ticks`**: PRESERVE, unchanged.
- **`min_confluence_grade`**: PRESERVE, unchanged.
- **`MARKET_CONDITION_NOT_TRADABLE`** / **`SIGNAL_BAR_VOLUME_TOO_LOW`**:
  not pursued this pass — 1/34 (2.9%) each, below the materiality bar,
  left alone per explicit operator instruction.

**60M 3-2-2 First Live: BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS.** The
old PF 10.36 / 18W-2L / $1,595.70 figures are not valid evidence for the
strategy as it is actually wired into runtime — they describe a fill model
the account's real risk controls never permit to occur. A future
bounded-stop 3-2-2 variant that fits inside the existing risk architecture
would be a new strategy requiring its own research/evidence pass from
zero; this report does not propose or design one.

## Disposition

- **[PR #365](https://github.com/imanisworld/autonomous-futures-system/pull/365)**:
  HOLD / DO NOT MERGE. It was justified as the runtime/evidence-parity
  correction 3-2-2 needed — this study shows that even fully corrected, the
  strategy has zero executable historical population, so merging the
  signal/risk exemptions now would change paper-execution runtime behavior
  with no validated strategy benefit. Its `replay/replay_engine.py`
  `canonical_4hr_only` correction may still be independently valuable for
  future 5-minute-native research infrastructure; if needed later, split
  it into its own replay-only PR rather than reviving #365 as-is.
- **PR #359** (deployed): implementation stays in the repository and on
  the box as-is. Not altered by this research session — with the
  legitimate gates (`max_stop_ticks`, `min_confluence_grade`, TRENDING,
  etc.) intact and unmodified on `main`, it is already fail-closed. Its
  classification changes conceptually from "PROMISING BUT UNPROVEN /
  collecting forward evidence" to **BROKEN FOR CURRENT RISK ARCHITECTURE —
  paper wiring exists, but historical executable evidence = zero.**
- **Next research priority**: ORB Reclaim V4, per prior ranking.

## Reproduction

```bash
# Pass 1 — main baseline (run on origin/main)
python3 scripts/strat_322_parity_validation.py \
    --label main_baseline --out /tmp/strat_322_main_baseline.json

# Pass 2 — corrected (run on claude/paper-execution-parity-fixes / #365)
python3 scripts/strat_322_parity_validation.py \
    --label corrected --out /tmp/strat_322_corrected.json

# Pass 3 — hypothetical ceiling (run on claude/paper-execution-parity-fixes / #365)
python3 scripts/strat_322_parity_ceiling_pass.py --out /tmp/strat_322_ceiling.json
```

Corpus: `data/replay_corpus_v1_5m/MNQ` (5-minute-native, #338-corrected
fields, gitignored — regenerate via `scripts/polygon_to_replay.py
--timeframe 5`, requires `POLYGON_API_KEY`).
