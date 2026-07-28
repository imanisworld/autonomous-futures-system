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

## Step 0 — observation identity (stamp every report, before anything else)

This report combines facts from at least two different sources — the local
checkout (`ops/project_check.py daily`) and live GitHub queries (step 2) —
taken at different moments. Never let those blend into an undated,
un-sourced blob. At the top of every report, capture and print:

- `repo_head`: local checkout's exact HEAD SHA (`git rev-parse HEAD`)
- `origin_main_sha`: `origin/<default>`'s SHA **fetched live** for this run
  (`git fetch origin <default> --quiet` then `git rev-parse origin/<default>`,
  or read live from GitHub directly) — not a possibly-stale local
  remote-tracking ref
- `generated_at`: this run's timestamp

If `repo_head` is not an ancestor of `origin_main_sha` (or vice versa in a
way that's unexpected), or if a fact from step 2 (a live GitHub query) is
about to be reported next to a fact from step 1 (the local checkout) without
noting they may reflect different points in time, say so explicitly. A
report that silently mixes "what my local checkout looked like" with "what
GitHub says right now" as if they were one consistent snapshot is describing
a repository state that never actually existed.

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
- for every local branch not merged into default: unique commit count
  (ancestry-based, `git branch --no-merged`) AND unique file count (a direct
  content diff against default's current tip), whether an `archive/*` tag
  already protects the branch's exact tip, and whether it's currently
  checked out in any worktree — combined into one of:
  - `ACTIVE WIP` — checked out in a worktree right now, full stop, regardless
    of everything else
  - `BLOCKER` — real content difference (unique files, not just unique
    commits), no archive tag, remote deleted
  - `REVIEW` — real content difference, remote still present (likely
    in-progress work, not yet ready to judge)
  - `LIKELY SQUASH-MERGED` — unique commits exist (ancestry looks unmerged)
    but the file-level diff against default's current tip is empty; this is
    the expected signature of a squash-merged PR, not evidence of anything
    lost. Confirm via GitHub PR state in step 2, but do not treat this as a
    BLOCKER — `git branch --no-merged` alone is unreliable proof of
    "unmerged" once squash-merges are in play, which is exactly why this
    tool checks content, not just ancestry
  - `OK` — no unique commits or content vs default

Treat any `BLOCKER` line in its output as a same-day flag, not something to
resolve yourself — report it, do not delete or tag anything. `LIKELY
SQUASH-MERGED` is informational, not a same-day flag — it exists so a clean
squash-merge doesn't get misread as lost evidence. Either way, cross-check
step 2 before treating any classification here as final; GitHub PR state,
not local git state, is the actual source of truth for "was this merged."

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

Cross-reference: step 1 already separates real content-level `BLOCKER`s from
ancestry-only `LIKELY SQUASH-MERGED` branches, so a squash-merge false
positive should be rare by the time it reaches this step. If one still slips
through — e.g. a `BLOCKER` whose PR you confirm here was actually merged —
downgrade it explicitly and say why (cite the merged PR), do not silently
drop it. The reverse matters too: a `LIKELY SQUASH-MERGED` branch whose PR
you confirm here is still **open** is not actually squash-merged yet — its
empty content-diff is coincidental (or the PR reintroduces identical code),
not proof of landing; do not wave it through as "OK" without checking.

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

### Step 4a — no-trade liveness

`0 trades today` for a given strategy/instrument is not by itself evidence of
anything — it is equally consistent with "market didn't set up" and "the
service has been silently dead since last night." **`0 trades` alone can
never produce a PASS/healthy verdict on its own.** Before reporting any
strategy as quiet today, require:

- **fresh feeds**: journal/status evidence that market data was actually
  arriving during the session window (not just that the process is running)
- **expected detector evaluation**: evidence the strategy's detector logic
  actually ran against today's bars (a candidate-count-of-zero from the
  detector is different from the detector never being invoked at all)
- an explicit `WHY_NO_TRADE` reason, not silence — see `/futures-why-no-trade`
  for the single-incident deep-dive version of this same question

Classify each quiet strategy as exactly one of:
- **`NO TRADE — HEALTHY`**: feed was fresh, detector evaluated, no
  qualifying setup formed (or one formed and a named, working gate correctly
  rejected it)
- **`NO TRADE — SYSTEM FAILURE`**: feed was stale/absent, the detector did
  not run, an error suppressed output, or evidence for "it actually
  evaluated" cannot be produced at all

An inability to verify either fresh feeds or detector evaluation is itself
`NO TRADE — SYSTEM FAILURE` (or, if genuinely unknown, `NO TRADE —
UNVERIFIED`) — never default it to healthy for lack of contrary evidence.

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
OBSERVATION IDENTITY
  repo_head:
  origin_main_sha:      (live-fetched, not a stale local ref)
  generated_at:

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
  ACTIVE WIP (checked out in a worktree):
  BLOCKERS (unique content, no archive tag):
  REVIEW (unique content, remote still present):
  LIKELY SQUASH-MERGED (ancestry-unmerged, content matches default):
  OK:

NO-TRADE LIVENESS (per strategy/instrument with 0 trades today)
  NO TRADE — HEALTHY:
  NO TRADE — SYSTEM FAILURE:
  NO TRADE — UNVERIFIED:

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
- `0 trades` for any strategy/instrument today caps that line at `NO TRADE —
  UNVERIFIED` until fresh-feed and detector-evaluation evidence is actually
  produced — it may never default to `HEALTHY`.
- A report missing `OBSERVATION IDENTITY` (repo_head, live origin_main_sha,
  generated_at) is incomplete — do not present its other findings as
  trustworthy without it.

Safe next step:
If evidence-preservation BLOCKERs exist, the safe next step is naming the
exact branch(es) and letting the operator create the archive tag (or decide
otherwise) — never doing it automatically. If strategy-doc drift is found,
the safe next step is the smallest specific doc edit needed, named but not
applied. If everything is clean, say so plainly and stop — this command does
not itself authorize or block anything downstream.
