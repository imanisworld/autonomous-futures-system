# LC_ZONE Quality Audit — Reviewer Ruling — 2026-09-18

## Verdict

**HOLD 4HR TARGET-vs-ZONE A/B.**

**Current live LC_ZONE semantics: UNSAFE FOR TARGET-RULE VALIDATION.**

**Reaction-area quality: INCONCLUSIVE / CONTROL MATCH FAILURE.**

No runtime, strategy, target, risk, broker, collector, or execution rule was
changed by this audit.

The preregistered statistical output is preserved as
`NO EVIDENCE OF ZONE QUALITY`, but the reviewer does **not** elevate that into
a claim that the zone concept is worse than random because the control pool did
not achieve adequate common support on the preregistered matching features.

## Frozen audit trail

Preregistration:
- initial prereg commit:
  `c9056aa85f7bd92f38e87266c999c0be6e079f86`
- tightened decision-rule commit:
  `b4a1f48003da41602fad69e4435f43487bd42ff7`

Harness before outcomes:
- `14056bd` — initial harness.

Harness correction:
- `11bb0ef` — corrected completed-HTF market-hours semantics.
- correction rationale:
  `docs/lc-zone-quality-audit-harness-correction-2026-09-18.md`.

Eligible corrected output:
- full pair-level JSON SHA-256:
  `d4ac01ae94a64be9aa771c03f72eba8619bd874839d5df179e3cabc05c432b16`
  (large reproducible local artifact; not intended for Git commit);
- generated Markdown SHA-256:
  `9ceb6e936edc529d2f89107d6cab01d001eec9b1c557146e2ba04f4c3ee494ea`;
- committed summary JSON SHA-256:
  `50484e63e2179161a32e9b34850a26b1b230ae3db08759b35205e2185f546f35`.

The first v0 execution is explicitly INVALID and is not evidence. Its hashes
are retained only in the harness-correction note.

## What is proven

### 1. Current live HTF-zone timing is not stable enough for a target rule

Across every 15m as-of point on the mature MNQ/MES corpus, current live
aggregation was compared with the same zone detector restricted to completed
HTF clock buckets.

Nearest-zone identity disagreement:

| Instrument | TF | Supply | Demand |
|---|---:|---:|---:|
| MNQ | 1H | 3.57% | 3.39% |
| MNQ | 4H | **9.39%** | 5.63% |
| MES | 1H | 3.46% | 2.86% |
| MES | 4H | **8.72%** | 5.24% |

The preregistered 1% blocking threshold is exceeded.

Post-hoc mechanism diagnostic, restricted to exact HTF completion points:
- MNQ 1H supply 0.21%, demand 0.47%;
- MES 1H supply 0.00%, demand 0.00%;
- MNQ 4H supply 2.12%, demand 0.29%;
- MES 4H supply 1.65%, demand 0.13%.

Therefore most of the all-bars disagreement is caused by allowing the
still-forming HTF bucket to participate. A smaller 4H-supply identity mismatch
remains at completed points.

### 2. Two prior 4HR target-geometry rows used a premature 1H zone

- 2024-11-25 09:55 ET SHORT — zone base 08:00 ET; defining 09:00-10:00 1H
  impulse had not completed. Trade was a WIN and had been classified
  `BEYOND_ZONE`.
- 2025-07-29 09:50 ET SHORT — same 08:00 base / incomplete 09:00-10:00
  defining impulse. Trade was a WIN, was in the 4H 2→2 continuation subset,
  and had been classified `BEYOND_ZONE`.

This does not prove the old target-geometry conclusion has the wrong sign, but
it proves that its zone assignment was not fully causal for every row. It is
therefore **diagnostic only** and cannot authorize target clipping.

### 3. Rolling-MTR qualification itself is unstable

On completed HTF bars, the current detector recomputes the impulse threshold
against a later rolling MTR. This can make old zones appear late, disappear
while still unbroken/in-lookback, and later reappear.

Corrected audit:

- MNQ 1H: 21.11% of detected identities first appeared retroactively;
  expected-alive identity rows absent 9.42%; 115 identities reappeared.
- MES 1H: 23.13% late; absence 9.15%; 83 reappeared.
- MNQ 4H: 21.89% late; absence 14.05%; 59 reappeared.
- MES 4H: 23.51% late; absence 12.29%; 54 reappeared.

