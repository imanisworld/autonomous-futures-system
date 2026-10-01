# Agent Work State

> **Purpose:** durable resume/checkpoint ledger for Grok and other agents so work survives quota exhaustion, outages, context loss, and multi-day gaps without being recreated.
>
> This file is **not** strategy-status authority, deployment authority, or experiment authority. Authoritative records named in `AGENTS.md` always win.
>
> **Seeded from:** repository `main` `3737d532919859960bdefe8b11a611da785327ae` plus the 2026-09-30 ET operator closeout.
>
> Core rule: **reconcile first; do not redo proven work.**

## Mandatory startup gate

After any outage, quota reset, context loss, reconnect, or multi-day gap:

1. Read `AGENTS.md` and this file.
2. Read the authoritative record for the lane being touched.
3. Fetch current `main`.
4. Inspect related open PRs/branches/issues before creating anything.
5. If runtime matters, verify the box separately; do not infer runtime from GitHub.
6. Compare the requested task to **DONE / DO NOT REDO / NEXT / BLOCKED** below.
7. First returning session after a multi-day outage is **RECONCILIATION ONLY**. No coding, deployment, cleanup, new research, or runtime mutation until this ledger is reconciled with current evidence.

## Quota / context guard

When the platform exposes quota/usage:

- At roughly **70–75% used** or **25–30% remaining**, start no new workstream.
- Finish only the current atomic step.
- Verify the result.
- Write the checkpoint below.
- Stop before a hard provider limit destroys the handoff.

Any rate-limit, context-limit, or usage warning triggers the same behavior immediately.

When no quota meter is available, checkpoint after every completed atomic task and before every new independent workstream.

## Required checkpoint payload

Every substantial unit of work must leave:

- timestamp / session context;
- task and scope;
- what was verified;
- what changed;
- repository branch / PR / exact SHA;
- tests/checks and exact result;
- runtime mutations, if any (normally none);
- blockers / unknowns;
- **DONE** items;
- **DO NOT REDO** items;
- exact **NEXT** action.

If the agent cannot persist this file, it must return this payload verbatim-ready for the next agent/operator to save.

## Current checkpoint — 2026-09-30 ET

### DONE / DO NOT REDO

- **Paper reporter:** installed and verified; active pin `releases/b60931a6a9f8-reporter-1068-sixmarket-backport`. Do not rebuild merely because Grok returns.
- **Paper reporter smoke:** frozen-input comparison already passed; another Discord smoke is not required just to resume context.
- **Futures runtime tonight:** read-only health result PASS. Keep collecting; no strategy/config/deploy response to losses.
- **Docs closeout:** PR #1080 merged. Main at the seed checkpoint is `3737d532919859960bdefe8b11a611da785327ae`.
- **Gate-condition #1068:** decision already made — deferred until the next sanctioned futures release. Do not force it into the immutable live release or rewrite cron solely to pick it up.
- **#994:** WAIT. Stage B was not earned. Do not run the study unless a new explicit research decision changes that.
- **Secret discovery for #1037:** closed. Do not repeat secret scanning merely to reconstruct context.
- **SSH hardening:** not authorized yet. Do not disable password authentication, remove current access, or convert access paths until replacement/recovery proofs exist.
- **Branch cleanup:** no branch was deleted in the closeout because delete proof was absent. Do not mass-delete old release/hold/archive/research branches.

### CURRENT PRESERVE

- Futures trading service: pinned release `75f10e4540aa1f25b51b77c1ec2a2da40381a188`, Tradovate DEMO, live trading disabled.
- Paper/shadow evidence collection continues under frozen contracts.
- Reporter rollback pin: `releases/b60931a6a9f8-reporter-6512dc3578e9`.
- Preserve `candidate/tradovate-auth-only-41ae188` and `fix/watcher-resolve-only-when-cleared` unless fresh containment/deletion proof is produced.

### NEXT — operator window, in order

1. Prove phone SSH login.
2. Prove Hetzner/provider-console recovery.
3. Run candidate build/verify and a rollback drill in an explicit operator window.
4. Only after 1–3 pass, evaluate SSH hardening and old-access removal.
5. Carry gate-condition #1068 into the next sanctioned futures release.
6. Resolve remaining #1037 owner decisions: main protection/ruleset governance, historical operational exposure, retain-history vs fresh-root decision, public branch/ref cleanup, old VPS-address check, clean export.
7. Continue ordinary paper/shadow/guarded-DEMO collection. Losses alone do not trigger strategy changes.

### OPEN / NEEDS DECISION — verify before touching

- PR #1073 / branch `docs/options-state-todo-20260929`.
- PR #1077 / branch `claude/options-signa-validation-v2`.
- Closed-unmerged agent-safety doc branches associated with PR #1028 and #1036: verify whether their content is superseded before recreating anything.
- Forward evidence: `orb_reclaim` was stale and `vwap_rejection` quiet in the latest read-only check. This does not block current collection, but future summaries must not imply continuous evidence for those arms.

## Resume rule

A returning agent must not ask, “What should I redo?” It must answer:

1. What changed since this checkpoint?
2. Which items remain genuinely unresolved?
3. What is the smallest safe next action?

If nothing changed and no operator-window task is authorized: **do nothing and keep collecting.**
