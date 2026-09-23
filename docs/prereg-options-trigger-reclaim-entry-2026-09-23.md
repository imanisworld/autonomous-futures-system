# Options entry-timing forward test — first-sight vs trigger reclaim

**RESEARCH / PAPER EVIDENCE ONLY. NO EXECUTION CHANGE.**

## Question

The 2026-09-19 postmortem found that all five closed ACTIVE long rows were first observed after price had fallen back below the stored mechanical trigger. That five-row sample is too small to justify changing the entry rule.

This test asks one narrow question:

> When an otherwise-eligible options signal is first seen after price has slipped back through its trigger, does waiting for a fresh reclaim improve actual option economics versus entering immediately under the current rule?

This plan is frozen before any forward result is scored.

## Population

Use only new OPTIONS_PAPER_V1 episodes created after this preregistration is merged.

An episode is eligible for this comparison only when:

- it otherwise satisfies the existing frozen V1 signal, risk, contract-quality and remaining-R:R requirements;
- the setup has a stored mechanical trigger, invalidation and target;
- at first actionable observation, price is on the failed side of the trigger:
  - LONG: underlying < trigger;
  - SHORT: underlying > trigger;
- exact option contract identity and decision-time quote evidence are available.

Do not backfill the five Sep 16-18 postmortem rows into the forward sample.

## Paired arms

Each eligible episode is evaluated in two research arms. Neither arm changes production behavior.

### CONTROL — current first-sight entry

- Entry time: current V1 first actionable observation.
- Contract: the exact contract selected by the existing V1 process.
- Entry fill: existing ASK-entry rule, including existing fees/slippage assumptions.
- Underlying invalidation, target, management and option-exit rules: unchanged.

### RECLAIM — wait for trigger recovery

Use the **same episode, exact contract symbol, underlying invalidation and target** as CONTROL.

After first sight:

- LONG: first later valid observation where underlying is at or above the original trigger.
- SHORT: first later valid observation where underlying is at or below the original trigger.
- Reclaim must occur before the underlying invalidation or target has resolved the episode.
- Existing V1 eligibility must still hold at reclaim, including remaining R:R >= 1.0 and all frozen risk/quality gates that can be evaluated without hindsight.
- Entry fill is the recorded ASK for the exact CONTROL contract at the reclaim observation, with the same fees/slippage model.
- If no valid reclaim occurs, record **NO_ENTRY**. Do not manufacture an entry and do not score it as a loss.

No alternate reclaim thresholds, buffers, bar counts or confirmation filters may be added after collection starts.

## Outcomes

Primary economic outcome is **actual simulated option net P&L after ASK-entry/BID-exit translation and existing fees/slippage**.

Underlying target-hit rate is descriptive only and cannot determine the result.

For both arms record:

- eligible episode count;
- actual entries;
- NO_ENTRY count for RECLAIM;
- net option P&L after costs;
- profit factor after costs;
- win/loss/breakeven counts;
- max drawdown;
- average and median option P&L per eligible episode, counting RECLAIM NO_ENTRY as $0;
- average and median P&L per entered trade;
- underlying structural WIN/LOSS separately from option P&L;
- entry spread and time from first sight to reclaim.

The primary paired comparison is per-eligible-episode net option P&L:
**RECLAIM minus CONTROL**.

## Scoring gate

Do not make a policy recommendation until there are at least:

- **50 eligible paired episodes**, and
- **20 distinct trading days**, and
- **30 actual RECLAIM entries**.

If the sample has not reached all three conditions, status is **COLLECTING**.

At the first qualifying checkpoint, read the results once.

Classify:

- **FORWARD TEST SUPPORTS RECLAIM** only if:
  - RECLAIM net option P&L per eligible episode exceeds CONTROL;
  - RECLAIM total net P&L is positive;
  - RECLAIM profit factor exceeds CONTROL;
  - RECLAIM max drawdown is not worse than CONTROL;
  - both chronological halves show a non-negative RECLAIM-minus-CONTROL net difference.
- **NO EVIDENCE OF IMPROVEMENT** otherwise.
- **BLOCKED** if exact contract/quote lineage, entry timing, or outcome parity cannot be established.

No parameter adjustment or second attempt is allowed from this cohort.

## Integrity rules

- Forward rows only.
- Timestamp signals before either arm's entry.
- Exact contract identity must be preserved between paired arms.
- No hindsight contract reselection for RECLAIM.
- No changing stop, target, risk budget, contract filters, costs or exit rules between arms.
- Use the existing option fill model; do not substitute underlying target hits for option returns.
- Record rejected/no-entry reasons.
- Do not inspect interim P&L before the scoring gate. Counts and data-health checks are allowed.
- Any missing/ambiguous decision-time quote blocks that episode from economic scoring; do not interpolate it.

## Scope / restrictions

This preregistration does **not**:

- change V1 entry behavior;
- change scanner, alerts, risk rules, contract selection or management;
- deploy anything;
- enable Webull submission;
- touch futures;
- authorize real-money trading;
- interact with the MGC futures forward campaign.

Implementation, if needed, must be a separate read-only/offline evaluator or observer change with its own review.

## Existing open PR cleanup

PR #875 is a separate non-Strat internal paper-track project. It is **not part of this entry-timing experiment** and should remain separate/draft unless independently approved.

**No proof, no run.**
