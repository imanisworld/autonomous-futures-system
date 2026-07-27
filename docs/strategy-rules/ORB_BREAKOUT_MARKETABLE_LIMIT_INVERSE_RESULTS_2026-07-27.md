# ORB Breakout marketable-limit inverse — specification audit

## SPECIFICATION VERDICT

**INVALID / NEEDS RE-PREREGISTRATION**

The controlling preregistration is internally contradictory about
chronological sizing. The stopped run therefore cannot support an economic
classification.

This is **not** an `UNSAFE` result and **not** a `REJECT`. No accepted evidence
currently shows whether the ORB Breakout inverse won or lost.

## PREREGISTRATION SHA

`b2c586af8e2b624e93fe0bf18fbab4be15f2003d`

The inspection in this report is limited to the text frozen at that exact SHA.
No later interpretation is used to select between conflicting clauses.

## PREREGISTRATION SIZING TEXT

The controlling document says all of the following:

> Preserve the exact source attempt's planned entry, stop distance, target
> distance, contracts, strategy tag, instrument, session, marketable-limit
> offset, cost model, and same-bar resolution policy.

It separately says:

> The fixed population must contain one contract on every captured source
> order. The run aborts if #358 produces any ORB Breakout source attempt with a
> different size.

It then specifies the chronological pass as:

> The chronological inverse uses the same frozen sizing engine with a
> one-contract hard invariant; it aborts rather than silently changing size.

The chronological replay section also requires the full frozen
marketable-limit engine, normal account updates, the 20% breaker, and existing
position/account gates.

## PRECISE SIZING ANSWERS

### 1. Did the preregistration freeze exactly one contract for every trade?

**It clearly required one contract for the fixed source population, and it
named a one-contract hard invariant for the chronological inverse.**

However, it did not unambiguously define how that invariant must interact with
the inherited dynamic sizing engine when the inverse account path reaches a
different sizing tier.

### 2. Did it instead freeze #358's dynamic account-based sizing?

**It also required the chronological inverse to use the same frozen sizing
engine and preserve contracts.**

On the inverse path that engine recommended two contracts on 2025-10-15 after
prior inverse P&L changed account state. The text does not explicitly say that
this recommendation is diagnostic only or that submitted quantity must remain
one despite it.

### 3. Is the preregistration internally contradictory?

**Yes.**

It simultaneously requires:

1. a one-contract chronological candidate;
2. the inherited dynamic sizing engine and account path;
3. preservation of contracts; and
4. abort rather than silently changing size.

The document never resolves whether “one-contract hard invariant” means
“force every chronological order to quantity one” or “abort when the inherited
sizing engine recommends anything other than one.” Those interpretations
produce different experiments.

Selecting either interpretation after observing the two-contract recommendation
would be a post-freeze specification choice.

## WHETHER THE PRIOR ABORT WAS VALID

The abort was **mechanically consistent with one explicit preregistration
clause**: abort rather than silently changing size.

It was **not a valid economic rejection**. The event exposed a contradictory
experiment specification; it did not establish that the inverse strategy was
unsafe or unprofitable.

Accordingly:

- the abort event remains diagnostic evidence;
- the prior `UNSAFE` classification is withdrawn;
- the prior `REJECT` decision is withdrawn; and
- no partial fixed-population or chronological P&L is accepted.

## OBSERVED DIAGNOSTIC EVENT

| Field | Value |
|---|---|
| Date | 2025-10-15 |
| Bar timestamp | 2025-10-15T09:15:00+00:00 |
| Instrument/session | MNQ / London |
| Strategy | orb_breakout |
| Original direction | LONG |
| Required inverse direction | SHORT |
| Planned entry | 24924.0 |
| Original stop / target | 24911.5 / 24951.5 |
| Source #358 size | 1 contract |
| Chronological inverse recommendation | 2 contracts |

This event establishes the point at which the two sizing interpretations
diverge. It does not establish economic performance.

## RESULTS STATUS

- Fixed-population inverse: **NOT ACCEPTED**
- Chronological system-path inverse: **NOT ACCEPTED**
- Gross/net P&L: **NOT CLASSIFIED**
- Expectancy, PF, and win rate: **NOT CLASSIFIED**
- Temporal, direction, session, and yearly robustness: **NOT CLASSIFIED**
- Slippage sensitivity: **NOT CLASSIFIED**
- Concentration, drawdown, losing streak, and recovery: **NOT CLASSIFIED**
- Final economic verdict: **NONE**

Per the decision rule for an internally contradictory preregistration, the
experiment was not resumed and no interpretation was selected.

## WHAT A FUTURE PREREGISTRATION MUST RESOLVE

A new pass, if separately authorized, must state one sizing contract without
ambiguity. For a fixed-one-contract candidate it would need to say explicitly
that:

- every source and chronological inverse order is exactly one contract;
- account P&L and breaker state may evolve and suppress later attempts;
- the inherited sizing engine's quantity recommendation is recorded only as a
  diagnostic;
- that recommendation cannot increase or reduce submitted quantity; and
- enforcing quantity one is the candidate definition, not a rescue cap.

This report does not create that candidate or authorize a rerun.

No strategy parameter, eight-tick marketable limit, bracket, filter, session,
breaker rule, runtime code, #359, #360, deployed box, broker, configuration, or
deployment was changed.
