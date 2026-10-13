# Operator command reference — AFS

**Purpose:** Copy/paste commands for checking the repository and existing forward evidence. Use the **smallest relevant section**, not all commands every day. This is a convenience index, **not** a new source of strategy status, experiment outcomes, runtime authority, or approval.

- **Mac** = run in a terminal on your Mac, inside the `autonomous-futures-system` Git checkout unless stated otherwise.
- **GitHub** = run on a computer with an authenticated GitHub CLI (`gh`). These calls do not require the Mac repo checkout.
- **VPS** = run **only** inside an already authorized read-only shell/account with access to the named paths. If permission is denied, **stop**; do not switch users, broaden SSH/sudo permissions, or attempt another account.
- `READ ONLY` = no intentional file/service/branch mutation. `REQUIRES OPERATOR GO` = no ready-to-run mutation command is included here.
- **Do not paste** credentials, the contents of any `.env`, SSH material, raw broker/account identifiers, or potentially sensitive unredacted logs into GitHub/chat.

Authority: `AGENTS.md`; current operator decisions: `docs/futures-operator-todo.md`; strategy status: `docs/strategy-rules/Strategy_Inventory.md`; trial outcomes: `docs/research-trial-ledger.jsonl`. The existing `RUNBOOK.md` contains longer procedures.

## 1. Mac — what changed locally? (READ ONLY)

Run from the **existing repo checkout**, not an immutable VPS release:

```bash
git rev-parse --show-toplevel
git branch --show-current
git status --short
git status -sb
git log -3 --oneline
git branch -vv
git worktree list --porcelain
git stash list
```

**Interpretation:** A clean working tree does not prove every linked worktree is clean; inspect each separately before any cleanup. The SSH-material stash is **PRESERVE**. Never auto-pop, apply, drop, prune, reset, or clean it. These commands also do not update remote tracking refs.

For the existing agent's *read-only* branch/worktree classification, invoke `/repo-hygiene-check` **inside Claude Code**, not in Terminal. Its source is `.claude/commands/repo-hygiene-check.md`; the mirrored agent skill is `.agents/skills/source-command-repo-hygiene-check/SKILL.md`. Do not run it again if nothing material changed.

## 2. GitHub — current source, PRs, CI, security gate (READ ONLY)

Requires `gh` authenticated for `imanisworld/autonomous-futures-system`. A failing/pending check or an unavailable command is a finding; it is not approval to repair anything automatically.

```bash
gh api repos/imanisworld/autonomous-futures-system/branches/main --jq '.commit.sha'
gh pr list -R imanisworld/autonomous-futures-system --state open --limit 100
gh pr view 1226 -R imanisworld/autonomous-futures-system --json state,isDraft,headRefOid,mergeable,url
gh pr checks 1226 -R imanisworld/autonomous-futures-system
gh issue view 1184 -R imanisworld/autonomous-futures-system --json state,title,url
```

To check CI **at the exact current `main` SHA** (rather than relying on a badge):

```bash
MAIN_SHA="$(gh api repos/imanisworld/autonomous-futures-system/branches/main --jq '.commit.sha')"
printf 'main=%s\n' "$MAIN_SHA"
gh api "repos/imanisworld/autonomous-futures-system/commits/$MAIN_SHA/check-runs" --jq '.check_runs[] | [.name, .status, .conclusion] | @tsv'
```

For a remote branch tip without fetching or deleting branches:

```bash
git ls-remote origin refs/heads/main
git ls-remote origin refs/heads/ops/post-cap-shadow-report-20261005
```

These two `git ls-remote` commands run **from the local repo checkout** and do not refresh local tracking refs. PR closure or diverged ancestry alone is not safe-delete evidence.

## 3. VPS — release identity and service (AUTHORIZED READ ONLY)

First confirm you are in the **approved VPS read-only context**; never assume your Mac checkout proves the active box.

```bash
readlink -f /root/autonomous-futures-system
systemctl is-active futures-bot
systemctl show futures-bot -p ActiveState -p WorkingDirectory -p NRestarts -p ExecMainStartTimestamp --no-pager
TZ=America/New_York date '+%Y-%m-%d %H:%M:%S %Z %z'
```

**Current integrity HOLD:** A 2026-10-13 read-only report flagged an unexpected Python cache file in immutable release `94a71bd…`. The following commands inspect the currently selected release and that file **only** if the approved account allows it:

```bash
release_dir="$(readlink -f /root/autonomous-futures-system)"
printf 'release=%s\n' "$release_dir"
stat -c '%n | %F | %s bytes | %U:%G | %y' "$release_dir/scripts/__pycache__/options_122_prospective_collect.cpython-313.pyc"
sha256sum "$release_dir/scripts/__pycache__/options_122_prospective_collect.cpython-313.pyc"
```

