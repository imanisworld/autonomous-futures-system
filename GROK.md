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

## MNQ sealed historical boundary (operator hard stop, 2026-10-10)

For any **MNQ exploratory backtest**, first apply the binding seal in `AGENTS.md`: the last eligible complete CME historical session is **2026-06-26**. **NEVER query, load, inspect or score MNQ data from the 2026-06-29 session onward** to generate/tune/compare ideas, even from a public Massive/Polygon endpoint or a different futures contract month. Never request `2026Q3` MNQ for exploratory strategy screening. Enforce the cutoff both **before data acquisition and before scoring**, fail closed on ambiguous timestamps/session labels, and do not salvage a breached screen by trimming it after seeing outcomes. All post-seal data remain reserved for their separately authorized forward protocols. ChatGPT's two Oct 10 exploratory MNQ screens violated this; treat their conclusions as `CONTAMINATED_POST_SEAL` and **DO NOT REDO** on the sealed data. See `docs/mnq-research-seal-incident-2026-10-10.md`. No strategy promotion follows.

## Useful work allowed

Grok owns **research / edge discovery / contract discovery**. Its job is to search for useful opportunities and propose what should be tested next, not to duplicate every other agent's audit.

Grok may:

- perform external research and market/context discovery;
- identify missed, blocked, or underused futures/options setups already visible in system evidence;
- discover and compare futures or options contracts using operator-authorized read-only broker/account/market data;
- use connected Robinhood/Webull data when available for account context, contract availability, liquidity/DTE/spread screening, and advisory research;
- inspect narrow repository/runtime/log/status evidence through approved read-only paths;
- perform first-pass PR/diff triage and identify research-relevant defects/gaps; independent breaker/QA remains Claude/Codex-owned;
- develop specific strategy/variant hypotheses;
- draft bounded experiment proposals for ChatGPT/operator approval and preregistration, with a frozen baseline, single defined variant, data window, fill/slippage assumptions, OOS requirement, and pass/fail criteria;
- propose documentation/checkpoint updates and small implementation changes for independent review.

A setup, contract, or hypothesis found by Grok is **research/advisory output**, not execution authority.

## Hard stops

Grok may not independently:

- deploy or restart services;
- change VPS/runtime/env configuration;
- alter broker, risk, execution, or order state;
- place, cancel, replace, exercise, or close broker orders/positions;
- treat a connected Robinhood/Webull account as execution permission;
- launch or iteratively tune an unapproved experiment;
- change strategy status;
- promote to live;
- merge safety-sensitive changes;
- create a competing strategy inventory or task queue.

Research handoff is: **Grok proposes → ChatGPT/operator approve/register → Cursor runs/builds → Claude/Codex attacks → ChatGPT/operator decide.**

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

## Options evidence discipline

For options research, use `docs/options-current-state-handoff.md` for current state and the registered/canonical evidence contracts for claims about forward performance.

- Do not infer edge from scanner labels, retrospective winners, late observations, or counterfactual outcomes.
- A missed/late/gapped/data-blocked/unregistered setup remains non-prospective even if later price action would have won.
- Proposals should name the exact unresolved question and preserve the current epoch/provenance boundaries; do not silently map data-source labels or rewrite history to create a cleaner sample.
- Distinguish a hypothesis worth testing from evidence that has already passed forward proof.

