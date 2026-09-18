# MNQ/MES roll-provenance correction — 2026-09-18

**Verdict:** `ROLL_PROVENANCE_UNKNOWN` for the exact U6→Z6 switch boundary. Research evidence only; no execution authority.

## What was rechecked

Live VPS BarHistory was copied read-only and compared against Polygon dated contracts with the X0 identity rule (4-tick identity tolerance, runner-up separation >=20 ticks).

### 15m

- MNQ: `MNQU6` identified on 239 bars through **2026-09-14T16:30Z**; `MNQZ6` identified on 269 bars from **2026-09-14T22:00Z** through the provider-served window.
- MES: `MESU6` identified on 238 bars through **2026-09-14T16:15Z**; `MESZ6` identified on 270 bars from **2026-09-14T22:00Z** through the provider-served window.
- No contiguous U6→Z6 identity transition exists in the 15m evidence.

### 5m boundary check

- MNQ: U6 identified through **16:40Z**, Z6 from **22:00Z**.
- MES: U6 identified through **16:35Z**, Z6 from **22:00Z**.
- No unidentified rows inside the sampled 5m runs, but the multi-hour gap between runs means the switch instant itself is not observed.

Therefore **22:00Z is the first proven Z6 bar, not a proven switch timestamp**.

## X0 proof-tool defect and fix

`slx0-roll-proof-v1.5.1` could return `FEED_CONFIRMED` when an old-contract run occurred before a multi-hour gap and a new-contract run occurred at/after the declared seam. It checked contract labels on each side but did not require continuity across the seam.

`slx0-roll-proof-v1.5.2` fails closed:

- a raw contract change may still be reported descriptively;
- `feed_switch_observed_in_live_span` is true only for a contiguous identified contract change;
- `reconcile_seam()` returns `NOT_OBSERVABLE` when the last proven old-contract bar and first proven new-contract bar are separated by more than the inferred bar interval;
- wrong-side identified contracts still return `FEED_CONTRADICTED`.

No historical bars are rewritten and no runtime, strategy, risk, broker, configuration or deployment path is changed.

## Current ruling

- Current Z6 identity after the gap is strongly supported where Polygon serves matching bars.
- The exact U6→Z6 seam remains **unproven**.
- Do not use the Sep-14 transition to certify a scheduler seam or historical continuous-contract roll rule.
- Fixed-dated-contract windows remain the safest confirmatory design when exact roll provenance is required.

Rule: **No proof, no run.**
