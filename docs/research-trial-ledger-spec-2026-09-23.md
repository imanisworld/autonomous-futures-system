# Research Trial Ledger — minimal control specification (2026-09-23)

**Status: SPECIFICATION ONLY — NOT IMPLEMENTED. Docs-only. No code, no runtime, no
strategy, risk, broker, collector or deployment change. Nothing in this document
authorizes a research run, a promotion, or a change to any active forward campaign.**

Basis: `main@991df5ff0cc349a70dcfda72cc477e697c564c6a`. Read-only audit that established
the gap: `docs/architecture-gap-review-2026-09-23.md` §4 and the 2026-09-23 falsification
pass (verdict **GAP REAL, narrow**).

---

## 1. The gap, in one paragraph

The repository can preserve a study's *conclusion* while losing the *history of what was
tried*. Preregistration freezes one study; `ops/evidence_registry.py` summarizes evidence
already written; `docs/strategy-rules/Strategy_Inventory.md` records strategy conclusions;
the one-look receipt (`scripts/options_reclaim_entry.py`) enforces look *count*. None of
them requires a study to be registered before it runs, gives it a durable identity, records
planned/running/completed/aborted, or guarantees that abandoned and negative variants are
retained. The concrete failure case: the 2026-09-23 cross-market grid searched ~76k cells
per market and reports "0/47 picks pass" (`docs/cross-market-improvement-grid-2026-09-23.md`),
while the cell-level attempt history lives under `private/` (`.gitignore:65`) — unversioned.

## 2. Scope and non-goals

**In scope — exactly four things:**

1. one append-only trial ledger file;
2. one rule: a scored research run must have a ledger entry (and its prereg) *before* it runs;
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
- Required fields on every line:

| Field | Type | Rule |
|---|---|---|
| `trial_id` | string | `T-<YYYY-MM-DD>-<slug>`; unique per PLANNED line; slug = prereg filename stem |
| `event` | enum | `PLANNED` \| `RUNNING` \| `COMPLETED` \| `ABORTED` \| `SUPERSEDED` |
| `recorded_at` | ISO-8601 UTC | when the line was appended |
| `recorded_by` | string | agent/operator label (no e-mail) |
| `prereg_path` | string | repo path of the prereg doc |
| `prereg_commit` | 40-hex | commit at which the prereg was frozen |
| `family` | string | strategy/hypothesis family label (free text, short) |
| `population` | string | instruments + data window + corpus identity, one line |
| `variant_set` | object | `{"count": int, "manifest": "<repo path or null>"}` — count of cells/variants declared by the prereg; manifest is the retained cell inventory (see §6) |
| `prior_exposed` | string | `none` or a one-line disclosure of previously seen cells/lanes |

- Required additionally on `COMPLETED` / `ABORTED` / `SUPERSEDED` lines:

| Field | Rule |
|---|---|
| `disposition` | `VALIDATED` \| `PROMISING_BUT_UNPROVEN` \| `WAIT` \| `RESEARCH_ONLY` \| `BROKEN` \| `OVERFIT` \| `RETIRE` \| `NOT_RUN` — the Inventory taxonomy plus `OVERFIT` and `NOT_RUN` |
| `result_artifact` | repo path of the results doc/JSON, or `null` with a `reason` for ABORTED/NOT_RUN |
| `result_commit` | commit that added the result artifact (COMPLETED only) |
| `attempts_in_family_before` | integer — number of prior trials (any disposition) in the same `family` at registration time; the multiple-testing count |

- Optional: `notes` (one line), `supersedes` (`trial_id`).

## 5. Rule — register before you run

A scored research run (anything whose output is intended to become an evidence artifact
under `docs/`) is valid only if, at the time the result artifact is committed:

1. a `PLANNED` line for its `trial_id` exists in the ledger **at a commit that is an ancestor
   of the result commit and not the same commit**;
2. that line's `prereg_commit` is likewise an ancestor of the result commit;
3. the result artifact (JSON or Markdown front matter) cites the `trial_id`.

A result artifact that fails any of these is **UNREGISTERED** and must not be cited as
evidence in the Inventory, a handoff, or a promotion facts file. It is not deleted; it is
recorded with an `ABORTED` line (`disposition: NOT_RUN`, `reason: unregistered`) so the
attempt is still on the record.

Counts-only forward evaluators (blind lanes such as #929/#937, #947/#948, #949/#951) register
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

1. **Ledger integrity:** file parses line-by-line; every line has the required fields;
   every `trial_id` has a `PLANNED` line before any other event; enum values are valid;
   `recorded_at` is non-decreasing within a `trial_id`.
2. **Append-only:** the test ships with the count and SHA-256 of the ledger's first N lines
   as of the previous release (`tests/fixtures/research_trial_ledger.anchor.json`); the
   current file must start with exactly those bytes. Editing history breaks the anchor.
3. **Register-before-run:** for every `docs/*results*.json`, `docs/*-results-*.md` and
   `docs/*evaluator*.md` created **after the ledger start date** (§8), the artifact must
   cite a `trial_id` whose `PLANNED` line's commit is an ancestor of, and not equal to, the
   artifact's first commit (`git log --diff-filter=A`). Older artifacts are exempt.
4. **Retention:** every `variant_set.manifest` path exists; for private studies the manifest
   lists ≥ `variant_set.count` entries.
5. **Linkage:** every `docs/prereg-*.md` created after the start date has at least one
   ledger line.

The test is read-only, runs under the existing unrestricted `pytest -q`
(`.github/workflows/ci.yml:27`), and needs git history available in CI (`fetch-depth: 0`
— the only workflow change; to be confirmed at implementation review).

## 8. Grandfathering

- **Start date** = the merge date of the ledger's first commit. Nothing before it is
  retroactively required.
- Backfill is optional and, if done, uses `event: COMPLETED` lines with
  `notes: "historical backfill"` and no `attempts_in_family_before` claim. Backfill must not
  be used to reconstruct a multiple-testing count after the fact.
- The 2026-09-23 cross-market grid is the first candidate for a retention manifest (§6)
  because its cell history is private; that is a follow-up, not part of this spec.

## 9. What this deliberately does not do

- It does not compute or gate on any statistic; `attempts_in_family_before` is a count for
  humans and future preregs to cite.
- It does not block a research script from *executing* — the repo cannot control a local
  shell. It blocks the *result* from becoming evidence without a prior registration, which
  is the guarantee that matters.
- It does not replace, wrap, or import `ops/evidence_registry.py`.
- It does not change how preregs are written; it adds one required cross-reference.

## 10. Open questions for review (answer before any code)

1. Should `trial_id` be cited in prereg docs too (two-way link), or only in results?
2. Is Markdown front matter acceptable for citing `trial_id` in `.md` results, or should
   every result carry a JSON sidecar?
3. Where do option-scanner / options-lane studies register — same ledger (recommended) or
   a second file?
4. Who appends: the agent running the study, in the same PR as the prereg (recommended).
5. Does the ledger start date coincide with the first forward look of #929 (~May 2027) or
   with merge? Recommended: merge — forward lanes register their existence now.

## 11. Ordered next steps

1. Review this spec (operator + independent reviewer); resolve §10.
2. If approved: one PR containing the empty ledger file, the anchor fixture, the CI test
   and the `fetch-depth` line. No other files.
3. Register the three active forward lanes and the next prereg as the first entries.
4. Later, separately: retention manifest for the 2026-09-23 grid.

No implementation is authorized by this document.
