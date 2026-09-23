# Research Trial Ledger — minimal control specification (2026-09-23, rev 2)

**Status: SPECIFICATION ONLY — NOT IMPLEMENTED. Docs-only. No code, no runtime, no
strategy, risk, broker, collector or deployment change. Nothing in this document
authorizes a research run, a promotion, or a change to any active forward campaign.**

Basis: `main@991df5ff0cc349a70dcfda72cc477e697c564c6a`. Read-only audit that established
the gap: `docs/architecture-gap-review-2026-09-23.md` §4 and the 2026-09-23 falsification
pass (verdict **GAP REAL, narrow**).

**Rev 2 (same day) — changes after independent review:** the guarantee is renamed from
"register before you run" to **register-before-evidence**, which is what a repository can
actually prove (§5, §9); append-only is enforced against the trusted base branch, not a
fixture that could be rewritten in the same PR (§7); lanes already running at ledger start
are **ADOPTED**, not retro-`PLANNED` (§8); an unregistered study that did run is recorded as
`UNREGISTERED_ATTEMPT` / `INVALID_EVIDENCE`, never as `NOT_RUN` (§5); the multiple-testing
count moves to the `PLANNED` line where it is frozen (§4); `family` becomes a stable
machine id plus a label (§4). The §10 questions carry proposed answers.

---

## 1. The gap, in one paragraph

The repository can preserve a study's *conclusion* while losing the *history of what was
tried*. Preregistration freezes one study; `ops/evidence_registry.py` summarizes evidence
already written; `docs/strategy-rules/Strategy_Inventory.md` records strategy conclusions;
the one-look receipt (`scripts/options_reclaim_entry.py`) enforces look *count*. None of
them requires a study to be registered before its result can count as evidence, gives it a
durable identity, records planned/running/completed/aborted, or guarantees that abandoned
and negative variants are retained. The concrete failure case: the 2026-09-23 cross-market
grid searched ~76k cells per market and reports "0/47 picks pass"
(`docs/cross-market-improvement-grid-2026-09-23.md`), while the cell-level attempt history
lives under `private/` (`.gitignore:65`) — unversioned.

## 2. Scope and non-goals

**In scope — exactly four things:**

1. one append-only trial ledger file;
2. one rule: a result cannot become evidence unless its trial (and prereg) were registered
   in an earlier commit — **register-before-evidence**;
3. one retention rule: an attempted variant set cannot disappear;
4. one CI test that enforces 1–3 mechanically.

**Explicitly out of scope — do not touch:** `ops/evidence_registry.py`,
`docs/strategy-rules/Strategy_Inventory.md`, the one-look receipt mechanism, existing
prereg/result documents, `ops/project_check/*` runtime behavior, any runtime, broker, risk,
collector, notification or deployment path. No research database, no statistics
(deflated Sharpe / PBO etc.), no automation that runs studies.

## 3. Definitions (kept distinct on purpose)

| Term | Meaning here | Existing home |
|---|---|---|
| Preregistration | Freezes one study's hypothesis, population, thresholds, cells, permitted variants | `docs/prereg-*.md` |
| Evidence registry | Read-only summary of evidence already collected by active lanes | `ops/evidence_registry.py` |
| Strategy inventory | Per-strategy evidence verdict + execution posture | `docs/strategy-rules/Strategy_Inventory.md` |
| **Trial ledger** (new) | Append-only history of every *attempted* study/variant set: identity, prereg link, status, disposition, retention pointer | this spec |

A **trial** is one preregistered study execution over one declared variant/cell set.
A cell-level search (e.g. 1,253 cells) is *one* trial whose variant set is the cell list;
individual cells are not separate trials unless a prereg declares them so.

## 4. The ledger file

- Path: `docs/research-trial-ledger.jsonl` (versioned, public-safe — no box paths, no
  strategy internals beyond what the linked prereg already states).
- Format: one JSON object per line, UTF-8, append-only. **Existing lines are never edited
  or deleted.** A state change is a new line for the same `trial_id`; the latest line for a
  `trial_id` is its current state.

### 4.1 Events

| `event` | Meaning | Counts toward the register-before-evidence guarantee? |
|---|---|---|
| `PLANNED` | Registered before any scoring; the normal first line | yes |
| `ADOPTED` | A lane/study already running when the ledger started (§8); first line instead of `PLANNED` | **no** — recorded for identity and retention only |
| `RUNNING` | Optional; scoring/collection in progress | — |
| `COMPLETED` | Result artifact committed | — |
| `ABORTED` | Registered but never scored, or stopped before a result | — |
| `SUPERSEDED` | Replaced by a later trial (`supersedes` on the new one) | — |
| `UNREGISTERED_ATTEMPT` | A study **did run** and produced a result without a prior `PLANNED` line; recorded after the fact so the execution is not erased | no — its result is `INVALID_EVIDENCE` |

### 4.2 Required fields on every line

