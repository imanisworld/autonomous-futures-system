# Whole-System Adversarial Audit Packet

**Audit date:** 2026-07-24
**Source baseline verified:** `001f2b936ece272fb25f96d1a9b405cd05ad7ed3` (`main`)
**Audit mode:** read-only source verification and consolidation; no runtime, strategy,
risk, configuration, Pine, replay, execution, journal, deployment, corpus, or generated
evidence changes.

## Final verdict

**NOT READY FOR UNQUALIFIED WHOLE-SYSTEM EVIDENCE RELIANCE.**

The audit found three blockers:

1. a current cross-instrument state leak in the active ORB continuation gate;
2. a live-only regime input that replay does not reconstruct; and
3. checked-in replay corpora that retain an obsolete, direction-losing Strat
   representation while a current strategy consumer still interprets the ambiguous
   value as specifically bearish.

The production posture remains fail-closed in several important places, and the
account-wide risk counters were confirmed to be account-wide by design. The result is
not “the system is generally unsafe.” It is narrower: multi-instrument state isolation
and live/replay evidence equivalence are not yet strong enough to treat all existing
historical and forward results as measuring the current decision semantics.

## Provenance and consolidation boundary

This packet consolidates the five completed Claude reports from the preceding session:

1. `DailyState` field scoping;
2. journal reconstruction and instrument filtering;
3. ORB live/replay derivation parity;
4. 4HR and Strat 2-1-2/1-2-2 persisted detector-state scoping; and
5. directional Strat bar-type representation in Polygon/CSV replay.

It also carries forward the two high-severity claims independently verified in the
parent session before those reports were launched: ORB played-state contamination and
the live-only `window_direction` input. Three broader agents stopped at Claude's
session limit (session-scoped state, non-`DailyState` mutable state, and the full
field-by-field live/replay sweep). Their partial notes are **not** treated as findings;
those domains appear under unresolved surfaces.

No audit agent was rerun. No new corpus or replay result was generated.

## Counts

| Severity | Count |
|---|---:|
| BLOCKER | 3 |
| MATERIAL | 6 |
| MINOR | 4 |

“NOT A DEFECT” determinations are included in the MINOR count because they close
explicit audit hypotheses and prevent them from being reopened as defects without new
evidence.

---

# BLOCKERS

## B-01 — ORB continuation “played” state leaks across instruments

**Classification:** CONFIRMED DEFECT

### Source proof

- `risk/risk_engine.py:65-75`, `DailyState`: the ORB flags are single booleans on a
  date-scoped object and carry no instrument key.
- `journal/journal_logger.py:520-552`, `_compute_daily_state` /
  `_apply_orb_break_state`: the full day's shared journal is replayed into those
  booleans without reading `entry["instrument"]`.
- `strategy/signal_engine.py:775-789`, `DecisionEngine.evaluate`: any approved trade
  while its instrument is above/below its own ORB mutates the shared boolean.
- `strategy/signal_engine.py:1639-1669`, `_iter_enabled_setups`: the boolean is then
  applied to the current instrument's ORB status and skips every non-exempt strategy.

### Concrete reproduction

1. MNQ receives an approved trade while `MNQ.state.orb.status == "above"`.
2. `daily_state.orb_break_long_played` becomes `True`.
3. Before MNQ resets that state, an MES bar arrives with
   `MES.state.orb.status == "above"`.
4. `_iter_enabled_setups` sets `orb_continuation_blocked=True` for MES even though
   MES has not played its own ORB break.
5. Every strategy outside `_ORB_CONTINUATION_EXEMPT` is skipped for that MES bar.

The inverse MES-to-MNQ path is identical. A reclaim/rejection on one instrument can
also clear the other instrument's state.

### Affected evidence/results

- **Affected:** multi-instrument live/demo journals and forward-proof interpretation
  whenever MNQ and MES decisions interleave; any mixed-instrument replay using one
  `DailyState`; “why no trade” and opportunity-denominator conclusions derived from
  those rows.
