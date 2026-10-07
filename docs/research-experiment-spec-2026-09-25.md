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
Canonical typed evidence contract (U1) lives in `ops/evidence_row.py` and is
enforced by `ops/research_experiment_runner.py`.

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
(`population_size_differs`, `required_metric_missing`, unresolved SHAs, typed
evidence-contract failures, etc.) are `INVALID EXPERIMENT`, not evidence
against the candidate.

### 8.1 Common evidence envelope

Every runner-written evidence bundle includes `evidence_envelope.json` with a
common identity layer:

- `schema_version` (evidence-row schema, currently `1.0.0`)
- `experiment_id`, `trial_id`, `setup_type`
- `evidence_type` (`coverage` or `trade_execution`; **required** on new specs)
- `promotion_eligible` (`false` for coverage; trade_execution may be `true`)
- `strategy_identity` when applicable
- `code_sha` (real 40-hex commit SHA; `unknown` is rejected), `data_identity`,
  `runner_version`, `generated_at`
- `execution_model_id` when the evidence type requires frozen execution assumptions
- `preregistration_identity` when present; `prior_exposure_identity` only from an
  explicit prior-exposure field (never from `population`)

This envelope is identity/provenance only. It does not invent trade fields.

Frozen pre-U1 options experiment IDs may omit `evidence_type` and are treated as
`coverage`. New specs that omit `evidence_type` fail closed.

### 8.2 Typed evidence rows

Evidence types are explicit. Do **not** force trade fields onto every experiment.
Coverage evidence cannot carry trade-lookalike fields to bypass trade validation.

| `evidence_type` | Meaning | Trade fill / stop / target / MAE / MFE / P&L required? |
|---|---|---|
| `coverage` | Non-trade measurement (for example options coverage/geometry); not promotion-eligible | **no** |
| `trade_execution` | Promotion-quality futures trade execution evidence | **yes**, via the typed row contract |

`trade_execution` rows must carry causal timing fields that remain distinct:

1. `signal_ts` — signal formation time  
2. `decision_ts` — decision availability time (`>= signal_ts`)  
3. `earliest_legal_order_ts` — earliest legal submission time (`>= decision_ts`)  
4. `fill_ts` — actual/simulated fill time (`>= earliest_legal_order_ts` when filled)

Filled rows with `fill_ts < earliest_legal_order_ts` fail closed as
`INVALID EXPERIMENT`. Exact equality is allowed when the frozen execution model
permits it. `NO_FILL` rows must not carry exit / P&L / MAE / MFE / R outcome
fields. `FILLED` rows require `direction` (`LONG`/`SHORT`) with consistent
brackets, `costs_fees >= 0`, `net_pnl = gross_pnl - costs_fees` within $0.01,
`mfe >= 0`, and `mae <= 0`. Row `data_fingerprint` must match the experiment
dataset identity.

Trade scoring uses canonical `r_multiple` on `FILLED` rows only. Legacy funnel
keys (`entered` / `completed` / `result`) are rejected on `trade_execution`.
A `NO_FILL` never scores as a win.

### 8.3 Execution-model identity

Trade experiments pin a frozen `execution_assumptions` bundle on the spec
(entry/fill model, same-bar ambiguity rule, stop/target handling, slippage,
commission, exchange/broker fees, sizing). The runner derives a stable
`execution_model_id` from that bundle. Historical studies keep their pinned
assumptions; later brokerage-fee changes must not silently rewrite them.

Missing execution-model identity, missing data identity, evidence-type mismatch,
or non-finite economic fields fail closed.

### 8.4 Chronological partitions (U2)

Specs may declare `chronological_partitions` with three non-overlapping
half-open windows `[start, end)` in UTC:

1. `development` — fitting / iteration window  
2. `validation` — held-out confirmation window after development  
3. `untouched_oos` — once-only out-of-sample window after validation  

Ordering is fail-closed:

- each window requires `start` / `end` with `end > start`
- `development.end <= validation.start`
- `validation.end <= untouched_oos.start`
- all boundaries normalize to UTC before comparison / fingerprinting