| Field | Type | Rule |
|---|---|---|
| `trial_id` | string | `T-<YYYY-MM-DD>-<slug>`; `<slug>` = prereg filename stem; unique per first line (`PLANNED`/`ADOPTED`/`UNREGISTERED_ATTEMPT`) |
| `event` | enum | §4.1 |
| `recorded_at` | ISO-8601 UTC | when the line was appended; non-decreasing within a `trial_id` |
| `recorded_by` | string | agent/operator label (no e-mail) |
| `prereg_path` | string | repo path of the prereg doc (`null` only on `UNREGISTERED_ATTEMPT`) |
| `prereg_commit` | 40-hex | commit at which the prereg was frozen (`null` only on `UNREGISTERED_ATTEMPT`) |
| `family_id` | string | **stable machine id**, `^[a-z0-9_]+$` (e.g. `mnq_322_first_live`); the multiple-testing family key |
| `family_label` | string | human label, free text |
| `population` | string | instruments + data window + corpus identity, one line |
| `variant_set` | object | `{"count": int, "manifest": "<repo path or null>"}` — count of cells/variants declared by the prereg; manifest = retained cell inventory (§6) |
| `prior_exposed` | string | `none` or a one-line disclosure of previously seen cells/lanes |

### 4.3 Required additionally on the first line (`PLANNED` / `ADOPTED` / `UNREGISTERED_ATTEMPT`)

| Field | Rule |
|---|---|
| `attempts_in_family_before` | integer — number of prior trials with the same `family_id` (any event, any disposition) at registration time. **Frozen on the first line; never restated on later lines.** This is the multiple-testing count. |

### 4.4 Required additionally on `COMPLETED` / `ABORTED` / `SUPERSEDED` / `UNREGISTERED_ATTEMPT`

| Field | Rule |
|---|---|
| `disposition` | `VALIDATED` \| `PROMISING_BUT_UNPROVEN` \| `WAIT` \| `RESEARCH_ONLY` \| `BROKEN` \| `OVERFIT` \| `RETIRE` \| `NOT_RUN` \| `INVALID_EVIDENCE` — the Inventory taxonomy plus `OVERFIT`, `NOT_RUN` (registered, never scored) and `INVALID_EVIDENCE` (scored, not registered first) |
| `result_artifact` | repo path of the results doc/JSON; `null` with a `reason` for `ABORTED`/`NOT_RUN` |
| `result_commit` | commit that added the result artifact (`COMPLETED` and `UNREGISTERED_ATTEMPT`) |

- Optional on any line: `notes` (one line), `supersedes` (`trial_id`).

### 4.5 Family id collisions

`family_id` values that are equal after stripping `_`, `-`, spaces and case (e.g.
`mnq_322` vs `MNQ-322`) are treated as a collision and rejected by the CI test (§7). A new
family is introduced only by a first line whose `attempts_in_family_before` is `0`.

## 5. Rule — register-before-evidence

**What is guaranteed.** A result artifact counts as evidence only if, at the time it is
committed:

1. a `PLANNED` line for its `trial_id` exists in the ledger at a commit that is an ancestor
   of the result commit and **not the same commit**;
2. that line's `prereg_commit` is likewise a strict ancestor of the result commit;
3. the result artifact cites the `trial_id` (JSON key `trial_id`; Markdown front matter
   `trial_id:`), and the prereg cites it too (two-way link).

**What is not guaranteed, stated plainly.** The repository cannot observe a local shell. A
study can be executed, its result seen, and only then registered — the ledger cannot detect
that sequence. What it does prove is that the *registration commit precedes the evidence
commit*, which makes silent post-hoc registration a deliberate act that leaves a trace
(commit order and timestamps), not an accident. A true pre-run check (for example, a study
script refusing to write a result file unless a `PLANNED` line for its `trial_id` already
exists in `HEAD`) is a possible later hardening and is **not** part of this spec.

**Unregistered results.** A result artifact that fails 1–3 is `INVALID_EVIDENCE`. It is not
deleted. It receives an `UNREGISTERED_ATTEMPT` line carrying `result_artifact`,
`result_commit` and `attempts_in_family_before`, so the fact that the study **ran** is on
the record and counts against the family. It must not be cited as evidence in the Inventory,
a handoff, or a promotion facts file.