- **Potentially affected:** current-week MES/MNQ comparisons and any evidence lane
  that counted suppressed candidates from a shared journal.
- **Not shown affected by this finding:** single-instrument Polygon replays run with
  a fresh `DailyState` per instrument.

### Shared root cause

Instrument-specific market-structure lifecycle state was placed on an account/day
state object without an explicit scope dimension.

---

## B-02 — `window_direction` changes live regime decisions but is absent in replay

**Classification:** CONFIRMED DEFECT

### Source proof

- `context/market_context.py:240-244`, `MarketState.window_direction`: the field is
  explicitly documented as live-only and defaults to `None` elsewhere.
- `webhook/runner.py:650-662`: live ingestion records the bar and populates
  `window_direction` from six recent instrument-scoped bars.
- `strategy/signal_engine.py:1268-1290`, `_has_directional_structure`: the field is
  decision-affecting when it agrees with EMA trend.
- `strategy/signal_engine.py:1292-1317`, `_score_market_condition`: it can convert a
  Pine `CHOPPY` label to `RANGE_BOUND`.
- `replay/replay_engine.py`: there is no `window_direction` assignment; replay leaves
  the dataclass default in place.

### Concrete reproduction

Use a bar whose supplied market condition is `CHOPPY`, whose three-bar Strat sequence
is not a full directional run, but whose last six closes produce `window_direction ==
trend.direction`. Live returns `RANGE_BOUND`; replay over the same bars returns
`CHOPPY`. Because `CHOPPY` is non-tradable, candidate admission differs.

### Affected evidence/results

- **Affected:** all replay results used to validate the current CHOPPY veto or regime
  routing on bars where the six-bar signal, rather than the strict three-bar Strat
  run, is decisive.
- **Affected but unquantified:** range-signal proof, current-week regression, regime
  routing studies, missed-opportunity/gate sweeps, and reconstructed-market-condition
  comparisons that assume replay exercised the live veto.
- **Not affected:** bars already labeled `TRENDING`, `RANGE_BOUND`, or `DEAD`, and
  CHOPPY bars where the strict Strat-run branch independently reaches the same result.

### Shared root cause

A decision-authoritative feature was added after state construction rather than made a
required, identically-derived input of both pipelines.

---

## B-03 — Stored replay bar types remain ambiguous and `vwap_hold` treats ambiguity as bearish

**Classification:** CONFIRMED DEFECT

### Current source and artifact proof

- The Polygon converter itself was fixed in merged commit
  `13983175c6a445eae70b9ccedade7fe344f86724`; current
  `scripts/polygon_to_replay.py:271-287` emits directional `1/2U/2D/3`.
- The checked-in Polygon corpus was not regenerated. For example,
  `data/replay_polygon/MES/MES_2026-03-26.jsonl:1-12` still contains bare
  `"current_bar_type": "2"` values.
- `scripts/csv_to_replay.py:90-97`, `bar_type_str`, also emits bare `"2"` from the
  three undirected Pine label columns.
- `strategy/signal_engine.py:2073-2090`, `_try_vwap_hold`, documents that the bar
  must be `two_down` but accepts `"2"` as equivalent to `two_down`.
- The same bar-type fields feed `_strat_run_direction` and the 2-1-2/1-2-2 arming
  path (`strategy/signal_engine.py:1249-1266`, `2501-2535`).

### Concrete reproduction

Take a genuine `two_up` bar in the old Polygon corpus. Its stored token is `"2"`.
Present it below VWAP with trend `DOWN`. `_try_vwap_hold` accepts the token as satisfying
the required `two_down` confirmation, so a bullish directional bar can qualify a short.
Conversely, strict canonical comparisons cannot distinguish 2U from 2D and may fail to
arm or recognize directional runs at all.

### Affected evidence/results

Treat as directionally unreliable any result generated from the existing undirected
Polygon/CSV rows where the decision depended on the bar-type direction, including:

