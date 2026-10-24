# Proposal: quarantine legacy armed commands (source/docs only)

**Status:** PROPOSAL / HOLD. **Operator approval required** before any deletion, moving a file, disabling a command, closing a PR, or merging this proposal.

**Scope:** This document proposes a disposition for legacy operator commands referenced as `execute-armed` and `arm-live` in both futures and options lanes. No executable files, aliases, scripts, broker paths, risk settings, deployment paths, environment variables, service units, or release artifacts are changed by this proposal.

## Evidence and limits

- GitHub PR [#1226](https://github.com/imanisworld/autonomous-futures-system/pull/1226), head branch `cursor/repo-hygiene-audit-20261013-2bb5` at review time, remains **open and blocked**. It contains old operational instructions intermixed with later history. **Do not merge #1226 as-is.**
- The exact `execute-armed` / `arm-live` file paths and active call sites have **not** been established from the current default branch. Names in this document are search targets, **not** verified runnable commands.
- The pre-recut installed release differs from `main`; GitHub source inspection cannot prove whether historical commands are reachable on any computer or server.

## Recommended disposition: preserve history, quarantine entrypoints after proof

1. **Inventory first (read-only):** On the specific legacy branch, list exact files, symbols, wrappers, aliases and invocation references for both command families. Check futures and options separately. Record file SHA, callers, scripts they dispatch to, required arguments, environment gates and whether invocation can connect to a broker or mutate order/session state.
2. **Archive provenance:** Preserve the old branch SHA and PR URL in the historical cleanup index. Keep relevant operator runbook and audit history, with a clear dated **LEGACY / DO NOT EXECUTE** label. Prefer retaining Git history over copying runnable command bodies into new active documentation.
3. **Quarantine proposal:** Only after exact paths and callers are verified, submit a second, tightly scoped PR that removes the armed names from discoverable/active agent command menus **or** replaces them with non-executable historical pointers. Do not install substitute scripts. Any removal must have explicit operator approval, a reviewed diff, and regression checks proving current read-only commands and required safeguards are unchanged.
4. **Close or archive old PR:** After the operator chooses a disposition, leave #1226 unmerged and record whether it was superseded/closed. Do not delete its branch before the operator authorizes that separate action.
5. **Safety review:** Independently inspect the exact PR head, CI, references from current runbooks/agents, and release tooling. Test any proposed quarantine with synthetic, non-broker fixtures; ensure no live/paper execution feature is introduced or weakened.

## Acceptance checks before any subsequent code/command change

- [ ] Exact branch/head and all four futures/options command entrypoints identified or explicitly proven absent
- [ ] All aliases, callers, and dispatch targets recorded; no guessed paths
- [ ] No new order/cancel/flatten/execute/broker submission path
- [ ] Active read-only audit workflows remain usable
- [ ] Historical evidence remains accessible
- [ ] Operator has approved the exact quarantine/delete scope in writing
- [ ] Grok PM has reviewed the exact PR head and CI passes

**Current action:** docs proposal only. No deletion, rename, code mutation, or activation. **Do not merge without Grok PM exact-head review and the operator's explicit approval.**
