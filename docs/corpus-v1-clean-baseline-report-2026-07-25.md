# Corpus v1 — Clean Baseline Evidence Report (CORRECTED)

**REJECTED as evidence-valid in its first form (operator review) and corrected in place.**
The original version of this report paired TRADE decisions to OUTCOME rows via a
positional per-instrument FIFO queue — the exact pre-#327 defect
(`adaptive/journal_reader.py`'s `paper_order_id` identity join) reintroduced in a new
script. This version reuses that same identity join instead of a second independent
parser. See **Root cause and delta vs. the original numbers** below before reading
anything else in this document — it changes what the numbers below actually mean.

**Pinned version**: `main@662894654a9edaf2ae66673a34f340966245bc73`
**Instruments**: MNQ, MES
**Range**: 2025-07-24 → 2026-07-23 (12 completed months)
**Source**: fresh Polygon historical data, canonical 15m replay timeframe, current production config (unmodified) — realistic stop-first same-bar fills, commissions + slippage included, `strategy_status` gate applied as-is. No strategy changes during the run.

Raw artifacts:
- `scripts/corpus_v1_results.json` — full machine-readable results (this doc is rendered from it)
- `scripts/corpus_v1_raw_trades.jsonl` — one row per trade (resolved, open-with-identity, or unjoinable_legacy), both instruments
- `logs/replay_corpus_v1/{MNQ,MES}/journal_YYYY-MM-DD.jsonl` — raw per-day replay journals (immutable, includes every NO_TRADE/RISK_REJECTED/TRADE/OUTCOME row)
- `data/replay_corpus_v1/{MNQ,MES}/` — derived replay candles used as replay input

## Root cause and delta vs. the original (FIFO) numbers

Applying the exact-`paper_order_id` identity join proves the existing Corpus v1
journals carry **no usable trade identity at all**: `replay/replay_engine.py` mints a
`paper_order_id` on every `PaperBroker` fill (the field already exists on the `Fill`
dataclass and is populated on every entry/exit), but never forwards it into
`journal.log_decision()` or `journal.log_outcome()`. Every TRADE and OUTCOME row this
run produced has `paper_order_id: null`. A fail-closed identity join against that data
can therefore resolve **zero** trades — not a bug in the corrected reporter, the honest
result of joining against journals that don't carry the join key.

| | Original (FIFO, WRONG) | Corrected (identity join, HONEST) |
|---|---:|---:|
| Attempts | 747 | 747 |
| Resolved | 724 | 0 |
| Unjoinable (no identity) | 0 (never reported) | 747 |
| Win rate | 45.2% | — |
| Net P&L | $53,428 | $0 |

**The original $53,428 / 45.2% WR numbers are not validated and must not be used.**
They were produced by pairing each approved TRADE with whatever OUTCOME happened to
be next in that instrument's queue that day — with no check that it was actually the
same position. On a day with more than one trade per instrument (common here — the
per-day smoke test alone showed multiple same-day trades), a mis-timed or out-of-order
OUTCOME write, or an orphaned CANCELLED row, could silently attach the wrong result to
the wrong trade. Whether it actually did so cannot be determined after the fact without
the identity field — that's exactly why fail-closed is correct here instead of trying to
guess which FIFO-paired results happened to be right.

**This is not evidence that the strategies lost money or that the replay engine is**
**broken** — it is evidence that this specific evidence-generation path cannot currently
prove trade outcomes at all. The underlying replay fills, risk gating, and signal
formation are unaffected; only the journal-to-report join is broken.

**Next decision (not made here)**: fixing this for real requires threading
`paper_order_id` from the `Fill` objects `PaperBroker`/`ReplayEngine` already produce
into `journal.log_decision()`/`log_outcome()` inside `replay/replay_engine.py` itself —
a replay-engine code change, not a reporting-layer one — followed by a full Corpus v1
rerun. That is a separate, not-yet-authorized decision.

**Separate defect noted, not fixed here**: `scripts/run_replay_batch.py::_strategy_breakdown`
still uses the same positional FIFO pairing this report used to use. It reads the same
identity-less replay journals, so switching it to the identity join alone would not change
its output today — it needs the same `replay_engine.py` fix above to matter. Recommend
sharing `adaptive.journal_reader.JournalReader`'s logic there too rather than maintaining
a third independent parser, once the underlying identity gap is fixed.

## Full period, by instrument (post-fix: 0 resolved everywhere — see root cause above)

| Instrument | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|
| MNQ | 420 | 0 | 420 | — | $0 | — |
| MES | 327 | 0 | 327 | — | $0 | — |
| COMBINED | 747 | 0 | 747 | — | $0 | — |

## H1 vs H2

H1: 2025-07-24 → 2026-01-23  ·  H2: 2026-01-24 → 2026-07-23

| Scope | Half | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |
|---|---|---:|---:|---:|---:|---:|---:|
| MNQ  H1 | 211 | 0 | 211 | — | $0 | — |
| MNQ  H2 | 209 | 0 | 209 | — | $0 | — |
| MES  H1 | 162 | 0 | 162 | — | $0 | — |
| MES  H2 | 165 | 0 | 165 | — | $0 | — |
| COMBINED  H1 | 373 | 0 | 373 | — | $0 | — |
| COMBINED  H2 | 374 | 0 | 374 | — | $0 | — |

## Quarterly

| Scope | Quarter | Range | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |
|---|---|---|---:|---:|---:|---:|---:|---:|
| MNQ | Q1 | 2025-07-24..2025-10-23 | 120 | 0 | 120 | — | $0 | — |
| MNQ | Q2 | 2025-10-24..2026-01-23 | 91 | 0 | 91 | — | $0 | — |
| MNQ | Q3 | 2026-01-24..2026-04-23 | 105 | 0 | 105 | — | $0 | — |
| MNQ | Q4 | 2026-04-24..2026-07-23 | 104 | 0 | 104 | — | $0 | — |
| MES | Q1 | 2025-07-24..2025-10-23 | 77 | 0 | 77 | — | $0 | — |
| MES | Q2 | 2025-10-24..2026-01-23 | 85 | 0 | 85 | — | $0 | — |
| MES | Q3 | 2026-01-24..2026-04-23 | 81 | 0 | 81 | — | $0 | — |
| MES | Q4 | 2026-04-24..2026-07-23 | 84 | 0 | 84 | — | $0 | — |
| COMBINED | Q1 | 2025-07-24..2025-10-23 | 197 | 0 | 197 | — | $0 | — |
| COMBINED | Q2 | 2025-10-24..2026-01-23 | 176 | 0 | 176 | — | $0 | — |
| COMBINED | Q3 | 2026-01-24..2026-04-23 | 186 | 0 | 186 | — | $0 | — |
| COMBINED | Q4 | 2026-04-24..2026-07-23 | 188 | 0 | 188 | — | $0 | — |

## Per-strategy — full period

### MNQ

| Strategy | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|
| orb_reclaim | 263 | 0 | 263 | — | $0 | — |
| orb_breakout | 68 | 0 | 68 | — | $0 | — |
| vwap_reclaim | 50 | 0 | 50 | — | $0 | — |
| orb_rejection | 16 | 0 | 16 | — | $0 | — |
| pdl_reclaim | 15 | 0 | 15 | — | $0 | — |
| vwap_rejection | 8 | 0 | 8 | — | $0 | — |

### MES

| Strategy | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|
| orb_reclaim | 327 | 0 | 327 | — | $0 | — |

### COMBINED

| Strategy | Attempts | Resolved | Unjoinable | WR | Net P&L | Expectancy |
|---|---:|---:|---:|---:|---:|---:|
| orb_reclaim | 590 | 0 | 590 | — | $0 | — |
| orb_breakout | 68 | 0 | 68 | — | $0 | — |
| vwap_reclaim | 50 | 0 | 50 | — | $0 | — |
| orb_rejection | 16 | 0 | 16 | — | $0 | — |
| pdl_reclaim | 15 | 0 | 15 | — | $0 | — |
| vwap_rejection | 8 | 0 | 8 | — | $0 | — |

## Why-no-trade — full period, combined

Unaffected by the identity-pairing defect above — NO_TRADE/RISK_REJECTED rows are
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

- This is a **descriptive evidence run**, not a go/no-live decision. No strategy, gate, or runtime code changed as part of generating it (the identity-join fix is in the analysis/reporting layer only — `scripts/corpus_v1_report.py` — not in the replay engine or any strategy).
- The standing evidence-phase directive (no new strategies/gates/runtime changes until 2026-09-30 or a DIRTY packet / ≥20-resolved-reversal-trades review) is untouched by this run.
- The old 622-day corpus (`data/replay_polygon/{MNQ,MES}/`) is unaffected and untouched by this run — kept separate under `data/replay_corpus_v1/`. It keeps its existing caveat: original Polygon source data is gone, so M-05's impact on it is unprovable, never retroactively certified clean. This new corpus does not currently resolve any trades either, for the separate reason documented above.
- `strategy_status` (`risk_rules.yaml`) was left exactly as currently configured in production — the why-no-trade breakdown (unaffected by the identity defect) still correctly shows `SHADOW_ONLY`/`DISABLED` strategies as blocked-candidate attempts (e.g. `STRATEGY_NOT_PAPER_ELIGIBLE`) that never execute a trade.

