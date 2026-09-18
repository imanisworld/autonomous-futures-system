# 4HR Zone Interaction and Target Geometry — 2026-09-18

## Supersession note — later LC_ZONE quality audit

**DO NOT use this document to authorize target clipping.**

A later preregistered independent LC_ZONE audit found:
- current live HTF zone semantics are **UNSAFE FOR TARGET-RULE VALIDATION**;
- 2 rows in this study used a 1H zone before its defining impulse 1H candle completed;
- one affected row changes from `BEYOND_ZONE` to `BEFORE_ZONE` under strict completed-HTF semantics;
- the corrected persistent-at-formation LC_ZONE v1 construct was negative in the preregistered placebo comparison (−4.08 pp, 95% CI [−5.67, −2.41]), but the control match failed common-support review; reaction-area quality is therefore **inconclusive**, not proven inferior.

The historical results below remain useful as provenance only. The previously
listed zone-clipped-target next step is **cancelled / blocked** for LC_ZONE v1.
See `docs/lc-zone-quality-audit-2026-09-18.md`.

## Verdict

**PROMISING BUT UNPROVEN / AUDIT ONLY / PAPER ONLY — HISTORICAL DIAGNOSTIC ONLY AFTER THE LATER ZONE AUDIT.**

Scope: canonical MNQ 4HR Re-Trigger trades whose completed 4H context is
`strat_22_continuation` (n=29). No execution, detector, risk, config,
broker, collector, deployment, or live/paper strategy logic was changed.

This pass re-derives opposing 1H/4H supply/demand with the exact
`context.location_context` aggregation and zone detector from completed
15-minute bars only, then freezes the nearest opposing zone at entry.

Artifacts:
- `scripts/4hr_zone_interaction_2026-09-18.json`
- sha256 `0608545ffd08b0b2ee2d10cc0b366e685b6b66f52aa9a9b560454fc94903569e`
- `scripts/4hr_target_zone_geometry_2026-09-18.json`
- sha256 `17e035d840fd297fdd9c06abc94e72ed9e43fb66da4108f6a8c77520894941a5`

## Zone-response definitions

- Touch: price reaches the near edge of the frozen opposing zone.
- Reject: after touch, first decisive 5m close returns to the entry side of the near edge.
- Accept: after touch, first decisive 5m close closes through the far edge.
- Hold2: the next 5m bar also closes beyond the far edge.
- Window: canonical entry through canonical trade exit only.
## Interaction result

Among the 29 continuation-context trades:

- ACCEPT_CLOSE_THROUGH: n=4, +$365.58, +$91.39/trade.
- REJECT_CLOSE_BACK: n=6, +$26.12, +$4.35/trade.
- TOUCH_UNRESOLVED: n=8, +$1,482.66, +$185.33/trade.
- NO_TOUCH_BEFORE_EXIT: n=9, +$134.68, +$14.96/trade.
- NO_OPPOSING_ZONE: n=2, +$76.04.

The reject cell is not a useful discriminator. It is approximately flat and
does not support a generic "touch supply/demand => reverse" rule.

Only four trades closed cleanly through the zone before exit. That sample is
too small to establish a separate acceptance rule.

The eight TOUCH_UNRESOLVED trades were all winners. Seven of those eight had
their canonical target inside the opposing zone; they reached the target
before price produced either a clean close-through or a clean close-back.

This makes target placement relative to the zone more informative than the
post-touch rejection label itself.
## Target geometry — 4H 2→2 continuation

Using the exact frozen opposing-zone definition:

### Target BEFORE opposing zone
- n=7
- 71.4% wins
- +$284.64
- +$40.66/trade
- H1 +$46.56
- H2 +$238.08
- 3-tick net +$275.64

### Target INSIDE opposing zone
- n=14
- 78.6% wins
- +$1,971.78
- +$140.84/trade
- H1 +$718.64
- H2 +$1,253.14
- 3-tick net +$1,954.78

Split by opposing-zone timeframe:
- 1H zone: n=7, +$895.14, +$127.88/trade.
- 4H zone: n=7, +$1,076.64, +$153.81/trade.
### Target BEYOND opposing zone
- n=6
- 50.0% wins
- **−$247.38**
- **−$41.23/trade**
- H1 **−$153.96**
- H2 **−$93.42**
- 3-tick net **−$256.38**

This is the only target-position cell that is negative in both chronological
halves for the continuation subset.

## Cross-check on all 80 canonical MNQ 4HR trades

The same geometry is directionally visible outside the continuation subset:

- BEFORE_ZONE: n=14, +$393.28, both halves positive.
- INSIDE_ZONE: n=39, +$2,354.78, both halves almost identical and positive.
- BEYOND_ZONE: n=22, +$229.94 overall, but H2 **−$173.74**.
- NO_ZONE: n=5, −$91.40.

So "inside opposing zone" is not merely an artifact of the 29-trade
continuation slice. However the effect is strongest and cleanest there.
## Interpretation

The current evidence supports this narrower model:

**4H continuation can legitimately target into opposing supply/demand, but
extending the canonical target beyond that opposing zone is the weak geometry.**

The data do **not** support:
- automatically fading the first touch of supply/demand;
- automatically blocking continuation because a zone is in the path;
- requiring a simple immediate 1→2 precursor.

The data do support further testing of:
- continuation context;
- target location inside the nearest opposing 1H/4H zone;
- rejection/acceptance as trade-management information only after the zone is
  actually reached.

## Classification

- MNQ 4HR 2→2 continuation context: **PROMISING BUT UNPROVEN**.
- Target inside opposing zone: **PROMISING BUT UNPROVEN**.
- Target beyond opposing zone: **BROKEN in this continuation sample**.
- First-touch rejection rule: **NOT SUPPORTED**.
- Close-through acceptance rule: **WAIT — n=4**.

## Safe next step — superseded

The formerly proposed zone-clipped-target A/B is **not authorized** under the
current LC_ZONE v1 detector.

Current safe action:
- continue 4HR trigger-timing evidence collection;
- keep supply/demand target rules unchanged;
- if zone research is reopened, preregister a new zone construct and validate
  that construct independently before re-running target geometry.

No runtime or paper-lane zone change is supported by this study.
