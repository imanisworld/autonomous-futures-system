# proof_builder Design Memo (Futures)

**Status: design only. No code, no config, no strategy changes, no broker
changes, no runtime changes exist yet. This memo is not an approval to
build, deploy, or promote anything — it is the locked contract that any
future implementation PR must be checked against.**

## Why this exists

Right now two different failure modes are tangled together and cannot be
told apart from the outside:

- "the bot is too strict / too passive" (a gating or entry-model problem)
- "the strategy itself does not work" (a setup-quality problem)

`proof_builder` is a diagnostic mode whose only job is to separate those
two. It is not a new strategy, not a relaxed version of the live bot, and
not a step toward loosening `locked_current`. It exists to answer one
question per missed or taken trade: **which part of the system produced
this outcome — the trend gate, the entry model, the setup itself, or the
fill/journal classification?**

`locked_current` (today's actual bot behavior) is unaffected by this memo
and by any eventual `proof_builder` build. Nothing here changes its
config, its code path, or its default.

## Separate process architecture

```
locked_current runner                    proof_builder process
──────────────────────                    ──────────────────────
strategy/signal_engine.py                 its own package (e.g. proof_builder/),
config/settings.py                        own entry point (e.g. python -m
risk_rules.yaml                           proof_builder.run), own config file
Tradovate demo account A                  Tradovate demo account B (isolated)
```

`proof_builder` is a **separate runnable process**, not a flag inside the
existing bot. It shares no import path with `strategy/signal_engine.py`
or any other `locked_current` decision code. The only permitted contact
between the two sides is a single, explicitly pure, read-only module:
`locked_current_shadow_evaluator` (contract below). Nothing else in
`proof_builder` may import from the live/demo runner's package, and
nothing in the live/demo runner may import from `proof_builder`.

This is the primary isolation boundary. It is enforced structurally (by
what is and is not importable), not by a runtime flag — a flag can be
flipped by mistake; an import that does not exist cannot.

## Fail-closed config boundary (secondary, redundant boundary)

Even though `proof_builder` must never share a runtime with
`locked_current`, if any mode-selection value exists anywhere in shared
configuration, it must:

- default to `locked_current` when absent
- fall back to `locked_current` on any unrecognized or malformed value
- never be settable to `proof_builder` from the live/demo runtime's own
  config surface

This is deliberately redundant with the separate-process boundary above.
Redundant safety is the point: no single mistake (a bad import, a bad
config value) should be sufficient to blur the two paths.

## proof_builder allowed behavior

- Loosen the trend filter from a hard block to advisory-only (logged,
  never blocking).
- Replace the passive retest entry with a signal-close / near-market
  entry.
- Raise the trade cap, to accumulate evidence faster.
- Trade MNQ/MES only.
- Trade 1 contract only.
- Require a stop on every trade (unchanged from `locked_current`).
- Run against its own, separate, isolated Tradovate demo account.
- Write to its own journal namespace only.
- Call `locked_current_shadow_evaluator` (see contract below) to label
  what `locked_current` would have decided on the same signal, purely
  for comparison.

## proof_builder forbidden behavior

- No live orders, anywhere, under any condition. The capability to place
  a live order must not be reachable in `proof_builder`'s import graph —
  not gated by a flag, structurally absent.
- No import of `locked_current`'s strategy/decision code, broker client,
  runner, or config.
- No writes to `locked_current`'s journal, position state, or config.
- No trading on any symbol other than MNQ/MES.
- No sizing above 1 contract.
- No stop-less trades.
- No promotion of any `proof_builder` result to `locked_current`
  behavior, to a scanner setting, or to any `FixtureStatus`-style
  "proven" label. A `proof_builder` result is evidence to review, not an
  approval of anything.

## `locked_current_shadow_evaluator` contract

This is the only bridge between the two sides, and it must be narrow
enough that it cannot become a second one by accident.

**Must be:**
- A pure function: same inputs always produce the same output, no side
  effects.
- Read-only: it inspects a signal and returns a label. It writes nothing.

**Must not:**
- Import any broker client.
- Import the live/demo runner.
- Construct or return an order object of any kind.
- Mutate any config.
- Write to any position state.
- Write to any journal except the single comparison row
  `proof_builder` itself records for its own run.
- Return approval language of any kind — no `APPROVED`, `TRADE`,
  `ORDER`, `ENTER`, `SIZE`, `SUBMIT`, or equivalent.

**Allowed return values (labels only):**

```
LOCKED_CURRENT_WOULD_NO_TRADE
LOCKED_CURRENT_WOULD_WAIT_RETEST
LOCKED_CURRENT_WOULD_TRIGGER
LOCKED_CURRENT_WOULD_BLOCK_TREND
LOCKED_CURRENT_WOULD_BLOCK_RISK
```

If `locked_current`'s real decision logic needs to change for any reason
in the future, `locked_current_shadow_evaluator` must be updated to match
it as its own explicit, reviewed change — it is not permitted to import
the live logic directly just to stay in sync automatically. Staying
manually in sync is the cost of keeping the two sides structurally
separate, and that cost is intentional.

## Journal isolation

Every `proof_builder` row (its own trades, and its shadow-comparison
labels) must carry a tag that makes it structurally impossible to
aggregate with or mistake for `locked_current` data — e.g. a distinct
`source=proof_builder` field, a separate table, or an equivalent hard
partition. No shared summary, dashboard, or report may combine the two
without that tag being explicit in the output.

## Comparison layer

The comparison layer answers exactly these questions, and nothing more:

1. What did `proof_builder` do?
2. What would `locked_current` have labeled (via the shadow evaluator)?
3. What happened after (the real, realized outcome)?
4. Which bucket does this belong to?

### Buckets

```
rules_too_strict        -- locked_current would have blocked/waited,
                            proof_builder took it, and it worked
entry_too_passive        -- locked_current's retest entry would have
                            missed the fill, proof_builder's near-market
                            entry caught it, and it worked
strategy_bad             -- proof_builder takes the trade and it loses,
                            with no gating or entry-timing explanation
fill_classification_wrong -- the journal/fill labels don't match what
                            price action implies actually happened
inconclusive              -- none of the above cleanly applies
```

A single trade may only be assigned to one bucket. If a trade's facts
don't cleanly support exactly one bucket, it is `inconclusive`, not
force-fit into the nearest one.

## Required tests before implementation

Any implementation PR must include, at minimum:

- A structural test that `proof_builder`'s package does not import the
  live/demo runner, its broker client, or its strategy/decision modules
  (an AST-based import check, matching the pattern already used
  throughout `options_manager/validation/*`'s test suite).
- A structural test that the live/demo runner does not import anything
  from `proof_builder`.
- A test that `locked_current_shadow_evaluator` returns only the five
  labels above — nothing else, and specifically none of the forbidden
  approval-language tokens.
- A test that `locked_current_shadow_evaluator` performs no I/O (no
  broker call, no file write, no journal write) — the same no-I/O
  structural checks already used for the options advisory-layer modules.
- A test that every `proof_builder` journal row carries the isolation
  tag, and that no existing report/dashboard query can return
  `proof_builder` rows without explicitly asking for them.
- A test that the trade cap, symbol restriction (MNQ/MES only), 1-
  contract sizing, and stop-required rule are all enforced before any
  trade is recorded as taken.
- A test that config defaults to `locked_current` and fails closed to
  `locked_current` on any missing or unrecognized mode value, if such a
  config value exists at all.
- A test proving no live-order capability is importable from
  `proof_builder`'s package (not just "unused" — structurally absent
  from the import graph).

## Explicit statement

**This memo documents a design only. It does not approve building,
deploying, running, or promoting `proof_builder`, and it does not change
any current behavior of `locked_current`. Futures remains
HOLD / OBSERVATION ONLY. No implementation work should begin from this
memo alone — a separate, explicitly scoped increment is required, and
that increment must be checked against every contract in this document
before merge.**
