# Futures post-close deployment readiness — 2026-09-24

## Verdict

**HOLD / AUDIT ONLY.**

This document is a deployment-readiness plan only. It does not authorize a deploy, service restart, TradingView change, broker-env mutation, strategy activation, or cleanup.

Last verified deployed futures release:
`cddef4a4b6583f31007268789545c7be24345d75` (#1007)

Frozen repository candidate reviewed here:
`44202701a2763c0b2ee7b1acbc06672ec6a100ef` (#1015)

If `main` moves after this document, this candidate is stale. Recompute the diff from the actually deployed release to the new exact candidate before any promotion.

## What was already done

Do not duplicate these items:

- The generic atomic-release / release-integrity / rollback framework already exists in `scripts/atomic_release.sh`, `docs/env-and-deploy.md`, the runbook, and prior deployment proof packages.
- PR #1015 already records the source/runtime split and says that source safety work is ahead of the verified runtime.
- FI-10, FI-11, FI-13, and FI-18 are fixed in source by #1013/#1014; #1011 is their tests-only reproduction/coverage PR.
- #994 is already the open MNQ ORB Stage-A research PR. Do not recreate or duplicate it.
- Round-4 S/D preservation is a separate blocked research task and is not part of this deployment candidate.

## Exact deployed-to-candidate delta

GitHub compare from `cddef4a` to `44202701` is **9 commits ahead / 0 behind**.

| Commit / PR | Classification | Runtime implication |
|---|---|---|
| `d6358e2` / #1003 | Pine advisory fix | TradingView script change only; backend already detects VWAP rejection. |
| `58ab077` / #1008 | Runtime data-path safety | Rejects non-finite/non-positive OHLC and out-of-order 5m bars before they reach lanes. |
| `08b2cff` / #1009 | Research only | Corrects offline non-Strat VWAP construction; no runtime/broker path. |
| `223f5cd` / #1011 | Tests only | Fault-injection coverage/reproduction; no production behavior. |
| `71fa287` / #1012 | Docs only | Runtime/source-state documentation only. |
| `6f2d75b` / #1010 | Shadow-input + Pine | Adds overnight high/low and RTH open to payload/Pine so existing shadow observers can receive them. No gate/order authority. |
| `cfd3313` / #1013 | Broker safety | Automated Tradovate submission now fails closed unless an exact expected account ID is configured; pinned path still requires positive balance. |
| `501b7b9` / #1014 | Runtime state safety | Corrupt safety-sensitive journal/collector state fails closed; observation-only shadow resolver remains fail-soft. |
| `4420270` / #1015 | Docs only | Records FI fixes and source/runtime split. |

The compare changes production/runtime files under:
- `context/five_min_feed.py`
- `context/wide_stop_forward_collector.py`
- `execution/tradovate_broker.py`
- `journal/journal_logger.py`
- `strategy/shadow_resolver.py`
- `tradingview/risksentinel_context.pine`
- `webhook/payload.py`
- `webhook/runner.py`

Therefore this is **not** a docs/tests-only promotion.

## Account-pin status

Repository docs already record that `TRADOVATE_EXPECTED_ACCOUNT_ID` was set on the previously verified demo runtime.

That proves configuration presence only. It does **not** supersede #1015's newer requirement:

> Before any future broker-connected deployment, independently prove that the configured numeric account ID is the intended Tradovate DEMO account. Do not guess or infer it.

Required box proof before candidate promotion:

1. `TRADOVATE_ENV=demo`.
2. `LIVE_TRADING_ENABLED=false`.
3. `TRADOVATE_EXPECTED_ACCOUNT_ID` is present, numeric, and matches the intended demo account from broker account truth.
4. If `EXPECTED_TRADOVATE_ACCOUNT_ID` is used by the live-box guard, it matches the same approved account identity.
5. The selected account has a readable positive balance.
6. No unexpected second account can be silently selected; #1013 must block rather than fall back.
7. Do not print or commit the raw account ID in a public artifact.

**Until this proof is completed: HOLD.**

## Exact pre-deploy gate

Run only after market close and under explicit operator GO.

### 1. Freeze the candidate

- Record exact target SHA: `44202701a2763c0b2ee7b1acbc06672ec6a100ef`.
- Confirm current `main` still equals that SHA. If not, STOP and re-audit.
- Re-run `cddef4a...target` compare and confirm no new runtime files entered.
- Preserve the current deployed release as the rollback SHA.
- Preserve a backup of the active shared `.env` before any mutation.

### 2. Capture current runtime truth

Before restart/promotion, prove on the actual box:

- active futures release/cwd = expected deployed baseline;
- release integrity = PASS;
- futures service healthy;
- watcher state understood/reconciled;
- broker environment = DEMO;
- live trading = disabled;
- account pin proof above = PASS;
- broker position = flat;
- working orders = zero;
- no deploy lock / stale promotion lock;
- current journals and collector state are readable;
- current evidence epochs / lane state are captured so restart does not silently reset them.

Any mismatch = **HOLD**.

### 3. Candidate verification before promotion

Build/verify the exact candidate without switching the active service first.

At minimum verify:

- exact SHA and release manifest;
- source files non-writable / integrity enforcement intact;
- required Python environment resolves;
- focused regressions for #1008, #1010, #1013, #1014 pass;
- full test suite or the standing release-gate suite passes for the exact candidate;
- `python3 scripts/doctor.py --strict` passes with proof-critical environment pins;
- live-box drift guard reports no missing/mismatched proof-critical pins;
- candidate starts cleanly in verification mode and `/health` passes;
- account pin cannot fall back to a first-account default;
- malformed/corrupt state cannot reopen a lane or erase an open-position/claim safety fact.

Candidate verification is not deployment authority.

## TradingView boundary

Two commits in this delta touch `tradingview/risksentinel_context.pine`:

- #1003: VWAP-rejection advisory-label correction.
- #1010: emit `overnight_high`, `overnight_low`, and `rth_open`.

These can be handled in **one** coordinated TradingView script save / alert recreation instead of two separate changes.

Keep this separate from the server release:

- server merge/deploy does not prove TradingView is updated;
- TradingView update does not prove server release is active;
- verify the saved script version and alert set independently;
- #1010's new payload fields are optional on the server side, so missing fields must remain non-authoritative rather than inventing values;
- do not change strategy/risk logic while recreating alerts.

## Promotion gate

Only after the pre-deploy and candidate-verification gates pass:

1. Promote the exact frozen candidate.
2. Restart only the services required by the sanctioned release procedure.
3. Do not change live/paper/demo authority during the deploy.
4. Do not change risk limits, strategy enablement, instruments, epochs, or collectors as part of this release.
5. Keep `LIVE_TRADING_ENABLED=false`.

## Immediate post-deploy proof

Before calling the deployment successful, prove:

- active symlink/cwd points to the exact candidate release;
- release integrity PASS;
- service PID/restart count sane;
- `/health` healthy;
- `LIVE_TRADING_ENABLED=false`;
- `TRADOVATE_ENV=demo`;
- exact account-pin reconciliation PASS without exposing the raw ID;
- broker flat and zero working orders;
- journal continuity preserved;
- evidence/lane epochs preserved;
- #1008 data-quality guard is active;
- #1013 unpinned broker submission fails closed;
- #1014 corrupt safety state fails closed;
- shadow observers tolerate absent #1010 fields until TradingView is separately updated;
- watcher/drift guard returns to a clean state;
- no unexpected Discord/runtime errors.

Do not count a successful process restart as proof by itself.

## Rollback

Rollback target remains the last independently verified deployed futures release:

`cddef4a4b6583f31007268789545c7be24345d75`

Before promotion:
- prove that release still exists and passes its manifest/integrity check;
- preserve the active `.env` backup;
- record the exact rollback command/path using the existing atomic-release tooling.

Immediate rollback / HOLD triggers include:

- release-integrity failure;
- service health failure;
- account-pin mismatch/unverifiable account identity;
- any live-trading enablement;
- unexpected broker position or working order;
- journal/collector continuity failure;
- lane state unexpectedly reset/opened;
- malformed payload accepted where #1008 should block it;
- unpinned broker submission not blocked;
- watcher/drift guard cannot be reconciled;
- unexplained runtime error after restart.

After rollback, re-prove release identity, integrity, health, DEMO/live-disabled posture, broker flatness, zero orders, and journal continuity.

## Do not bundle

Do not use this deployment to:

- merge or run #994;
- preserve or test Round-4 S/D;
- tune a strategy;
- change risk rules;
- change max contracts/trades/day;
- expand instruments;
- enable live trading;
- add a broker route;
- clean up unrelated files;
- change alert formatting or other presentation work.

## Safe next step

**HOLD until actual-box account identity and pre-deploy state are proven after the close.**

If those proofs pass, the smallest next action is candidate verification of exact SHA `44202701...`; only after that should an operator decide whether to promote it.
