# Agent Work State

> **Purpose:** durable resume/checkpoint ledger for Grok and other agents so work survives quota exhaustion, outages, context loss, and multi-day gaps without being recreated.
>
> This file is **not** strategy-status authority, deployment authority, or experiment authority. Authoritative records named in `AGENTS.md` always win.
>
> **Checkpoint base:** repository `main` `6ad0bac2bcbbce6dbb88f181ad4aa469e3711421` (PR #1096 merged 2026-10-02). Always fetch current `main`; this stored SHA is a comparison base, not a perpetual current-state claim.
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

## Current checkpoint — 2026-10-02 ET

### DONE / DO NOT REDO

- **Runtime read, 2026-10-02 02:06–02:09Z (read-only `afs-ro`):** futures-bot PID `791194`, `NRestarts=0`, active since 2026-09-30 00:35:54Z on `75f10e4540aa1f25b51b77c1ec2a2da40381a188`; Tradovate DEMO `HEALTHY`; live trading disabled; preflight 2026-10-02 00:37:01Z passed, not armed, 0 positions, 0 working orders. Not re-read since; re-verify before any mutation.
- **4HR MNQ forward evidence:** 0 prospective fills since the 2026-09-09 wide-stop epoch. The `wide_stop_4k` lane saw 2 candidates, both blocked before an order (2026-09-15 `REGIME_RESTRICTED`; 2026-09-30 `ENTRY_DETACHED_FROM_PRICE`). The natural-1m 4HR lane never wrote evidence; cause and the new-epoch rules are in `docs/4hr-natural-1m-observation-epoch-2026-10-01.md`. Do not re-audit; do not backfill.
- **Merged 2026-10-02 (none deployed):** #1077 Signa v2 (`8c4e2e4`), #1092 4HR natural-1m observation (`8b7d968`), #1094 observation publish fail-safe (`489b55b`), #1095 observation status path follows `LOG_DIR` (`1c43291`), #1096 release-script bytecode/fingerprint fixes (`6ad0bac`), plus docs #1073 and #1093.
- **Reviews done:** `/options-diff-review` of #1077 at `9387964` = APPROVE (260 tests). `/futures-diff-review` of #1092 = APPROVE PAPER-DEPLOY; its should-fix landed as #1094.
- **Release review `75f10e4` → `6ad0bac` done (2026-10-02):** code tightens safety; #1096 is release-script tooling only (bytecode off and fingerprint pin on every integrity check); `strategy/`, `journal/`, and `risk_rules.yaml` unchanged; all 7 live-only commits are patch-present on main (stale-bearer fix and FI-18 account pin intact). Verdict **HOLD for deploy** until the box preconditions under NEXT are proven. Re-review only commits after `6ad0bac`.
- **Live release preserved on GitHub:** tag `archive/futures-stale-bearer-curated-75f10e4-2026-09-29` → `75f10e4`.
- **60M 3-2-2 corpus rerun:** operator re-confirmed **HOLD** 2026-10-02. A rerun cannot change the $6k account-size blocker. Revisit near $6k equity, before quoting any 3-2-2 figure, or when forward 3-2-2 trades need a clean historical baseline.

### NEXT — futures release, in order

1. **Read-only box env proof** (names and value shape only; never print secrets):
   - `MAX_CONTRACTS_HARD_CAP` is an ASCII integer 1–6 and `EXPECTED_PROOF_MAX_CONTRACTS_HARD_CAP` equals it. Main's `load_config()` refuses to start without it, and `PaperBroker` / `TradovateBroker` refuse orders; live `risk_rules.yaml` has no fallback key.
   - `TRADOVATE_ENV` is exactly `demo` (no case folding, no whitespace).
   - `RELEASE_INTEGRITY_ENFORCED` state. If enforced: the unit `ExecStart` runs Python with `-B` (or `PYTHONDONTWRITEBYTECODE=1`) so the running service never writes `__pycache__` into the release; #1096 covers build/verify/promote and the fingerprint pin they pass.
   - `LOG_DIR=/root/afs-shared/logs`, so the observation status file lands beside the journal (#1095).
2. If all pass: build/verify an exact reviewed SHA through `scripts/atomic_release.sh`, then a separate operator deploy GO. Gate-condition #1068 rides along with that release.
3. Start the 4HR natural-1m epoch only per `docs/4hr-natural-1m-observation-epoch-2026-10-01.md` (both flags + both pins, then an operator read-only verification; that time is the epoch start).
4. #1077 on the options scanner is a separate deploy GO.

## Previous checkpoint — 2026-09-30 ET

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
- **PR #1073 MERGED** as `3c3dcd5b0567943e5af9d452d3adf7c06d41b7e5` (squash of reviewed head `0232c187e601b87c286af0ad70c5dd47acac3a58`). `docs/options-current-state-handoff.md` is now dated 2026-09-29. **Options task checklist: `docs/options-next-actions.md`.** Do not recreate that checklist elsewhere.
- **PR #1077 MERGED** as `8c4e2e4` (squash of reviewed head `93879644c9b248c8cb2d29b172b086fa9af5c88a`; exact-head CI green; `/options-diff-review` APPROVE). It adds display-only **Observation Rating (A/B/C/N/A)** and **AFS Trade Grade (A/B/C/F/N/A)** surfaces; neither changes scanner score, alert eligibility, setup state, contract selection, risk permission, orders, or execution. **Not deployed** to the options scanner. Do **not** start a second Signa-v2 implementation.
- Options production posture recorded by the merged #1073 remains advisory/read-only with the existing frozen evidence lanes; repository changes are not runtime proof.
- Real-data gates as recorded by the merged #1073 (none is permission to deploy): #1071 Epoch-3 real-data audit COMPLETE (`0dca986`; do not tune from the five-close sample); #1069 SPX→SPXW provider mapping/preflight COMPLETE on `d7a546c`, with an RTH check that SPXW 0DTE appears while the lane stays OFF still pending; #1067 real 66/66 RTH capacity proof inside the five-minute cadence still pending (`a4de6f9`). Verify which gates changed before doing work.

### OPEN / NEEDS DECISION — verify before touching

- #1077 options-scanner deploy: needs a separate operator GO; the scanner box release is unchanged.
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
