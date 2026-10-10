# Canonical prospective signal + outcome evidence (2026-10-06)

Code:
- `options_evidence/signal.py`
- `options_evidence/capture_adapter.py`
- `options_evidence/outcome.py`

Builds on `options_evidence/strategy_epochs.py`.

This is a schema, validation and read-only adapter. It has no provider, no file writer, no runtime wiring and no authority.

> **Integration pass (post-#1145 `ae8c897` / #1146 `445393f`).** The model
> follows the merged #1145 setup-capture observer exactly: levels and
> direction are attributes, not identity. The adapter consumes the #1145
> journal.

## Source of truth

#1145 (`alert_ranker/setup_capture*.py`, journal
`/root/afs-shared/logs/options_setup_capture.jsonl`) owns:
- detection
- WATCHING persistence across sessions
- dedupe
- late and gap classification
- SPX observation

The canonical layer **links** to those records. It does not re-detect or reclassify anything, and it does not copy the journal.

## Identity

| Id | Value | Meaning |
|---|---|---|
| `structure_id` | **exactly** the #1145 `structure_key`: `TICKER|timeframe|structure_close(UTC Z)|pattern` (parity test against `alert_ranker.setup_capture.structure_key`) | one market structure, shared by the watcher, scanner sightings and the canonical record |
| `signal_id` | `[structure_id, strategy, strategy_epoch]` | that structure judged under one strategy epoch; two strategies make two signals, never a merge |

Levels (`boundary_high` / `boundary_low` / `revision`) are attributes, as in #1145. A
`SOURCE_DRIFT` revision updates the same signal.

WATCHING is two-sided. Direction, trigger and invalidation exist only after the first break:
- LONG: trigger is `boundary_high`, invalidation is `boundary_low`
- SHORT: the reverse

## Lifecycle

```
WATCHING ─┬─> TRIGGERED ───┬─> OUTCOME_CLOSED
          │                └─> DATA_BLOCKED    (#1145 SIP reconciliation failed)
          ├─> MISSED_LATE ─┬─> OUTCOME_CLOSED  (counterfactual)
          │                └─> DATA_BLOCKED
          ├─> MISSED_GAP ──┬─> OUTCOME_CLOSED  (counterfactual; #1145 GAP_THROUGH_OPEN)
          │                └─> DATA_BLOCKED
          └─> INVALIDATED | EXPIRED | DATA_BLOCKED | AMBIGUOUS   (terminal)
```

Event types:
- `OPENED`
- `STATE`
- `LEVELS` (WATCHING only, revision must increase)
- `OBSERVATION` (capture evidence only, such as `capture_late`, `prospective_catch` or `sip_crossed_at`; never identity, state or authority)
- `LINK` (timeless metadata, allowed after terminal)
- `INTEGRITY` (integrity statuses only; any other key, including `execution_authority` / `authority_ref`, is refused)

The journal refuses all of the following:
- illegal transitions
- duplicate opens
- sequence gaps
- clock inversions
- resolution without direction or trigger time
- a `TRIGGERED` for a structure first seen, or only knowable (`setup_ready_time`), after its trigger (that must be `MISSED_LATE`)
- `MISSED_GAP` without gap evidence
- `OUTCOME_CLOSED` without `outcome_ref`
- chronology violations: structure close ≤ setup ready ≤ first seen; market events no later than the event that records them; trigger detection between the market trigger and its recording; captured cross times (`sip_crossed_at` / `trigger_crossed_at`) between the structure close and the event that records them
- wrongly typed values: levels, lags and prices must be finite non-bool numbers, capture flags exact bools, cross times timezone-aware ISO strings, integrity values exact `IntegrityStatus` names (no coercion of `"yes"`, `"false"`, `1`, `NaN`)
- provenance overwrites (see below)

### Provenance is append-only (review B4)

