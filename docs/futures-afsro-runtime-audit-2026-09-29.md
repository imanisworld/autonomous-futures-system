# Futures afs-ro runtime audit handoff — 2026-09-29

> **Status:** HANDOFF-REPORTED READ-ONLY EVIDENCE / HOLD.
>
> This record preserves a read-only `afs-ro` wrapper audit reported by the operator workflow around 2026-09-29 21:30 UTC. It was not independently re-run by this repository documentation session. Treat wrapper-inaccessible facts as unknown, not inferred from repository source.
>
> Core rule: **No proof, no run.**

## Verdict

**HOLD.** Do not repin the six-market reporter or promote a newer futures release until Tradovate DEMO authentication is restored and broker positions / working orders can be read and reconciled.

## Wrapper-reported runtime identity

- deployed curated release: `41ae1881655c235a26288244c81b8df2a7c8c968` (release directory prefix `41ae1881655c`);
- reported release time: 2026-09-24 16:46 UTC;
- service PID: `3254445`;
- service active since 2026-09-24 16:46:34 UTC;
- reported restart count: `0`;
- working directory: `/root/afs-releases/41ae1881655c-20260924-124607`;
- release link and working directory matched;
- reported manifest fingerprint prefix: `0e4e03a2…59f1`;
- reported risk-rules hash prefix: `ea3d5537…14b6`, with the auditor reporting the same risk-rules bytes on the box, in `41ae188`, and on then-current `main`.

Release integrity was **not fully proven** in this wrapper pass. The auditor reported matching hashes for three sampled files (risk rules plus two reporter scripts), not a complete manifest verification.

## Runtime safety posture reported through afs-ro

- live trading reported disabled;
- manifest reported `live_trading_enabled: false` and `paper_mode: true`;
- watcher reported broker environment `demo`;
- MNQ/MES price data was reported fresh around 21:01 UTC;
- watcher memory health reported HEALTHY, while also logging repeated swap-pressure warnings during the day.

The following effective values were **NOT ACCESSIBLE THROUGH AFS-RO** and must not be guessed from source:
- `TRADOVATE_ENV`;
- `SCHEDULE_MODE`;
- `MAX_CONTRACTS_HARD_CAP`;
- entry-fill model;
- MNQ/MES entry tolerances;
- whether the exact Tradovate account pin is actually populated in runtime configuration.

The deployed broker source reportedly contains the account-pin code, but runtime pin configuration was not readable through the wrapper.

## Broker authentication blocker

The wrapper audit reported Tradovate DEMO authentication failing with HTTP 401 / credentials rejected since **2026-09-25 23:07 UTC**, with the last successful heartbeat around **2026-09-25 20:56 UTC**.

Reported consequences:
- broker positions could not be queried;
- working orders could not be queried;
- broker flatness is therefore **UNKNOWN**;
- local journals reportedly showed no open positions, but local state is not a substitute for broker read-back;
- the broker-recovery state was reported `ACTION_REQUIRED` after more than 806 recovery attempts.

The auditor also reported a misleading watcher message: a "Resolved: Broker connection problem" notification around 21:01 UTC was followed by another 401 around 21:21 UTC. Do not classify that as a fixed monitoring defect yet; reproduce/inspect the alert-state transition after authentication is restored.

## Accounting discrepancy to reconcile

The audit reported today's summary as **0 trades but -$59.75 realized**. Cause was not established in the wrapper pass.

This is an unresolved reconciliation item. Do not infer a trade, fee, stale ledger, carryover, or broker event until the underlying rows are traced.

## Reporter state

The wrapper audit reported:
- #1068 six-market reporting code is **not** on the deployed futures release;
- deployed reporter scripts match `41ae188`, not current `main`;
- the observed daily paper-collection report showed MES and MNQ only;
- separate reporter pin directories / systemd timer definitions under `/root/afs-shared` were **NOT ACCESSIBLE THROUGH AFS-RO**.

Therefore #1068 is proven **repo-side only**. Reporter repin remains a separate operator-authorized action.

## Source / runtime split reported

At the time of the audit, repo `main` was `f48bcb8f42fab87f689016dc5257b507fcd017a5`, while the futures runtime remained on curated `41ae188...`.

The wrapper audit reported:
- #1013 exact-account-pin code: present in deployed source;
- #1014 fail-closed corrupt-state handling: present in deployed source;
- #1053 hard contract-cap rebuild: appears absent from deployed release;
- #1054 exact `TRADOVATE_ENV` validation: appears absent from deployed release;
- #1068 six-market reporting: absent from deployed release;
- #1070/#1072 promotion-gate changes: not deployed and not required for runtime trading behavior.

The #1053/#1054 absence statement is a wrapper-audit conclusion, not a deployment authorization. Before any future release, review the exact candidate diff from `41ae188...` to the proposed SHA and prove runtime env/account pins required by those fail-closed changes.

## Access boundary discovered

Accessible through `afs-ro` in this pass:
- release link / release history;
- service PID, cwd, active state, restart count;
- watcher state;
- selected logs/reports;
- release manifest;
- selected file hashes.

Reported **NOT ACCESSIBLE THROUGH AFS-RO**:
- `.env` effective values;
- systemd unit files and separate reporter pins;
- deploy-lock state;
- installed deploy wrapper details;
- effective sshd configuration;
- SSH users / authorized keys;
- sudo / forced-command restrictions;
- agent-account restrictions;
- Tailscale / private-network state;
- provider-console break-glass setup;
- MacBook / phone operator-access setup.

## Safe next sequence

1. Operator restores Tradovate **DEMO** authentication without exposing credentials in chat, logs, or repository files.
2. Run a read-only broker reconciliation: exact expected demo account pin/status, positions, working orders, and flatness.
3. Trace the **0 trades / -$59.75 realized** discrepancy to concrete journal/accounting rows.
4. Re-run the broker alert-state check and determine whether the premature "Resolved" message is reproducible.
5. With fuller operator access, complete the runtime/env/access map that `afs-ro` could not prove.
6. Only then decide the exact release/reporter action:
   - isolated six-market reporter repin, or
   - exact reviewed futures candidate including required source-side safety deltas such as #1053/#1054.
7. No old-access lockdown until replacement MacBook/phone and break-glass paths are proven.

No deployment, restart, repin, broker mutation, or access mutation is authorized by this document.
