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


## Resume / duplicate-work prevention

This section is mandatory for Grok and every other agent. It does not expand any agent's authority. Grok's compact bootstrap is `GROK.md`; if it conflicts with this file, `AGENTS.md` wins.

- **Checkpoint-first, diff-first.** After an outage, quota reset, context loss, tool reconnect, or multi-day gap, do not perform a repo-wide reconciliation by default.
- Start by reading only `AGENTS.md`, `docs/agent-work-state.md`, the authoritative record for the requested lane, current `main` SHA, and the exact active branch/PR named by the checkpoint when applicable.
- Compare those identifiers to the saved checkpoint. If they are unchanged and the requested task is already **DONE / DO NOT REDO**, resume from the saved **NEXT** item or stop. Do not re-audit unchanged completed work.
- If something changed, inspect only the changed scope first. Expand the audit only as far as the detected drift requires.
- A **full reconciliation** is required only when the checkpoint is missing, internally contradictory, materially stale, conflicts with an authoritative record, cannot identify the active work, or the requested action depends on safety-critical runtime facts that are no longer proven.
- Treat entries marked **DONE** or **DO NOT REDO** in `docs/agent-work-state.md` as closed unless new evidence proves the prior result invalid.
- Before opening a new branch, PR, study, audit, or implementation, search narrowly for an existing equivalent artifact in the relevant scope. Do not scan the whole repository merely to reconstruct context.
- After each meaningful unit of work, checkpoint durable state: what was verified, what changed, branch/PR/SHA, tests/checks, blockers, and the exact next action.
- When the platform exposes a usage/quota indicator, stop starting new work at roughly **70–75% used** (or **25–30% remaining**). Use the remaining budget only to finish the current atomic step, verify it, and write the checkpoint. Any rate-limit/usage warning triggers the same checkpoint behavior.
- If usage/quota visibility is unavailable, checkpoint at every completed atomic task and before starting any new independent workstream. Never rely on end-of-session memory alone.
- If more information is needed, fetch the **smallest additional source** that can answer the unresolved question. Escalate incrementally: checkpoint → exact task file/PR → changed diff → authoritative lane record → runtime proof if required. Do not jump directly to a full repo audit.
- If `docs/agent-work-state.md` conflicts with an authoritative record or fresh runtime proof, the authoritative/fresh evidence wins. Update the work-state file to remove the contradiction before continuing.
- If the agent cannot write the checkpoint itself, return a complete checkpoint payload for the operator/next agent to persist before more work begins.

## Futures safety

Before changes affecting futures execution, risk, broker routing, fills, strategy logic, journals, or deployment:

1. Inspect the relevant current code/config.
2. Run the matching audit/skill where available.
3. Treat any new or broadened broker/order path as high risk.
4. Keep live-trading gates fail-closed unless the operator explicitly authorizes a posture change.
5. Do not place a real test order merely to validate code or connectivity.

## Options safety

- Options changes must preserve the existing human-confirmation and execution restrictions unless explicitly authorized.
- Read-only broker/account/market-data access may be used for research, contract discovery, liquidity screening, position/context awareness, and advisory output when the operator has connected or otherwise authorized that data source.
- Connecting Robinhood, Webull, or another broker/data source grants **data access only**. It does not grant order, cancel, replace, exercise, assignment-management, or position-closing authority.
- Candidate contract discovery must preserve the current options risk/DTE/liquidity policy unless the operator explicitly approves a separate research hypothesis. A discovered contract is not an approved trade.
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

## Research agent roles

Lock these roles. Do not invent a parallel research automation layer. The purpose is to keep agents complementary instead of having every tool repeat the same audit.

| Role | Owner | Allowed | Forbidden |
|---|---|---|---|
| Research / edge + contract discovery | Grok (when used) | External research; market/context discovery; identify missed or underused setups; futures and options contract discovery/comparison; liquidity/DTE/spread/context screening from operator-authorized read-only broker/account/market data (including connected Robinhood/Webull when available); narrow repo/runtime inspection; first-pass PR/diff triage; log/status analysis; research-relevant defect/gap identification; hypothesis development; propose bounded experiments for ChatGPT/operator approval and preregistration, with frozen parameters and pass/fail criteria; documentation/checkpoint proposals; small implementation proposals for independent review | Deploying/restarting services; mutating runtime/env/broker/risk/execution/order state; placing/canceling/replacing/exercising orders; autonomously launching or iterating experiments; continuous parameter search; changing strategy status; promoting/merging safety-sensitive changes; maintaining a competing inventory or queue |
| Builder / mechanical experiment runner | Cursor | Implement operator-approved/spec-defined changes; fix proven defects; run already registered and approved trials and replays; produce reproducible artifacts under the trial ledger / experiment-spec chain; surface existing advisory/telemetry data | Autonomously inventing strategy hypotheses; continuous variant search/tuning; changing strategy status; promotion; live execution |
| Independent breaker / QA | Claude and/or Codex | Try to falsify Grok/Cursor work; implementation review; execution-safety review; live/replay parity; lookahead / optimistic-fill checks; leakage/overfit/statistical sanity checks; spec-vs-code match | Being the primary experiment generator; silently updating strategy status; promoting because a test passed |
| System lead / reconciliation + approvals | ChatGPT + operator (human) | Maintain the work split; reconcile contradictory evidence; choose which proposed experiment is worth registering; approve experiment contracts; accept/reject implementation; approve inventory classification changes; approve progression | Autopromotion to live; treating one agent's output as sufficient proof without required independent checks |
| Forward evidence collector | Production paper/demo/observer lanes | Collect frozen prospective evidence and journal outcomes under the approved epoch/config | Research, tuning, changing its own parameters, changing strategy status, or expanding execution authority |

