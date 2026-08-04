# Independent Audit Protocol

**Status:** BINDING for all independent audit passes.
**Applies to:** Any AI auditor (Claude, Codex, or other) running an audit of this system.
**Purpose:** Ensure Claude and Codex audit independently — neither anchors on the other's conclusions, and neither alters the repository state the other is auditing.

This audit will be run independently in Claude and Codex.

Complete your first-pass audit without reading, relying on, or attempting to match the other auditor's conclusions. Do not treat previous AI summaries, handoff documents, comments, PR descriptions, or strategy-status tables as proof. Verify material claims against source code, tests, repository history, deployment state, logs, broker state, and preserved evidence.

## Repository safety

- Operate read-only.
- Do not edit, format, generate, restore, delete, rename, commit, push, merge, deploy, restart, or reconfigure anything.
- Do not create audit fixes during this pass.
- Do not switch or reset branches if doing so could disturb another worktree or active process.
- Prefer a separate clean worktree or clone pinned to the audited commit.
- Do not run commands that could place, cancel, replace, or reconcile broker orders.
- Do not run webhooks against production or broker-connected endpoints.
- Do not expose, print, copy, or modify secrets.
- Do not install dependencies or change lockfiles without explicit authorization.
- Do not start persistent services, schedulers, workers, monitors, tunnels, or background processes.

## Establish the exact audit target first

Before evaluating the system, record:

- Repository path
- Current branch
- Local `HEAD`
- Remote branch SHA
- Dirty or untracked files
- Open PR head SHAs
- Deployed SHA, if provable
- Runtime host or environment inspected
- Broker environment
- Paper/demo/live flags
- Date and time of the evidence

If the repository, deployment, and runtime are on different commits, audit them separately. Do not combine findings from different versions into one system verdict.

## Evidence standard

For every material finding, provide:

- Claim
- Classification
- Exact evidence
- File and line numbers, command output, test, log entry, commit, PR, endpoint, or runtime record
- Evidence date
- Whether the evidence applies to source, replay, shadow, paper runtime, deployed runtime, or broker state
- Confidence level: PROVEN, PARTIALLY PROVEN, UNKNOWN, or CONTRADICTED

Use UNKNOWN when evidence is unavailable. Never convert missing evidence into a passing result.

A passing unit test proves only the behavior covered by that test. It does not prove deployment state, broker behavior, data quality, strategy profitability, or end-to-end safety.

## Command record

Include a concise audit command log showing:

- Commands executed
- Relevant exit codes
- Tests selected
- Tests skipped
- External systems queried
- Anything that could not be inspected

Do not claim "all tests pass" unless the complete relevant suite was executed successfully against the exact audited commit.

## Shadow-system requirements

Do not infer the shadow system from component names or documentation. Trace the complete path:

`market data → signal generation → permission gates → trade intent → suppression or approval → simulated execution → journal/log/status output`

Identify:

- Every entry point
- Scheduler or webhook trigger
- Strategy registry
- Instrument and session filters
- Data source and timeframe
- State persistence
- Deduplication
- Risk checks
- Order suppression point
- Output stores
- Status endpoints
- Notification path
- Restart behavior
- Failure behavior

Determine whether "shadow" means:

- Signal observation only
- Simulated fills
- Paper broker submission
- Live-compatible code with submission disabled
- A mixture of these modes

Search for alternate or hidden execution routes, including direct broker-client calls, maintenance scripts, manual endpoints, CLI commands, scheduled jobs, legacy adapters, test utilities, and fallback paths.

Reconcile shadow activity across:

- Signals produced
- Trade intents journaled
- Orders attempted
- Orders suppressed
- Simulated or broker fills
- Outcomes recorded
- Notifications sent

Counts that cannot reconcile must be reported as a blocker.

## Replay and fill validation

Verify that replay, shadow, and runtime share identical formulas and configuration for:

- Indicators
- Trend classification
- Session boundaries
- Signal timing
- Bar completion
- Entry eligibility
- Stop calculation
- Target calculation
- Risk/reward
- Tick rounding
- Slippage
- Commissions
- Same-bar stop/target collisions
- Carry-forward behavior
- Missing bars
- Contract rollover
- Time zones and daylight-saving transitions

Trace the implementation. Shared names or similar formulas are not sufficient proof of parity.

## Contradiction ledger

Create a dedicated table for conflicts between:

- Documentation and code
- Tests and runtime behavior
- Replay and live/shadow formulas
- Local and deployed commits
- Strategy classifications and current evidence
- Journal counts and broker records
- Status endpoints and underlying stores
- Environment flags and actual execution routes
- Claude and Codex findings after both independent audits are complete

Do not resolve contradictions by choosing the more optimistic source. Mark them as blockers until independently resolved.

## Removal standard

Do not recommend deletion merely because code or evidence appears unused.

Classify each removal candidate as:

- SAFE TO DELETE
- ARCHIVE
- DEPRECATE
- RETAIN
- UNKNOWN

Before marking anything SAFE TO DELETE, prove that it is:

- Not referenced
- Not deployed
- Not required for replay or historical reproducibility
- Not the only copy of evidence
- Not used by another branch, worktree, script, deployment, or scheduled process
- Free of unique strategy or incident history

## Final prioritization

Divide recommendations into:

1. Immediate safety blockers
2. Required before further paper execution
3. Required before strategy validation
4. Monitoring and auditability gaps
5. Documentation or cleanup
6. Optional improvements

For every proposed change, include:

- Exact problem it solves
- Evidence that the problem exists
- Smallest safe change
- Files or components affected
- Required regression tests
- Deployment risk
- Rollback method
- Whether the change affects shared strategy or execution logic

Do not propose broad rewrites where a narrow correction is sufficient.

End with:

- The five highest-risk findings
- The five highest-value next actions
- A list of claims still unproven
- A list of components that must not be touched
- The single smallest safe next step

No implementation is authorized by this audit.

## Run isolation and reconciliation

Run Claude and Codex against separate clean worktrees pinned to the same SHA. Do not show either model the other model's report until both first passes are finished. Then use a third reconciliation pass to compare contradictions rather than simply combining the reports.
