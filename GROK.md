# GROK.md

## Startup contract

For repository work, start here. Keep startup cheap.

1. Read `AGENTS.md`.
2. Read `docs/agent-work-state.md`.
3. Read only the authoritative lane record needed for the requested task:
   - futures strategy/status: `docs/strategy-rules/Strategy_Inventory.md`
   - options current state: `docs/options-current-state-handoff.md`
4. Fetch current `main` SHA and compare it with the checkpoint.
5. If the checkpoint and active task identifiers are unchanged, resume from **NEXT**. Do not re-audit completed work.
6. If something changed, inspect only that changed scope. Fetch more context only when the narrower evidence is insufficient.
7. Full repo/runtime reconciliation is an escalation path, not the default startup path.

## Useful work allowed

Grok may perform bounded read-only triage:

- external research and market/context discovery;
- narrow repository inspection;
- PR/diff review;
- read-only log/status analysis through approved access;
- defect/gap identification;
- documentation/checkpoint proposals;
- small implementation proposals for independent review.

## Hard stops

Grok may not independently:

- deploy or restart services;
- change VPS/runtime/env configuration;
- alter broker, risk, execution, or order state;
- launch unapproved experiments;
- change strategy status;
- promote to live;
- merge safety-sensitive changes;
- create a competing strategy inventory or task queue.

## Quota guard

When usage is visible, stop starting new work at roughly 70–75% used (25–30% remaining). Finish the current atomic step, verify it, update the durable checkpoint, and stop.

If usage visibility is unavailable, checkpoint after each completed atomic task and before starting a new independent workstream.

If a usage/rate-limit warning appears, checkpoint immediately.

## Resume question

Do not ask what to redo. Determine:

1. Did checkpoint identifiers change?
2. What changed in the smallest relevant scope?
3. What remains unresolved?
4. What is the smallest safe next action?
5. What exact additional information is required, if any?

If nothing changed and no authorized work is pending: stop. Do not manufacture work.
