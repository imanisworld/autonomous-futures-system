# Options alert model and setup-quality research scaffold (2026-10-06)

Both modules read from the canonical prospective signal (`options_evidence/signal.py`).
Neither one is wired to production. Neither one can grant authority.

> **Integration pass (post-#1145 / #1146).** The canonical signal now mirrors
> the #1145 setup-capture model: WATCHING is two-sided and has no direction
> until the first break. The alerts and research modules were updated to match.

## Alert / surfacing model: `options_evidence/alerts.py` (Workstream 11)

- **Kinds.** WATCHING, NEAR_TRIGGER, TRIGGERED, MISSED_GAP, MISSED_LATE,
  INVALIDATED, EXPIRED, DATA_BLOCKED, AMBIGUOUS. Each one is projected 1:1
  from the lifecycle state. For a #1145 capture, that state comes through
  `capture_adapter`.
  OUTCOME_CLOSED is research bookkeeping, so it is never surfaced.
- **NEAR_TRIGGER.** This is the only derived kind. It applies when a signal is
  WATCHING and is within `near_trigger_r` of the **nearer** boundary.
  - The distance is measured in units of the setup range (`boundary_high - boundary_low`).
  - The alert reports `near_side` HIGH or LOW. It never reports a direction:
    `direction`, `trigger` and `invalidation` stay null until the watcher
    resolves the break.
  - `near_trigger_r` is a **required** policy value and has no default.
  - When the price is through the trigger but the watcher has not resolved it,
    the alert stays NEAR_TRIGGER. A TRIGGERED alert comes only from the
    watcher's canonical state.
- **Authority.** Every alert record carries `trade_authority: false`. No alert
  carries an execution-authority field.
- **Evidence state is explicit.** Alerts also carry `data_integrity`,
  `signal_integrity`, and `prospective_catch`. A canonical TRIGGERED
  lifecycle notice is not presented as a validated trade merely because its
  state says TRIGGERED; degraded/pending/non-catch state stays visible.
- **Exact alert inputs.** `last_price` and `near_trigger_r` reject bool,
  malformed, non-finite, and non-positive values rather than coercing them.
- **Noise control.** `AlertLedger` lets each (signal, kind) pair through once.
- **Delivery.** `DeliveryPolicy` is **off by default**. `enabled` must be an
  exact boolean; strings such as `"false"` are refused. Delivery also needs an
  explicit channel and an explicit set of kinds. This PR does not wire Discord
  or any other sender.

## Setup-quality research scaffold: `options_evidence/research_features.py` (Workstream 9)

There is no scoring model: the module defines no score, weight, rank, or grade,
and a test pins that.

### Candidate factors

Each signal row must state every one of these factors:
- setup family
- timeframe continuity
- HTF alignment
- SPY/QQQ regime
- GEX regime
- flip relationship
- Signa
- level quality
- distance to resistance/support
- gap context
- trigger geometry
- contract quality
- the #1089 observational rating

A factor is either OBSERVED, with a value, `as_of` and source, or UNAVAILABLE,
with a reason. Values are never imputed.

### Look-ahead guard

A factor observed after the signal's trigger detection time is refused. For a
signal that has not triggered yet, the cutoff is its first-seen time.

### Research population

The population contains only rows that meet all of these conditions:
- the canonical signal record passes #1151 `verify_record(..., registry=...)`,
  including exact epoch definition/scope/source/provenance checks
- the signal is a canonical `prospective_catch` **with a TRIGGERED resolution**
  (`resolution_state`, which `verify_record` ties to history); a catch flag on a
  signal that never triggered is excluded as `not_prospective_catch`
- the signal appears exactly once in the input: every triple of a `signal_id`
  that repeats (a replay, or conflicting outcomes) is excluded as
  `duplicate_signal`, so one catch never counts twice
- the outcome identity/resolution/catch fields agree with the signal
- the outcome uses an `executed` or `paper_equivalent` trade basis with an
  exact boolean `executed` field; counterfactual outcomes are excluded
- VALID data integrity and VALID signal integrity on **both** the canonical
  signal record and the outcome record
- executed outcomes also require VALID execution integrity
- the persisted feature row independently passes its schema, exact key set
  (no extra keys such as a score), identity, decision-cutoff,
  factor-completeness, and no-look-ahead checks
- a known finite `result_R`

The result never selects rows: winners, losers and scratches all stay in, and
a test pins this.

Legacy and unregistered rows are excluded and counted, as are rows with
degraded integrity, forged/mismatched scope, malformed outcome provenance,
invalid persisted feature rows, or an unknown result. Persisting a feature row
does not bypass the original `build_feature_row` look-ahead boundary.

### The #1089 rating

The #1089 rating is a *factor under test*. It is never an outcome label and
never a filter. #1089 telemetry is not proof of setup quality.

### Next research step

Testing whether any factor predicts outcome needs a preregistered study, under
the existing experiment-spec / trial-ledger chain, on a clean prospective epoch.
This scaffold does not start one. Today no registered epoch has resolved
outcomes, because `122-IEX-E1` has an UNRESOLVED stop and target.
