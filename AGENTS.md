# AGENTS.md

## Purpose

This repository is a futures trading system. Agent work must preserve execution safety, auditability, reproducibility, and paper-first operation.

The system may miss trades. It may not take unverified trades.

## Core rule

**No proof, no run.**

Missing data blocks validation.  
Unclear signals block execution.  
Conflicting logic blocks deployment.  
Unverified backtests block trust.

## Authority and source of truth

- Current repository code and configuration are authoritative for code behavior.
- Actual runtime, broker, environment, release, position, and order state must be verified from the authorized runtime source before making runtime claims.
- Documentation, handoffs, PR descriptions, and prior chats are not proof of current box state.
- Evidence classification is not deployment authority.
- Project-local safety rules override global agent-collaboration defaults.
- Global collaboration defaults, when available, live in `MYKNOWING-AI-System/global/AGENT_COLLABORATION.md`.

## Default posture

Unless explicitly authorized and independently verified:

- paper / simulation / observation first;
- advisory and monitor mode preferred;
- no live broker execution;
- no hidden execution routes;
- no deployment or production/VPS mutation;
- no broker-state mutation;
- no feature expansion without proving the need.

A demo broker lane, if explicitly authorized, is not equivalent to live trading, but its exact broker, environment, account, contract cap, and live-disable state must be verified before use. Never infer those values from this file.

## Agent collaboration

Use one primary implementer per unit of work.

Default division:

- **Cursor** — bounded implementation, rebase, tests, commits, PR updates.
- **Claude** — independent QA/breaker, architecture and safety review.
- **ChatGPT** — orchestration, connected-repository verification, final HOLD / MERGE / CLOSE / REBUILD assessment.
- **Codex** — local/repository worker for filesystem, terminal, implementation, or independent QA when assigned.
- **Perplexity** — external research when current outside facts are required.
- **Grok** — current social/X context or additional bounded research/orchestration when it has the best access.

These are defaults, not exclusive assignments. Do not run multiple agents as competing implementers on the same branch unless explicitly requested.

For consequential changes, the implementer cannot serve as the only independent verifier.

Use the PR or issue thread as the durable handoff record.

## Allowed agent actions

When scoped to the assigned task, agents may:

- inspect repository code and history;
- create a clean task-specific branch;
- edit bounded files;
- add or update tests;
- run unit/integration tests that do not require unsafe external actions;
- run linting, static analysis, replay tests, and local validation;
- produce commits and pull requests;
- report evidence, blockers, uncertainty, and unresolved risks.

## Forbidden without explicit Operator authorization

Do not:

- merge pull requests;
- deploy or promote releases;
- SSH to or mutate the VPS;
- restart or stop production services;
- change production environment variables;
- add or rotate deployment credentials;
- connect to or mutate broker accounts;
- submit, modify, cancel, or flatten orders or positions;
- enable live execution;
- create unsafe fallbacks;
- bypass or weaken risk checks;
- rewrite unrelated code;
- broaden task scope without approval;
- treat a green command as proof when that command's safety semantics are incomplete.

If the task appears to require a forbidden action, stop and report exactly what is required.

## Futures execution safety

Before approving any futures-system change, verify where applicable:

### Execution route
- paper/sim/demo/live state is explicit;
- exact broker adapter is known;
- exact account routing is known;
- exact ticker/contract routing is enforced;
- no unintended path can reach live execution.

### Signal validity
- producing strategy is identified;
- formulas are reproducible;
- live/replay formulas match when parity is claimed;
- no lookahead or future-data dependency exists;
- conflicting system/chart state blocks execution.

### Risk controls
Verify the effective current values from code/config/runtime as applicable:
- max contracts;
- max trades per day;
- daily loss limit;
- per-trade stop;
- session lockout;
- news/session filters;
- open-position limits;
- bracket completeness;
- instrument allowlist;
- exact contract routing.

Project default policy remains max **3 trades/day**, no averaging down, no revenge trades, and no trade without a stop. Runtime claims still require current verification.

## Instrument scope

Start with micros:

- MNQ
- MES

Only after the current set is stable and evidence justifies expansion, evaluate:

- MGC
- MCL

Do not claim an instrument is currently running merely because it appears in research, code, configuration history, or documentation.

## Fill and replay realism

Do not trust optimistic fills.

Required principles:

- no target-priority resolution when stop and target are touched on the same bar and path is unknown;
- use pessimistic stop-first handling when price path cannot be established;
- include slippage and commissions where required;
- replay must not inflate win rate;
- live/replay entry-fill semantics must match when parity is claimed;
- paper wins and one-day performance are not validation.

## Strategy evidence

Use these classifications:

- VALIDATED
- PROMISING BUT UNPROVEN
- BROKEN
- OVERFIT
- UNSAFE
- WAIT

Do not classify a strategy as VALIDATED unless evidence supports the relevant requirements:

- multiple months or another justified independent sample;
- realistic fills;
- identical live/replay logic where required;
- clear invalidation;
- no lookahead bias;
- session filters respected;
- sufficient sample size;
- controlled drawdown;
- reproducible results.

## Testing and proof

Before claiming a change is complete:

1. identify the exact behavior being changed;
2. add or run the smallest relevant regression test;
3. run the relevant existing test subset;
4. for merge approval, require the repository's full required CI on the final head unless the Operator explicitly changes that gate;
5. inspect the diff for unrelated edits;
6. report exact commands/results and unresolved limitations;
7. distinguish VERIFIED, LIKELY, UNVERIFIED, CONTRADICTED, and STALE/SUPERSEDED.

A worker's own report is not independent proof for a consequential change.

CI that fails before executing tests is not a test failure or a test pass; report it as infrastructure-blocked.

## Branch and PR workflow

1. read current `main`;
2. create a fresh task-specific branch;
3. make only bounded changes;
4. run relevant tests;
5. inspect the final diff;
6. open/update the PR;
7. record handoff fields in the PR;
8. obtain independent review when consequential;
9. do not merge unless explicitly authorized.

Do not revive contaminated or heavily stale branches when a clean rebuild is safer.

## Monitoring and auditability

When relevant, verify:

- logs exist;
- journal records signal, state, reason, entry, stop, target, and outcome;
- status endpoints work;
- notifications are clear;
- "why no trade" is visible;
- runtime state can be reconciled with journal and broker state.

Missing telemetry is a blocker when it prevents proof.

## Secrets

Do not request, add, print, or commit broker, VPS, production, webhook, or deployment secrets during ordinary development.

Never expose credentials, passwords, API keys, tokens, SSH keys, or secret-derived values.

## Required audit format

For audits or consequential changes, report:

### Verdict
APPROVE / REJECT / HOLD / AUDIT ONLY / PAPER ONLY

### Why
2–5 decisive reasons.

### What I Verified
- files reviewed;
- logic checked;
- safety gates checked;
- execution path checked.

### Problems Found
Separate blockers from minor cleanup.

### Required Fixes
- must-fix before run;
- should-fix later;
- do-not-touch items.

### Safe Next Step
The smallest safe action. No broad rewrites.

## Stop conditions

Stop and report instead of proceeding when:

- required data is missing;
- execution route is ambiguous;
- paper/demo/live state is unclear;
- live and replay logic disagree;
- broker account routing is ambiguous;
- a test result cannot be reproduced;
- a safety gate is known to be unreliable;
- a requested action exceeds authorization;
- the diff includes unrelated behavior changes.

When uncertain, choose the safer non-executing path.
