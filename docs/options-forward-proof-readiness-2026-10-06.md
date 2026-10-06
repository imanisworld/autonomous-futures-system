# Options forward-proof readiness (2026-10-06)

Gate: `options_evidence/proof_readiness.py`. **Day 1 has not started for any
options epoch. Nothing here starts it.**

## What the old docs said vs. what GitHub/main shows

The earlier note said "PR #273 first, Polygon source amendment second,
official proof window not started." Main shows:

| Item | Reality on `main` (verified at `445393f`) |
|---|---|
| PR #273 | **Merged 2026-07-13.** It froze the **TQQQ/SQQQ stock-advisory** paper-proof manifest (`data/stocks_advisory_paper_proof/PROOF_MANIFEST.md`). It is not an options artifact. The recorded sha256 `ab1c99ed…` matches the file. |
| Polygon amendment | `PROOF_MANIFEST_AMENDMENT_1_polygon_source.md` is committed and its hash matches. |
| Superseding amendment | `PROOF_MANIFEST_AMENDMENT_2_tradingview_source.md` **reverted the official source to TradingView/BATS** after Polygon returned HTTP 403 (plan entitlement) for dates ≥ 2026-07-11. Its hash matches. |
| Official Day 1 (stock-advisory) | The amendment text records "Official days: 0 of 20". The official `journal.jsonl` is git-ignored and local-only, so later sessions are **UNVERIFIED** from the repo. |

The "#273 → Polygon" ordering is stale on both counts. Both steps are done,
and a later amendment overrode Polygon. This history belongs to the
stock-advisory lane and does not satisfy any options prerequisite.

## Prerequisites for an options forward-proof epoch

The gate derives five checks from the epoch registry. They cannot be asserted
by hand:

| Prerequisite | Source |
|---|---|
| strategy epoch frozen | registry `status = FROZEN` |
| outcome definitions frozen | no `UNRESOLVED` in the epoch's `target` section |
| OOS reference registered | hash-pinned untouched OOS R outcomes |
| costs/friction frozen | OOS `cost_model` stated |
| no threshold changes during epoch | registry hash validated at load, with the `FROZEN_PINS` test |

Four checks need runtime evidence: an `EvidenceRef` with a source and a
timestamp. A bare `True` is not accepted.

| Prerequisite | Expected evidence |
|---|---|
| capture integrity | `capture_integrity_evidence(records, ...)` over the #1145 setup-capture current records (`SetupCaptureJournal.peek_state()["current"]`) for the proof window. It passes only with `catch_count >= 1` and with every triggered structure an `is_prospective_catch`. Any `MISSED_LATE`, late or SIP-pending `TRIGGERED`, `DATA_BLOCKED` or `AMBIGUOUS` row fails it. |
| dedupe | one #1145 `structure_key` → one canonical signal per epoch (`capture_adapter.fold_capture_rows`, #1151) on live journal output |
| data source frozen | live feed equals the epoch's declared source |
| integrity monitoring active | `ops/options_observer_status.py` (PR #1147) reporting `overall: OK`. That requires: the `setup_capture` heartbeat (latest `_clock` row) is fresh and `clock_ok`; SPX is not `data_delayed`; there are no unreadable journal lines; and `options-setup-capture.service` runs from a pinned release, not the live tree |

## Current status

| Epoch | Ready? | Blockers |
|---|---|---|
| `options_122` / `122-IEX-E1` | **NO** (non-tradable) | stop/target/runner UNRESOLVED; no OOS reference; no cost model. These registry blockers hold even with passing runtime evidence, and a test pins that. The runtime proofs are also absent: #1145 collector timer not installed; observer status not installed; #1145 unit not release-pinned. |
| `OPTIONS_PAPER_V1` (V1-EPOCH-3) | **not registered** in the strategy-epoch registry | it lives in `docs/options_v1_evidence_epoch.json` as a cohort boundary, with no preregistered OOS R distribution |

## To start a clean options proof window

1. The #1145 setup-capture collector (merged `ae8c897`) is deployed
   release-pinned with its timer installed. Capture timing (via
   `capture_integrity_evidence`) and dedupe are then proven on the live box.
   The 2026-10-05 SPY 1H structure was missed by the old scanner path. That is
   historical and is not backfilled.
2. Operator and reconciliation choose the strategy and preregister an epoch
   with resolved stop/target, an untouched OOS R distribution, a cost model,
   and a `FitnessPolicy`. The epoch is added to the registry as FROZEN.
3. Install and run the observer status tool.
4. Run `evaluate_readiness(...)` with the runtime `EvidenceRef`s. Only when it
   returns `ready=True` may the operator record Day 1. Day 1 is the first full
   session after that timestamp, with no backfill.
