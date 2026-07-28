# /session-safety-check

Purpose:
Catch branch/worktree collisions, stale local state, dirty files, and
wrong-branch commits — before work starts, and again immediately before
commit/push. This is the thin operator-facing layer over
`ops/project_check.py`; the script does the read-only git work, this command
layers the GitHub PR check and tells you how to read the result.

Two modes. Run START once per work session; run PRECOMMIT immediately before
every commit/push in that session.

Read-only only: this command never commits, pushes, resets, rebases, checks
out, pulls, deletes, or modifies anything. Neither does the script it wraps.

## Mode A — START

Run:

```
python3 -m ops.project_check session-start
```

This reports and writes a local baseline (`.git/project_check/session_start.json`,
untracked, never pushed) for the later PRECOMMIT check to compare against:
- repo root, current branch, current HEAD SHA
- `origin/<default>` SHA (from local remote-tracking refs only — pass
  `--fetch` if you want it refreshed first; this is the only network call
  the script ever makes, and it is opt-in)
- local `<default>` ahead/behind/diverged vs `origin/<default>`
- current worktree, and all worktrees
- dirty tracked files, untracked files
- branches with deleted remotes
- a best-effort, git-only proxy for "closed-unmerged branches not preserved
  by an archive tag" (real closed-PR state lives on GitHub — see below)
- whether the current branch changed during the check itself

Then check open PRs relevant to this repo — via `gh pr list` if available
locally, otherwise the GitHub MCP tools (`list_pull_requests` /
`search_pull_requests`) — and note anything that overlaps the branch you're
about to work on. If the script's proxy flagged any branch as looking
abandoned-without-an-archive-tag, cross-check its real PR state here before
treating it as anything more than a heads-up.

## Mode B — PRECOMMIT

Run immediately before every commit and before every push:

```
python3 -m ops.project_check precommit
```

Fails closed (`FAIL-CLOSED` verdict, nonzero exit) on:
- current branch differs from the branch captured at session start
- worktree differs from the one captured at session start
- HEAD is not a descendant of the session-start HEAD (branch history was
  rewritten underneath you — reset/rebase/force-push since session start)
- detached HEAD
- a merge or rebase is in progress (ambiguous repository state)
- unresolved merge conflicts present
- another worktree currently has the intended branch checked out

Reports plainly, without failing on their own:
- current branch, HEAD, repo root, worktree
- changed files, staged files, untracked files
- upstream, ahead/behind

If no session-start baseline exists yet, PRECOMMIT still runs every other
check but reports `WARN` and says so explicitly — branch/worktree/HEAD
continuity cannot be verified without one. Run `session-start` first in that
case rather than trusting a cold PRECOMMIT run at face value.

A `FAIL-CLOSED` verdict means: stop, do not commit or push, and resolve the
named condition manually. This command takes no corrective action itself —
it only refuses to say things look safe when they don't.

## Forbidden actions

- Do not auto-commit, auto-push, reset, rebase, checkout, pull, delete, or
  modify anything, in either mode.
- Do not treat the script's "closed-unmerged, no archive tag" proxy as
  final — it is a local git-only heuristic; GitHub is the source of truth for
  actual PR state.
- Do not skip PRECOMMIT because START already ran earlier in the session —
  branch/worktree state can change between them (that's the failure mode
  this command exists to catch).

## Safe next step

If START surfaces a branch/worktree collision, an unexpectedly diverged
`<default>`, or a BLOCKER-looking unpreserved branch, resolve or confirm it
before starting new work — do not proceed on an ambiguous baseline. If
PRECOMMIT returns `FAIL-CLOSED`, the only safe next step is to manually
investigate the specific reason given; do not force the commit/push through.