That is a detector-definition issue, not a strategy P&L issue.

## What the preregistered reaction test said

Under a safer research abstraction — completed HTF formation, then freeze the
zone until break/age — the current impulse-base geometry was compared with
non-impulse pseudo-zones.

Matched first-touch population:
- real zones 3,643;
- controls 6,394;
- all 3,643 real zones forced to a nearest control;
- 2,698 pairs had complete reactions on both sides.

Primary 0.5-MTR clean rejection within 2h:
- actual zones: **86.43%**;
- controls: **90.51%**;
- uplift: **-4.08 pp**;
- paired bootstrap 95% CI: **[-5.67, -2.41] pp**.

Splits:
- MNQ: -3.90 pp;
- MES: -4.25 pp;
- 1H: -3.36 pp;
- 4H: -6.83 pp;
- supply: -0.21 pp;
- demand: **-8.53 pp**.

Secondary metrics were mixed:
- 1.0-MTR clean rejection: actual 76.06%, control 72.05%;
- far-edge close-through: actual 23.09%, control 22.61%;
- mean +2h directional close return: actual -0.555 MTR, control +0.013;
- mean 2h MFE: actual 3.141 MTR, control 2.662;
- mean 2h penetration: actual 3.424 MTR, control 2.441.

The preregistered classification emitted **NO EVIDENCE OF ZONE QUALITY**.

## Why the reviewer does not treat that as a clean control result

The matching design had no preregistered common-support/caliper gate and forced
every real zone to a control even when the control geometry was materially
different.

Pre-outcome feature balance after matching:

All matches:
- zone width / MTR: real mean 1.980 vs control 1.176; SMD **0.72**;
- formation distance / MTR: real mean 1.233 vs control 0.327; SMD **1.05**.

Preferred exact-hour matches only:
- width SMD **0.64**;
- distance SMD **1.12**.

Those are not credible "matched" distributions. A post-hoc closest-match
sensitivity remained negative, but it cannot repair a control design after the
outcome has been read.

Therefore:
- the negative primary comparison is retained exactly as observed;
- it does **not** prove the zone concept is inferior to a properly matched
  placebo;
- the reaction-area question remains **INCONCLUSIVE**.

## Freshness observation

Descriptive only, not a gate:

- first test: n=2,935, 0.5-MTR clean rejection 86.71%;
- second test: n=2,663, 68.42%;
- third-or-later: n=21,704, 45.07%.

The monotonic decline is notable, but the prereg explicitly prohibited turning
freshness into a strategy gate from this study.

## Ruling on the planned 4HR target A/B

**Do not run it yet.**

Reason:
1. current live nearest-zone identity fails the completion-semantics gate;
2. two existing 4HR target-geometry rows are proven non-causal;
3. the zone detector can retroactively add/remove old zones as rolling MTR
   changes;
4. the attempted reaction-quality benchmark did not have adequately matched
   controls.

Running target clipping now would optimize a strategy around an unverified
location variable.

## Smallest safe next study

Offline only; no runtime change:

1. Freeze a candidate zone at the close of a **completed** 1H/4H defining
   impulse candle.
2. Freeze the impulse qualification at formation; afterward the zone can change
   only through documented test count, break, or age-out — not because a later
   rolling MTR reclassifies the historical impulse.
3. Benchmark each real zone against a **geometry-transplanted placebo**:
   - choose a non-impulse event using instrument / timeframe / kind /
     half-year / HTF-hour only;
   - transplant the real zone's exact normalized width and exact normalized
     distance from price using the placebo event's causal MTR;
   - therefore real and placebo geometry are matched by construction instead
     of nearest-neighbor approximation.
4. Freeze the control design before reading those outcomes.
5. Treat the same MNQ/MES corpus as exploratory because its zone outcomes have
   now been consumed. Any promotion claim needs prospective or otherwise
   untouched confirmation.

Only if that detector/control study shows a stable location effect should the
4HR canonical-target vs zone-clipped-target A/B reopen.

## Operational state

Unchanged:
- 1m/5m/15m evidence collection continues;
- 1m remains context / armed-trigger evidence only;
- no zone gate is added;
- no target is changed;
- no live execution is enabled;
- no broker route is changed.

**No proof, no run.**
