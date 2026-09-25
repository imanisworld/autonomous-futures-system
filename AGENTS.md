# AGENTS.md

## Purpose

This repository powers AFSVP futures/options research, paper/demo execution, evidence collection, and controlled releases. Treat changes as production-adjacent even when live trading is disabled.

## Operating contract

- Evidence first. Inspect the current code, config, tests, logs, and runtime state before drawing conclusions.
- No proof, no run. Mark anything not directly verified in the current session as unverified.
- Do not claim a test, deploy, restart, health check, or runtime state succeeded without checking the result.
- Prefer the smallest safe change. Do not broaden scope without evidence that it is required.
- Preserve rollback paths and existing safety gates.
- Never expose, print, commit, or copy production secrets, broker credentials, API keys, private SSH keys, or the contents of `/root/afs-shared/.env`.
- Do not infer production behavior from `main` alone. Verify the deployed exact SHA and runtime state when production behavior matters.

## Repository workflow

- Work from a normal Git checkout/worktree, not from the live release tree.
- Do not edit `/root/autonomous-futures-system` or any directory under `/root/afs-releases` in place.
- Use a branch for code changes. Read the complete relevant diff before commit, push, merge, or deploy.
- Run the relevant tests and repo-specific audit/skill before proposing promotion.
- Existing reusable agent skills live under `.agents/skills/`.
- Claude command equivalents live under `.claude/commands/`.

## Futures safety

Before changes affecting futures execution, risk, broker routing, fills, strategy logic, journals, or deployment:

1. Inspect the relevant current code/config.
2. Run the matching audit/skill where available.
3. Treat any new or broadened broker/order path as high risk.
4. Keep live-trading gates fail-closed unless the operator explicitly authorizes a posture change.
5. Do not place a real test order merely to validate code or connectivity.

## Options safety

- Options changes must preserve the existing human-confirmation and execution restrictions unless explicitly authorized.
- A change that creates or broadens an options broker/order/execution path is a hard stop for independent review.

## Deployment

Deployments are explicit operator-directed actions, never an automatic consequence of finishing code.

- Deploy exact reviewed commit SHAs, never a moving branch name.
- Use the repository's sanctioned controlled-release tooling and deploy lock.
- `scripts/atomic_release.sh` implements the immutable release build/verify/promote flow and must retain its safety gates.
- Do not bypass the deploy lock, release-integrity checks, posture gates, or behavior-neutral gate.
- Do not use `--force-lock` unless the lock has been independently proven stale/abandoned and the operator has authorized breaking it.
- Pre-deploy: verify reviewed SHA, current deployed SHA, lock state, runtime posture, health, and relevant tests.
- Post-deploy: verify the deployed SHA, service health, integrity, expected runtime posture, and that evidence/journal writing still works.
- If verification fails, stop and report the exact failure; do not keep making changes until it "looks fixed."

## VPS access

Agent-specific VPS accounts are intentionally separated. Do not reuse identities across tools.

- Never request or use unrestricted root access when the approved agent account and controlled command path are sufficient.
- Do not weaken SSH restrictions or sudo rules to make an agent task easier.
- Production secrets stay on the VPS; agents should consume redacted status/evidence surfaces or narrowly scoped controlled commands.

## Output standard

For substantial work, report:
- what was verified,
- what changed,
- tests/checks run and their results,
- remaining uncertainty or blockers,
- exact next action when one is required.
