# VWAP Hold — reconciliation of the 2026-07-26 NY-only cell with the 2026-09-07 audit

Status: **CLOSED — the NY-only ioc_close cell is a fill-reference artifact; under the production-faithful reference VWAP Hold is negative.** Evidence only; no strategy, risk, replay, broker, config, or deployment change.

Script: `scripts/vwap_hold_reconciliation_2026-09-07.py` → `scripts/vwap_hold_reconciliation_2026-09-07.json`. It imports the 2026-07-26 package's own `ioc_fill`, `resolve_via_broker` and `cell_metrics` unmodified, and reproduces the committed 348-arm `ioc_close` matrix **field-for-field ("EXACT MATCH on every field, every cell")** before changing anything.

## The two results were not comparable on any axis

| Axis | 2026-07-26 NY-only cell | 2026-09-07 audit lane |
|---|---|---|
| Population | 348 replay-**APPROVED** `vwap_hold` rows (107 NY) from `logs/retest_baseline_off` | 4,579 **ungated** `_try_vwap_hold` hits |
| Corpus | 5m Polygon, 2024-07-02 → 2026-06-26 | 15m condition-fixed, 2025-07-24 → 2026-07-23 |
| Fill reference | close of the first 5m bar at/after `bar_ts + 15m` | the 15m decision bar's own close |
| Entry slippage | 0 (costs at the metrics layer) | 1–3 ticks inside the broker |
| Concurrency | unlimited | one position at a time |
| Half split | trade-count median | date median |

This pass holds corpus (5m Polygon), fill model (package `ioc_fill`), exit engine (package `resolve_via_broker`) and half-split (both rules reported) fixed, and varies only the population and the fill reference.

## Finding 1 — the gates do select, but for fillability, not direction

The same replay rejected 1,609 `vwap_hold` rows; **1,598 of them failed `ENTRY_DETACHED_FROM_PRICE`** (1,268 on that gate alone). The audit's 6–8% IOC fill rate was the ungated population — entries at VWAP − 2 ticks with price already far below VWAP — not a fill-model difference: the broker's `ioc_limit` and the package's `ioc_fill` apply the identical "limit at entry ∓ 32 ticks" rule.

Next-bar direction control (entry at the arrival close ± 1 tick, exits at 30/60/120 min and end of day, no stop, $1.48 round-turn):

| Population | n | Positive horizons | Best t | 60 min mean / t | EOD mean / t |
|---|---:|---:|---:|---|---|
| Approved, all | 348 | 4/4 | 1.86 | +$12.20 / 1.86 | +$9.86 / 0.38 |
| Approved, NY | 107 | 4/4 | 1.47 | +$16.10 / 1.00 | +$19.09 / 0.67 |
| Approved, non-NY | 241 | 4/4 | 1.69 | +$10.47 / 1.69 | +$5.76 / 0.17 |
| Rejected, all | 1,609 | 0/4 | −0.50 | −$2.52 / −0.59 | −$28.60 / −1.80 |
| Rejected, NY | 744 | 4/4 | 1.12 | +$2.67 / 0.36 | +$10.53 / 0.72 |
| Rejected, non-NY | 865 | 0/4 | −1.24 | −$6.98 / −1.43 | −$62.21 / −2.33 |

The detached-entry gate separates a weakly-positive set from a directionless-to-negative one, so it is doing real work. But the approved set's direction never reaches t = 2 at any horizon, and **NY is not where it lives** — the non-NY approved control (t 1.69) is at least as good as NY (t 1.47). The "NY-specific edge" reading of 2026-07-26 does not survive a direction control.

## Finding 2 — the NY-only cell's sign depends on a 5-minute look-ahead in the fill reference

Journal `bar_ts` is the 15-minute bar's **open** time. The replay engine that generated these arms fills at `candle.close` of that decision candle (`replay/replay_engine.py:755`), i.e. the price at `bar_ts + 15m` — which is the close of the 5m bar starting at `bar_ts + 10m`, and (to within a tick) the **open** of the 5m bar starting at `bar_ts + 15m`. The package's `ioc_close` checks marketability and fills at that arrival bar's **close**: the price five minutes after the IOC would have been sent. Its `ioc_open` leg (the PR #307 baseline) is the honest reference.

Static exit, 2-tick cost, identical arms and exit engine:

| Population | Fill reference | Filled | Net | PF | H1 / H2 |
|---|---|---:|---:|---:|---|
| NY-107 | arrival-bar **close** (2026-07-26 canonical) | 55 (51%) | **+$458.80** | 2.18 | +$174 / +$284 |
| NY-107 | arrival-bar **open** (package `ioc_open`) | 35 (33%) | −$143.92 | 0.74 | −$65 / −$79 |
| NY-107 | **decision-bar close** (replay / production) | 35 (33%) | **−$326.92** | **0.49** | −$125 / −$202 |
| All-348 | arrival-bar close | 146 (42%) | +$960.92 | 1.70 | +$834 / +$127 |
| All-348 | arrival-bar open (committed `ioc_open` cell) | 105 (30%) | −$41.28 | 0.97 | H2 negative |
| All-348 | decision-bar close | 105 (30%) | +$126.82 | 1.08 | +$546 / −$419 |

Runner exit is not a rescue: NY-107 under the decision-bar reference is −$3.26 / PF 1.00 on 35 fills, and its +$556 under `ioc_open` versus −$3 under decision-close — a few ticks of entry difference — shows how fragile a 0.5R trail on a 7-point stop is.

The 2026-07-26 operator decision that "`close` is canonical" was correct about production; it was applied to the wrong bar. Production and replay use the **decision bar's** close, which the package's `ioc_open` approximates and its `ioc_close` overshoots by five minutes. Twenty of the 55 "fills" exist only because the price moved toward the entry during those five minutes, and the P&L sign follows them.

## Finding 3 — time windows and rejected fills add nothing

- Approved NY arms inside the audit's window (2025-07-24 → 2026-06-26, n=52): control 2/4 positive, best t 1.36; static 2-tick +$272.94 on 22 fills under the arrival-close reference but −$155.74 / PF 0.48 under the decision-bar reference. Pre-window (n=55): +$185.86 vs −$171.18. Neither period rescues the cell.
- The 40 rejected arms that were still IOC-fillable (2.5%) made +$935 / PF 4.6 — a curiosity on n=40, not evidence; they are by construction the rare rejected rows where price came back to the entry.

## Verdict

The raw predicate carries no direction (audit); the replay's detached-entry gate selects a weakly-positive subset (t ≤ 1.9) that is not NY-specific; and the one cell that passed every gate on 2026-07-26 passes only under a fill reference five minutes later than the order would exist. Under the reference the replay engine and production actually use, the canonical NY-only cell is **35 fills, −$326.92, PF 0.49, both halves negative** at 2 ticks — it fails honest fill and walk-forward.

Per the inventory taxonomy that is **BROKEN — negative evidence**, superseding PROMISING BUT UNPROVEN. The open exit-mode and sample-expansion items are moot. The `ioc_close` re-scoring doc and its JSON stay as provenance; their headline should be read as "arrival-bar-close reference", not as the production fill.

What would reopen it: a fresh population under the decision-bar reference with both halves positive on ≥ 60 fills — nothing in the current corpus supplies that.
