# Agent Work State

> **Purpose:** durable resume/checkpoint ledger for Grok and other agents so work survives quota exhaustion, outages, context loss, and multi-day gaps without being recreated.
>
> This file is **not** strategy-status authority, deployment authority, or experiment authority. Authoritative records named in `AGENTS.md` always win.
>
> **Checkpoint base:** repository `main` `7d09c62f49ac1e5bf907e84f21e6da78be97c299` immediately before the post-#1081 checkpoint refresh. Always fetch current `main`; this stored SHA is a comparison base, not a perpetual current-state claim.
>
> Core rule: **checkpoint first; diff first; do not redo proven work.**

## Mandatory startup gate

After any outage, quota reset, context loss, reconnect, or multi-day gap, use the cheapest safe resume path:

1. Read `AGENTS.md` and this file.
2. Fetch only the current `main` SHA.
3. Read the authoritative record for the requested lane.
4. If this checkpoint names an active branch/PR for the task, inspect that exact branch/PR.
5. Compare those identifiers to this checkpoint and classify the result:
   - **UNCHANGED:** resume from **NEXT** immediately. Do not re-audit completed work.
   - **SCOPED DRIFT:** inspect only the changed diff/files/PRs needed to understand that drift.
   - **INSUFFICIENT STATE:** fetch the next-smallest source needed to resolve the unknown.
   - **SAFETY-CRITICAL DRIFT:** verify the exact runtime facts required by the proposed action before mutation.
6. Inspect broader related PRs/branches/issues only if the scoped check shows they are relevant.
7. Perform a full repository/runtime reconciliation only if the checkpoint is missing, contradictory, materially stale, cannot identify the active work, conflicts with an authoritative record, or scoped evidence cannot resolve a safety-critical question.

A returning session is therefore **RESUME FIRST, ESCALATE ONLY AS NEEDED** — not a mandatory full reconciliation.

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
- **Docs closeout:** PR #1080 and agent-resume/governance PR #1081 merged; PR #1083 then refreshed this checkpoint metadata. The stored repo SHA above is the pre-refresh comparison base. Fetch current `main` instead of inferring it from this file.
- **Gate-condition #1068:** decision already made — deferred until the next sanctioned futures release. Do not force it into the immutable live release or rewrite cron solely to pick it up.
- **#994:** WAIT. Stage B was not earned. Do not run the study unless a new explicit research decision changes that.
- **Secret discovery for #1037:** closed. Do not repeat secret scanning merely to reconstruct context.
- **SSH hardening:** not authorized yet. Do not disable password authentication, remove current access, or convert access paths until replacement/recovery proofs exist.
- **Branch cleanup:** no branch was deleted in the closeout because delete proof was absent. Do not mass-delete old release/hold/archive/research branches.

### CURRENT PRESERVE

- **Runtime freshness boundary:** the runtime facts below are the last verified 2026-09-30 ET checkpoint, not perpetual current-state claims. Before any runtime/broker/access mutation, obtain the smallest fresh read-only proof required. Later external monitor/agent messages do not supersede this checkpoint unless independently verified.
- Futures trading service: last verified pinned release `75f10e4540aa1f25b51b77c1ec2a2da40381a188`, Tradovate DEMO, live trading disabled.
- At the last verified 2026-09-30 ET checkpoint, paper/shadow evidence collection was continuing under frozen contracts.
- Last verified reporter rollback pin: `releases/b60931a6a9f8-reporter-6512dc3578e9`.
- Preserve `candidate/tradovate-auth-only-41ae188` and `fix/watcher-resolve-only-when-cleared` unless fresh containment/deletion proof is produced.

### NEXT — operator window, in order

1. Prove phone SSH login.
2. Prove Hetzner/provider-console recovery.
3. Run candidate build/verify and a rollback drill in an explicit operator window.
4. Only after 1–3 pass, evaluate SSH hardening and old-access removal.
5. Carry gate-condition #1068 into the next sanctioned futures release.
6. Resolve remaining #1037 owner decisions: main protection/ruleset governance, historical operational exposure, retain-history vs fresh-root decision, public branch/ref cleanup, old VPS-address check, clean export.
7. Continue ordinary paper/shadow/guarded-DEMO collection. Losses alone do not trigger strategy changes.

### OPTIONS CHECKPOINT — resume, do not rebuild

Options resume is governed by the same checkpoint-first / diff-first rule.

- **Current-state authority:** `docs/options-current-state-handoff.md`. Do not create a competing options status file.
- **PR #1073** (`docs/options-state-todo-20260929`, exact head `0232c187e601b87c286af0ad70c5dd47acac3a58`) is already the dedicated options state/TODO refresh. It changes only `docs/options-current-state-handoff.md` and `docs/options-next-actions.md`. It is open/draft. Do **not** recreate that checklist in this PR or another branch; continue/review #1073 itself.
- **PR #1077** (`claude/options-signa-validation-v2`, exact head `33e5d6dc33f8527de6bb72380793938cf288413b`) is already the active Signa-v2 observation/evidence change. It is open/draft and requires QA/review before merge. Do **not** start a second Signa-v2 implementation.
- Options production posture recorded by #1073 remains advisory/read-only with the existing frozen evidence lanes; repository changes are not runtime proof.
- #1073 records three separate real-data gates rather than permission to deploy: the #1071 real SQLite run, #1067 real 66-symbol five-minute capacity proof, and #1069 real SPX/SPXW provider proof with that lane OFF. A returning agent should verify which of those gates, if any, changed before doing work.
- If #1073 merges, use its `docs/options-next-actions.md` as the options task checklist and update this checkpoint to the merged SHA. If it does not merge, inspect only its current diff/state rather than reconstructing the options backlog from older docs.

### OPEN / NEEDS DECISION — verify before touching

- PR #1073 / branch `docs/options-state-todo-20260929`: continue/review existing work; do not duplicate it.
- PR #1077 / branch `claude/options-signa-validation-v2`: QA/review existing work; do not duplicate it.
- Closed-unmerged agent-safety doc branches associated with PR #1028 and #1036: verify whether their content is superseded before recreating anything.
- Forward evidence: `orb_reclaim` was stale and `vwap_rejection` quiet in the latest read-only check. This does not block current collection, but future summaries must not imply continuous evidence for those arms.

## Resume rule

A returning agent must not ask, “What should I redo?” It must answer:

1. Did the checkpoint identifiers actually change?
2. If yes, what changed in the smallest relevant scope?
3. Which items remain genuinely unresolved?
4. What is the smallest safe next action?
5. What additional information, if any, is specifically required before that action?

If more information is needed, retrieve only that information first. Broaden the search only when the narrower evidence is insufficient.

If nothing changed and no operator-window task is authorized: **do nothing and keep collecting.**
