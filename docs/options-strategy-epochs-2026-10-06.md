# Options strategy epochs (2026-10-06)

Registry: `config/options_strategy_epochs.json`. Code: `options_evidence/strategy_epochs.py`.
Research/authority layer only. It runs nothing, collects nothing, and grants no
execution authority.

## What an epoch is

`strategy` + `epoch` (for example `322` / `2026Q4_v1`) names one exact
definition: the material sections `setup`, `trigger`, `target`, `filters`,
`authority`, plus `thresholds`. `definition_sha256` covers those and nothing
else. Provenance (dates, commit, preregistration doc, notes) is outside the
hash.

Any material change → different hash → **new epoch**, with its own
preregistration. Results from different epochs are never pooled.

## Enforced invariants

| Invariant | Mechanism |
|---|---|
| No silent in-place edit of a definition | stored hash must equal recomputed hash at load |
| No silent edit of definition *and* hash together | `FROZEN_PINS` in `tests/test_options_strategy_epochs.py` |
| No relabel (same definition, new name) | one hash per strategy |
| No overlapping epochs of one strategy | earlier epoch must set `effective_until` ≤ later `effective_from` |
| Preregistration metadata | FROZEN/RETIRED need `effective_from`, 40-hex `source_commit`, `preregistration_doc` |
| Expected OOS reference | optional `oos_reference`: source, artifact path, artifact sha256, raw per-trade `r_outcomes`, cost model |
| Registry cannot grant trading | `definition.authority` is exactly `observation_only: true`, `execution_authority: false`, `risk_reservation: false`, `trade_alerts: false` (exact JSON booleans, all four stated, no other key) |
| Unknown keys refused, not ignored | allowlists at registry (`schema`, `notes`, `epochs`), epoch, `oos_reference` and `authority` level; `tradable`, `active`, `trading_authority`, a top-level `observation_only` etc. are refused; duplicate JSON object keys are refused |
| No coercion | identifiers and SHAs must be `str` and match in full (a trailing newline fails); text fields are non-blank without surrounding whitespace; `r_outcomes` is a non-empty JSON list of finite non-bool numbers (strings/bools refused); thresholds are finite non-bool numbers; `NaN`/`Infinity` refused |
| One error type | every malformed input (unrepresentable dates, overflow, absurd nesting, wrong types) raises `RegistryError`, never an internal exception |
| Authority holds however an epoch is created | `assert_observation_only` runs in `StrategyEpoch.__post_init__`, `validate_epochs` and `EpochRegistry.__post_init__`: `observation_only is True`, `status` is an `EpochStatus`, and `definition.authority` passes the same exact-four-boolean check as the loader. A directly constructed epoch (as fitness/readiness code and tests do) cannot opt out; consumers may call it again at use time |
| Loaded settings are immutable | `definition`/`thresholds` are deep-frozen (read-only mappings, tuples) on construction; freezing does not change the hash |
| New signals stamped only by exact match | `resolve_epoch`: running definition must be exactly the material sections and hash-match a FROZEN epoch covering the timestamp; anything else, including malformed/non-finite input, is `UNREGISTERED_EPOCH`; a naive/invalid `at` raises `RegistryError` |
| History never relabelled | `epoch_label_for_record` returns the record's own `strategy_epoch` only if it names a FROZEN/RETIRED epoch of the record's `strategy` (both exact strings), else `LEGACY_UNVERSIONED` (no stamp) / `UNREGISTERED_EPOCH`; it never assigns |

## Registered now

Only `options_122` / `122-IEX-E1`, transcribed from
`docs/options-122-prospective-collector-preregistration-2026-09-18.md`:
observation-only; stop/target/runner `UNRESOLVED`; thresholds 60s cadence /
120s capture lag / 16m SIP reconciliation delay; source commit = immutable
release `36e73f1…`; effective from the first scheduled activation
(2026-09-21 13:00Z). **No OOS reference exists**, so no fitness verdict is
possible for this epoch.

### Collector stamp: `policy_epoch` is not `strategy_epoch`

`scripts/options_122_prospective_collect.py` stamps every row with
`policy_epoch: "122-IEX-E1"` (its `POLICY_EPOCH` constant). That value names
this registered FROZEN epoch (a test pins the match), but it is the collector's
policy-version stamp, not an epoch-registry stamp: collector rows carry no
`strategy` and no `strategy_epoch`. `epoch_label_for_record` reads only
`strategy` + `strategy_epoch` and never promotes `policy_epoch`, so existing
collector rows read as `LEGACY_UNVERSIONED` and enter no epoch population.
Rows become epoch-labelled only when a writer stamps `strategy` +
`strategy_epoch` from `resolve_epoch` at write time; historical rows are not
rewritten.

Nothing else was registered. Registering a tradable strategy epoch (with an
untouched OOS R distribution) is an operator + reconciliation decision, not an
agent one.

## Adding an epoch

1. Write and merge a preregistration doc.
2. Append an entry (never edit a FROZEN one), compute
   `definition_hash(definition, thresholds)`, store it, and add it to
   `FROZEN_PINS`.
3. To retire the previous epoch, set its `effective_until`. That is the only
   field you may add to a frozen entry.

## Known non-blocking follow-ups

These fail closed (they raise; nothing is labelled or authorised) but raise a
built-in exception instead of `RegistryError`. They are reachable only from
Python callers, not from registry JSON:

1. `resolve_epoch` with a broken `tzinfo` on `at`, or `registry=None`.
2. `epoch_label_for_record` with an array-like `strategy_epoch` value, or `registry=None`.
3. `load_registry(None)` / a non-path argument.
