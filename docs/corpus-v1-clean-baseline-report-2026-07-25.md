# Corpus v1 — Clean Baseline Evidence Report (VERIFIED, corrected 2026-07-25)

**Status: VERIFIED, corrected.** Regenerated end-to-end after PR #332 fixed the
root cause (`replay/replay_engine.py` never wrote `paper_order_id` to the
journal). Same already-downloaded Polygon candle data, fresh replay run,
identity-based TRADE↔OUTCOME join. **0 unjoinable trades across both
instruments, every split.** As of the 2026-07-25 correction below, **0 open
trades either** — every one of the 747 attempts is now WIN or LOSS. The
figures in every table in this document are the corrected (747-resolved)
numbers, not the original (pre-correction, 724-resolved) run — see
"Correction history" item 5 for the exact delta.

**Pinned version**: `main@a5434794e471137af83f6e5886b535fb9e3cfcd5` (post-#332)
**Instruments**: MNQ, MES
**Range**: 2025-07-24 → 2026-07-23 (12 completed months)
**Source**: fresh Polygon historical data, canonical 15m replay timeframe, current production config (unmodified) — realistic stop-first same-bar fills, commissions + slippage included, `strategy_status` gate applied as-is. No strategy changes during the run.

Raw artifacts:
- `scripts/corpus_v1_results.json` / `scripts/corpus_v1_raw_trades.jsonl` — the **original, pre-correction** run output (724 resolved, 23 open) — kept as the historical record of what the raw per-day replay itself produced. Not what this document's tables reflect.
- `scripts/corpus_v1_results_corrected.json` / `scripts/corpus_v1_raw_trades_corrected.jsonl` — **the current closure record** (747 resolved, 0 open) — this document's tables are rendered from this file. Produced by `scripts/corpus_v1_apply_orphan_correction.py` folding `scripts/corpus_v1_orphan_resolution.py`'s carry-forward resolutions into the original raw trades; see item 5 below and `docs/strategy-validation-pass-2026-07-24.md`.
- `logs/replay_corpus_v1/{MNQ,MES}/journal_YYYY-MM-DD.jsonl` — raw per-day replay journals (immutable, includes every NO_TRADE/RISK_REJECTED/TRADE/OUTCOME row, each carrying its `paper_order_id`)
- `data/replay_corpus_v1/{MNQ,MES}/` — derived replay candles used as replay input (unchanged from the original run — no new Polygon pull was needed)

## Correction history (for the audit trail)

1. **First version**: FIFO TRADE/OUTCOME pairing (copied from `ioc_baseline_622d_analysis.py`) — reintroduced the pre-#327 defect. REJECTED on operator review.
2. **Second version**: fixed to `JournalReader`'s identity join, which proved the *then-current* journals carried no `paper_order_id` at all (`replay_engine.py` never wrote it) — every trade came back `unjoinable_legacy`, 0 resolved, $0 net P&L. RETRACTED the $53,428 figure pending root-cause fix.
3. **Root-cause fix**: PR #332 threaded `paper_order_id` from `PaperBroker` `Fill` objects through `replay_engine.py` into the journal (no FIFO fallback, no new id scheme). Merged to `main@a5434794e471137af83f6e5886b535fb9e3cfcd5`.
4. **Fourth version**: full Corpus v1 replay rerun from the same already-downloaded candle data under the fixed engine. 0 unjoinable everywhere, but 23 attempts (all `orb_reclaim`, 22 MES/1 MNQ) came back genuinely open — no OUTCOME row at all, because `replay_engine.py`'s per-day loop never scans past that day's own candle file. A per-strategy validation pass (`docs/strategy-validation-pass-2026-07-24.md`) initially treated those 23 as safely excludable from WR/PF/expectancy math.
5. **This version (2026-07-25 correction)**: operator HOLD verdict — excluding 23 non-random, single-cause missing outcomes is not neutral and can bias the reported numbers. Resolved via **carry-forward**: `scripts/corpus_v1_orphan_resolution.py` restored each of the 23 exact positions into a `PaperBroker` built from the same production config, then fed it real subsequent-day candles (already on disk) through the same `resolve_position()` call `replay_engine.py` itself uses, until each resolved. All 23 resolved within 1-3 days (11 WIN / 12 LOSS, net +$696.88). `scripts/corpus_v1_apply_orphan_correction.py` then folded those into the raw trades and recomputed every split. **Attempts unchanged at 747; resolved moved 724 → 747; net P&L moved $53,428.05 → $54,124.93 (+1.30%); WR 45.17% → 45.25%; PF (newly reported this pass) 1.957 → 1.959; expectancy $73.80 → $72.46.** The correction is real but immaterial to every classification already on record — it does not touch the H1/H2/Q3 concentration question that actually gates `orb_reclaim`'s PROMISING BUT UNPROVEN status. Carry-forward reflects the *strategy/replay design rule* (`orb_reclaim` has no day-only-exit rule, per `execution/day_only_exit.py`), not a claim about live broker order-management fidelity — those are different questions; see `docs/strategy-validation-pass-2026-07-24.md` for the full distinction and the MES 2026-07-21 Day-TIF-bracket-expiry precedent that makes it worth stating explicitly.

## Full period, by instrument

Corrected (747/747 resolved, 0 open, 0 unjoinable). Pre-correction figures were
724 resolved / $53,428.05 net / no PF reported — see Correction history item 5.

| Instrument | Attempts | Resolved | Open | WR | PF | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|---:|
| MNQ | 420 | 420 | 0 | 48.6% | 2.321 | $36,931 | $88 |
| MES | 327 | 327 | 0 | 41.0% | 1.603 | $17,194 | $53 |
| COMBINED | 747 | 747 | 0 | 45.3% | 1.959 | $54,125 | $72 |

## H1 vs H2

H1: 2025-07-24 → 2026-01-23  ·  H2: 2026-01-24 → 2026-07-23 (corrected)

| Scope | Half | Attempts | Resolved | WR | PF | Net P&L | Expectancy |
|---|---|---:|---:|---:|---:|---:|---:|
| MNQ  H1 | 211 | 211 | 46.0% | 1.859 | $8,356 | $40 |
| MNQ  H2 | 209 | 209 | 51.2% | 2.569 | $28,575 | $137 |
| MES  H1 | 162 | 162 | 40.7% | 1.628 | $4,370 | $27 |
| MES  H2 | 165 | 165 | 41.2% | 1.595 | $12,824 | $78 |
| COMBINED  H1 | 373 | 373 | 43.7% | 1.762 | $12,726 | $34 |
| COMBINED  H2 | 374 | 374 | 46.8% | 2.041 | $41,399 | $111 |

H1 and H2 are sign-consistent (both positive) but magnitude-uneven — H2 carries most
of the combined edge ($41,399 vs $12,726). This is the walk-forward split the 12-month
range was specifically chosen to enable; it does not by itself indicate a problem, but
it means the full-period aggregate should not be read as evenly distributed across the year.

## Quarterly

Corrected. All 23 resolved orphans landed in Q1/Q2/Q3/Q4 as follows: MNQ's 1 orphan
in Q2; MES's 22 spread across all four quarters.

| Scope | Quarter | Range | Attempts | Resolved | WR | PF | Net P&L | Expectancy |
|---|---|---|---:|---:|---:|---:|---:|---:|
| MNQ | Q1 | 2025-07-24..2025-10-23 | 120 | 120 | 49.2% | 2.344 | $5,067 | $42 |
| MNQ | Q2 | 2025-10-24..2026-01-23 | 91 | 91 | 41.8% | 1.552 | $3,288 | $36 |
| MNQ | Q3 | 2026-01-24..2026-04-23 | 105 | 105 | 52.4% | 2.674 | $13,424 | $128 |
| MNQ | Q4 | 2026-04-24..2026-07-23 | 104 | 104 | 50.0% | 2.485 | $15,151 | $146 |
| MES | Q1 | 2025-07-24..2025-10-23 | 77 | 77 | 37.7% | 1.274 | $718 | $9 |
| MES | Q2 | 2025-10-24..2026-01-23 | 85 | 85 | 43.5% | 1.842 | $3,652 | $43 |
| MES | Q3 | 2026-01-24..2026-04-23 | 81 | 81 | 50.6% | 2.541 | $10,595 | $131 |
| MES | Q4 | 2026-04-24..2026-07-23 | 84 | 84 | 32.1% | 1.152 | $2,229 | $27 |
| COMBINED | Q1 | 2025-07-24..2025-10-23 | 197 | 197 | 44.7% | 1.905 | $5,785 | $29 |
| COMBINED | Q2 | 2025-10-24..2026-01-23 | 176 | 176 | 42.6% | 1.674 | $6,940 | $39 |
| COMBINED | Q3 | 2026-01-24..2026-04-23 | 186 | 186 | 51.6% | 2.613 | $24,019 | $129 |
| COMBINED | Q4 | 2026-04-24..2026-07-23 | 188 | 188 | 42.0% | 1.699 | $17,381 | $92 |

All four quarters are individually positive, before and after correction.

## Per-strategy — full period

Corrected. Only `orb_reclaim` changes (it is the only strategy with any orphans);
the other five are byte-identical to the pre-correction figures.

### MNQ

| Strategy | Attempts | Resolved | WR | PF | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|
| orb_reclaim | 263 | 263 | 46.8% | 2.222 | $28,127 | $107 |
| orb_breakout | 68 | 68 | 54.4% | 2.245 | $3,656 | $54 |
| vwap_reclaim | 50 | 50 | 56.0% | 4.309 | $3,759 | $75 |
| orb_rejection | 16 | 16 | 18.8% | 1.449 | $128 | $8 |
| pdl_reclaim | 15 | 15 | 53.3% | 2.856 | $807 | $54 |
| vwap_rejection | 8 | 8 | 62.5% | 4.432 | $453 | $57 |

### MES

| Strategy | Attempts | Resolved | WR | PF | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|
| orb_reclaim | 327 | 327 | 41.0% | 1.603 | $17,194 | $53 |

### COMBINED

| Strategy | Attempts | Resolved | WR | PF | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|
| orb_reclaim | 590 | 590 | 43.6% | 1.880 | $45,321 | $77 |
| orb_breakout | 68 | 68 | 54.4% | 2.245 | $3,656 | $54 |
| vwap_reclaim | 50 | 50 | 56.0% | 4.309 | $3,759 | $75 |
| orb_rejection | 16 | 16 | 18.8% | 1.449 | $128 | $8 |
| pdl_reclaim | 15 | 15 | 53.3% | 2.856 | $807 | $54 |
| vwap_rejection | 8 | 8 | 62.5% | 4.432 | $453 | $57 |

`orb_reclaim` carries most of the volume (590/747 attempts). `orb_rejection` is the
weakest individual strategy (18.8% WR, $128 net P&L on 16 attempts) — smallest sample
of the six, not yet at the "sufficient" threshold either direction.

## Why-no-trade — full period, combined

Unaffected by the identity-pairing history above — NO_TRADE/RISK_REJECTED rows are
complete, standalone decision rows and never needed TRADE/OUTCOME pairing.

Total NO_TRADE/RISK_REJECTED decision rows across both instruments, all bars: **42218**

| Failed gate | Count |
|---|---:|
| SIGNAL_BAR_VOLUME_TOO_LOW | 16731 |
| TREND_STRENGTH_BELOW_REQUIRED | 6956 |
| WEAK_BAR_CLOSE | 4325 |
| EMA_STACK_NOT_ALIGNED_SOFT | 3948 |
| ENTRY_DETACHED_FROM_PRICE | 3779 |
| UNSPECIFIED | 2713 |
| MARKET_CONDITION_NOT_TRENDING | 2137 |
| EMA_STACK_NOT_ALIGNED | 2030 |
| STRATEGY_NOT_PAPER_ELIGIBLE | 1885 |
| MARKET_CONDITION_NOT_TRADABLE | 1881 |
| STRAT_DIRECTION_CONFLICT | 297 |
| min_confluence_grade | 24 |
| max_daily_loss | 1 |

| Blocked candidate strategy | Count |
|---|---:|
| no_candidate | 36529 |
| vwap_hold | 3922 |
| orb_breakout | 854 |
| pdh_reclaim | 599 |
| pdl_reclaim | 146 |
| orb_reclaim | 61 |
| orb_rejection | 57 |
| vwap_reclaim | 31 |
| vwap_rejection | 19 |

## Why-no-trade — per instrument, full period

### MNQ (total: 21932)

| Failed gate | Count |
|---|---:|
| SIGNAL_BAR_VOLUME_TOO_LOW | 7546 |
| TREND_STRENGTH_BELOW_REQUIRED | 5620 |
| ENTRY_DETACHED_FROM_PRICE | 3109 |
| WEAK_BAR_CLOSE | 2021 |
| EMA_STACK_NOT_ALIGNED | 1097 |
| MARKET_CONDITION_NOT_TRENDING | 1084 |
| MARKET_CONDITION_NOT_TRADABLE | 1027 |
| EMA_STACK_NOT_ALIGNED_SOFT | 1024 |
| UNSPECIFIED | 982 |
| STRAT_DIRECTION_CONFLICT | 290 |

### MES (total: 20286)

| Failed gate | Count |
|---|---:|
| SIGNAL_BAR_VOLUME_TOO_LOW | 9185 |
| EMA_STACK_NOT_ALIGNED_SOFT | 2924 |
| WEAK_BAR_CLOSE | 2304 |
| UNSPECIFIED | 1731 |
| STRATEGY_NOT_PAPER_ELIGIBLE | 1690 |
| TREND_STRENGTH_BELOW_REQUIRED | 1336 |
| MARKET_CONDITION_NOT_TRENDING | 1053 |
| EMA_STACK_NOT_ALIGNED | 933 |
| MARKET_CONDITION_NOT_TRADABLE | 854 |
| ENTRY_DETACHED_FROM_PRICE | 670 |

## Scope notes / what this is not

- This is a **descriptive evidence run**, not a go/no-live decision. No strategy or gate code changed to produce these numbers — the only code changes across this whole exercise were in the analysis/reporting layer (`scripts/corpus_v1_report.py`, `scripts/corpus_v1_orphan_resolution.py`, `scripts/corpus_v1_apply_orphan_correction.py`) and the replay-engine identity plumbing (`execution/paper_broker.py`, `replay/replay_engine.py`, PR #332) — never in decision/signal/risk logic. The 2026-07-25 orphan correction specifically touches none of `replay_engine.py`, execution code, strategy configuration, or gates — it is a read-only analysis script orchestrating already-existing, already-audited `PaperBroker`/config code.
- The standing evidence-phase directive (no new strategies/gates/runtime changes until 2026-09-30 or a DIRTY packet / ≥20-resolved-reversal-trades review) is untouched by any part of this work.
- The old 622-day corpus (`data/replay_polygon/{MNQ,MES}/`) is unaffected and untouched — kept separate under `data/replay_corpus_v1/`. It keeps its existing caveat: original Polygon source data is gone, so whether the same identity gap affected it is unprovable, never retroactively certified clean by this work. This is a separate, additional, version-bound baseline.
- `strategy_status` (`risk_rules.yaml`) was left exactly as currently configured in production — the why-no-trade breakdown correctly shows `SHADOW_ONLY`/`DISABLED` strategies as blocked-candidate attempts (e.g. `STRATEGY_NOT_PAPER_ELIGIBLE`) that never execute a trade.
- **Next decision (not made here)**: whether/how these results inform any promotion or go-live decision is separate and not yet made — see the standing [[project_promotion_gate_blocker]] and [[project_golive_gate]] threads.

