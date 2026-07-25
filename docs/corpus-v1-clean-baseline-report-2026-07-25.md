# Corpus v1 — Clean Baseline Evidence Report (VERIFIED)

**Status: VERIFIED.** Regenerated end-to-end after PR #332 fixed the root cause
(`replay/replay_engine.py` never wrote `paper_order_id` to the journal). Same
already-downloaded Polygon candle data, fresh replay run, identity-based
TRADE↔OUTCOME join. **0 unjoinable trades across both instruments, every split.**
The numbers below are byte-identical to the original (pre-correction) FIFO-derived
figures — this dataset never had a genuine misattribution, but that was previously
an assumption, not a proof. It is now a proof.

**Pinned version**: `main@a5434794e471137af83f6e5886b535fb9e3cfcd5` (post-#332)
**Instruments**: MNQ, MES
**Range**: 2025-07-24 → 2026-07-23 (12 completed months)
**Source**: fresh Polygon historical data, canonical 15m replay timeframe, current production config (unmodified) — realistic stop-first same-bar fills, commissions + slippage included, `strategy_status` gate applied as-is. No strategy changes during the run.

Raw artifacts:
- `scripts/corpus_v1_results.json` — full machine-readable results (this doc is rendered from it)
- `scripts/corpus_v1_raw_trades.jsonl` — one row per trade (all 747 resolved or open-with-identity; 0 unjoinable), both instruments
- `logs/replay_corpus_v1/{MNQ,MES}/journal_YYYY-MM-DD.jsonl` — raw per-day replay journals (immutable, includes every NO_TRADE/RISK_REJECTED/TRADE/OUTCOME row, each carrying its `paper_order_id`)
- `data/replay_corpus_v1/{MNQ,MES}/` — derived replay candles used as replay input (unchanged from the original run — no new Polygon pull was needed)

## Correction history (for the audit trail)

1. **First version**: FIFO TRADE/OUTCOME pairing (copied from `ioc_baseline_622d_analysis.py`) — reintroduced the pre-#327 defect. REJECTED on operator review.
2. **Second version**: fixed to `JournalReader`'s identity join, which proved the *then-current* journals carried no `paper_order_id` at all (`replay_engine.py` never wrote it) — every trade came back `unjoinable_legacy`, 0 resolved, $0 net P&L. RETRACTED the $53,428 figure pending root-cause fix.
3. **Root-cause fix**: PR #332 threaded `paper_order_id` from `PaperBroker` `Fill` objects through `replay_engine.py` into the journal (no FIFO fallback, no new id scheme). Merged to `main@a5434794e471137af83f6e5886b535fb9e3cfcd5`.
4. **This version**: full Corpus v1 replay rerun from the same already-downloaded candle data under the fixed engine. 0 unjoinable everywhere. Numbers match step 1's original figures exactly — proving those figures were factually correct all along, even though the process that produced them (FIFO) could not be trusted to guarantee that in general.

## Full period, by instrument

| Instrument | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|
| MNQ | 420 | 419 | 0 | 48.4% | $36,831 | $88 |
| MES | 327 | 305 | 0 | 40.7% | $16,598 | $54 |
| COMBINED | 747 | 724 | 0 | 45.2% | $53,428 | $74 |

## H1 vs H2

H1: 2025-07-24 → 2026-01-23  ·  H2: 2026-01-24 → 2026-07-23

| Scope | Half | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |
|---|---|---:|---:|---:|---:|---:|---:|
| MNQ  H1 | 211 | 210 | 0 | 45.7% | $8,256 | $39 |
| MNQ  H2 | 209 | 209 | 0 | 51.2% | $28,575 | $137 |
| MES  H1 | 162 | 150 | 0 | 39.3% | $3,804 | $25 |
| MES  H2 | 165 | 155 | 0 | 41.9% | $12,793 | $83 |
| COMBINED  H1 | 373 | 360 | 0 | 43.1% | $12,060 | $34 |
| COMBINED  H2 | 374 | 364 | 0 | 47.2% | $41,368 | $114 |

H1 and H2 are sign-consistent (both positive) but magnitude-uneven — H2 carries most
of the combined edge ($41,368 vs $12,060). This is the walk-forward split the 12-month
range was specifically chosen to enable; it does not by itself indicate a problem, but
it means the full-period aggregate should not be read as evenly distributed across the year.

## Quarterly

| Scope | Quarter | Range | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |
|---|---|---|---:|---:|---:|---:|---:|---:|
| MNQ | Q1 | 2025-07-24..2025-10-23 | 120 | 120 | 0 | 49.2% | $5,067 | $42 |
| MNQ | Q2 | 2025-10-24..2026-01-23 | 91 | 90 | 0 | 41.1% | $3,188 | $35 |
| MNQ | Q3 | 2026-01-24..2026-04-23 | 105 | 105 | 0 | 52.4% | $13,424 | $128 |
| MNQ | Q4 | 2026-04-24..2026-07-23 | 104 | 104 | 0 | 50.0% | $15,151 | $146 |
| MES | Q1 | 2025-07-24..2025-10-23 | 77 | 71 | 0 | 36.6% | $513 | $7 |
| MES | Q2 | 2025-10-24..2026-01-23 | 85 | 79 | 0 | 41.8% | $3,292 | $42 |
| MES | Q3 | 2026-01-24..2026-04-23 | 81 | 77 | 0 | 50.6% | $10,448 | $136 |
| MES | Q4 | 2026-04-24..2026-07-23 | 84 | 78 | 0 | 33.3% | $2,346 | $30 |
| COMBINED | Q1 | 2025-07-24..2025-10-23 | 197 | 191 | 0 | 44.5% | $5,580 | $29 |
| COMBINED | Q2 | 2025-10-24..2026-01-23 | 176 | 169 | 0 | 41.4% | $6,480 | $38 |
| COMBINED | Q3 | 2026-01-24..2026-04-23 | 186 | 182 | 0 | 51.6% | $23,871 | $131 |
| COMBINED | Q4 | 2026-04-24..2026-07-23 | 188 | 182 | 0 | 42.9% | $17,497 | $96 |

All four quarters are individually positive.

## Per-strategy — full period

### MNQ

| Strategy | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|
| orb_reclaim | 263 | 262 | 0 | 46.6% | $28,027 | $107 |
| orb_breakout | 68 | 68 | 0 | 54.4% | $3,656 | $54 |
| vwap_reclaim | 50 | 50 | 0 | 56.0% | $3,759 | $75 |
| orb_rejection | 16 | 16 | 0 | 18.8% | $128 | $8 |
| pdl_reclaim | 15 | 15 | 0 | 53.3% | $807 | $54 |
| vwap_rejection | 8 | 8 | 0 | 62.5% | $453 | $57 |

### MES

| Strategy | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|
| orb_reclaim | 327 | 305 | 0 | 40.7% | $16,598 | $54 |

### COMBINED

| Strategy | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|
| orb_reclaim | 590 | 567 | 0 | 43.4% | $44,624 | $79 |
| orb_breakout | 68 | 68 | 0 | 54.4% | $3,656 | $54 |
| vwap_reclaim | 50 | 50 | 0 | 56.0% | $3,759 | $75 |
| orb_rejection | 16 | 16 | 0 | 18.8% | $128 | $8 |
| pdl_reclaim | 15 | 15 | 0 | 53.3% | $807 | $54 |
| vwap_rejection | 8 | 8 | 0 | 62.5% | $453 | $57 |

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

- This is a **descriptive evidence run**, not a go/no-live decision. No strategy or gate code changed to produce these numbers — the only code changes across this whole exercise were in the analysis/reporting layer (`scripts/corpus_v1_report.py`) and the replay-engine identity plumbing (`execution/paper_broker.py`, `replay/replay_engine.py`, PR #332) — never in decision/signal/risk logic.
- The standing evidence-phase directive (no new strategies/gates/runtime changes until 2026-09-30 or a DIRTY packet / ≥20-resolved-reversal-trades review) is untouched by any part of this work.
- The old 622-day corpus (`data/replay_polygon/{MNQ,MES}/`) is unaffected and untouched — kept separate under `data/replay_corpus_v1/`. It keeps its existing caveat: original Polygon source data is gone, so whether the same identity gap affected it is unprovable, never retroactively certified clean by this work. This is a separate, additional, version-bound baseline.
- `strategy_status` (`risk_rules.yaml`) was left exactly as currently configured in production — the why-no-trade breakdown correctly shows `SHADOW_ONLY`/`DISABLED` strategies as blocked-candidate attempts (e.g. `STRATEGY_NOT_PAPER_ELIGIBLE`) that never execute a trade.
- **Next decision (not made here)**: whether/how these results inform any promotion or go-live decision is separate and not yet made — see the standing [[project_promotion_gate_blocker]] and [[project_golive_gate]] threads.

