# Bar evidence — AFS-0523 honest-IOC mismatches

Read-only. No scoring, no gate call, no Databento/Polygon API, no edits to the data root or checkouts.

## Verdict

**CONFIRMED.** The 12 honest-IOC mismatches are the decision-bar 5-minute close disagreeing between the Oct 4 Polygon reference and the Databento-built 5m tree. The close deltas are exactly +0.75 on 2024-09-06 and −0.25 on 2026-06-11, in the same direction as the fill deltas. Open, high, and low agree on both days. Volume agrees on both days. Reconstructing the SHORT IOC fill as `close − slippage` recovers every observed expected and actual fill at 1, 2, and 3 ticks.

## How the decision bar is identified

In `/workspace/tmp/afs-gate-000f4312`:

- `research/afs0523_322_repro_wrapper.py` `_run_window` loads the 5m audit tree with `load_bars(audit_5m, "MNQ")` and scores honest-IOC via `run_bracket_stage(..., fill_model="ioc_limit")`.
- `scripts/edge_decomposition_audit.py` `resolve_bracket` takes `row = bars.rows[cand.bar_idx]` and calls `PaperBroker.execute_bracket(..., market_price=float(row["close"]))`.
- `execution/paper_broker.py` documents `market_price` as the decision bar's close. For `ioc_limit`, LONG fills at `min(entry + 32 ticks, close + slip)` and SHORT fills at `max(entry - 32 ticks, close - slip)`. MNQ tick size is 0.25. Honest-IOC is the only of the three models that fills at that close; original/pre-armed fill at trigger/touch levels and already matched.
- `scripts/322_trigger_timing_ab_2026_09_18.py` `ledger()` writes that same `bars.rows[c.bar_idx]` as `trigger_bar_ts` / `trigger_bar_open/high/low/close`.

The Oct 4 reference of record is `docs/research-evidence/T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01/result.json` (sha256 `0d0d8d72c48b3c9dd1e7065b23a5422176a765ac7b1eb594e2833b72f5d5a9b1`). Its `trigger_bar_*` fields are the decision bar used for the reference IOC fills.

## Corpora

- Official Oct 4 5m path: `data/replay_corpus_v1_5m_4hr_audit/MNQ`, tree pin `7f09a7f82ee282e892f5db9b86be3130e6a5c1210d86828a56b9666bb06afd35`. That directory is **not present** on this box under that name.
- Polygon 5m actually read: `/workspace/transition_export_2026-10-01/repo/data/replay_polygon_5m/MNQ` (621 day files, tree sha256 `a290d0c45089ce49d7750adccce0b20f6a56725d740a51e2382649701b7e9fbd`). It does not byte-match the official pin (these files include indicator columns). Its decision-bar OHLC matches the Oct 4 ledger on all 34 candidate dates, so the prices below are the reference prices of record.
- Databento 5m: `/workspace/afs-shared/data/databento_afs0523_2026-11-09/reproduction/audit_5m/MNQ` (pinned tree `a08d909274fa5a06e9234b1c0975977d91a00b1f749c0560fd6d41859410c1e3`).
- Both trees timestamp bars as UTC bar-start instants.
- 1-minute raw exists: `/workspace/afs-shared/data/databento_afs0523_2026-11-09/reproduction/MNQ.FUT_ohlcv-1m_20240702_20260627.parent.dbn.zst`. No decoded 1-minute JSONL tree is present, and the `databento` Python decoder is not installed. The five 1-minute bars inside each 5-minute window were **not** extracted. No API was used.

Contract labels: Polygon 5m rows carry `instrument: "MNQ"`. Databento 5m rows have no contract field; they live under `audit_5m/MNQ/`. The adapter selects the front-month outright with `sources.polygon_client.front_contract` and `roll_days=8`, then writes a continuous MNQ tree. Calendar front month: 2024-09-06 → `MNQU4`; 2026-06-11 → `MNQU6`. Root symbol matches (`MNQ`). Specific expiry is not stored in either 5m JSONL.

