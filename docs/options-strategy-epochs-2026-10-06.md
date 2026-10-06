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
| Registry cannot grant trading | `authority.execution_authority` must be `false`; `observation_only` must be stated |
| New signals stamped only by exact match | `resolve_epoch`: running definition must hash-match a FROZEN epoch covering the timestamp, else `UNREGISTERED_EPOCH` |
| History never relabelled | `epoch_label_for_record` returns the record's own stamp, or `LEGACY_UNVERSIONED` / `UNREGISTERED_EPOCH`; it never assigns |

## Registered now

Only `options_122` / `122-IEX-E1`, transcribed from
`docs/options-122-prospective-collector-preregistration-2026-09-18.md`:
observation-only; stop/target/runner `UNRESOLVED`; thresholds 60s cadence /
120s capture lag / 16m SIP reconciliation delay; source commit = immutable
release `36e73f1…`; effective from the first scheduled activation
(2026-09-21 13:00Z). **No OOS reference exists**, so no fitness verdict is
possible for this epoch.

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
