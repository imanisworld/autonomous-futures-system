# /daily-reconciliation

Purpose:
One daily, read-only source-of-truth pass while the repo is moving quickly —
PR hygiene, branch/worktree hygiene, evidence preservation, deployed-state
tracking, and strategy-status reconciliation, in one report. This is broader
than `/repo-hygiene-check` (git/PR hygiene only) and does not replace
`/futures-promotion-gate` (per-strategy pipeline evidence) or
`/futures-deployment-safety-audit` (live-box state) — it points at both when
their inputs look stale.

Core rule: report drift, do not fix it. This command never edits docs,
config, or code, and never deletes/merges/tags anything.

## Step 1 — mechanical git/branch/worktree/evidence-preservation pass

Run:

```
python3 -m ops.project_check daily
```

This covers, read-only and without any fetch/mutation by default:
- local `<default-branch>` vs `origin/<default-branch>` ahead/behind/diverged
- all active worktrees
- branches whose configured upstream was deleted (`[gone]`)
- local-only branches (no upstream configured)
- `archive/*` tags present, dereferenced to their commit
- for every local branch not merged into default: unique commit/file count
  vs default, whether an `archive/*` tag already protects its exact tip, and
  a proxy classification (`BLOCKER` when the branch looks abandoned with no
  archive tag, `REVIEW` when it looks like active WIP, `OK` otherwise)

Treat any `BLOCKER` line in its output as a same-day flag, not something to
resolve yourself — report it, do not delete or tag anything. The script's
"unmerged + remote deleted" proxy is a substitute for GitHub PR state, not a
replacement for it — cross-check step 2 before trusting a BLOCKER or clearing
one.

Pass `--fetch` only if you want `origin/<default>` refreshed first (the only
network call this tool makes); omit it to stay purely local.

## Step 2 — GITHUB (use `gh` or the GitHub MCP tools; the script above makes no required network calls)

Report:
- PRs opened today
- PRs merged today
- PRs closed-unmerged today
- current open PRs (flag any with no activity in >5 days as **stale**)
- for each closed-unmerged PR found today or recently: does its branch appear
  in step 1's evidence-preservation output, and if so with what
  classification

Cross-reference: a branch step 1 flagged `BLOCKER` whose PR you confirm here
was merged (not closed-unmerged) is a false positive — squash-merges often
leave the branch looking "not merged" in git ancestry even though its content
landed. Downgrade it explicitly and say why, do not silently drop it.

## Step 3 — EVIDENCE PRESERVATION (finalize using step 1 + step 2)

For every closed-unmerged research branch confirmed in step 2:
- unique files/commits (from step 1)
- archive tag coverage (from step 1)
- **flag as BLOCKER** if it has unique evidence and no archive tag
- never delete anything, never create an archive tag as part of this command
  — creating the tag once a BLOCKER is confirmed is the operator's next
  action, using the existing convention in `docs/BRANCH_ARCHIVE_INDEX.md`
  (annotated tag `archive/<slug>-<date>`, pushed and remote-verified, then
  the branch entry recorded in that index)

## Step 4 — DEPLOYED STATE

This is only verifiable against the actual running box, which this repo
checkout does not have access to. Report:
- current known deployed SHA: **UNVERIFIABLE from this checkout** — run
  `/futures-deployment-safety-audit` against the live box, or
  `ops/live_box_guard.py`'s drift-report machinery directly on the box
- whether deployed SHA matches intended release: same — defer to the audit
  above
- current evidence epoch(s) / active paper-forward strategies: read from
  `risk_rules.yaml`'s `enabled_concepts` (per-instrument overrides included)
  and any `PAPER_ELIGIBLE`/`SHADOW_ONLY` annotations in the same file as the
  *intended* state — label it intended, not confirmed-running, unless the
  live-box audit above was actually run today

Do not present `risk_rules.yaml`'s committed state as proof of what is
currently deployed — that is exactly the gap `/futures-deployment-safety-audit`
exists to catch (main can diverge from the deployed SHA between merge and
deploy).

## Step 5 — STRATEGY SOURCE OF TRUTH

Compare, side by side:
- `docs/strategy-rules/Strategy_Inventory.md` (master table + per-strategy
  profile section)
- the relevant strategy README(s) under `docs/strategy-rules/`
- `risk_rules.yaml` comments near `strategy_permission_gate` /
  `enabled_concepts` / per-instrument disables
