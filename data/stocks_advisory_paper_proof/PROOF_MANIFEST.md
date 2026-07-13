# TQQQ/SQQQ Paper Advisory Bot v1 — Forward Paper-Proof Manifest

Status: **PRE-PROOF CONFIGURATION — JULY 10 VALIDATION ONLY — OFFICIAL DAY 1 NOT STARTED**

This file is the single source of truth for the proof window's locked
configuration. Once official Day 1 begins, every field below is frozen for
the entire window. Any code or configuration change pauses the proof and
requires a new versioned manifest (a new file, not an edit to this one).

Manifest frozen at: `2026-07-13T15:24:02Z`

## Core identity

| Field | Value |
|---|---|
| `git_sha` | `b338695` (merge commit for PR #272, "Add manual TQQQ/SQQQ paper-proof harness") |
| `strategy_version` | `tqqq_sqqq_decision_v1` |
| `position_notional` | $1,000 (`floor(1000 / raw_entry_price)`, minimum 1 share) |
| `modeled_slippage` | 0.15% per side (applied to both entry and exit legs) |
| `dedup_key` | `(trade_date, strategy_version)` |
| `journal_path` | `data/stocks_advisory_paper_proof/journal.jsonl` (official — does not yet exist; created on first official run) |

## Proof-completion gates (both required)

| Field | Value |
|---|---|
| `minimum_days` | 20 trading days |
| `minimum_completed_trades` | 30 completed paper trades |
| `minimum_profit_factor` | 1.30 |
| `required_net_expectancy` | > 0 |

## Decision thresholds — labeled explicitly per instruction

`allowed_max_gap_percent = 2.0`, `allowed_min_first_hour_range = 1.0`,
`allowed_max_first_hour_range = 10.0`.

**These are initial v1 operator-selected proof thresholds derived from
existing test fixtures (`tests/test_stocks_tqqq_sqqq_decision.py:105-108`),
not previously validated production settings.** No other numeric default
exists anywhere in the codebase. Once the proof starts, these three values
remain frozen for the entire window — no tuning based on interim results.

## Data sources (locked, hash-verified 2026-07-13)

**Decision/session bars** — the exact bars each official run evaluates:

| Symbol | Path | SHA-256 | Rows read | RTH bars | Coverage |
|---|---|---|---|---|---|
| QQQ | `BATS_QQQ, 5.csv` | `e028f9e1ea5771bfc90b71d8779d25d7a7a66f5b470cbd04dd940983bea95335` | 884 | 387 | 2026-07-06 .. 2026-07-10 (5 dates) |
| TQQQ | `BATS_TQQQ, 5.csv` | `ab52efacafe7d3666f5959c45a9a97ecc0062bfeec4cbff50053ba4ccd8388c0` | 1744 | 702 | 2026-06-29 .. 2026-07-10 (9 dates) |
| SQQQ | `BATS_SQQQ, 5.csv` | `9931f13f300bfc4ed2611a9fd9e5873cf639c279544925738e5ced34ecb9f3f1` | 1743 | 702 | 2026-06-29 .. 2026-07-10 (9 dates) |

**Relative-volume history** — used *only* to compute the 20-clean-session
trailing baseline, never for session/decision bars:

| Symbol | Path | SHA-256 | Rows read | RTH bars | Coverage |
|---|---|---|---|---|---|
| QQQ (history) | `BATS_QQQ, 5 (9).csv` | `85d1e142fe1a8d56564d308aa5b9f3b96f63e56011c9930e178da5d2c615b97c` | 20126 | 8190 | 2026-02-09 .. 2026-07-10 (105 dates) |

All nine numbered QQQ export duplicates (`BATS_QQQ, 5 (1).csv` through `(9)`)
were confirmed byte-for-byte identical in date range/row count during
selection; `(9)` was picked arbitrarily among equals.

## Relative-volume methodology (locked)

```
QQQ cumulative RTH volume through the decision cutoff
÷
average QQQ cumulative RTH volume through the same cutoff
  over the prior 20 complete sessions (malformed/partial sessions excluded)
```

- Decision cutoff = the opening-range window (first 60 minutes from the
  session's first RTH bar, matching `qqq_signal_builder.OPENING_RANGE_MINUTES`)
  plus the one confirmation bar immediately after it — reuses
  `qqq_signal_builder._opening_range_bars()` directly rather than
  re-deriving the cutoff definition.
- A session counts as "complete" only if its first RTH bar starts exactly at
  09:30 (mirrors `csv_loader.build_day_sessions`'s own missing-open-bar
  exclusion) and it has at least one bar after the opening range.
- Full-day volume is never used for this calculation — only cumulative
  volume through the decision cutoff, on both sides of the ratio.

## 2026-07-10 validation replay (NOT official Day 1)

Run once, read-only against production code, to confirm the harness
executes end-to-end against real data before freezing the window.

- Relative volume input: `958,280 ÷ 1,567,768.65 = 0.6112`
  (numerator = cutoff cumulative volume from the locked session file above;
  denominator = average cutoff cumulative volume over the 20 clean prior
  sessions 2026-06-10 through 2026-07-09, from the RV-history file above)
- Result: `decision=NO_TRADE`, `final_status=no_trade`,
  `reason="QQQ is inside the first-hour range"`
- Journaled to `data/stocks_advisory_paper_proof/validation_2026-07-10.jsonl`
  — a separate file from the official proof journal, `data_source` field
  explicitly tagged `VALIDATION_REPLAY_NOT_OFFICIAL_DAY1`.
- **2026-07-10 does not count toward the 20-day / 30-trade gates.** It is
  not backdated into the official window.

## Official Day 1

Not yet started. Official Day 1 = the next completed trading session
**after** this manifest is frozen (2026-07-13T15:24:02Z) — i.e. the first
manual CLI invocation run against a trading day that closes on or after
2026-07-14, writing to the official `journal.jsonl` above. `start_date`
will be recorded here, and in the first official journal entry, once that
run actually happens.

## Operational rules for the duration of the window

- Use only completed session data (no intraday/partial-day runs).
- Do not rerun or replace a recorded day.
- Journal every `TRADE`, `NO_TRADE`, and `INVALID` outcome.
- Do not tune thresholds, sizing, friction, or decision logic.
- Do not delete losses or restart the sample.
- Do not add live feeds, schedulers, broker wiring, or automation during
  the window.
- Any code or configuration change pauses the proof and requires a new
  versioned manifest.
