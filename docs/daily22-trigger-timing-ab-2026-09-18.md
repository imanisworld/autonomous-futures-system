# MNQ Daily 2-2 Trigger-Timing / Entry-Architecture Audit — 2026-09-18

## Verdict

**TIMING-SENSITIVE / RULE IDENTITY MUST BE EXPLICIT / PAPER ONLY.**

The existing Daily 2-2 historical result reproduces exactly under its activation
contract. The positive result is therefore not a lost or irreproducible number.

However, that contract is not an immediate first-touch breakout entry. It waits
for the first-break 5m bar to complete, evaluates the strict context bundle at
that close, then uses the close as the IOC market reference.

A true pre-armed first-touch variant cannot admit any trade while preserving
both of these existing rules:

- target fixed at exactly 2R from the planned structural entry;
- actual-fill R:R must remain >= 2.0.

With any adverse entry slippage, actual R:R is strictly below 2.0.

No runtime or deployment change is authorized by this audit.
## Frozen audit trail

Preregistration:
- docs/prereg-daily-22-trigger-timing-ab-2026-09-18.md
- commit cd4c207a03f818e5f0fd3988294dca83343a62a8

Frozen harness:
- scripts/daily22_trigger_timing_ab_2026_09_18.py
- commit 6d88ac4d280efedd48d4447b4accaba8ec0392cb
- pre-run SHA-256 ccd86ab408b7da9a9fccc8b2e1305558ca6c6deb6fa7dd2839c5c0a0938d63be

Primary corpus:
- data/replay_corpus_v1_5m_4hr_audit/MNQ
- 621 files
- 2024-07-02 through 2026-06-26
- tree SHA-256 7f09a7f82ee282e892f5db9b86be3130e6a5c1210d86828a56b9666bb06afd35

Result artifact:
- scripts/daily22_trigger_timing_ab_2026-09-18.json
- SHA-256 8ace67f29d10949b99ee7e618be5f90a461798be5abd04e60ba849aa08c45e10
- 838,460 bytes
- second full-corpus run: byte-identical PASS

Focused verification:
- 71/71 passed
- Daily timing-contract tests
- Daily collector/state-integrity tests
- project-check daily/promotion
- demo-qualification gate

