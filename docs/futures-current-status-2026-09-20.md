# Futures Current Status — 2026-09-20

This is the concise operator-facing source of truth for the futures system after the 2026-09-20 4HR continuation and Signa futures-context work. Historical audit documents remain evidence records; where an older summary conflicts with this file, this file governs current status unless a later dated document explicitly supersedes it.

## Verdict

**PAPER / SHADOW / GUARDED DEMO EVIDENCE ONLY. NO LIVE EXECUTION APPROVED.**

Core rule remains: **No proof, no run.**

## Verified deployed runtime

Active futures runtime after the latest documentation pass:

- deployed release: `98c360da26619407408dbd2bc435540ea37a310e`;
- release integrity: **1,377/1,377 files checked**;
- `LIVE_TRADING_ENABLED=false`;
- `TRADOVATE_ENV=demo`;
- `SCHEDULE_MODE=always_on_shadow`;
- `MAX_CONTRACTS_HARD_CAP=1`;
- `SIGNA_API_ENABLED=true`;
- broker flatness before/after promotion: **0 open positions / 0 working orders**;
- watcher active, futures service active, options scanner active;
- deploy lock absent after promotion;
- watcher has no BLOCKED findings;
- known non-blocking watcher warning remains: `post_epoch_spans_releases`.

This release includes:

- #798 `780b5ea` — 4HR continuation treatment tag in the 1m observer evidence;
- #799 `98c360d` — read-only Signa futures context lane.

## What changed on 2026-09-20

### 1. 4HR 4H 2→2 continuation treatment tagging — #798

The 4HR 1m observer now records read-only treatment metadata for every natural eligible 4HR 1m touch:

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

### 2. Signa Futures Context Lane v1 — #799

The futures journal context now includes `context.signa_futures_context`.

Purpose:

- regime/context tagging;
- confirmation/conflict tagging;
- research segmentation.

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

The lane also plans for broader regime proxies `TLT` and `VIX`, but v1 does not fetch new futures-path Signa data from them. It normalizes Signa data already present on the alert/state.

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
- `trade_authorized=false`.

It does **not** enter, block, rank, resize or reroute futures trades.

## Current lane posture

| Lane / feature | Current posture |
|---|---|
| MNQ 4HR broad control | Paper evidence + guarded Tradovate DEMO evidence route remains the only DEMO-approved lane |
| MNQ 4HR 4H 2→2 continuation | Observation metadata only; no paper-fill, DEMO or live authority |
| Signa Futures Context v1 | Journal context only; no execution authority |
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

## Required next step

Collect natural evidence. Do **not** add another strategy/filter/route merely because the metadata exists.

For the next natural 4HR event, audit:

1. 4HR arm exists before touch;
2. true 1m touch is timestamped;
3. completed-1H stop anchor is causal;
4. 4H continuation tag is present and correct;
5. Signa futures context tag is present/missing/partial as expected;
6. no fill/risk/execution authority leaks from observer metadata;
7. broad control and treatment subset can be compared later without double-counting.

## Do not touch

- live execution;
- broker order submission beyond already guarded DEMO evidence route;
- risk loosening;
- stop/target changes;
- 4HR replacement with the continuation treatment;
- Signa as a futures entry/blocking gate;
- instrument expansion beyond observation/research;
- old historical evidence rewriting.