## 2024-09-06

- Direction: **SHORT**
- Trigger: 18741.5
- Decision bar: `2024-09-06T14:00:00+00:00` (2024-09-06 10:00 EDT)
- Calendar front month: `MNQU4`
- Oct 4 IOC 1-tick fill (reference): 18753.75 (`RESOLVED` / TARGET_HIT)

| source | timestamp | open | high | low | close | volume | instrument |
|---|---|---:|---:|---:|---:|---:|---|
| Oct 4 ledger trigger_bar (Polygon run) | 2024-09-06T14:00:00+00:00 | 18745.75 | 18768.0 | 18709.5 | 18754.0 | — | MNQ |
| Polygon 5m export | 2024-09-06T14:00:00+00:00 | 18745.75 | 18768.0 | 18709.5 | 18754.0 | 32519 | MNQ |
| Databento audit_5m | 2024-09-06T14:00:00+00:00 | 18745.75 | 18768.0 | 18709.5 | **18754.75** | 32519 | MNQ (path) |

- Open/high/low agree: **true**
- Volume agrees: **true** (32519)
- Close difference (Databento − Oct 4/Polygon): **+0.75**

| slippage | Oct 4 fill (expected) | implied from Oct 4 close | Databento fill (actual) | implied from Databento close | fill delta | net expected | net actual | net delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1tick | 18753.75 | 18753.75 | 18754.5 | 18754.5 | +0.75 | 60.02 | 61.52 | +1.50 |
| 2tick | 18753.5 | 18753.5 | 18754.25 | 18754.25 | +0.75 | 59.52 | 61.02 | +1.50 |
| 3tick | 18753.25 | 18753.25 | 18754.0 | 18754.0 | +0.75 | 59.02 | 60.52 | +1.50 |

SHORT IOC fill = close − N ticks. Close delta +0.75 equals every fill delta. Net delta is 1.50 dollars per contract (MNQ $2/point), matching the observed nets.

## 2026-06-11

- Direction: **SHORT**
- Trigger: 28927.75
- Decision bar: `2026-06-11T14:55:00+00:00` (2026-06-11 10:55 EDT)
- Calendar front month: `MNQU6`
- Oct 4 IOC 1-tick fill (reference): 28928.5 (`RESOLVED` / TARGET_HIT)

| source | timestamp | open | high | low | close | volume | instrument |
|---|---|---:|---:|---:|---:|---:|---|
| Oct 4 ledger trigger_bar (Polygon run) | 2026-06-11T14:55:00+00:00 | 29029.5 | 29034.75 | 28884.0 | 28928.75 | — | MNQ |
| Polygon 5m export | 2026-06-11T14:55:00+00:00 | 29029.5 | 29034.75 | 28884.0 | 28928.75 | 728 | MNQ |
| Databento audit_5m | 2026-06-11T14:55:00+00:00 | 29029.5 | 29034.75 | 28884.0 | **28928.5** | 728 | MNQ (path) |

- Open/high/low agree: **true**
- Volume agrees: **true** (728)
- Close difference (Databento − Oct 4/Polygon): **-0.25**

| slippage | Oct 4 fill (expected) | implied from Oct 4 close | Databento fill (actual) | implied from Databento close | fill delta | net expected | net actual | net delta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1tick | 28928.5 | 28928.5 | 28928.25 | 28928.25 | -0.25 | 10.02 | 9.52 | -0.50 |
| 2tick | 28928.25 | 28928.25 | 28928.0 | 28928.0 | -0.25 | 9.52 | 9.02 | -0.50 |
| 3tick | 28928.0 | 28928.0 | 28927.75 | 28927.75 | -0.25 | 9.02 | 8.52 | -0.50 |

SHORT IOC fill = close − N ticks. Close delta -0.25 equals every fill delta. Net delta is 0.50 dollars per contract (MNQ $2/point), matching the observed nets.