Activation source:
- 9caaaa3e2fbe5cb6ff20941229e3498794bcb6de (#545)

## Gate 0 — activation baseline reproduction

Required reference:
- 34 non-overlapping fills;
- +$13,885.18 net;
- PF 2.02;
- both chronological halves positive;
- 2024 / 2025 / 2026 positive;
- max drawdown 25.15%.

Reproduced:
- 34 fills / 34 resolved;
- 15 wins / 19 losses;
- **+$13,885.18** net;
- PF **2.0171**;
- H1 **+$5,661.84**;
- H2 **+$8,223.34**;
- 2024 **+$3,601.12**;
- 2025 **+$4,318.86**;
- 2026 **+$5,965.20**;
- max drawdown **25.1528%**.

**Gate 0 PASS.**

This closes the provenance concern around the previously summary-only 34-trade
activation result.
## Trading-day identity correction

Legacy activation identity:
- 330 first-break events;
- 211 continuation events.

Current C14-corrected CME identity:
- 327 first-break events;
- 210 continuation events.

There are 17 legacy-only and 14 current-only event identities. The differences
cluster around CME holidays / holiday-adjacent sessions and are consistent with
the already-proven #775 trading-day correction.

The current-identity completed-close model remains materially similar:
- 34 resolved fills;
- 15 wins / 19 losses;
- **+$13,571.68** net;
- PF **1.9482**;
- H1 **+$5,724.84**;
- H2 **+$7,846.84**;
- max drawdown **26.6323%**;
- all 2024 / 2025 / 2026 positive.

So #775 changes population identity but does not explain away the historical
positive completed-close result.
## Raw trigger-close detachment

Current-identity structural continuation first breaks: **210**.

All 210:
- median absolute trigger-close detachment: **34 ticks**;
- p90 absolute: **201 ticks**;
- maximum absolute: **2,759 ticks**;
- median adverse detachment: **3 ticks**;
- p90 adverse: **159 ticks**;
- maximum adverse: **2,759 ticks**;
- adverse detachment >8-tick IOC tolerance: **91/210**;
- completed-close IOC cancellations: **91/210**.

Trigger-bar-close context-approved subset: **176**:
- median absolute detachment: **34 ticks**;
- p90 absolute: **201 ticks**;
- maximum: **2,759 ticks**;
- median adverse detachment: **6 ticks**;
- p90 adverse: **173 ticks**;
- >8 adverse ticks: **82/176**;
- IOC cancellations: **82/176**;
- another **17** rows remain within IOC reach but fail actual-fill R:R >=2.

The completed 5m close is therefore materially detached from the structural
first-break level for a large fraction of the population.
## Model A — existing completed-close architecture

Model A preserves the current paper contract:
- first structural Daily boundary break consumes the day;
- context evaluated from the completed trigger 5m bar;
- decision-close IOC8;
- 1 adverse entry tick;
- actual-fill R:R >=2;
- risk <=$1,750;
- one open swing;
- static Daily stop / fixed 2R target;
- multi-day hold.

Current identity:
- 210 structural continuation days;
- 34 fills / 34 resolved;
- 15 wins / 19 losses;
- **+$13,571.68**;
- PF **1.9482**;
- both halves positive;
- max DD **26.6323%**.

Disposition:
- 118 skipped while a prior swing remained open;
- 32 IOC cancellations;
- 17 context rejects;
- 9 risk rejects;
- 34 resolved fills.

This remains a reproducible historical hypothesis. It is not proof of an
immediate breakout-touch entry.
## What the 34 fills actually are

Every one of the 34 current-identity Model A fills had a trigger-bar close that
was already favorable versus the planned structural breakout entry.

Signed close detachment among the 34 fills:
- minimum: **-202 ticks**;
- median: **-33 ticks**;
- maximum: **-2 ticks**;
- adverse closes: **0/34**.

Negative means favorable to the trade: below the planned entry for LONG, above
it for SHORT.

The 1-tick adverse IOC fill is therefore applied to a price that has already
retraced favorably from the structural breakout level. This favorable
re-entry is what allows the strict actual-R:R >=2 gate to pass.

The historical evidence should therefore be described as:

**first-break detected -> wait for completed 5m context -> enter only when the
decision-close IOC still offers at least 2R.**

It should not be described as a true first-touch breakout fill.
## Model B — causal pre-armed first touch

Preregistered Model B:
- prior completed 5m context only;
- resting structural stop-entry for the next 5m bar;
- first break consumes the opportunity;
- 1 / 2 / 3 adverse entry ticks;
- fixed original Daily stop / fixed original 2R target;
- actual-fill R:R >=2;
- risk <=$1,750;
- trigger bar eligible to resolve pessimistically.

Result at **1 tick**:
- 210 structural continuation days;
- 86 prior-context rejects;
- 124 prior-context-approved first-break opportunities;
- **0 fills**;
- all 124 otherwise eligible opportunities rejected by ACTUAL_RR_BELOW_2.

At **2 ticks**: 0 fills.

At **3 ticks**: 0 fills.

For the 124 one-tick R:R rejects:
- minimum actual R:R: **1.7114**;
- median: **1.99794**;
- maximum: **1.99933**.

The zero-fill result is structural, not an outcome/sample accident.
## Algebraic cause

Let:
- planned entry = E;
- stop distance = R;
- fixed target = E + 2R for LONG;
- adverse fill slippage = s > 0.

Actual LONG fill = E + s.

Then:
- actual risk = R + s;
- actual reward = 2R - s;
- actual R:R = (2R - s) / (R + s).

For every s > 0:

**(2R - s) / (R + s) < 2.**

SHORT is symmetric.

Therefore a target fixed at exactly planned 2R plus a strict actual-fill
R:R >=2 requirement is incompatible with any adverse true-touch fill.

A zero-slippage fill at exactly the planned entry can equal 2R, but that is not
an acceptable realistic-fill assumption.
## Interpretation

This audit does **not** show that the Daily structural signal is broken.

It shows there are two materially different strategies hiding under one label:

1. **Completed-close / favorable-pullback Daily 2-2**
   - currently implemented;
   - causal at the completed 5m decision point;
   - historically reproducible and positive;
   - requires a favorable post-break close before admission.

2. **Immediate first-touch Daily 2-2 breakout**
   - not what the current historical fills represent;
   - incompatible with the current fixed-2R + actual-R:R>=2 contract under
     realistic adverse slippage;
   - **BROKEN AS CURRENTLY SPECIFIED**.

The two must not share one expectancy claim.

The current paper evidence remains useful for the completed-close hypothesis,
but it is **not promotion-grade evidence for a first-touch breakout strategy**.
## Required rule decision before any implementation change

Do not tune from this result.

A separately preregistered strategy decision would be required to choose among
materially different contracts such as:
- explicitly keep the completed-close / favorable-pullback architecture;
- redesign target placement from actual fill so true-touch retains 2R;
- pre-register a target >2R from planned entry to absorb realistic slippage;
- lower the actual-fill R:R floor;
- test a fill-constrained stop-limit architecture with explicit non-fill risk.

Those choices change strategy or risk semantics. This audit does not select one.

## Operational ruling

- Daily 2-2 completed-close hypothesis: **PROMISING BUT UNPROVEN / PAPER ONLY**;
- Daily 2-2 true-touch version under current rules:
  **BROKEN / ZERO ADMISSIBLE FILLS**;
- existing historical completed-close evidence:
  **REPRODUCED, BUT MUST NOT BE RELABELED AS FIRST-TOUCH EVIDENCE**;
- live execution: **NOT AUTHORIZED**;
- new paper-fill authority: **NOT AUTHORIZED**;
- current VPS: **NO DEPLOY / NO RESTART**;
- current Daily ledger: **DO NOT RESET**.

No proof, no run.