- `capture_late`, `gap_through` and `data_delayed`, once true, never become false.
- A `prospective_catch` that was true and is revoked never comes back. It can be set true only on a pre-armed `TRIGGERED` signal whose capture is neither late nor gapped.
- `sip_crossed_at` / `trigger_crossed_at` are write-once.
- Pre-arming is measured against the **earliest** recorded cross: the resolved trigger and any captured cross time. A later SIP reconciliation whose cross precedes first sight or setup-ready un-arms the signal, revokes an existing catch and demotes VALID; a catch claimed together with such a cross is refused. (#1145 reconciliation does not re-check pre-arming, so the adapter passes that catch on as `False`.)
- `signal_integrity` and `data_integrity` can only be demoted once known (`VALID → DEGRADED → INVALID`); they cannot be promoted or reset to `UNKNOWN`. `INVALID` is final.
- `MISSED_LATE` / `MISSED_GAP` set `signal_integrity` to `DEGRADED`; `DATA_BLOCKED` / `AMBIGUOUS` set both integrities to `INVALID`; late or revoked capture evidence demotes a `VALID` signal.
- `resolution` (TRIGGERED / MISSED_LATE / MISSED_GAP) is read from history, so it survives `OUTCOME_CLOSED`; records carry `resolution_state`.
- `signal_integrity == VALID` requires a registered epoch, a non-blocked state, and, after a TRIGGERED resolution, pre-arming plus `prospective_catch` evidence that is not late or gapped. This is checked on every construction, including direct `ProspectiveSignal(...)` and `dataclasses.replace`, as are the exact types of every capture-evidence value.
- `verify_record` derives resolution/lifecycle from append-only `history`, validates legal lifecycle ordering and monotonic detection times, and refuses a summary that disagrees. `prospective_catch=true` is valid only for a history-backed `TRIGGERED` resolution with pre-armed capture evidence, VALID signal integrity, and no late/gap provenance.

### Epochs (review B6)

`open_signal` validates `strategy_epoch` against the #1150 registry (default: the committed registry; `SignalJournal(registry)` and `fold_capture_rows(..., registry=...)` take another). The epoch must exist and be FROZEN or RETIRED, and the signal must fall inside the epoch's **declared scope**, matched exactly:

| Scope | Epoch declares | Signal must |
|---|---|---|
| timeframe | `definition.setup.timeframe` | equal it as written (`30M` ≠ `30m`, `1h` ≠ `1H`; no normalization) |
| setup family | `definition.setup.family` (`STRAT_a_b_c`) | have pattern family `abc:…` |
| universe | `definition.setup.universe`, a known name in `signal.EPOCH_UNIVERSES` (`PRIMARY_20`, drift-tested against the collectors) | have its ticker in that universe |
| data source | `definition.trigger.arm_source` | have `data_source` exactly `capture_id:level_source` (one `:`, both parts non-empty) with `level_source` equal to it |
| effective dates | `effective_from` / `effective_until` | have structure close and first-seen inside the window |
| trigger feed (for VALID) | `definition.trigger.provisional_source` / `authoritative_reconciliation` | have capture `trigger_source` equal to one of them |

Undeclared or unknown scope fails closed. Otherwise the signal opens as `UNREGISTERED_EPOCH`, keeping `requested_epoch` and `epoch_reason`, and can never be VALID. For example, a 1H `222` structure on 2026-09-01 is not `122-IEX-E1` (30m, `122` family, from 2026-09-21). Nor is a #1145 signal: its levels come from `public_regular_30m` and its triggers from `alpaca_iex` / `alpaca_sip`, while `122-IEX-E1` declares `public_regular_session_chart` and `alpaca_iex_trades` / `alpaca_sip_trades_delayed`. Mapping one vocabulary to the other would need an explicit registry entry; none is inferred. A journal refuses an `OPENED` event whose claimed epoch is not its registry entry. Records carry `epoch_definition_sha256`, and `verify_record(record, registry=...)` checks it and the scope for every record with a registered label, VALID or not.

## Watcher → canonical mapping (`capture_adapter.fold_capture_rows`)

| #1145 row | canonical | preserved |
|---|---|---|
| first `WATCHING` | `OPENED` (two-sided `Levels`) | `structure_key`, timeframe, structure close, `knowable_at` → setup-ready, `first_seen_at`, `level_source`/`capture_id` → data source |
| re-persisted `WATCHING` | nothing, or `LEVELS` if the revision increased | dedupe |
| `SOURCE_DRIFT` | `LEVELS` | same signal |
| `RESOLUTION TRIGGERED` | `STATE TRIGGERED` + `OBSERVATION` + `INTEGRITY` | direction; market time = SIP cross, else first cross (the #1145 "true cross"); `detected_at`; feed/source/resolution; lags; `capture_late`; `prospective_catch` |
| `RESOLUTION MISSED_LATE` | `STATE MISSED_LATE` | reason (e.g. `iex_no_cross_sip_cross`), unchanged |
| `RESOLUTION GAP_THROUGH_OPEN` | `STATE MISSED_GAP` | `gap_through`, `first_print_price` |
| `EXPIRED` / `NO_TRIGGER` | `STATE EXPIRED` | `NO_TRIGGER` kept in the reason |
| `INVALIDATED` / `DATA_BLOCKED` / `AMBIGUOUS` | same-named state | — |
| `RECONCILIATION` | `OBSERVATION` + `INTEGRITY`, or `STATE DATA_BLOCKED` when #1145 demoted the row | SIP lag, `capture_late`, `prospective_catch` |
| `COLLECTOR_*`, `SOURCE_BLOCKED`, `JOURNAL_REPAIR` | ignored (also diagnostic-only in #1145) | — |

Integrity derivation:
- `signal_integrity` is VALID **iff** #1145 `is_prospective_catch` holds for a TRIGGERED row **and** the fold's epoch is registered and matches the structure's context. A test checks equality with `catch_count` over the in-context structures.
- Under the default (unregistered) epoch it is capped at DEGRADED.
- It is UNKNOWN while a TRIGGERED row is pending SIP.
- It is DEGRADED for late, gap or missed captures.
- It is INVALID for DATA_BLOCKED and AMBIGUOUS.
- `data_integrity` is DEGRADED when `data_delayed` (SPX), and INVALID when the row is blocked or ambiguous.

These guarantees are tested against real #1145 engine output (#1145's own Oct 5 fixtures):
- one #1145 key maps to exactly one canonical signal
- repeated scanner sightings (`shadow:9925` / `9933`) only extend `scanner_sighting_ids`, and an unmatched sighting never creates a signal
- late, gap and expired classifications survive the mapping unchanged
- a row that claims authority, or is not observation-only, is refused (exact booleans: `"false"` is a claim)
- missing trigger evidence becomes `DATA_BLOCKED` with no synthesized time
- the #1145 journal is never written, and a torn tail is skipped, not repaired

The adapter's default `strategy_epoch` is the collector version (`capture-v0.2`). That is not a registered strategy epoch, so these signals open as `UNREGISTERED_EPOCH` (with `requested_epoch="capture-v0.2"`) and research and fitness exclude them until an operator registers a real one.

## Authority (review B1)

- A canonical signal is structurally observation-only: `observation_only is True` and `execution_authority is False` on every construction, including direct constructors and `dataclasses.replace`. `execution_integrity` is always `NOT_APPLICABLE`.
- No event can grant authority. `open_signal` refuses anything but `observation_only=True`; `INTEGRITY` refuses authority keys whatever their value.
- Execution authority is a separate, human-approved record outside this layer.
- The adapter only produces observation-only signals.

## Outcome evidence (Workstream 8)

There is no change in intent. `OutcomeEvidence` now requires a resolved signal, because a two-sided WATCHING structure has no risk unit. R and MAE/MFE come from the resolved trigger and invalidation. Every value is OBSERVED, DERIVED, UNAVAILABLE or NOT_APPLICABLE, and UNAVAILABLE and NOT_APPLICABLE need a reason. Premium paths are observed quotes only.

Review hardening:
- **B3:** every field is type-checked on construction. Prices, R and P&L must be finite non-bool numbers, hit times timezone-aware datetimes, and `executed` an exact bool.
- **B2:** only a prospective catch (`ProspectiveSignal.is_prospective_catch`) can be `executed=True`.
- **B2:** anything that is not a verified prospective catch (a miss, including after `OUTCOME_CLOSED`; a late, gapped or SIP-pending `TRIGGERED` capture; an unregistered or out-of-scope epoch) must use `pnl_basis="counterfactual"`, with no P&L and no OBSERVED R. Hypothetical DERIVED analytics may stay on the record. `result_r_value` returns a value only for `prospective_catch is True` with an `executed` / `paper_equivalent` basis, so fitness and research never score a non-catch as a trade result.
- An outcome cannot report VALID signal integrity for a non-VALID signal, nor launder INVALID data integrity.
- `validate_outcome(..., registry=...)` also requires the signal's epoch to be that registry's entry, so a hand-built `StrategyEpoch` cannot certify a catch.
- The adapter takes level revisions as exact non-negative ints (no `int()` coercion of `"3"`, `2.9` or `True`).

## Not done here

- No persistence of canonical events. A runtime consumer would replay `fold_capture_rows` over the #1145 journal, which is cheap and deterministic.
- No wiring of scanner sighting rows. Scanner rows carry setup types such as `H1_222_CONTINUATION`, not #1145 pattern tokens, so the caller must supply the pattern key, as #1145's `link_scanner_first_sight` does.