- `vwap_hold` counts and outcomes in
  `docs/ioc-faithful-baseline-622d-2026-07-06.md`,
  `docs/mes-mnq-mechanical-research-2026-07-09.md`,
  `docs/strategy-matrix-tranche1-2026-07-14.md`,
  `docs/vwap-hold-isolated-fill-model-comparison-2026-07-23.md`, and their JSON
  result artifacts;
- CHOPPY-to-RANGE_BOUND directional-run conclusions from the old corpus; and
- any Strat 2-1-2/1-2-2 evidence produced from those stored tokens.

This does **not** mean every reported trade or aggregate is wrong. It means the
membership of the evaluated population is not trustworthy where bar direction was a
gate. Results that did not consume these fields are outside this finding.

### Shared root cause

Generated datasets have no enforced semantic-version/lineage gate tying them to the
converter version, and a consumer attempted backward compatibility by assigning a
specific meaning to an ambiguous legacy token.

---

# MATERIAL

## M-01 — Persisted 4HR and Strat 2-1-2/1-2-2 detector states are date-global

**Classification:** CONFIRMED DEFECT

### Source proof

- `risk/risk_engine.py:85-93`: both detector dicts live on the single `DailyState`.
- `journal/journal_logger.py:520-527`: reconstruction is last-writer-wins across the
  full day's journal, without an instrument filter.
- `strategy/four_hr_retrigger.py:150-158,232-247`: the 4HR function receives an
  instrument but the persisted dict contains no instrument, and validity checks only
  `trading_date`.
- `strategy/strat_212_122.py:110-145,193-203`: the Strat advance function receives no
  instrument at all; the armed dict contains price levels and date but no instrument.
- `strategy/signal_engine.py:2483-2535`: each incoming instrument reads and overwrites
  those same dicts.

### Concrete reproduction

MES arms a 4HR state near the MES price scale; MNQ arrives next and evaluates MNQ bars
against the MES trigger/target. For 2-1-2/1-2-2, MNQ arms; the next MES bar compares MES
OHLC with MNQ entry/stop/target and normally spends or corrupts the one-bar watch state
before MNQ's next bar arrives.

### Affected evidence/results

- **Affected:** any mixed-instrument replay with `allow_mixed_instruments=True`.
- **Future-live blocker:** enabling `strat_4hr_retrigger`, `strat_212`, or `strat_122`
  in the shared live pipeline without changing state scope.
- **Not shown affected:** currently deployed execution, because these executable
  concepts are excluded from `enabled_concepts`; single-instrument isolated detector
  replays are also outside the demonstrated collision.
- **Historical interpretation:** existing isolated 4HR and Strat studies are not
  invalidated solely by this issue, but they do not prove restart/interleaving safety.

---

## M-02 — CSV replay cannot exercise London ORB behavior

**Classification:** EVIDENCE BLIND SPOT

`scripts/csv_to_replay.py:376-377,539-583` reads and emits one generic ORB and no
`london_orb_*` fields. `replay/replay_engine.py:707-716` consequently forces London
CSV bars to `orb.status="undefined"`. Live builds London ORB from Pine
(`webhook/state_builder.py:301-318`), and Polygon replay derives it independently
(`scripts/polygon_to_replay.py:226-245`).

**Reproduction:** convert a CSV containing London-session bars, then build replay
state. Because `candle.london_orb_high is None`, the fail-closed branch executes and
ORB-consuming strategies cannot fire.

**Affected evidence/results:** CSV-derived London-session validation for
`orb_breakout`, `orb_reclaim`, `orb_rejection`, and any ORB confluence component.
Polygon-derived London ORB studies are not affected by this specific omission.

---

## M-03 — Live ORB fallback cannot derive reclaim/rejection states

**Classification:** PROBABLE DEFECT

`webhook/state_builder.py:146-160`, `derive_orb_status`, returns only
`above/below/inside/undefined`, while Pine and replay use previous close to produce
reclaim/rejection states (`tradingview/risksentinel_context.pine:82-98`;
`scripts/csv_to_replay.py:100-122`). Live normally trusts Pine's supplied status
(`webhook/state_builder.py:306-322`), so the defect is latent.

