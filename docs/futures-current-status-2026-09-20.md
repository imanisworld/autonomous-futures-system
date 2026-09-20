# Futures Current Status — 2026-09-20

This is the concise operator-facing source of truth for the futures system after the 2026-09-20 4HR continuation, Signa shared snapshot, and Futures Signa Context v2 work. Historical audit documents remain evidence records; where an older summary conflicts with this file, this file governs current status unless a later dated document explicitly supersedes it.

## Verdict

**PAPER / SHADOW / GUARDED DEMO EVIDENCE ONLY. NO LIVE EXECUTION APPROVED.**

Core rule remains: **No proof, no run.**

## Update — 2026-09-20 options RTH alert-delivery safety

The service pins are now intentionally split. `futures-bot.service` remains on **`c7798d4993d1ecfd872313cfc5c84da2cda6625d`**. `options-scanner.service` is now pinned to **`a0c34818faaad37b20d8c05249e8f5442d8d7141`**, a curated options-only release containing the merged #824/#825 RTH alert-delivery guard on top of the prior scanner baseline. Only `options-scanner.service` restarted; the futures PID and cwd remained unchanged. The options production DB path/counts were preserved, health stayed advisory-only/read-only, release integrity passed 1,394/1,394, and the deployed no-network proof blocked a 15:59 ET scan from delivering at 16:01 ET with `market_closed`. The service-aware drift gate passes both immutable releases.

## Update — 2026-09-20 operator-message deployment

At the operator-message deployment point, the futures-bot and options-scanner runtimes were both pinned to **`c7798d4993d1ecfd872313cfc5c84da2cda6625d`**. The later options-only RTH safety promotion above supersedes that shared-pin state for `options-scanner.service` only. Release integrity passed for **1,394/1,394 files** on both service paths. `futures-bot.service` and `options-scanner.service` restarted cleanly; `afs-watcher.service` remained active with watcher source matching the deployed release. Health proof after restart: futures `/health` returned `ok=true`, `live_trading_enabled=false`, `broker=tradovate`, and `webhook_secret_required=true`; options `/health` returned `status=healthy`, `advisory_only=true`, Public read-only provider, `order_supported=false`, `account_endpoints_forbidden=true`, scheduler running, and `signa_context_pull_enabled=true`.

The deployed delta from the prior futures release `ed1212b2552f0bfd990d4f4f80d2e05dd5c3d22c` to `c7798d4` is presentation/read-only/reporting only: standardized Discord operator cards, options scanner card formatting, read-only Signa storage reporting, public terms/privacy wording, and related tests/docs. No files changed under `execution/`, `risk/`, `risk_rules`, broker routes, `webhook/runner`, `strategy/`, `config/`, or journal runtime paths. No strategy, risk, order, broker, or collector logic was changed by this deploy. The installed server drift gate remains `/root/bin/afs-drift-gate.sh`, matching `scripts/afs-server-drift-gate.sh`; it was not replaced by the non-server drift script.

## Backlog / not active — Market Hours v2

Nasdaq/NYSE extended-equity-session changes are now a tracked infrastructure backlog item, not an active strategy change. The next safe step is spec-only: centralize market-hours/session labeling, keep RTH as the default authority, separate overnight/premarket/after-hours evidence cohorts, suppress alerts outside explicitly approved sessions, and require provider/liquidity/slippage proof before any overnight equity session can influence alerts, scoring, risk, or entries. No runtime change, futures rule change, risk change, order change, or deploy is authorized by this backlog item.

## Verified deployed runtime

Previous Signa v2 deployment proof before the later operator-message deploy:

- deployed release: `a02320268e26a05f56b159f03d3cf441e776ef46`;
- release integrity: **1,390/1,390 files checked**;
- `LIVE_TRADING_ENABLED=false`;
- `BROKER=tradovate`;
- `TRADOVATE_ENV=demo`;
- `SCHEDULE_MODE=always_on_shadow`;
- `MAX_CONTRACTS_HARD_CAP=1`;
- broker position: **null**;
- broker account open P&L: **0.0**;
- live preflight: **not armed**;
- positions readable: **0 position rows**;
- orders readable: **0 order rows**;
- no open positions: **true**;
- no working orders: **true**;
- watcher active and futures service active;
- deploy lock absent after promotion;
- failed systemd units: **0**.

Live preflight is not armed with reason `preflight_failed:heartbeat_fresh`. This does not approve or block this context-only deployment because live trading remains disabled.