## Spot-check: other IOC fills whose fill_price matched

There are 20 honest-IOC fills in the Oct 4 1-tick ledger (19 `RESOLVED` + 1 `UNRESOLVED` / `EOD_BAR_MISSING` on 2025-01-20). Two of those are the mismatch days above. That leaves **18** fills whose fill_price matched, of which **17** are `RESOLVED` — the 17 named in the review note — plus 2025-01-20, whose decision-bar close also agrees.

On every one of those 18 dates the decision-bar close is identical between the Oct 4/Polygon reference and Databento, and open/high/low also agree.

| date | direction | status | decision bar ET | Oct 4 / Polygon close | Databento close |
|---|---|---|---|---:|---:|
| 2024-09-11 | SHORT | RESOLVED | 2024-09-11 10:00 EDT | 18800.0 | 18800.0 |
| 2024-10-10 | LONG | RESOLVED | 2024-10-10 10:05 EDT | 20385.5 | 20385.5 |
| 2024-10-11 | LONG | RESOLVED | 2024-10-11 10:00 EDT | 20361.5 | 20361.5 |
| 2024-11-26 | SHORT | RESOLVED | 2024-11-26 10:15 EST | 20924.5 | 20924.5 |
| 2025-01-20 | SHORT | UNRESOLVED | 2025-01-20 10:40 EST | 21657.5 | 21657.5 |
| 2025-02-26 | LONG | RESOLVED | 2025-02-26 10:00 EST | 21303.0 | 21303.0 |
| 2025-03-17 | SHORT | RESOLVED | 2025-03-17 10:35 EDT | 19945.0 | 19945.0 |
| 2025-03-21 | LONG | RESOLVED | 2025-03-21 10:20 EDT | 19759.75 | 19759.75 |
| 2025-05-01 | LONG | RESOLVED | 2025-05-01 10:25 EDT | 19999.75 | 19999.75 |
| 2025-06-04 | SHORT | RESOLVED | 2025-06-04 10:00 EDT | 21694.5 | 21694.5 |
| 2025-06-05 | LONG | RESOLVED | 2025-06-05 10:40 EDT | 21831.5 | 21831.5 |
| 2025-06-26 | LONG | RESOLVED | 2025-06-26 10:00 EDT | 22564.0 | 22564.0 |
| 2025-08-11 | LONG | RESOLVED | 2025-08-11 10:00 EDT | 23726.25 | 23726.25 |
| 2025-09-05 | SHORT | RESOLVED | 2025-09-05 10:00 EDT | 23837.25 | 23837.25 |
| 2025-09-30 | LONG | RESOLVED | 2025-09-30 10:45 EDT | 24828.75 | 24828.75 |
| 2025-10-16 | SHORT | RESOLVED | 2025-10-16 10:10 EDT | 25043.5 | 25043.5 |
| 2026-04-01 | LONG | RESOLVED | 2026-04-01 10:20 EDT | 24176.75 | 24176.75 |
| 2026-05-13 | LONG | RESOLVED | 2026-05-13 10:50 EDT | 29310.25 | 29310.25 |

## Additional note (does not create a fill mismatch)

One other candidate day has a different decision-bar close: **2026-03-06** LONG at `2026-03-06T15:00:00+00:00`. Oct 4/Polygon close 24751.5 vs Databento 24764.5 (delta +13.0). Open/high/low also disagree that day. IOC status is `NO_FILL` / `ENTRY_NOT_FILLED` on the reference (close is 36 ticks beyond the 32-tick cap). The Databento close is even farther from the trigger, so it is also a no-fill. That is why it did not appear among the 12 mismatches.

## What this does not include

- The five Databento 1-minute bars inside each 5-minute window were not read. Raw 1m parent DBN exists; no local decoder.
- The official `replay_corpus_v1_5m_4hr_audit` directory was not found. Reference OHLC comes from the Oct 4 ledger and a Polygon 5m export whose decision-bar prices match that ledger exactly.

