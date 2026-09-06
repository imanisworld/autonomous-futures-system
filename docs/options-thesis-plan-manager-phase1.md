# Options Thesis / Plan Manager — Phase 1 foundation

## Problem

The old options alert flow could emit the same directional idea many times. A
persistent upstream state can therefore look like many independent
confirmations even when no new proof appeared.

The Phase 1 plan manager changes the unit of state from **message** to
**thesis**:

`(ticker, direction, setup_type, timeframe)` → one evolving plan until it is
invalidated, exited, or expired.

This is advisory state only. It sends no Discord messages, places no orders,
selects no broker action, and mutates no live position.

## Reuse instead of recreation

Targets are delegated to the existing `options_manager.levels.find_targets`
authority. The plan adapter adds source provenance and filters semantically
wrong level kinds before passing them to that existing function.

No second Strat classifier, target algorithm, contract validator, risk engine,
or broker surface is introduced.

## Signa rule

The completed Signa effectiveness audit found no material incremental value.
Accordingly:

- Signa is telemetry only in this manager.
- missing, aligned, or opposed Signa cannot alter `actionable`.
- Signa is not a conviction confirmation.
- repeated polling of an unchanged Signa fingerprint increments
  `signa_repeat_count`, not `signa_event_count`.
- a Signa-only change is `telemetry_only` and does not request a user-facing
  plan update.
- an unverified gamma-labelled level is ignored, so a Signa pivot cannot be
  relabelled as GEX and become a target.

## Lifecycle

Every thesis uses the required options lifecycle vocabulary:

- `WATCHING`
- `TRIGGERED`
- `ACTIVE`
- `INVALIDATED`
- `EXITED`
- `EXPIRED`

`ACTIVE` is a human/advisory lifecycle mark only. The manager has no order API.
Once active, later observations keep the position active until an explicit exit,
invalidation, or expiry; polling does not silently demote it back to triggered.
Terminal theses cannot be reopened in place.

## Actionability

A plan is actionable only when all of the following explicit evidence is true:

- mechanical trigger exists
- entry and underlying invalidation exist
- two structural targets are valid
- contract proof is valid
- portfolio-risk proof is valid
- SPY/QQQ are aligned
- higher timeframes are aligned
- event risk is clear

Signa is deliberately absent from that list.

This module accepts the contract/risk/market booleans as caller-supplied proof;
it does not replace the canonical validators. Wiring those validators into the
plan manager is a later integration step.

## Targets

Targets require explicit supplied structural levels. The existing target finder
selects the nearest two valid levels on the profit side and computes underlying
R:R. The plan adapter preserves the selected level's source label.

For calls, only resistance-labelled levels may become profit targets. For puts,
only support-labelled levels may become profit targets.

Gamma-labelled levels are usable only when explicitly marked `verified_gamma`.
There is no symmetric projection, fake wall, or Signa-pivot fallback in this
plan layer.

## Conviction

`HIGH_CONVICTION_CANDIDATE` is an evidence label for forward shadow testing,
not a sizing rule.

There is **no default threshold**. Without an explicitly supplied
`high_conviction_min_confirmations`, an actionable thesis is `STANDARD`.

The optional independent confirmation pool is:

1. full timeframe continuity
2. clean continuation or retest
3. strong level confluence
4. exceptional liquidity
5. strong target room

Signa is not part of the pool. The label never changes max contracts, dollar
risk, portfolio risk, or execution behavior.

## Material updates / anti-spam

A future notifier may use `PlanUpdate.should_emit_update`, which becomes true
only for a material plan change such as:

- new thesis
- lifecycle-status change
- actionability change
- conviction-label change
- entry/invalidation change
- target change
- blocking-reason change

A repeated or changed Signa observation by itself is journal telemetry, not a
new trade alert.

## Still missing after this foundation

This PR intentionally does **not** solve the whole options system. Follow-up
work still includes:

- demote remaining Signa authority in the existing market-context validator and
  legacy alert path to match the completed effectiveness audit
- wire canonical scanner/advisory/contract/portfolio validators into plan proof
  instead of caller booleans
- causal structural-level collection for PDH/PDL, prior candle highs/lows,
  weekly levels, broadening boundaries, and verified GEX when available
- contract selection and DTE/strike choice
- explicit aggregate-risk budget operator decision before deployment
- separate review of the still-hardcoded $300 per-trade default
- persistent thesis store / journal and restart recovery
- material-update renderer for Discord/API
- active-position management updates at T1/T2/invalidation
- forward shadow calibration of any high-conviction threshold
- regular-hours latency acceptance before delayed SIP data is trusted for
  timely alerts

No proof, no promotion. No new information, no new alert.
