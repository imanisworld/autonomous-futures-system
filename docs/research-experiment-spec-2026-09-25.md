# Research Experiment Spec — minimum approved-run contract (2026-09-25)

**Status: CONTRACT ONLY — NOT A RUNNER.** This document defines the smallest
machine-checkable experiment specification needed to unblock a future AFS
Experiment Runner. It authorizes no research run, no strategy change, no risk
change, no broker action, no merge, and no deployment.

Basis: existing research governance only —

- `docs/strategy-rules/Strategy_Inventory.md` (strategy status authority — not replaced by this contract)
- `docs/research-trial-ledger.jsonl`
- `docs/research-trial-ledger-spec-2026-09-23.md`
- `docs/prereg-*.md`
- `docs/research-trial-manifests/`
- `docs/research-evidence/`
- `AGENTS.md` (agent role lock; no autonomous experiment invention)

This is architecture plumbing, not strategy research. It does not invent
hypotheses, optimize parameters, or promote candidates.

---

## 1. Why this exists

The trial ledger answers: *was this study registered before evidence?*

Preregs answer: *what was frozen in prose?*

Neither answers, in machine-checkable form:

> Has an operator approved a specific baseline-vs-candidate comparison with one
> declared changed-variable set, identical population, and explicit execution
> assumptions — such that an Experiment Runner may execute it?

Without that binding, a runner would have to invent or infer the contract.
That is forbidden. **No approved experiment specification, no run.**

---

## 2. Scope and non-goals

**In scope — exactly:**

1. one JSON Schema (`docs/research-experiment-spec.schema.json`);
2. one live-spec directory (`docs/research-experiment-specs/`);
3. one example under `docs/research-experiment-specs/examples/`;
4. CI tests that enforce schema + linkage for non-example specs;
5. this human contract.

**Out of scope — do not add in this PR or treat as authorized by it:**

- Experiment Runner automation or scripts that execute studies;
- a parallel research database or ledger replacement;
- statistics / promotion gates;
- strategy, risk, broker, collector, paper, or live behavior changes;
- automatic `COMPLETED` ledger writes (still a separate human/agent step).

---

## 3. Binding chain

```
operator approval
    → experiment_id (this contract)
        → trial_id (research-trial-ledger.jsonl)
            → prereg_path (docs/prereg-*.md)
            → population / variant_set (ledger frozen fields)
            → variant_manifest when required
            → evidence_path docs/research-evidence/<trial_id>/
```

The experiment spec does **not** replace the ledger. It sits *on top* of a
`PLANNED` or `ADOPTED` trial and adds the fields the ledger deliberately does
not carry (baseline/candidate SHAs, changed variables, held constants,
execution assumptions, required metrics, approval-to-run).

---

## 4. Paths and identity

| Artifact | Path |
|---|---|
| Schema | `docs/research-experiment-spec.schema.json` |
| Live specs | `docs/research-experiment-specs/<experiment_id>.json` |
| Examples only | `docs/research-experiment-specs/examples/<experiment_id>.json` |
| Evidence (unchanged) | `docs/research-evidence/<trial_id>/` |

`experiment_id` format: `E-<YYYY-MM-DD>-<slug>-<NN>`  
`trial_id` format: unchanged ledger form `T-<YYYY-MM-DD>-<slug>-<NN>`

One live file per experiment. Filename stem must equal `experiment_id`.

---

## 5. Status / approval semantics

| `status` | Meaning | Runner may execute? | Ledger linkage required? |
|---|---|---|---|
| `EXAMPLE` | Documentation fixture only | **no** | **no** |
| `DRAFT` | Incomplete / awaiting operator approval | **no** | yes (identity must already bind) |
| `APPROVED` | Operator has approved this exact byte-level comparison | **yes** | yes |
| `REVOKED` | Prior approval withdrawn | **no** | yes |
| `SUPERSEDED` | Prior approval replaced; do not run | **no** | yes |

**Approval rule.** Only an operator (human approval authority) may set
`status` to `APPROVED`. Agents may draft `DRAFT` specs. Agents must not
self-approve.

When `status` is `APPROVED`:

- `approved_by` and `approved_at` (UTC `...Z`) are required;
- every required comparison field must be present and schema-valid;
- the linked trial must already exist in the ledger with first event
  `PLANNED` or `ADOPTED` (never `UNREGISTERED_ATTEMPT`);
- `prereg_path` and `population` must equal the ledger frozen values;
- `evidence_path` must be exactly `docs/research-evidence/<trial_id>/`;
- if the ledger `variant_set.count > 1`, `variant_manifest` must equal the
  ledger `variant_set.manifest`.

**Replacement.** The *newer* live spec sets optional `supersedes` to the prior
`experiment_id`. The prior file is updated only by changing `status` to
`SUPERSEDED` (comparison fields stay frozen).

**Immutability after first APPROVED appearance.** Once a live spec has been
committed with `status=APPROVED`, later commits may only change:

- `status` → `REVOKED` or `SUPERSEDED`;
- optional `notes` (provenance only; never comparison fields).