Research flow: **Grok proposes → ChatGPT/operator approve/register → Cursor runs/builds → Claude/Codex attacks → ChatGPT/operator decide.**

Grok may aggressively search for edge and candidate contracts, but a hypothesis or discovered contract is **advisory/research only** until it enters the approved experiment/execution path. Read-only account linkage is context, not execution permission.

No agent autonomously launches an unapproved experiment or promotes anything to live execution. Prefer continuing already-defined campaigns and registered trials over restarting completed audits or building an "experiment selector." New experiments are allowed only when they answer a material unresolved question, are approved by ChatGPT/operator, and are preregistered before scoring.

External Grok (or other off-repo) research loops are unverified until explicitly inventoried; their absence does **not** authorize a new autonomous research or execution loop.

## Strategy / evidence source of truth

| Concern | Authoritative record | Notes |
|---|---|---|
| Futures strategy evidence verdict + execution posture | `docs/strategy-rules/Strategy_Inventory.md` | Futures strategy-status truth. `ops/project_check/daily.py` reads its Master Table. |
| Options current state / evidence posture | `docs/options-current-state-handoff.md` | Options-lane current-state authority. Preserve frozen cohorts/evidence boundaries; dated options notes are provenance unless this file explicitly incorporates them. |
| Experiment / trial history | `docs/research-trial-ledger.jsonl` | Append-only attempt history. Spec: `docs/research-trial-ledger-spec-2026-09-23.md`. |
| Approved baseline-vs-candidate run contract + fail-closed runner | `docs/research-experiment-specs/` (+ schema/spec docs); `ops/research_experiment_runner.py` / `scripts/afs_experiment_runner.py` | Sits *on top of* a ledger trial. Does not replace inventory or ledger. Runner executes only `APPROVED` specs; zero promotion/deploy authority. |
| Active lane sample / ops memory | `ops/evidence_registry.py` summaries | Read-only operational memory of collected evidence. Not status authority. |
| Runtime enablement / broker / release | Box config + release manifest | Never infer from docs or inventory alone. |

Dated `docs/futures-current-status-*.md`, futures handoffs, and reconciliation notes are **provenance and operator narrative**. They must point at the futures inventory (strategy status) and trial ledger (experiment history) instead of independently declaring current futures strategy status. For options, `docs/options-current-state-handoff.md` remains the current-state authority; older options status/handoff notes are provenance unless incorporated there. Other agents may **read** the authoritative records and **propose** updates; they must not silently maintain competing versions.

## Output standard

For substantial work, report:
- what was verified,
- what changed,
- tests/checks run and their results,
- remaining uncertainty or blockers,
- exact next action when one is required.

## Options evidence / progression contract

These are stable options-lane rules; volatile PR/SHA status belongs in `docs/options-current-state-handoff.md`, not here.

- The canonical options evidence path is prospective: capture → canonical signal → plan/contract/risk validation → outcome → fitness/research.
- A realised/scorable options outcome must preserve canonical provenance and be tied to the registered strategy epoch/scope. A retrospective observation, late/missed trigger, gap-through miss, data-blocked row, unregistered epoch, or other counterfactual row cannot be relabelled into a prospective catch or realised trade result.
- A FROZEN epoch is only a locked definition. It is not forward-proof readiness, paper readiness, or execution authority.
- Progression order is: merged/reviewed source → separately verified observer/runtime posture → proof-ready registered epoch → prospective forward evidence → paper automation only if evidence supports it. Do not skip stages because alerts, previews, or backtests look good.
- Options readiness audits must distinguish **ready to collect forward proof** from **ready for paper automation**.
- Modernization/cleanup must classify artifacts before deletion and preserve replay-required or historical evidence. Simplify implementation, not safeguards.