Companion options-scanner note: later on 2026-09-20, the options scanner was first promoted from its old service-specific release `58f1c50583d8bb747c0b221eabb75af376b10ecc` to `1fc0ad9c97d6daca02905058b14123135605c769` solely to run the conservative Signa scheduled context pull. That earlier Signa enablement required an `options-scanner.service` restart while `futures-bot.service` stayed on `a02320268e26a05f56b159f03d3cf441e776ef46`. The scanner was subsequently promoted to `c7798d4993d1ecfd872313cfc5c84da2cda6625d` for the presentation/read-only operator-message release, then to `a0c34818faaad37b20d8c05249e8f5442d8d7141` for the options-only #824/#825 RTH alert-delivery safety fix. The futures bot remains on `c7798d4`. Signa context rows remain observation-only with `trade_authority=false`. First natural options RTH scheduled-cycle proof is still pending.

This release lineage includes:

- #798 `780b5ea` — 4HR continuation treatment tag in the 1m observer evidence;
- #799 `98c360d` — read-only Signa futures context lane v1;
- #803 `a7e0c16` — shared Signa snapshot store;
- #805 `4550d22` — options Signa pulls write raw snapshots first;
- #807 `a023202` — Futures Signa Context Lane v2 reads shared snapshots.

## What changed on 2026-09-20

### 1. 4HR 4H 2→2 continuation treatment tagging — #798

The 4HR 1m observer records read-only treatment metadata for every natural eligible 4HR 1m touch:

- definition: `completed_et_wall_clock_4h_sequence_v1`;
- treatment: `4hr_prearmed_4h22_continuation_v1`;
- latest completed 4H sequence;
- treatment eligibility true/false/unknown;
- constituent completed 4H bar starts/counts/types.

Historical corrected pre-armed A/B result:

| Population | Fills | W/L | Net | PF | H1 | H2 |
|---|---:|---:|---:|---:|---:|---:|
| Broad 4HR control, 1 tick | 80 | 40/40 | +$1,414.60 | 1.299 | +$861.80 | +$552.80 |
| 4H 2→2 continuation, 1 tick | 29 | 16/13 | +$1,444.58 | 2.032 | +$683.28 | +$761.30 |
| Non-continuation complement, 1 tick | 51 | 24/27 | -$29.98 | 0.991 | +$362.50 | -$392.48 |

At 3 adverse ticks, the continuation treatment remains positive:

- net **+$1,402.58**;
- PF **1.984**;
- H1 **+$663.28**;
- H2 **+$739.30**.

Interpretation:

- the treatment is **PROMISING BUT UNPROVEN / OBSERVATION ONLY**;
- top-three-month concentration remains too high: about **94%** of 1-tick net and **96%** of 3-tick net;
- the active completed-5m IOC8 paper path did not cleanly inherit the improvement;
- no paper-fill authority, DEMO authority, live authority, strategy replacement, stop change, target change or risk-rule change was added.

### 2. Shared Signa snapshot path — #803 / #805

Signa raw responses now have a shared snapshot layer:

- raw snapshots are stored in `signa_snapshots`;
- each snapshot has a deterministic `snapshot_id`;
- options-specific context rows can reference the shared `snapshot_id`;
- futures context can later reference the same `snapshot_id` instead of pulling QQQ/SPY/VIX/TLT again.

This prevents duplicate pulls and mismatched timestamps between options and futures. It does not create trading authority.

### 3. Futures Signa Context Lane v2 — #807

The futures journal context now includes `context.signa_futures_context` with shared snapshot references when available.

Purpose:

- regime/context tagging;
- confirmation/conflict tagging;
- research segmentation;
- shared `snapshot_id` attribution back to `signa_snapshots`.

It is **not** a trade trigger and not a validator.

Proxy mapping:

| Futures | Proxy context |
|---|---|
| MES / ES | SPY |
| MNQ / NQ | QQQ |
| M2K / RTY | IWM |
| MYM / YM | DIA |
| MGC / GC | GLD |
| MCL / CL | USO + XLE |
| MBT | BTC |

The lane also recognizes broader regime proxies such as `TLT` and `VIX` for shared snapshot collection/segmentation.

New v2 fields include:

- `schema_version = signa_futures_context_v2`;
- `snapshot_ids`;
- `snapshot_refs`;
- `snapshot_status`;
- per-observation `snapshot_id`;
- per-observation `snapshot_ref`;
- per-observation `snapshot_age_seconds`.

