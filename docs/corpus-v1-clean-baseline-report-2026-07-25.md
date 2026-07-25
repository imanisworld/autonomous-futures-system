# Corpus v1 — Clean Baseline Evidence Report

**Pinned version**: `main@662894654a9edaf2ae66673a34f340966245bc73`
**Instruments**: MNQ, MES
**Range**: 2025-07-24 → 2026-07-23 (12 completed months)
**Source**: fresh Polygon historical data, canonical 15m replay timeframe, current production config (unmodified) — realistic stop-first same-bar fills, commissions + slippage included, `strategy_status` gate applied as-is. No strategy changes during the run.

Raw artifacts:
- `scripts/corpus_v1_results.json` — full machine-readable results (this doc is rendered from it)
- `scripts/corpus_v1_raw_trades.jsonl` — one row per approved/resolved trade, both instruments
- `logs/replay_corpus_v1/{MNQ,MES}/journal_YYYY-MM-DD.jsonl` — raw per-day replay journals (immutable, includes every NO_TRADE/RISK_REJECTED/TRADE/OUTCOME row)
- `data/replay_corpus_v1/{MNQ,MES}/` — derived replay candles used as replay input

## Full period, by instrument

| Instrument | Attempts | Resolved | WR | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|
| MNQ | 420 | 419 | 48.4% | $36,831 | $88 |
| MES | 327 | 305 | 40.7% | $16,598 | $54 |
| COMBINED | 747 | 724 | 45.2% | $53,428 | $74 |

## H1 vs H2

H1: 2025-07-24 → 2026-01-23  ·  H2: 2026-01-24 → 2026-07-23

| Scope | Half | Attempts | Resolved | WR | Net P&L | Expectancy |
|---|---|---:|---:|---:|---:|---:|
| MNQ  H1 | 211 | 210 | 45.7% | $8,256 | $39 |
| MNQ  H2 | 209 | 209 | 51.2% | $28,575 | $137 |
| MES  H1 | 162 | 150 | 39.3% | $3,804 | $25 |
| MES  H2 | 165 | 155 | 41.9% | $12,793 | $83 |
| COMBINED  H1 | 373 | 360 | 43.1% | $12,060 | $34 |
| COMBINED  H2 | 374 | 364 | 47.2% | $41,368 | $114 |

## Quarterly

| Scope | Quarter | Range | Attempts | Resolved | WR | Net P&L | Expectancy |
|---|---|---|---:|---:|---:|---:|---:|
| MNQ | Q1 | 2025-07-24..2025-10-23 | 120 | 120 | 49.2% | $5,067 | $42 |
| MNQ | Q2 | 2025-10-24..2026-01-23 | 91 | 90 | 41.1% | $3,188 | $35 |
| MNQ | Q3 | 2026-01-24..2026-04-23 | 105 | 105 | 52.4% | $13,424 | $128 |
| MNQ | Q4 | 2026-04-24..2026-07-23 | 104 | 104 | 50.0% | $15,151 | $146 |
| MES | Q1 | 2025-07-24..2025-10-23 | 77 | 71 | 36.6% | $513 | $7 |
| MES | Q2 | 2025-10-24..2026-01-23 | 85 | 79 | 41.8% | $3,292 | $42 |
| MES | Q3 | 2026-01-24..2026-04-23 | 81 | 77 | 50.6% | $10,448 | $136 |
| MES | Q4 | 2026-04-24..2026-07-23 | 84 | 78 | 33.3% | $2,346 | $30 |
| COMBINED | Q1 | 2025-07-24..2025-10-23 | 197 | 191 | 44.5% | $5,580 | $29 |
| COMBINED | Q2 | 2025-10-24..2026-01-23 | 176 | 169 | 41.4% | $6,480 | $38 |
| COMBINED | Q3 | 2026-01-24..2026-04-23 | 186 | 182 | 51.6% | $23,871 | $131 |
| COMBINED | Q4 | 2026-04-24..2026-07-23 | 188 | 182 | 42.9% | $17,497 | $96 |

## Per-strategy — full period

### MNQ

| Strategy | Attempts | Resolved | WR | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|
| orb_reclaim | 263 | 262 | 46.6% | $28,027 | $107 |
| orb_breakout | 68 | 68 | 54.4% | $3,656 | $54 |
| vwap_reclaim | 50 | 50 | 56.0% | $3,759 | $75 |
| orb_rejection | 16 | 16 | 18.8% | $128 | $8 |
| pdl_reclaim | 15 | 15 | 53.3% | $807 | $54 |
| vwap_rejection | 8 | 8 | 62.5% | $453 | $57 |

### MES

| Strategy | Attempts | Resolved | WR | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|
| orb_reclaim | 327 | 305 | 40.7% | $16,598 | $54 |

### COMBINED

| Strategy | Attempts | Resolved | WR | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|
| orb_reclaim | 590 | 567 | 43.4% | $44,624 | $79 |
| orb_breakout | 68 | 68 | 54.4% | $3,656 | $54 |
| vwap_reclaim | 50 | 50 | 56.0% | $3,759 | $75 |
| orb_rejection | 16 | 16 | 18.8% | $128 | $8 |
| pdl_reclaim | 15 | 15 | 53.3% | $807 | $54 |
| vwap_rejection | 8 | 8 | 62.5% | $453 | $57 |

## Why-no-trade — full period, combined

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

- This is a **descriptive evidence run**, not a go/no-live decision. No strategy, gate, or runtime code changed as part of generating it.
- The standing evidence-phase directive (no new strategies/gates/runtime changes until 2026-09-30 or a DIRTY packet / ≥20-resolved-reversal-trades review) is untouched by this run.
- The old 622-day corpus (`data/replay_polygon/{MNQ,MES}/`) is unaffected and untouched by this run — kept separate under `data/replay_corpus_v1/`. It keeps its existing caveat: original Polygon source data is gone, so M-05's impact on it is unprovable, never retroactively certified clean. This is a new, additional, version-bound baseline, not a replacement or validation of the old one.
- `strategy_status` (`risk_rules.yaml`) was left exactly as currently configured in production — strategies marked `SHADOW_ONLY`/`DISABLED` still show up as blocked-candidate attempts in the why-no-trade breakdown (e.g. `STRATEGY_NOT_PAPER_ELIGIBLE`) but never execute a trade, which is correct, expected behavior, not a data gap.

