# Logged options fixture cross-check — ns-v0.1

## Purpose

Use the real-trade fixture inventory already in
`options_manager/validation/fixture_status.py` as a **coverage regression**
against the non-Strat observer.

This does not turn reconstructed trades into strategy proof. The fixture
inventory explicitly records that every historical candidate was reconstructed
after the fact and that no candidate currently qualifies as a clean
scanner-identification proof fixture.

The cross-check answers only:

> When a logged fixture already names a setup family that ns-v0.1 can detect,
> does the observer actually produce that broad mechanical family somewhere in
> the recorded fixture window?

A match is not expectancy, not a valid pre-trade plan, and not paper/live
permission.

## Frozen mappings

The mapping is intentionally sparse and is fixed before reading ns-v0.1
outcomes.

| Fixture | Existing evidence | ns-v0.1 treatment |
|---|---|---|
| EBAY | Existing fixture/management evidence explicitly says the trade waited for a PDL reclaim | Testable as semantic `PDL_RECLAIM_LONG`; ns-v0.1 predicate is currently named `PDL_REJECTION_LONG` (`low < PDL && close > PDL`) |
| HOOD | Existing fixture calls it support-hold continuation / pullback-reclaim, but also says the planned 92/95/100 source is unproven | `OUT_OF_SCOPE` until a frozen planned-level source exists |
| AMD | Existing fixture trigger is explicitly premarket | `OUT_OF_SCOPE` because ns-v0.1 is RTH-only |
| All other tracked fixtures | No proven setup family usable by ns-v0.1, contradicted as described, scalp noise, or incomplete | `UNTESTABLE`; never infer a family from the outcome candles |

This means only EBAY can currently produce a mechanical historical-family match
without rewriting the fixture evidence.

## Result vocabulary

- `COVERAGE_MATCH` — expected mechanical family exists in the loaded window.
- `NO_COVERAGE_MATCH` — the window is loaded but the expected family is absent.
- `DATA_MISSING` — a testable fixture window has no ns-v0.1 rows loaded.
- `OUT_OF_SCOPE` — fixture names a family, but the current observer cannot
  causally test it under its frozen scope.
- `UNTESTABLE` — the fixture does not contain a proved setup family suitable
  for this observer.

Every row has `edge_claim_allowed=false`.

## Running

After the relevant historical sessions have been populated in the dedicated
ns-v0.1 database:

```bash
python scripts/options_non_strat_fixture_crosscheck.py
python scripts/options_non_strat_fixture_crosscheck.py --json logs/non_strat_fixture_crosscheck.json
```

The script opens the observer database read-only. It performs no provider,
broker, Webull, scanner, alert, risk, or execution calls.

## Historical data requirement

The cross-check does not silently fetch or backfill historical sessions.
Morning/box work may populate the dedicated observer DB for the exact fixture
windows with the already-built after-close observer. Provider completeness must
be checked before interpreting a missing family.

## Paper / Webull boundary

This cross-check is deliberately not the paper-order lane.

Raw structural coverage and historical fixture agreement must remain distinct
from:

1. defining an entry/invalidation/target rule;
2. proving a complete internal paper candidate;
3. selecting a causal option contract;
4. passing options risk/quality gates;
5. Webull sandbox submission.

The existing Webull sandbox paper-order adapter is capable of submit/cancel/
detail plumbing but its controlled paper placement, lifecycle and cancel proof
remain an external gate. No fixture match bypasses that gate.