**Blind forward lanes.** Counts-only evaluators (#929/#937, #947/#948, #949/#951) register
once at lane start; the eventual single look is a `COMPLETED` line. They do not register per
tick.

## 6. Rule — attempted variants cannot disappear

For every trial, the **variant/cell inventory** must be retained in a versioned location:

- if the study's artifacts are in-repo, the results doc/JSON already is the inventory
  (`variant_set.manifest` points at it);
- if the study's data/scripts are private (`private/`, `.git/info/exclude`), a **manifest**
  must be committed under `docs/research-trial-manifests/<trial_id>.json` listing every
  attempted variant/cell identity and the SHA-256 of the private per-cell results file. The
  private data may stay private; the *list of what was tried* may not.

Abandoned or negative trials receive an `ABORTED` or `COMPLETED` line with a negative
disposition. Removing a trial's lines, its manifest, or a superseded result artifact is a
ledger violation. The existing convention of keeping superseded artifacts side by side
(`scripts/strat_212_122_canonical_evidence_results_pre_pr338_superseded.json`) becomes the
rule rather than a habit.

## 7. Enforcement — one CI test

`tests/test_research_trial_ledger.py` (to be written only after this spec is approved):

1. **Ledger integrity:** file parses line-by-line; every line has the required fields for
   its event; every `trial_id`'s first line is `PLANNED`, `ADOPTED` or
   `UNREGISTERED_ATTEMPT`; enum values valid; `recorded_at` non-decreasing per `trial_id`;
   `attempts_in_family_before` present only on first lines; `family_id` collision check
   (§4.5).
2. **Append-only, against the trusted base:** in CI the test reads
   `git show origin/main:docs/research-trial-ledger.jsonl` (the base branch, which a PR
   cannot rewrite) and requires the PR's file to begin with exactly those bytes. Locally the
   same check runs against `origin/main`; if the base file is unavailable the test **fails
   closed**, it does not skip. No fixture, no anchor file — nothing that can be edited in
   the same PR as the ledger.
3. **Register-before-evidence:** for every `docs/*results*.json`, `docs/*-results-*.md` and
   `docs/*evaluator*.md` first added **after the ledger start commit** (§8), the artifact
   must cite a `trial_id` whose `PLANNED` line's first commit is a strict ancestor of the
   artifact's first commit (`git log --diff-filter=A`), and whose prereg cites the same
   `trial_id`. `ADOPTED` trials are exempt from the ancestry check (§8). Artifacts older
   than the start commit are exempt.
4. **Retention:** every `variant_set.manifest` path exists; for private studies the manifest
   lists ≥ `variant_set.count` entries.
5. **Linkage:** every `docs/prereg-*.md` first added after the start commit has at least one
   ledger line.

The test is read-only, runs under the existing unrestricted `pytest -q`
(`.github/workflows/ci.yml:27`), and needs base-branch history in CI (`fetch-depth: 0` and
an `origin/main` ref — the only workflow change; to be confirmed at implementation review).

## 8. Grandfathering

- **Start commit** = the commit that first adds the ledger file to `main`. Nothing added
  before it is retroactively required.
- **Lanes already running at the start commit** (currently #929 MNQ ex-Asia, #947/#948
  MGC 4H wide, #949/#951 options reclaim) are registered with an `ADOPTED` first line that
  cites their real prereg path and commit and carries `notes: "running before ledger start;
  not preregistered by this ledger"`. `ADOPTED` is explicitly **excluded** from the
  register-before-evidence guarantee; their eventual look is a `COMPLETED` line. This keeps
  the ledger historically truthful instead of implying the ledger preregistered them.
- Historical completed studies may be backfilled with `COMPLETED` lines carrying
  `notes: "historical backfill"`; backfill must **not** assign `attempts_in_family_before`
  values as if they had been frozen at the time. Backfill is optional.
- The 2026-09-23 cross-market grid is the first candidate for a retention manifest (§6)
  because its cell history is private; that is a follow-up, not part of this spec.

## 9. What this deliberately does not do

- It does not compute or gate on any statistic; `attempts_in_family_before` is a frozen
  count for humans and future preregs to cite.
- It does not stop a research script from *executing* — see §5. It stops the *result* from
  becoming evidence without a prior registration commit, and it records unregistered
  executions instead of hiding them.
- It does not replace, wrap, or import `ops/evidence_registry.py`.
- It does not change how preregs are written beyond one required cross-reference
  (`trial_id`).

## 10. Open questions — proposed answers (pending operator confirmation)

| # | Question | Proposed answer |
|---|---|---|
| 1 | Two-way `trial_id` link (prereg ↔ result) or results only? | **Two-way.** `trial_id` is derived from the prereg stem, so the prereg can cite it in the same PR that adds the `PLANNED` line. |
| 2 | Markdown citation form? | **Front matter `trial_id:`** (or an HTML comment `<!-- trial_id: … -->` if the doc has no front matter). No mandatory JSON sidecar. |
| 3 | Options-lane studies: same ledger? | **One shared ledger** for futures and options. `family_id` separates them. |
| 4 | Who appends? | **The study owner/agent**, in the same PR as the prereg, before any scoring. |
| 5 | Start date? | **Merge of the ledger file.** Already-running lanes are `ADOPTED` (§8), not retro-`PLANNED`. |

## 11. Ordered next steps

1. Review this revision; confirm §10.
2. If approved: one PR containing the empty ledger file, the three `ADOPTED` lines, the CI
   test and the `fetch-depth` line. No other files.
3. The next new prereg is the first `PLANNED` entry.
4. Later, separately: retention manifest for the 2026-09-23 grid; optional pre-run
   hardening in study scripts (§5).

No implementation is authorized by this document.