When `chronological_partitions` are declared, an active
`evaluation_partition` is **mandatory** (on the spec or via CLI
`--partition`). Declared partitions with no active partition are INVALID.
CLI `--partition` must not contradict a partition declared on the spec.
No declared partitions + no active partition remains valid for legacy specs.

The resolved active partition and its normalized window are passed into
`ExperimentContext`. For `trade_execution`, every scored row's `signal_ts`
must satisfy `start <= signal_ts < end`. Coverage evidence must not invent
timestamps; if it cannot prove membership in an untouched OOS window, it
must not claim `untouched_oos`. Development runs must not score OOS-dated
rows and OOS runs must not score development-dated rows.

`untouched_oos` is once-only for the **exact** approved `trial_id`.
Renaming `experiment_id`, reformatting an equivalent window string, or
changing the OOS window under the same trial cannot grant a second look.
The normalized OOS window fingerprint is preserved on the receipt as
recorded evidence only — it is not the reuse key. There is no silent
family-wide OOS lock.

Write ordering is fail-closed: validity is determined in memory first;
an exclusive lock covers OOS receipt check + append; the receipt is
appended to `docs/research-oos-consumption-ledger.jsonl` **before** any
VALID OOS evidence bundle is written. If receipt append fails, no VALID
OOS bundle is written. If bundle writing crashes after receipt append,
the trial remains consumed. Invalid/blocked/incomplete runs do not
consume the window. Concurrent duplicate consumption is prevented with
`fcntl` exclusive locking.

The ledger is a repo-governed append-only artifact. Fresh checkouts and
independent agents must see prior OOS consumption; CI enforces
register-before-count consistency against the trial ledger.

Development and validation partitions may be re-run when governance allows;
U2 does not change lifecycle semantics beyond the OOS once-only gate.

### 8.5 Futures replay adapter (U3)

`setup_type: futures_replay` registers
`ops/research_experiment_adapters/futures_replay.py`. It runs the existing
offline replay path (`ReplayEngine` → decision → risk → `PaperBroker` →
journal) and translates journaled `TRADE` / `RISK_REJECTED` decisions and
their `OUTCOME` rows into canonical `trade_execution` rows. It contains no
strategy, risk, or fill logic of its own.

Fail-closed requirements:

- `evidence_type: trade_execution`.
- `baseline.commit_sha` and `candidate.commit_sha` must both equal the
  exact SHA of the checkout executing the adapter, with no tracked
  modifications. Arms may differ only through `changed_variables`, applied
  as `SystemConfig` overrides; fields owned by the execution model
  (slippage, same-bar rule, breakeven, runner, entry model, entry
  tolerance, live/paper flags, log dir) cannot be changed variables.
- `data.dataset_id` is a repo-relative replay manifest whose SHA-256 equals
  `data.dataset_hash`; every `days[]` entry (and optional `htf[]` entry)
  carries a `sha256` of its file.
- `days[]` must be globally chronological across all instruments: each file
  is internally ordered and starts strictly after every earlier file ends.
  ReplayEngine carries rolling balance and open positions across files, so a
  swapped or overlapping manifest fails closed before replay.
- The paper-to-broker mirror hook must be disabled.
- The 2-1-2 / 1-2-2 intrabar restore path is not supported.

`execution_assumptions` use a closed vocabulary (anything else fails):

| key | allowed values |
|---|---|
| `entry_fill_model` | `market` \| `stop_market` |
| `same_bar_ambiguity_rule` | `stop_first` (pessimistic only) |
| `stop_handling` | `fixed_stop` \| `breakeven_at_1r` |
| `target_handling` | `fixed_limit` |
| `slippage_assumption` | `adverse_ticks=<n>` (market entry and stop exits) |
| `commission` | `usd_per_contract_per_side=<x>` |
| `exchange_broker_fees` | `usd_per_contract_per_side=<x>` |
| `sizing_assumptions` | `replay_risk_engine` |

Row derivation (mechanical only):

- `signal_ts` = signal bar start label; `decision_ts` =
  `earliest_legal_order_ts` = signal bar close.