**Reproduction:** provide valid ORB high/low but omit `orb_status` on a bar that moves
from above the ORB to inside. Replay returns `rejected_high`; live fallback returns
`inside`, suppressing rejection logic.

**Affected evidence/results:** no known canonical Pine row, because the current Pine
template sends status. Alternate/legacy/malformed webhook paths are unproven, making
this a probable rather than confirmed production defect.

---

## M-04 — Live ORB duration is configurable but Polygon replay is fixed at 15 minutes

**Classification:** DESIGN AMBIGUITY

Pine exposes `i_orb_min` from 5 to 60 minutes
(`tradingview/risksentinel_context.pine:13`); Polygon replay hard-codes
`ORB_MINUTES=15` (`scripts/polygon_to_replay.py:63-66`).

**Reproduction:** set the TradingView chart to a non-15-minute ORB and replay the same
day with Polygon conversion. The ORB high/low can differ, changing status and every
ORB-anchored setup.

**Affected evidence/results:** all Polygon ORB studies **if** the deployed chart was
not at its default. Current chart input was not available in the completed evidence,
so no historical result is affirmatively invalidated here.

---

## M-05 — Missing exact open-time bar can carry the prior day's Polygon ORB

**Classification:** PROBABLE DEFECT

Polygon replay resets only on an exact 09:30 ET or 03:00 ET bar
(`scripts/polygon_to_replay.py:213-238`); Pine resets on the first in-session
transition (`tradingview/risksentinel_context.pine:235-242`). If the exact open bar is
missing, the converter has no reset branch and can retain the previous range.

**Reproduction:** remove the 09:30 bar but retain 09:45. Pine initializes on its
session transition; Polygon conversion does not enter `is_ny_open_bar` and retains the
old ORB variables.

**Affected evidence/results:** only dates with an exact open-bar gap. The completed
audit did not enumerate such dates, so the affected historical subset remains unknown.

---

## M-06 — Cross-instrument webhook serialization is assumed, not proven

**Classification:** EVIDENCE BLIND SPOT

Journal file appends are locked, but the completed journal audit did not establish an
atomic lock around the whole sequence “reconstruct `DailyState` → pass open-position
gate → submit/record order.” The single-position architecture depends on that
serialization. `ops/block_visibility.py:1-15` documents the single-position gate, and
`journal/journal_logger.py:340-533` shows reconstruction and mutation are separate
operations.

**Reproduction scenario requiring verification:** MNQ and MES webhooks begin close
enough that both reconstruct `has_open_position=False` before either records its
approved trade. Whether broker/preflight layers serialize or reject the second request
under all paper/demo/restart modes was not completed.

**Affected evidence/results:** no historical incident is attributed to this finding.
If the assumption fails, the single-slot journal reconstruction can hide one of two
positions, contaminating position lifecycle, reconciliation, and all subsequent daily
state. This is the highest-risk unverified assumption remaining.

---

# MINOR

## N-01 — Reconcile freshness is account-wide in an instrument-specific diagnostic

**Classification:** CONFIRMED DEFECT

`journal/journal_logger.py:221-237`, `last_reconcile_ts`, returns the latest
`session=="reconcile"` outcome with no instrument filter. It is used for block
visibility rather than order gating.

**Reproduction:** MES reconciles recently; MNQ remains stale. An MNQ block record can
display the MES timestamp as its latest reconcile.

**Affected evidence/results:** health/block-visibility timelines and orphan-diagnostic
interpretation; not trade admission or P&L.

---

## N-02 — `ORBData.timeframe_minutes=15` is hard-coded but currently inert

**Classification:** NOT A DEFECT

Live and replay both construct `ORBData` with `timeframe_minutes=15`
(`webhook/state_builder.py:371-376`; `replay/replay_engine.py:741-746`). The completed
ORB audit found no decision consumer of this field. It is misleading metadata if the
chart uses another duration, but no current behavior or historical result depends on
it.

---

## N-03 — `get_open_position()` is account-wide by design

**Classification:** NOT A DEFECT

