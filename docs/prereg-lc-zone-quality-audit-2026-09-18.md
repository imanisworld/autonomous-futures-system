# Preregistration — LC_ZONE Quality Audit — 2026-09-18

## Purpose

Determine whether the current `context.location_context` 1H/4H impulse-base
supply/demand zones identify future reaction areas better than matched
non-impulse pseudo-zones, without changing any strategy or runtime rule.

This is an **offline diagnostic only**. No result from this study directly
authorizes a strategy gate, target change, paper-fill route, DEMO order, or live
execution.

## Frozen source

- Instruments: MNQ and MES only.
- Corpus: `data/replay_polygon_v2/{MNQ,MES}`, 15m Polygon-derived bars.
- Current LC_ZONE rule remains unchanged:
  - aggregate 15m bars to 1H / 4H;
  - impulse body >= 1.2 x median true range of the current HTF lookback;
  - prior HTF bar full high-low range = zone;
  - up impulse -> demand, down impulse -> supply;
  - later HTF close beyond the far edge breaks the zone;
  - later overlapping HTF bars increment test count;
  - lookbacks 120 x 1H and 60 x 4H.
- No threshold is selected from outcomes.

## Gate 0 — higher-timeframe completion semantics

Before evaluating zone reactions, compare:

A. **Current live aggregation semantics** — `aggregate(past, minutes)` exactly as
implemented today.

B. **Strict completed-HTF semantics** — a 1H/4H bucket is eligible only after
its full clock bucket has completed. The same `detect_zones` rule is then
applied unchanged.

Measure, at every 15m as-of point:
- nearest unbroken supply/demand identity agreement;
- top/bottom boundary agreement;
- rate of current-live nearest zones whose defining impulse HTF bucket was not
  complete at the as-of time;
- within-bucket zone churn (appears, changes, or disappears before HTF close).

**Blocking rule:** if nearest-zone identity differs on >= 1.0% of comparable
as-of rows, or if any 4HR target-geometry row used a zone created from an
incomplete HTF impulse bucket, current-live aggregation is classified
**UNSAFE FOR TARGET-RULE VALIDATION**. Reaction-quality analysis will still be
reported, but the strict completed-HTF version becomes the only candidate
semantics for any future rule study.

## Zone-quality population

Use strict completed-HTF semantics for the primary quality test so that a zone
is never evaluated before its defining impulse candle is complete.

A zone becomes available at the close of the impulse HTF candle. Identity:
`instrument + timeframe + kind + base formed_ts + top + bottom`.

For each zone, observe future 15m bars only after availability. Zone lifetime is
capped at the detector's HTF lookback (120 x 1H or 60 x 4H) and ends earlier if
a completed HTF close breaks the far edge.

Primary event = **first future 15m touch** of the zone while valid.

## Matched control

Controls are pseudo-zones generated without an impulse:

- same instrument and HTF;
- candidate pseudo-impulse has the same sign as its assigned kind but
  `0.20 <= abs(body) / MTR < 0.80`;
- prior HTF bar full high-low range is the pseudo-zone;
- demand must lie below the pseudo-impulse close; supply above it;
- same lifetime / break / first-touch rules as real zones.

Each real zone is matched 1:1 without replacement to a control using only
pre-outcome features:
- instrument;
- timeframe;
- kind;
- same calendar half-year;
- same HTF bucket start hour;
- nearest Euclidean distance on standardized
  `zone_width / MTR` and `distance_from_availability_close_to_near_edge / MTR`.

If exact hour matching leaves no candidate, relax hour only while preserving
instrument/timeframe/kind/half-year. Report exact-hour and relaxed match counts.
No outcome is used for matching.

## Reaction metrics

At the first touch, freeze MTR15 from data available through the touch bar.

Direction away from the zone:
- demand -> up;
- supply -> down.

Primary binary outcome:
**clean_rejection_2h_0.5MTR** = price reaches 0.5 x MTR15 away from the zone's
near edge within the touch bar + next 7 completed 15m bars before any 15m close
beyond the far edge.

Pessimistic ambiguity: if the same 15m bar can satisfy both the rejection
threshold and far-edge failure, count failure first.

Secondary outcomes:
- clean rejection at 1.0 x MTR15 within 2h;
- far-edge close-through within 2h;
- directional close return at +1h and +2h in MTR15 units;
- MFE away from near edge over 2h / MTR15;
- maximum penetration beyond near edge over 2h / MTR15;
- probability of receiving a valid first touch before expiry.

## Required splits

Report independently:
- MNQ / MES;
- 1H / 4H;
- supply / demand;
- combined instrument x timeframe cells.

Freshness / degradation is descriptive and uses real zones only:
- first HTF test (fresh);
- second HTF test;
- third-or-later HTF test.

Do not convert a descriptive freshness pattern into a strategy gate from this
study.

## Decision rule

The current zone concept earns **QUALITY SIGNAL SUPPORTED** only if, on matched
first-touch pairs:

1. combined MNQ+MES primary clean-rejection uplift is >= +5.0 percentage points;
2. paired bootstrap 95% CI lower bound for the uplift is > 0;
3. MNQ and MES both have positive uplift;
4. 1H and 4H both have non-negative uplift (>= -2.0 pp allowed as noise, but
   neither may show a material negative effect).

If combined uplift is positive but the CI crosses zero or one instrument
reverses, classify **PROMISING BUT UNPROVEN**.

If uplift is near zero, classify **NO EVIDENCE OF ZONE QUALITY**.

If controls materially outperform the detected zones, classify **BROKEN AS A
REACTION-AREA DETECTOR**.

Regardless of outcome, this audit cannot classify a trading strategy as
VALIDATED.

## Prohibited after-the-fact changes

Do not:
- tune the 1.2 impulse threshold;
- change the 0.20/0.80 control band;
- change reaction thresholds;
- change matching features;
- drop losing timeframes/instruments;
- redefine broken/fresh after seeing results;
- select only the 4HR trade population.

Any boundary alternative (base body, proximal-only, multi-bar base, BOS/volume
confirmation) is a new preregistered follow-up, not a rescue inside this study.