- `fill_ts` = signal bar close (`market`) or the triggering bar's start
  (`stop_market`); `exit_ts` = resolution bar close.
- `fill_price`, `exit_price`, `exit_reason`, `gross_pnl` come from the
  journal; `gross_pnl` must agree with the price move within $0.01.
- `costs_fees` = (commission + fees) × 2 sides × contracts;
  `net_pnl` = gross − costs; `r_multiple` = net ÷ initial risk dollars
  (|fill − stop| in ticks × tick value × contracts).
- `mae` / `mfe` are bar-granular price points over the bars from fill
  through the resolution bar.
- `candidate_signal_id` is a deterministic hash of instrument, strategy,
  signal time, direction, entry, stop, and target.
- `CANCELLED` entries become `NO_FILL` with `no_fill_reason`;
  `RISK_REJECTED` decisions become `NO_FILL` with `reject_reason`.

With an active U2 partition, no candle at or after the window end is
replayed, and only candidates whose `signal_ts` falls in `[start, end)`
become members. A candidate whose position the replayed data leaves
unresolved fails the run instead of being dropped.

Known limit: strategy configuration outside `changed_variables` and the
execution model resolves from `risk_rules.yaml` at the executing SHA plus
the process environment. Each arm records `base_config_sha256` and
`arm_config_sha256`; binding that snapshot into required evidence identity
belongs to U4.

### 8.6 Mandatory evidence identity gate (U4)

`ops/evidence_identity.py` classifies one evidence bundle directory
(`python -m ops.evidence_identity <bundle>`):

- `PROMOTION_QUALITY` — the only class promotion may count;
- `REFERENCE_ONLY` — no canonical envelope (legacy/historical evidence),
  coverage evidence, or an envelope that does not claim promotion
  eligibility. Readable as reference, never counted;
- `INVALID` — claims promotion eligibility but fails any check below.

Promotion-quality requires, from the runner bundle itself:

- U1 envelope contract with `evidence_type: trade_execution`;
  `experiment_id`, `trial_id`, `preregistration_identity`,
  `strategy_identity` (versioned by `code_sha`), bound to the single
  canonical strategy identity present across baseline/candidate trade rows; `code_sha`,
  `data_identity`, `evaluation_partition`, `execution_model_id`;
- `data_identity` pinned as `dataset_hash:<sha256>`;
- the bundle at `docs/research-evidence/<trial_id>/`, with a
  `bundle_manifest.json` (written last by the runner) whose per-file
  SHA-256 values still match and which lists every required bundle file;
- the bundled spec APPROVED, equal to the repository's
  `docs/research-experiment-specs/<experiment_id>.json`, declaring
  chronological partitions, and agreeing with the envelope on prereg path,
  commit SHA, dataset identity, execution model, and evaluation partition;
- `runner_report.json` status `VALID`;
- the trial registered in `docs/research-trial-ledger.jsonl` with a
  `PLANNED`/`ADOPTED` first event and the same prereg path; when that first
  row records `prior_exposed`, the envelope's `trial_prior_exposed` must
  equal it (the runner copies it verbatim; it is never inferred);
- `untouched_oos` evidence carries its `oos_consumption_receipt.json`,
  identical to the ledger receipt and agreeing on `code_sha` and
  `dataset_hash`; non-OOS evidence carries no receipt.

Historical evidence is never re-labelled or given fabricated identity. CI
fails if any tracked `evidence_envelope.json` or `bundle_manifest.json` sits
outside `docs/research-evidence/<trial_id>/` or classifies `INVALID`.

Consumed-trial partition lock: once a trial has an OOS receipt, every
later run of that trial must declare chronological partitions whose
normalized `untouched_oos` window equals the receipt's window. Removing
partitions (legacy path) or redrawing windows over the consumed dates is
`INVALID`; re-running development/validation on the recorded partitions
remains allowed. There is still no family-wide lock.

Append-only ledgers: CI fails if any existing line of
`docs/research-trial-ledger.jsonl` or
`docs/research-oos-consumption-ledger.jsonl` is changed, reordered, or
removed in any commit after anchor `cedeab8` or in the working tree.

Runner version 1.3.0.

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