`journal/journal_logger.py:554-630` maintains one open-position slot and attaches the
instrument to the returned record. The completed journal audit verified that
execution-affecting consumers compare the record's instrument with the current bar
before resolution. `ops/block_visibility.py:1-15` explicitly documents the
single-position account-wide gate.

**Closed hypothesis:** the lack of an instrument parameter is not itself a
wrong-instrument execution defect. The separate atomicity question is M-06.

**Affected evidence/results:** none from the account-wide design alone.

---

## N-04 — Account/day risk counters correctly aggregate MNQ and MES

**Classification:** NOT A DEFECT

The completed `DailyState` audit traced `trade_count`, `consecutive_losses`,
`has_open_position`, balances, realized P&L, `last_loss_at`, `consecutive_wins`,
session counts, and session P&L/time fields. Their consumers use single account-wide
limits, and the broker architecture exposes one position slot. Mixing instruments for
those fields matches the declared account/day scope. They must not be “fixed” by
adding per-instrument filtering without a separate risk-policy decision.

**Affected evidence/results:** none; this closes a false-positive class.

---

# Shared root causes and deduplication

| Root cause | Consolidated findings | Why these are one family |
|---|---|---|
| Scope is implicit rather than encoded in state keys | B-01, M-01, N-01, M-06 | Date/account objects carry instrument-specific lifecycle or diagnostic state; correctness depends on call order and convention. |
| Live/replay authority is duplicated | B-02, M-02, M-03, M-04, M-05 | Equivalent semantic fields are populated by different formulas, defaults, or fallback vocabularies. |
| Evidence artifacts lack enforced semantic lineage | B-03 | Source was corrected, but checked-in corpora still encode old semantics and consumers accept ambiguous legacy values. |
| Fail-closed behavior hides coverage loss | M-02, M-03 | Suppression is safer than false execution but can make replay appear comprehensive while a strategy/session was never exercised. |

B-01 is not duplicated with M-01: both arise from missing instrument scope, but B-01
affects currently enabled strategy selection, while M-01 concerns dormant persisted
detectors and mixed-instrument replay.

## Historical evidence impact matrix

| Evidence/result family | B-01 | B-02 | B-03 | M-01 | M-02 | Other material |
|---|---|---|---|---|---|---|
| Single-instrument Polygon strategy replays | No demonstrated impact | Conditional CHOPPY subset | **Affected where bar direction gates** | No demonstrated impact | No (Polygon has London ORB) | M-04/M-05 conditional |
| Mixed-instrument replay | **Affected** | Conditional | **Affected** | **Affected** | Backend-dependent | M-04/M-05 conditional |
| Live/demo multi-instrument journals | **Affected** | Live is authoritative path | Corpus issue does not apply | Dormant while strategies disabled | No | M-03/M-06 conditional |
| CSV-based London replay | State-run dependent | Conditional | **Affected where bar direction gates** | Run dependent | **Structurally uncovered** | M-03 not applicable |
| 4HR / 2-1-2 / 1-2-2 isolated studies | No demonstrated impact | Conditional if DecisionEngine regime gate used | **Affected if old directional corpus used** | Interleaving/restart not proven | Backend-dependent | Promotion remains blocked |
| Operational block/reconcile reports | **May affect candidate-denominator context** | No | No | No | No | N-01 affects freshness display |

“Affected” means the issue can change population membership, state, or decision outcome.
It does not assert that every aggregate value changed or prescribe regeneration in
this audit.

# Coverage map

