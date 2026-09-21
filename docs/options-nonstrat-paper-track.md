# Options non-Strat paper track (`nst-v0.1`)

## Verdict

**PAPER ONLY / INTERNAL SIM BUILT / WEBULL MIRROR BLOCKED PENDING ROUND-TRIP PROOF.**

This track is stacked on the observer-only `ns-v0.1` lane. It does not make a
non-Strat event actionable merely because the event exists.

The required sequence is:

```
ns-v0.1 event
→ frozen geometry rule id + source reference
→ explicit underlying invalidation + target from that rule
→ exact option contract + decision-time quote
→ existing options risk gate
→ existing contract-quality gate
→ internal paper preparation
→ internal paper round-trip when an exact exit quote is supplied
→ optional Webull sandbox mirror only after separate lifecycle proof
```

## What is built

### Internal paper preparation

`options_manager/non_strat_paper_track.py` accepts:

- one `ns-v0.1` event;
- explicit underlying invalidation;
- explicit underlying target;
- a frozen geometry-rule id and source reference (no hindsight-picked bracket);
- exact option strike/expiry;
- exact contract symbol;
- entry bid/ask/last, volume, OI, IV, Delta, theta, underlying price,
  timestamp and provider;
- exactly **1 contract**.

It refuses:

- an event from another observer version;
- a family outside the frozen `ns-v0.1` population;
- missing episode identity;
- missing geometry-rule provenance;
- a decision quote timestamped before the event could first be known from delayed SIP;
- a decision quote more than one 5m cadence after first visibility;
- a decision-time underlying price outside the original bracket;
- less than 1.0R remaining at the decision-time underlying price;
- two or more contracts;
- missing exact contract identity;
- malformed LONG/SHORT stop/target geometry;
- failed existing options risk gate;
- failed existing contract-quality gate;
- failed local broker-boundary caps.

Signa/GEX are not invented as authority. Because the legacy
`OptionTradePacket` requires scalar Signa fields, this track uses explicit
**unavailable sentinels** (`score=0`, `grade=C`, `bias=NEUTRAL`) and empty
GEX. Those values intentionally generate observational warnings rather than a
direction-aligned fiction. They cannot turn a setup into strategy proof.

The packet's underlying entry is the **decision-time underlying price from the
exact option snapshot**, not the earlier event close. The event close remains
stored as the structural trigger. This distinction prevents delayed SIP
detection from being credited with an entry price the system could not have
acted on.

### Internal paper simulation

`simulate_non_strat_round_trip()` reuses
`options_manager.paper_sim.simulate_round_trip()`.

The exit quote must be for the **same ticker and exact contract symbol** as the
entry quote. Entry uses the existing ASK fill rule by default; exit uses the
existing BID fill rule by default; configured slippage/fees continue to apply.

No new fill model is introduced.

### Append-only internal journal

`options_manager/non_strat_paper_journal.py` writes only to a caller-selected
dedicated SQLite file.

It records:

- preparation identity/status;
- source event/family/direction;
- structural trigger price separately from decision-time underlying price;
- decision quote timestamp;
- explicit stop/target geometry plus frozen geometry-rule id/source references;
- exact contract identity;
- entry quote;
- local preview readiness;
- Webull block state;
- later internal simulated result.

The journal has no update/delete path and stores no broker credentials, account
ids or tokens.

## Geometry rule registry (fail-closed)

`options_manager/non_strat_paper_track.py::GEOMETRY_RULES` maps a
`geometry_rule_id` to the `docs/` file that freezes its stop/target
definition. **It is empty in `nst-v0.1`.** `prepare_non_strat_paper_candidate`
returns `DATA_BLOCKED / geometry_rule_not_registered` for every plan until a
prereg adds an entry, so a paper ticket can never carry self-declared
provenance. Tests register a throwaway rule and remove it; runtime code never
registers anything.

Adding a rule is a trading-path change under the post-freeze rule: prereg
with fixed thresholds → ≥ 5 ns-v0.1 sessions of evidence → staged rollback
(delete the journal file, unwire) → explicit operator GO.

## Logged real-trade fixtures

Logged real-trade coverage is intentionally isolated in PR #874 rather than
inside this paper-track PR.

That cross-check uses the existing hand-authored fixture inventory only where
the stored evidence already names a mechanically testable family. It does not
reverse-engineer a setup from outcome candles. A historical family match is
coverage regression only and does not authorize this paper track.

## Webull state

The repo already contains an **unwired** Webull sandbox options paper-order
module capable of:

- BUY_TO_OPEN single-leg limit submission;
- cancel by client-order id;
- order-detail lookup.

It is sandbox/paper only and fail-closed behind explicit flags.

That is **not enough for this track**.

A filled research position currently has no proven automated
`SELL_TO_CLOSE` / round-trip exit lifecycle. Submitting entries before that
proof would create paper positions the system cannot safely manage and would
violate the standing no-trade-without-exit-control rule.

Therefore `nst-v0.1` always returns:

```
webull_submit_allowed = false
webull_block_reason = webull_round_trip_lifecycle_unproven
```

It still builds and validates the deterministic local Webull preview intent so
the eventual mirror route can be reconciled without changing the setup
population.

## What is NOT authorized

This track does not:

- change the production options scanner;
- alert on non-Strat events;
- create internal candidates without explicit stop and target;
- submit a Webull order;
- enable the Webull paper-submit env flag;
- enable live options trading;
- touch futures execution;
- change risk limits;
- make Webull paper fills evidence of strategy edge.

## Morning box proof / operator work

Nothing needs to be changed on the box tonight.

Before Webull can move from local intent to paper mirror, the sandbox lifecycle
must be proven during supported option trading hours:

1. verify exact sandbox/paper config and live flags remain false;
2. use one controlled one-contract sandbox option;
3. prove placement returns a stable order id;
4. prove detail lookup reports the expected state;
5. if unfilled, prove cancel;
6. review the isolated SELL_TO_CLOSE plumbing in PR #877;
7. prove #877 against the real sandbox with a filled entry and exact-contract close;
8. prove the closed position is reconciled from order detail / position state;
9. prove duplicate ticket id cannot create a second entry;
10. journal request/result metadata without secrets;
11. only then consider a default-off runtime mirror flag.

Until all ten are complete:

```
Internal paper simulation: AVAILABLE only for forward-timely exact geometry + quotes
Webull local preview intent: AVAILABLE
Webull paper submission for non-Strat setups: BLOCKED
Live Webull: DISABLED
```

## Classification

The setup families remain:

**PROMISING BUT UNPROVEN / PAPER-RESEARCH ONLY**

A large positive paper sample would still not promote them without realistic
fills, reproducible definitions, controlled drawdown, multiple months, and
forward evidence.