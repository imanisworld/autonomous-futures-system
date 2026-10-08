# /repo-hygiene-check

Run a read-only repository hygiene audit.

Do not modify files.
Do not create branches.
Do not delete branches.
Do not delete worktrees.
Do not prune remotes.
Do not clean ignored files.
Do not stash.
Do not reset.
Do not commit.
Do not push.
Do not change git config.

Core rule:
No cleanup before classification.

## Required checks

Run read-only checks only:

```bash
git branch --show-current
git status --short
git status -sb
git log -3 --oneline
git branch -vv
git branch --merged main
git branch --no-merged main
git branch -r
git worktree list
git stash list
```

If worktrees exist, check dirty status in each worktree without modifying anything.

If possible, identify PR status for remote branches:

* open
* merged
* closed
* unknown

Account for squash merges: a branch may be merged by PR even if Git ancestry does not show it as merged.

## Classify repo state

Return one:

* CLEAN
* DIRTY
* HOLD
* UNSAFE TO WORK

## Classify each branch/worktree/stash

Use:

* KEEP
* ACTIVE WIP
* SAFE DELETE LOCAL
* SAFE DELETE REMOTE
* REVIEW FIRST
* DIRTY — DO NOT DELETE
* UNKNOWN — DO NOT TOUCH

## Required output

VERDICT
CLEAN / DIRTY / HOLD / UNSAFE TO WORK

CURRENT STATE

* current branch
* tracking state
* ahead/behind
* working tree status
* latest commits

DIRTY FILES
List modified/untracked files, if any.

WORKTREES
For each:

* path
* branch
* clean/dirty
* dirty files
* delete classification

STASHES
For each:

* stash name
* likely purpose
* touched files if inspected
* classification

LOCAL BRANCHES
For each relevant branch:

* ahead/behind
* merged/not merged
* dirty worktree attached?
* classification

REMOTE BRANCHES
For each relevant remote branch:

* PR status if known
* safe to delete or keep
* classification

ACTIVE WIP
List all active work that must not be deleted.

CLEANUP CANDIDATES
Separate:

* safe now
* review first
* do not touch

SAFE NEXT STEP
Give the smallest safe action.
Do not perform cleanup.

## File / artifact modernization classification

When the task is codebase modernization, dead-code removal, or simplification, classify files/artifacts separately from branches/worktrees.

Use:

- ACTIVE_RUNTIME — required by a currently supported runtime path.
- ACTIVE_RESEARCH — used by an approved/current research workflow.
- REPLAY_REQUIRED — needed to reproduce or validate historical/current evidence.
- HISTORICAL_EVIDENCE — provenance that should be retained but not treated as current authority.
- COMPATIBILITY_ONLY — retained solely for an older caller/format; requires caller proof before removal.
- DEAD / UNREFERENCED — no current caller, no replay/evidence requirement, no rollback dependency, and no supported runtime use.

Deletion/consolidation rules:

- Never delete based only on age, naming, or a search result.
- Search callers/imports/config/systemd/docs/tests and check runtime/replay/evidence dependencies.
- Historical evidence may be archived/clearly labeled rather than removed.
- A duplicate validator may be consolidated only after its authority relationship is understood; never remove a stricter fail-closed safeguard because it looks redundant.
- Runtime collectors/services require a separate retirement proof (effective unit/reference check, journal consumers, rollback/provenance needs) before deletion.
- Prefer one current-state authority plus historical provenance over multiple competing status documents.
- Modernization must reduce complexity without weakening provenance, risk, exact validation, release pinning, or execution isolation.

Report modernization candidates as: **safe now / requires migration / preserve as evidence / unknown-do-not-touch**.