File absence, a permission error, or a changed symlink requires a fresh, authorized integrity audit; **none by itself establishes integrity PASS**. Do **not** remove the file or edit anything under `/root/afs-releases`. Any recovery must follow the sanctioned immutable-release/deploy-lock process under separate operator approval. The source-of-truth blocker is in `docs/futures-operator-todo.md`.

## 4. VPS — six-micro / Polygon evidence before October 18 (AUTHORIZED READ ONLY)

The six-micro forward trial's first eligible session was **2026-10-12**. Check timer/service **and** journal; an enabled timer or a present state file does not prove scored, completed round-turns.

```bash
systemctl is-enabled six-micro-daily-tsmom.timer
systemctl list-timers --all --no-pager six-micro-daily-tsmom.timer
systemctl show six-micro-daily-tsmom.service -p Result -p ExecMainStatus -p ExecMainStartTimestamp -p ExecMainExitTimestamp --no-pager
find /root/afs-shared/logs/six-micro-daily-tsmom -maxdepth 2 -type f \( -name '*.jsonl' -o -name 'state.json' \) -printf '%TY-%Tm-%Td %TH:%TM %s %p\n' | sort
find /root/afs-shared/logs/six-micro-daily-tsmom -maxdepth 2 -type f -name '*.jsonl' -exec wc -l {} +
```

To check data content, an authorized reviewer must independently count valid eligible sessions and round-turns, detect `FRONT_MISMATCH` / roll-price gaps, and preserve chronology. Raw row counts do **not** establish trial success or failure. **Retiring a timer and canceling Polygon are separate operator decisions.**

## 5. VPS — Track B collection vs. zero fills (AUTHORIZED READ ONLY)

In the **existing reviewed checkout** with the matching repository script present, run the current read-only census. `-B` disables Python bytecode writing; the census itself has no write path:

```bash
python3 -B ops/collector_census.py --log-dir /root/afs-shared/logs
```

Relevant existing paths (some lane files are created **only when a candidate exists**):

```bash
find /root/afs-shared/logs/hypothetical_ledger/wide_stop_4k -maxdepth 1 -type f -printf '%TY-%Tm-%Td %TH:%TM %s %p\n' | sort
find /root/afs-shared/logs/tf5m -maxdepth 1 -type f -name 'bars_MNQ_2026-10-*.jsonl' -printf '%TY-%Tm-%Td %TH:%TM %s %p\n' | sort
```

**Interpretation:** `wide_stop_4k` candidate/forward state is **event-driven**, not a per-bar heartbeat. A stale state or zero fills alone does not mean the feed is dead. Compare the 5-minute MNQ bar stream, service/journal coverage, and registered October 12 onward eligibility before making a collection claim. A missing directory is a finding, not permission to create or backfill it.

Optional **stdout-only** existing forward-lane proof (its *default* behavior appends a proof journal, so **always** use `--no-log` for a read-only check):

```bash
python3 -B ops/forward_lanes_daily_proof.py --no-log
```

This proof script reports timer/health/state basics; it **does not** establish that Track B has complete eligible sessions or that six-micro has scored 40 trades. If it is absent in the reviewed checkout, do not install or copy an unreviewed version onto the VPS.

## 6. What is deliberately not a copy/paste command

| Task | Status | Authority / next step |
|---|---|---|
| Repair unexpected release file, rollback, or promote | **REQUIRES OPERATOR GO** | `AGENTS.md` and `docs/futures-operator-todo.md`; immutable-release path with fresh before/after proof |
| Stop/disable six-micro timer; remove its env; cancel Polygon | **REQUIRES OPERATOR GO** | Read-only journal census and trial disposition first; `docs/repo-hygiene-audit-2026-10-13.md` |
| Merge/close PRs or change security issue #1184 | **REQUIRES OPERATOR GO** | Independent exact-head review and unresolved security gate |
| Delete branches/worktrees/ignored data; apply/drop stash | **REQUIRES OPERATOR GO** | `docs/BRANCH_ARCHIVE_INDEX.md` and `docs/repo-hygiene-deferred-removal-tracker-2026-10-10.md`; preserve unique content and SSH stash |
| Run or rerun a trading experiment; modify broker/risk/execution | **REQUIRES SEPARATE APPROVED SPEC / GO** | `docs/research-trial-ledger.jsonl`, Strategy Inventory and `AGENTS.md` |

**When sharing results:** send the date/time with timezone, machine/context (Mac / GitHub / approved VPS), exact commit or release, the commands actually run, **redacted** results, and missing/denied checks. No proof, no run.
