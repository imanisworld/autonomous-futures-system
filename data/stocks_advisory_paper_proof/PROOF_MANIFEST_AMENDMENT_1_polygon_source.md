# Amendment 1 — Forward Data Source Changed to Polygon

Status: **PRE-PROOF CONFIGURATION — AMENDMENT DRAFTED — OFFICIAL DAY 1 STILL NOT STARTED**

This amendment supersedes **only** the data-source section of
`PROOF_MANIFEST.md` (frozen `2026-07-13T15:24:02Z`,
sha256 `ab1c99ed72b01659ef195e1931c52764166e552e54acfad279f2dccdd8d3955d`,
preserved in PR [#273](https://github.com/imanisworld/autonomous-futures-system/pull/273)).
It is a separate, additional file — the original manifest is not edited,
so its recorded hash remains valid and checkable. Everything else in the
original manifest (thresholds, position sizing, friction/slippage model,
dedup key, proof-completion gates) is unchanged and still governs.

Amendment drafted at: `2026-07-13T16:35:00Z` (approximate — see git commit
timestamp for the authoritative record once committed).

## Why

Because official Day 1 has not started, this is the correct time to change
the locked data source. Fetching from Polygon after each session's close is
more reliable and less error-prone than three manual TradingView exports
per day, and this repo already has a reviewed, tested, unmodified script
for exactly this (`scripts/polygon_stocks_backfill.py`, already used for
the `stocks_advisory` backtest lane and not part of the 9-file paper-proof
harness scope from PR #272).

## New primary forward data source

```text
Primary forward data source: Polygon completed 5-minute aggregate bars
Symbols: QQQ, TQQQ, SQQQ
Session: regular trading hours
Timezone: America/New_York
Frequency: 5 minutes
Retrieval timing: after session close
Relative-volume baseline: prior 20 complete QQQ sessions through the same decision cutoff
```

The relative-volume **methodology** itself is unchanged from the original
manifest (cumulative RTH volume through the decision-cutoff bar ÷ average
of the same over the prior 20 complete sessions, malformed/partial
sessions excluded, full-day volume never used) — only the bars feeding
that calculation now come from Polygon instead of TradingView/BATS
exports.

## Polygon query parameters (locked, from the existing unmodified script)

| Field | Value |
|---|---|
| Script | `scripts/polygon_stocks_backfill.py` (unmodified; not part of the 9-file harness scope) |
| Script SHA-256 | `eb3cbfa8927c7104ac94dcc322ad2a88d8c4006bb180704eb6d727a6bf2161d9` |
| Script commit | `b3386950a8a09554fa42cd8834351f4e52116145` (PR #272 merge, tracked/committed) |
| Endpoint | `/v2/aggs/ticker/{ticker}/range/5/minute/{from}/{to}` |
| Multiplier / timespan | 5 / minute |
| `adjusted` | `true` — Polygon's split-adjusted bars; this is the script's existing default, not a new choice made for this amendment. For a single most-recent trading day this only differs from unadjusted pricing if a split occurred on that exact day. |
| `sort` | `asc` |
| Limit / pagination | `50000` per page, follows `next_url` until exhausted |
| Regular-hours filter | Applied downstream by the *same*, unmodified `stocks_advisory/csv_loader.py` used throughout the harness — Polygon output is written in the exact `timestamp,open,high,low,close,volume` shape that loader already expects |

## Connectivity / format validation check (NOT official Day 1, NOT a decision run)

Run once against a historical window ending 2026-07-10, to confirm the
existing Polygon script still works end-to-end and to obtain real
paths/hashes/coverage for this amendment — not to make or journal any
TRADE/NO_TRADE decision, and not to replace the existing July 10
validation record.

- Requested range: `2026-06-01` to `2026-07-10`
- Retrieved: `2026-07-13` (this session)

| Symbol | Path | Rows | RTH session dates | Coverage | SHA-256 |
|---|---|---|---|---|---|
| QQQ | `data/stocks_advisory_paper_proof/polygon_connectivity_check_2026-07-13/QQQ_5min.csv` | 5376 | 28 | 2026-06-01 .. 2026-07-10 | `1cfb7270935d0870d13681fecefe444c096126e50602056440ac6e46d1d57804` |
| TQQQ | `data/stocks_advisory_paper_proof/polygon_connectivity_check_2026-07-13/TQQQ_5min.csv` | 5376 | 28 | 2026-06-01 .. 2026-07-10 | `1a41e8b92e39afd54ae80a4b777331fde1fb234a3f3e9bf428b80907f4f200a6` |
| SQQQ | `data/stocks_advisory_paper_proof/polygon_connectivity_check_2026-07-13/SQQQ_5min.csv` | 5376 | 28 | 2026-06-01 .. 2026-07-10 | `88c2dee62c506e8c7d8f60c7f2b555353ae0a670d5f829fddae4ef7fc5f57016` |

These raw CSVs and the script's own `manifest.json` are **not** committed
to git (see `.gitignore` addition below) — only their hashes are recorded
here, matching how the original TradingView `BATS_*.csv` exports are
handled.

**BATS overlap sanity check** (the script's own built-in cross-venue
comparison against the locked TradingView files, RTH-matched bars only):

| Symbol | Matched bars | Close correlation | Median bps diff | Max bps diff | Verdict |
|---|---|---|---|---|---|
| QQQ | 2184 | 0.9999956585 | 0.138 | 77.05 | PASS |
| TQQQ | 2184 | 0.9999926958 | 0.694 | 145.91 | PASS |
| SQQQ | 2184 | 0.9999600390 | 1.318 | 175.20 | PASS |

All three symbols pass the script's own tolerance gates (median bps <
20, max bps < 500, close correlation > 0.999). Volume shows expected
venue-to-venue divergence (Polygon is consolidated-tape across all
exchanges; the BATS export is single-venue), which the script's own
design already treats as informational, not a gating criterion — pricing
agreement is what's gated, and it passed comfortably.

## What this amendment changes

Instead of the operator manually exporting three TradingView CSVs each
day, the agent runs the existing Polygon backfill script by hand after
session close, validates the output (row counts, coverage, BATS/Polygon
overlap where applicable), and only then runs the unmodified paper-proof
CLI (`scripts/run_stocks_advisory_paper.py`) against the resulting CSVs.

## What does not change

- No live feed, scheduler, or automatic daily task — every run is a
  manual, one-off invocation, same as before.
- No broker or order access of any kind.
- No change to `tqqq_sqqq_decision.py`, thresholds, position sizing, or
  the friction/slippage model.
- No proof backdating — 2026-07-10 remains a validation-only record on
  its original TradingView source; the connectivity check above is a
  second, independent, still-non-official check of the same date and
  does not supersede or modify `validation_2026-07-10.jsonl`.
- Official Day 1 still starts only after this amendment is itself frozen
  (committed, reviewed, CI green) — not before.

## `.gitignore`

Adds `data/stocks_advisory_paper_proof/polygon_*/` so future dated Polygon
pull directories (raw CSVs + the script's own `manifest.json`) stay local,
matching how `BATS_*.csv` and the official `journal.jsonl` are already
excluded.