Possible tags include:

- `SIGNA_FTFC_LONG`;
- `SIGNA_MIXED`;
- `SIGNA_INCOMPLETE`;
- `SIGNA_INDEX_LONG`;
- `SIGNA_INDEX_SHORT`;
- `SIGNA_INDEX_UNKNOWN`;
- `RISK_ON`;
- `RISK_OFF`;
- `SIGNA_TRADE_ALIGNED`;
- `SIGNA_TRADE_CONFLICT`;
- `SECTOR_SUPPORTIVE`;
- `SECTOR_CONFLICT`;
- `SIGNA_CONTEXT_MISSING`;
- `SIGNA_CONTEXT_ERRORS`.

Authority boundaries:

- `observation_only=true`;
- `gate_authoritative=false`;
- `broker_evaluated=false`;
- `risk_evaluated=false`;
- `trade_authorized=false`;
- `execution_authority=false`.

It does **not** enter, block, rank, resize or reroute futures trades. The futures runtime reads the shared snapshot database in read-only mode only. It does not call Signa, create the database, create tables, or mutate options data.

## Current lane posture

| Lane / feature | Current posture |
|---|---|
| MNQ 4HR broad control | Paper evidence + guarded Tradovate DEMO evidence route remains the only DEMO-approved lane |
| MNQ 4HR 4H 2→2 continuation | Observation metadata only; no paper-fill, DEMO or live authority |
| Signa shared snapshots | Shared raw evidence layer; no trade authority |
| Futures Signa Context v2 | Journal context + shared snapshot references only; no execution authority |
| 3-2-2 First Live | Evidence/paper-only; not DEMO-approved |
| Daily 2-2 | Paper only; completed-close architecture remains separate from broken true-touch variant |
| Miyagi | Parked / replay fixed / thin sample; no deployment action |
| MES/MGC/MCL/M2K/MBT expansion | Observation/research only unless separately proven |
| Live trading | HOLD |

## Latest proof summary

For #798:

- PR #798 merged to `main` as `780b5ea`;
- GitHub CI passed;
- CodeQL passed;
- candidate release integrity: **1,374/1,374**;
- PaperBroker-isolated candidate verification passed;
- deployed release integrity passed.

For #799:

- PR #799 merged to `main` as `98c360d`;
- focused Signa/runner tests: **40 passed**;
- local full repo suite: **6,310 passed / 7 skipped**;
- GitHub CI passed;
- CodeQL passed;
- candidate release integrity: **1,377/1,377**;
- PaperBroker-isolated candidate verification passed;
- promotion gate passed;
- deployed release integrity passed.

For #803 / #805 / #807:

- shared snapshot store PR #803 merged as `a7e0c16`;
- options shared-snapshot consumer PR #805 merged as `4550d22`;
- futures shared-snapshot consumer PR #807 merged/deployed as `a023202`;
- futures Signa context tests: **9 passed**;
- Signa-related tests: **175 passed**;
- runner/context tests: **51 passed**;
- local full repo suite: **6,355 passed**;
- GitHub PR checks passed;
- post-merge main CI and CodeQL passed;
- immutable release integrity: **1,390/1,390**;
- candidate verified under PaperBroker-isolated posture;
- deployed release integrity passed.

## Required next step

Seed or verify the shared snapshot store with a controlled read-only Signa pull for the proxy set:

```text
QQQ, SPY, IWM, DIA, TLT, VIX, GLD, USO, XLE
```

Then wait for the next natural futures setup and confirm the journal row includes:

1. 4HR arm exists before touch;
2. true 1m touch is timestamped;
3. completed-1H stop anchor is causal;
4. 4H continuation tag is present and correct when applicable;
5. `context.signa_futures_context.snapshot_ids` is present when a fresh shared snapshot exists;
6. `context.signa_futures_context.snapshot_refs` is present when a fresh shared snapshot exists;
7. `context.signa_futures_context.snapshot_status` correctly reports OK, missing, stale, or partial;
8. no fill/risk/execution authority leaks from observer metadata;
9. broad control and treatment subset can be compared later without double-counting.

## Do not touch

- live execution;
- broker order submission beyond already guarded DEMO evidence route;
- risk loosening;
- stop/target changes;
- 4HR replacement with the continuation treatment;
- Signa as a futures entry/blocking gate;
- instrument expansion beyond observation/research;
- old historical evidence rewriting.
