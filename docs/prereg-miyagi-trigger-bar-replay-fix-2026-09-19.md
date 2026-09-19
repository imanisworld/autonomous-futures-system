# Preregistration — Miyagi Trigger-Bar Replay Fix — 2026-09-19

## Scope

**RESEARCH REPLAY FIX ONLY / NO RUNTIME WIRING / NO DEPLOYMENT.**

Base commit:
`3ad3c62a8de8da2596296dd9ec65fb7cd55854f2`

Binding audit:
`docs/futures-causal-mechanics-audit-2026-09-19.md`

## Proven defects to fix

1. The 12HR Miyagi replay excludes the trigger-touch 5m bar from immediate stop/T1 resolution even though the written rule makes the fixed stop active at entry.
2. A pre-armed stop-market gap-through must use the entry bar open as the base fill reference, not the stale trigger level.

## Frozen strategy identity

Do not change:
- candidate population;
- detector;
- 12HR setup definition;
- 60m reversal condition;
- 09:30 ET entry gate;
- entry trigger level;
- fixed stop;
- T1/T2;
- day-only flatten;
- commission;
- tick value;
- chronological split;
- slippage grid 1/2/3/4 ticks;
- account risk caps or R:R rules.

## Required replay behavior

- Entry remains the first eligible 5m bar whose range crosses the trigger.
- If the eligible bar opens through a pre-armed stop trigger:
  - LONG base fill = max(trigger, bar open);
  - SHORT base fill = min(trigger, bar open).
- Apply adverse entry slippage after the gap-aware base fill.
- Validate the actual slipped fill remains inside the fixed stop/target bracket; otherwise cancel fail-closed as `POST_FILL_INVALID_BRACKET`.
- The trigger bar becomes eligible for stop/T1 resolution immediately.
- If OHLC proves both entry and stop were touched in the same trigger bar and path order is unknowable, resolve pessimistically as STOP.
- If stop is not touched but target is touched on the trigger bar, resolve TARGET.
- Otherwise continue with the existing later-bar resolution and exact 15:55 ET day-only flatten rule.

## Evidence regeneration

Re-run the exact corrected Miyagi candidate populations:
- MNQ: 15 candidates;
- MES: 19 candidates;
- source window 2024-07-02 through 2026-06-26.

Required outputs:
- regenerated corrected evidence JSONs;
- regenerated operator-facing Miyagi evidence summary if applicable;
- exact before/after row reconciliation;
- 1/2/3/4-tick sensitivity;
- chronological halves;
- source hashes.

## Acceptance

Approve the fix only if:
- the frozen candidate population is unchanged;
- MNQ 2024-09-18 changes from the optimistic later TARGET to pessimistic same-trigger-bar STOP;
- MES 2025-12-04 resolves on the trigger bar without changing its final LOSS classification;
- no unrelated candidate changes except where gap-aware fill semantics causally require it;
- current targeted causal/parity tests pass;
- full repository tests pass;
- docs clearly retire superseded Miyagi headline metrics.

No paper-fill, DEMO, or live execution authority follows.

No proof, no run.
