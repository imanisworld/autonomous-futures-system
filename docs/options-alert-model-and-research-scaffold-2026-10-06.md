# Options alert model and setup-quality research scaffold (2026-10-06)

Both modules read from the canonical prospective signal (`options_evidence/signal.py`).
Neither one is wired to production. Neither one can grant authority.

## Alert / surfacing model: `options_evidence/alerts.py` (Workstream 11)

- **Kinds.** WATCHING, NEAR_TRIGGER, TRIGGERED, MISSED_GAP, MISSED_LATE,
  INVALIDATED, EXPIRED. Each one is projected 1:1 from the lifecycle state.
  OUTCOME_CLOSED is research bookkeeping, so it is never surfaced.
- **NEAR_TRIGGER.** This is the only derived kind. It applies when a signal is
  WATCHING and is within `near_trigger_r` risk units of its trigger.
  `near_trigger_r` is a **required** policy value and has no default.
  - When the price is through the trigger but the watcher has not resolved it,
    the alert stays NEAR_TRIGGER. A TRIGGERED alert comes only from the
    watcher's canonical state.
- **Authority.** Every alert record carries `trade_authority: false`. No alert
  carries an execution-authority field.
- **Noise control.** `AlertLedger` lets each (signal, kind) pair through once.
- **Delivery.** `DeliveryPolicy` is **off by default**. Delivery also needs an
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

The population contains only signals that meet all of these conditions:
- in a **registered** strategy epoch
- VALID data integrity and VALID signal integrity
- a known `result_R`

Legacy and unregistered rows are excluded and counted, as are rows with
degraded integrity or an unknown result.

### The #1089 rating

The #1089 rating is a *factor under test*. It is never an outcome label and
never a filter. #1089 telemetry is not proof of setup quality.

### Next research step

Testing whether any factor predicts outcome needs a preregistered study, under
the existing experiment-spec / trial-ledger chain, on a clean prospective epoch.
This scaffold does not start one. Today no registered epoch has resolved
outcomes, because `122-IEX-E1` has an UNRESOLVED stop and target.
