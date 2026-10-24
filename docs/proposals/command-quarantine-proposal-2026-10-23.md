# Proposal: quarantine legacy armed commands (source/docs only)

**Status:** PROPOSAL / HOLD. **Operator approval required** before any deletion, moving a file, disabling a command, closing a PR, or merging this proposal.

**Scope:** This document proposes a disposition for legacy operator commands referenced as `execute-armed` and `arm-live` in both futures and options lanes. No executable files, aliases, scripts, broker paths, risk settings, deployment paths, environment variables, service units, or release artifacts are changed by this proposal.

## Evidence and limits

- GitHub PR [#1226](https://github.com/imanisworld/autonomous-futures-system/pull/1226), head branch `cursor/repo-hygiene-audit-20261013-2bb5` at review time, remains **open and blocked**. It contains old operational instructions intermixed with later history. **Do not merge #1226 as-is.**
- **Located in a separate private repository**, `imanisworld/autonomous-futures-private`, branch `claude/trading-slash-commands-3rr224` at `4860e704` (read-only source inspection):
  - `.claude/commands/futures-arm-live.md` — describes preparing a live futures packet; forbids execution from the arming command.
  - `.claude/commands/futures-execute-armed.md` — describes execution of an armed futures packet, with a current-session live confirmation check.
  - `.claude/commands/options-arm-live.md` — describes preparing a live options packet; forbids execution from the arming command.
  - `.claude/commands/options-execute-armed.md` — describes execution of an armed options packet with live confirmation.
- **Primary hazard:** Grok's independent review reports **no code-level options execution backstop**. The connected Robinhood integration may place real-money options orders if invoked. Consequently, the options `arm-live`/`execute-armed` instructions are an unsafe agent-control surface; written checklist/confirmation instructions alone do not enforce a technical broker boundary. Do not invoke or use them for testing. The review's absence-of-backstop claim still requires an execution-path audit before any eventual claim that live-order access is impossible.
- **Cross-repository limitation:** This proposal PR is in `imanisworld/autonomous-futures-system`; it cannot quarantine or remove files in `imanisworld/autonomous-futures-private`. A separate, operator-authorized private-repo change would be required. The pre-recut installed release differs from `main`; source inspection does not establish whether these commands are accessible on a computer or server.

## Recommended disposition: preserve history, quarantine entrypoints after proof

1. **Inventory first (read-only):** On the verified private-repo branch, inspect the four named files and enumerate aliases, invocation references, integrations and actual dispatch paths separately for futures and options. Record the verified file SHA, callers, scripts or tools they may invoke, required arguments, environment gates and any broker/order mutation route. Do not run the commands or make trial calls.
2. **Archive provenance:** Preserve the old branch SHA and PR URL in the historical cleanup index. Keep relevant operator runbook and audit history, with a clear dated **LEGACY / DO NOT EXECUTE** label. Prefer retaining Git history over copying runnable command bodies into new active documentation.
3. **Quarantine proposal:** Only after exact paths and callers are verified, submit a second, tightly scoped PR **in `imanisworld/autonomous-futures-private`** that removes the armed names from discoverable/active agent command menus **or** replaces them with non-executable historical pointers. Do not install substitute scripts. Any removal must have explicit operator approval, a reviewed diff, and regression checks proving current read-only commands and required safeguards are unchanged.
4. **Close or archive old PR:** After the operator chooses a disposition, leave #1226 unmerged and record whether it was superseded/closed. Do not delete its branch before the operator authorizes that separate action.
5. **Safety review:** Independently inspect the exact PR head, CI, references from current runbooks/agents, and release tooling. Test any proposed quarantine with synthetic, non-broker fixtures; ensure no live/paper execution feature is introduced or weakened.

## Acceptance checks before any subsequent code/command change

- [x] Exact private repository, branch/head and all four command entrypoint files identified from source (usage/callers remain unknown)
- [ ] All aliases, callers, and dispatch targets recorded; no guessed paths
- [ ] No new order/cancel/flatten/execute/broker submission path
- [ ] Active read-only audit workflows remain usable
- [ ] Historical evidence remains accessible
- [ ] Operator has approved the exact quarantine/delete scope in writing
- [ ] Grok PM has reviewed the exact PR head and CI passes

**Current action:** docs proposal only; Grok's #1246 review requested these corrections. No deletion, rename, code mutation, or activation. **Do not merge without Grok PM exact-head review and the operator's explicit approval.**