- the latest evidence PRs (from step 2, or `git log --oneline -20 -- docs/strategy-rules/`)
- current runtime enablement (`enabled_concepts`, per-instrument overrides,
  `PAPER_ELIGIBLE`/`SHADOW_ONLY` status in `risk_rules.yaml`)

Flag, explicitly and by name:
- stale classifications — `Strategy_Inventory.md`'s verdict for a strategy is
  older than a merged evidence PR that changed it
- superseded historical metrics — a P&L/PF/sample-size figure still cited
  that a later canonical-evidence doc corrected (this repo has done this
  correction publicly multiple times, e.g. VWAP Hold's IOC-close re-scoring
  and NY-only correction, ORB Breakout's canonical-evidence pass — check
  whether the master table row and profile section still agree with each
  other and with the most recent dated note)
- strategy described as active but actually fail-closed — `risk_rules.yaml`
  lists it `PAPER_ELIGIBLE` but a risk/config gate (session restriction,
  per-instrument disable, standing evidence-phase directive) means it cannot
  actually reach a trade
- strategy described as promising but latest evidence says
  BROKEN/WAIT/OVERFIT — the doc's verdict field disagrees with its own most
  recent dated note in the same profile
- missing new strategy rows — a strategy with code/evidence
  (`strategy/strat_*.py`, `research/detector_*.py`,
  `docs/strategy-rules/*_EVIDENCE_*.md`) that has no corresponding
  `Strategy_Inventory.md` row at all
- research completed but inventory not reconciled — an evidence doc or PR
  landed with a verdict that never got copied into the master table

Do not automatically edit `Strategy_Inventory.md`, the README, or
`risk_rules.yaml` comments — report the drift and name the smallest specific
edit that would fix it; reconciling it is a separate, explicit action only
taken if asked.

## Forbidden actions

- Do not delete branches, worktrees, or tags.
- Do not create archive tags (report the need for one; creating it is a
  separate, explicit follow-up action, per `docs/BRANCH_ARCHIVE_INDEX.md`'s
  existing process).
- Do not merge, close, or comment-resolve any PR as part of this check.
- Do not edit `Strategy_Inventory.md`, README files, or `risk_rules.yaml`.
- Do not deploy, restart, or touch the live box.
- Do not treat `risk_rules.yaml`'s committed state as proof of the deployed
  box's actual running state.

## Required output format

```
GENERATED AT:

GITHUB
  opened today:
  merged today:
  closed-unmerged today:
  open PRs (n, stale flagged):

BRANCHES / WORKTREES
  stale merged branches:
  active worktrees:
  branches tracking deleted remotes:
  local-only branches:
  local <default> vs origin/<default>:
  unexpected remote branches:

EVIDENCE PRESERVATION
  BLOCKERS (unique evidence, no archive tag):
  REVIEW (looks like active WIP):
  OK:

DEPLOYED STATE
  deployed SHA: UNVERIFIABLE FROM THIS CHECKOUT / <value, with audit citation>
  matches intended release: UNVERIFIABLE / YES / NO
  intended evidence epoch / enablement (risk_rules.yaml, not confirmed-live):

STRATEGY SOURCE OF TRUTH
  stale classifications:
  superseded metrics:
  active-but-fail-closed:
  promising-but-latest-evidence-disagrees:
  missing rows:
  unreconciled research:

BLOCKERS (repo-wide, ranked):
SAFE NEXT STEP:
```

Safety gates:
- Any evidence-preservation `BLOCKER` caps this report's overall status at
  "action needed," even if everything else is clean.
- Deployed-state fields default to `UNVERIFIABLE FROM THIS CHECKOUT` — never
  assert a deployed SHA or enablement state as confirmed without today's
  `/futures-deployment-safety-audit` (or equivalent live-box check) to back
  it.
- A strategy-doc disagreement (table vs. profile vs. `risk_rules.yaml`) is
  reported as drift, not silently resolved in either direction.

Safe next step:
If evidence-preservation BLOCKERs exist, the safe next step is naming the
exact branch(es) and letting the operator create the archive tag (or decide
otherwise) — never doing it automatically. If strategy-doc drift is found,
the safe next step is the smallest specific doc edit needed, named but not
applied. If everything is clean, say so plainly and stop — this command does
not itself authorize or block anything downstream.
