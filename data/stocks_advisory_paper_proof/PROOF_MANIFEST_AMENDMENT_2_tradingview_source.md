# Amendment 2 — Official Forward Source Reverted to TradingView/BATS

Status: **PRE-PROOF CONFIGURATION — AMENDMENT DRAFTED — OFFICIAL DAY 1 STILL NOT STARTED**

This amendment supersedes `PROOF_MANIFEST_AMENDMENT_1_polygon_source.md`
(which locked Polygon as the official forward data source). Neither the
original manifest (`PROOF_MANIFEST.md`, frozen `2026-07-13T15:24:02Z`, sha256
`ab1c99ed72b01659ef195e1931c52764166e552e54acfad279f2dccdd8d3955d`) nor
Amendment 1 is edited — this is a new, additional file, so both prior
hashes remain valid and checkable. Everything else already frozen
(thresholds, position sizing, friction/slippage model, dedup key,
proof-completion gates, relative-volume methodology) is unchanged.

Amendment drafted at: 2026-07-13 (see git commit timestamp for the
authoritative record once committed).

## Why

Polygon was intended but is unavailable due to entitlement limits: the
configured `POLYGON_API_KEY`'s subscription plan returns `HTTP 403
NOT_AUTHORIZED` ("Your plan doesn't include this data timeframe") for any
date at or after 2026-07-11, confirmed via a direct query and via Polygon's
own `/v1/marketstatus/now` (the market itself closed normally; this is
purely a plan/entitlement restriction, not a data-timing issue). Amendment
1's own connectivity check never actually validated same-day access — it
only proved historical access, which was not the requirement.

On 2026-07-13, an attempt was made to run the proof using Polygon; it hit
the above 403, and the run fell back to manual TradingView/BATS export
without a new approved amendment authorizing that source for official use.
That run was clean and correctly executed in every other respect, but a
proof that doesn't follow its own frozen source designation cannot honestly
count as an official day. It has been reclassified as validation-only
evidence — see `VALIDATION_EVIDENCE_2026-07-13.md` and
`validation_2026-07-13.jsonl` (moved out of the official
`journal.jsonl`, `data_source` field's `OFFICIAL_DAY1:` prefix corrected to
`VALIDATION_REPLAY_NOT_OFFICIAL_DAY1:`, no other field changed).
2026-07-13 will not be rerun.

The manual TradingView/BATS export workflow has now worked cleanly twice
(2026-07-10 validation replay under the original manifest, and the
reclassified 2026-07-13 run) — a stronger track record than Polygon
currently has under this account's plan.

## New official forward data source

```text
Official forward source: manually exported TradingView/BATS 5-minute CSVs
Symbols: QQQ, TQQQ, SQQQ
Session: regular trading hours
Timezone: America/New_York
Frequency: 5 minutes
Retrieval timing: after session close, operator-exported
Relative-volume baseline: prior 20 complete QQQ sessions through the same decision cutoff
```

This reverts to the original manifest's data source. Polygon is not
prohibited from future reconsideration, but it requires its own new,
separately-approved amendment (and a verified plan upgrade) before it can
be used for an official day again — not a silent fallback mid-run.

## Unchanged (do not re-derive)

- Thresholds: `allowed_max_gap_percent=2.0`, `allowed_min_first_hour_range=1.0`,
  `allowed_max_first_hour_range=10.0` — still labeled initial v1
  operator-selected values, not validated production settings.
- Position sizing: `floor(1000 / raw_entry_price)`, minimum 1 share.
- Friction: 0.15% modeled slippage per side (both legs) + Robinhood
  regulatory fees.
- Dedup key: `(trade_date, strategy_version)`.
- Relative-volume methodology: cumulative RTH volume through the decision
  cutoff (opening range + 1 confirmation bar) ÷ average of the same over the
  prior 20 complete sessions, malformed/partial sessions excluded, full-day
  volume never used.
- Official journal path: `data/stocks_advisory_paper_proof/journal.jsonl`
  (still empty — no official day has been recorded).

## What this amendment does NOT do

- Does not start official Day 1 — it begins only after this amendment is
  itself frozen (committed, reviewed, CI green) and merged, applying to the
  next completed trading session after that point.
- Does not add any live feed, scheduler, or automation — every run remains
  a manual, one-off operator invocation.
- Does not change any threshold, sizing, friction, or decision-engine
  behavior.
- Does not retroactively count 2026-07-13, or any other already-run
  session, as an official day.

## Corrected current status

```text
Official days: 0 of 20
Completed paper trades: 0 of 30
2026-07-10: preserved validation NO_TRADE (original manifest, TradingView source)
2026-07-13: preserved validation NO_TRADE (Polygon unavailable, fell back without an approved amendment at the time)
Live execution: disabled
```