| Surface | Coverage | Result |
|---|---|---|
| All 16 `DailyState` fields | Complete report + blocker verification | Account-wide counters correct; ORB and detector lifecycle state mis-scoped |
| Journal daily-state reconstruction | Complete report | Instrument-specific state uses unfiltered last-writer-wins paths |
| Open-position reconstruction | Complete report | Single-slot design confirmed; no wrong-instrument resolution found |
| ORB live/Pine/Polygon/CSV level and status | Complete report | Live/Polygon core intent aligned; CSV London blind spot and fallback/config edge risks |
| 4HR and Strat persisted detectors | Complete report | No instrument identity in persisted state |
| Directional Strat bar representation | Complete report + current artifact check | Converter fixed; checked-in corpus and CSV representation remain ambiguous |
| CHOPPY/window regime parity | High-severity claim verified | Live-only decision input confirmed |
| Account/session risk counter scope | Complete report | Correctly account/session scoped |
| Reconcile freshness metadata | Complete report | Cross-instrument diagnostic timestamp confirmed |
| Session-scoped non-`DailyState` state | Partial only | Unresolved; agent stopped before final report |
| Other module/class mutable cross-bar state | Partial only | Stateless VWAP/GEX/range observations noted, but sweep did not complete |
| Every `MarketState` field live vs replay | Partial only | `window_direction` confirmed; full matrix incomplete |
| Concurrent request lifecycle / atomicity | Source concern only | Unverified |
| Deployment/runtime environment and chart inputs | Not audited in this packet | Unresolved by design |
| Broker/exchange concurrency and recovery semantics | Not audited in this packet | Unresolved by design |

# Unresolved surfaces

1. Full live-vs-replay population matrix for every `MarketState` and nested field,
   including structural regime, location context, GEX, Signa, HTF, supply/demand,
   key levels, and skipped-bar behavior.
2. Complete sweep of mutable module/class/broker caches outside `DailyState`,
   especially process-lifetime and restart behavior.
3. Session rollover and DST/holiday behavior for all session-keyed counters and
   histories.
4. Atomicity of concurrent MNQ/MES webhook evaluations through preflight, broker
   submission, journal write, and reconciliation.
5. Actual TradingView chart input values, especially `i_orb_min`, and whether all
   deployed alert variants send the full ORB status vocabulary.
6. Inventory of dates with missing exact 03:00/09:30 ET Polygon bars.
7. Artifact lineage: which committed reports were produced before versus after
   converter, VWAP reset, directional-bar, London-ORB, and regime reconstruction
   changes.
8. Broker-native fill/order truth for historical rows without order IDs; this packet
   did not reopen the known irreversible evidence gaps.

# Confidence assessment

## Confidence that major state/semantic blind spots have now been found

**Moderate-high (approximately 80%) for the audited core:** date/account state,
journal reconstruction, ORB authority, persisted detector state, and the known
direction/regime parity paths were inspected deeply and produced convergent root
causes.

**Moderate (approximately 65%) for the whole system:** three broad sweeps did not
complete, and runtime concurrency/deployment inputs were outside the available
evidence. The audit is strong enough to identify the current promotion/evidence
blockers, but not to claim exhaustive absence of other lifecycle or parity defects.

## Defect classes still insufficiently audited

- concurrent request races and transaction boundaries;
- process restart/recovery across every stateful subsystem;
- DST, holiday, missing-bar, and session-boundary transitions;
- broker/API partial failure, retry, duplication, and out-of-order response behavior;
- all-field live/replay default and authority differences;
- stale generated artifact detection and provenance enforcement; and
- deployed chart/environment drift from repository defaults.

## Single highest-risk unverified assumption remaining

**That MNQ and MES webhook evaluations are effectively serialized across the entire
read-state → decide → submit → journal lifecycle.**

The system's single-position and date-scoped-state design is safe only if two
instrument requests cannot both act on the same pre-trade snapshot. File-append locks
do not by themselves prove that invariant. A failure would combine the most dangerous
audited characteristics: last-writer-wins reconstruction, one visible open-position
slot, and cross-instrument lifecycle state.

# Final disposition

- Do not use current multi-instrument forward journals as unqualified proof of
  per-instrument strategy opportunity or suppression.
- Do not treat old Polygon/CSV direction-gated aggregates as equivalent to results
  from the fixed converter/current semantics.
- Do not promote the executable 4HR or Strat 2-1-2/1-2-2 detectors based only on
  isolated results; their shared persisted-state scope is not promotion-safe.
- This packet authorizes no fix, regeneration, activation, merge, or deployment.