All comparison fields (baseline, candidate, data, population,
changed_variables, held_constant, execution, required_metrics, criteria,
evidence_path, prereg_path, trial_id, experiment_id) are frozen. CI rejects
edits that alter them.

`EXAMPLE` files are schema-checked only and must live under `examples/`.

---

## 6. Required fields (summary)

See the JSON Schema for the authoritative shape. Conceptually:

- identity: `schema_version`, `experiment_id`, `trial_id`, `status`
- approval: `approved_by`, `approved_at` when `APPROVED`
- research question: `hypothesis` (supplied by the research layer; not invented here)
- linkage: `prereg_path`, `population`, `evidence_path`, optional `variant_manifest`
- comparison arms: `baseline.commit_sha`; `candidate.commit_sha` and/or `candidate.change`
- data: `data.source`, `data.window.{start,end}`, `data.dataset_id`, optional `data.dataset_hash`
- design: `setup_type`, `timeframe`, `changed_variables[]`, `held_constant[]`
- execution assumptions: `execution.{entry,exit,stop,target}_logic`, `sizing`, `friction`
- measurement: `required_metrics[]`; optional `acceptance_criteria` / `rejection_criteria`

**Changed variables.** `changed_variables` is an array with `minItems: 1`.
A single-variable experiment has length 1. Length > 1 is an explicit
multi-variable experiment — never implied.

**Candidate arm.** At least one of `candidate.commit_sha` or `candidate.change`
must be non-null. Prefer both when a distinct candidate commit exists.

**Dataset hash.** `dataset_hash` is optional in schema but required whenever an
immutable content hash is available. Missing hash is allowed only when the
dataset identity is otherwise pinned by `dataset_id` + window + source; the
future runner must still fail closed if coverage cannot be verified.

---

## 7. Validation rules (CI)

`tests/test_research_experiment_spec.py` enforces:

1. Schema file parses as Draft 2020-12 JSON Schema.
2. Every `docs/research-experiment-specs/**/*.json` validates against the schema.
3. Live specs (not under `examples/`) have filename stem == `experiment_id`.
4. Live `DRAFT` / `APPROVED` / `REVOKED` / `SUPERSEDED` specs link to the ledger:
   - `trial_id` present;
   - first event `PLANNED` or `ADOPTED`;
   - `prereg_path` and `population` match frozen ledger fields;
   - prereg file exists;
   - `evidence_path == docs/research-evidence/<trial_id>/`;
   - multi-variant manifest path matches when `variant_set.count > 1`.
5. Freeze rule against `origin/main` when the file already exists there:
   comparison fields unchanged except allowed status/notes transitions.
6. `EXAMPLE` specs are forbidden outside `examples/`; live `APPROVED` specs are
   forbidden inside `examples/`.

These tests are governance-only. They import no strategy, broker, risk, or
runtime modules.

---

## 8. Relation to the Experiment Runner

Implemented on `main` by #1047 (`df58d556fb1c1a462b2e968f3a6e7f47e6a7117a`).

CLI:

- `python scripts/afs_experiment_runner.py discover`
- `python scripts/afs_experiment_runner.py validate --spec …`
- `python scripts/afs_experiment_runner.py run --experiment-id …`

The runner must:

- discover only `status=APPROVED` live specs;
- refuse unchanged completed experiments unless explicitly requested,
  baseline/data changed, or reproducibility verification is being performed;
- write evidence only under the declared `evidence_path`;
- never alter production/paper strategy configuration;
- never promote, merge, deploy, or submit broker orders.

Result labels are mechanical against preregistered criteria. Integrity failures
(`population_size_differs`, `required_metric_missing`, unresolved SHAs, etc.)
are `INVALID EXPERIMENT`, not evidence against the candidate.

---

## 9. Authority boundary

This contract produces a shared source of truth for *what may be executed*.

It has zero authority to:

- modify strategy thresholds or production configuration;
- approve a strategy for promotion;
- merge or deploy;
- execute a trade.

Operator approval of a spec is approval to **measure**, not to **ship**.

---

## 10. Open ambiguities (explicit)

1. **Who may set `APPROVED`?** Operator only. Agent-authored PRs may include
   `DRAFT` specs; flipping to `APPROVED` requires operator intent in the same
   or a follow-up commit. CI cannot cryptographically prove human approval —
   it only checks field presence and linkage.
2. **Does `APPROVED` require a prior `PLANNED` ledger line in an ancestor
   commit?** Yes for register-before-evidence consistency when evidence is
   later written. This contract requires the trial to exist at HEAD; the
   ledger's stricter ancestry rule still governs evidence commits.
3. **Forward observation lanes vs offline A/B.** Both use this same schema.
   Forward lanes still freeze baseline/candidate identity even when the
   “candidate” is a policy description rather than a second commit SHA.
4. **Result storage beyond `evidence_path`.** Unchanged: ledger-governed
   evidence remains under `docs/research-evidence/<trial_id>/`. This contract
   does not create a competing results tree.
