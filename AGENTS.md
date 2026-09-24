# AGENTS.md

## Purpose

This repository is an autonomous futures trading system. Agent work must preserve execution safety, auditability, reproducibility, and paper-first operation.

The system is allowed to miss trades. It is not allowed to take unverified trades.

## Core Rule

**No proof, no run.**

Missing data blocks validation.  
Unclear signals block execution.  
Conflicting logic blocks deployment.  
Unverified backtests block trust.

## Default Operating Mode

Unless the user explicitly authorizes otherwise:

- paper trading only
- advisory / monitor mode preferred
- no live broker execution
- no hidden execution routes
- no deployment
- no production or VPS mutation
- no broker-state mutation
- no feature expansion without proving the need

The running futures bot uses a Tradovate demo lane (`PAPER_MODE=false`, `BROKER=tradovate`, `TRADOVATE_ENV=demo`, `LIVE_TRADING_ENABLED=false`, 1 contract). That lane is demo, not live. Do not treat its existence as a violation of this section or as permission to expand non-paper routing or move toward live. Live broker execution remains forbidden without explicit Operator authorization.

## Allowed Agent Actions

Agents may, when scoped to the assigned task:

- inspect repository code and history
- create a branch
- edit bounded files
- add or update tests
- run unit/integration tests that do not require unsafe external actions
- run linting, type checks, static analysis, replay tests, and local validation
- produce diffs, commits, and pull requests
- report evidence, blockers, uncertainty, and unresolved risks

## Forbidden Without Explicit Authorization

Do not:

- merge pull requests
- deploy
- SSH to or mutate the VPS
- restart or stop production services
- change production environment variables
- add or rotate deployment credentials
- connect to or mutate broker accounts
- submit, modify, cancel, or flatten orders or positions
- enable live execution
- create unsafe fallbacks
- bypass risk checks
- weaken safety gates to make tests pass
- rewrite unrelated code
- broaden task scope without approval
- treat a green command as proof when the command itself is known to have incomplete safety semantics

If an assigned task appears to require any forbidden action, stop and report exactly what is required.

## Broker and Execution Safety

Before approving any futures-system change, verify:

### Execution route

- Is this paper-only?
- Can it accidentally hit live?
- Is there a broker adapter involved?
- Is exact ticker routing enforced?
- Are paper/live modes visible and explicit?

### Signal validity

- What strategy produced the signal?
- Is trend logic unified?
- Are live and replay using the same formulas?
- Is the signal reproducible?
- Is there any lookahead or future-data dependency?

### Risk controls

Verify, where applicable:

- max contracts
- max trades per day
- daily loss limit
- per-trade stop
- session lockout
- news/session filters
- open-position limits
- bracket completeness
- instrument allowlist
- exact contract routing

Default futures posture:

- max trades per day is the value in `risk_rules.yaml` (`daily_limits.max_trades_per_day`) as loaded by `config/settings.py` (`load_config`); if this document and that configuration disagree, the configuration governs
- no averaging down
- no revenge trades
- no trades without a stop
- no trades during unclear chop
- no trades when system state disagrees with chart state
- no trades when replay/live formulas diverge

## Fill and Replay Realism

Do not trust optimistic fills.

Replay and paper execution must account for realistic execution behavior.

Required principles:

- no target-priority fills on same-bar stop/target straddles
- when price path is unknowable, use pessimistic stop-first handling
- include slippage and commissions where the test requires them
- replay must not inflate win rate
- live and replay entry-fill semantics must match when parity is claimed

## Strategy Validation

Classify strategy evidence as one of:

- VALIDATED
- PROMISING BUT UNPROVEN
- BROKEN
- OVERFIT
- UNSAFE
- WAIT

Do not call a strategy VALIDATED unless evidence supports all relevant conditions:

- multiple months or an otherwise justified independent sample
- realistic fills
- identical live/replay logic
- clear invalidation
- no lookahead bias
- session filters respected
- sufficient sample size
- controlled drawdown
- reproducible results

One-day performance, paper wins, or one timeframe alone are not validation.

## Testing Requirements

Before claiming a code change is complete:

1. Identify the exact behavior being changed.
2. Add or run the smallest relevant regression test.
3. Run the relevant existing test subset.
4. Before any merge, the full test suite (CI) must be green on the final head. Never report a hand-picked subset as "tests pass". When CI cannot run, say so explicitly; a local subset is not that proof.
5. Report exact commands and results.
6. Distinguish:
   - verified
   - likely
   - unverified
   - contradicted

A worker agent's self-report is not independent verification for safety-critical changes.

## Branch and Pull Request Rules

Default workflow:

1. start from current `main`
2. create a fresh task-specific branch
3. make only bounded changes
4. run relevant tests
5. inspect the diff for unrelated edits
6. open a PR
7. do not merge unless explicitly authorized

Do not revive or reuse contaminated branches when a clean branch is safer.

## Scope Discipline

Do not:

- build new features unless requested
- rewrite the app
- "improve" code without proving something is broken
- refactor unrelated files during a defect fix
- expand instruments without evidence

Preferred futures instruments:

MNQ, MES, M2K, MGC, MBT, and MCL already run. No new instrument without evidence.

## Monitoring and Evidence

When relevant, verify that:

- logs exist
- journal records signal, state, reason, entry, stop, target, and outcome
- status endpoints work
- notifications are clear
- "why no trade" is visible
- runtime state is reconcilable with journal/broker state

Do not treat missing telemetry as a harmless gap when it blocks proof.

## Secrets and External Services

Do not request or add broker, VPS, production, webhook, or deployment secrets during ordinary development/test setup.

Only use secrets proven necessary for the assigned task, and never print credentials, API keys, passwords, tokens, or secret-derived values.

## Agent Delegation

When delegation is available, use the most appropriate capable worker rather than making the user manually relay tasks.

The originating manager/lead agent remains responsible for:

- preserving requirements and constraints
- passing bounded acceptance criteria
- reviewing returned evidence
- identifying unresolved issues
- requiring independent QA where consequential
- preventing unverified completion claims

Delegation never bypasses this file's safety rules.

## Required Review Format

For audits or consequential changes, report:

### Verdict

APPROVE / REJECT / HOLD / AUDIT ONLY / PAPER ONLY

### Why

2-5 decisive reasons.

### What I Verified

- files reviewed
- logic checked
- safety gates checked
- execution path checked

### Problems Found

Separate blockers from minor cleanup.

### Required Fixes

- must-fix before run
- should-fix later
- do-not-touch items

### Safe Next Step

The smallest safe action. No broad rewrites.

## Stop Conditions

Stop and report instead of proceeding when:

- required data is missing
- execution route is ambiguous
- paper/live state is unclear
- live and replay logic disagree
- broker account routing is ambiguous
- a test result cannot be reproduced
- a safety gate is known to be unreliable
- a requested action exceeds authorization
- the diff includes unrelated behavior changes

When uncertain, choose the safer non-executing path.
