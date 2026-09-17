# Futures Known-Defect Disposition — 2026-09-17

**Status:** AUDIT ONLY / PAPER ONLY. This is a scope-control record so every observed imperfection does not become an automatic code project.

## Must-fix before relying on the affected evidence/runtime path

### #612 — shared-journal capacity erases later observation on deployed box

- **Repo:** fixed and tested.
- **Box:** not deployed under the standing release freeze.
- **Impact:** on a day where the shared main journal reaches max-trades or consecutive-loss capacity, the old deployed runner can stop before strategy evaluation and erase later candidate evidence.
- **Disposition:** include in the next sanctioned paper-only runtime release. Do not alter the limits.

### C8 — MES top-level `market_condition` missing

- **Root cause:** #376 universe short-circuit can leave `DecisionOutput.market_condition=None`; the correct payload/Pine value survives in `context.market_condition`.
- **Impact:** `read_other_instrument_regime()` and `regime_persistence()` consumers that rely on the top-level journal contract go blind for the affected MES rows.
- **Required repair:** at decision-journal write time, preserve an existing non-null top-level value; otherwise copy `context.market_condition` if present. If both are null, remain null.
- **Do not change:** DecisionEngine gates, Pine label, collector formula, historical rows.
- **Disposition:** small offline code/test PR, then include in a later sanctioned runtime release only after exact-head CI.

### C14 — holiday daily-session anchor divergence

- **Root-cause class:** proven; generalized calendar rule not yet proven.
- **Impact:** VWAP and other Pine daily-session constructs are not promotion-grade across holiday transitions.
- **Disposition:** execute the separate C14 proof plan. No date-specific patch. VWAP remains `NOT_ADMITTED`.

## Known issues that are not current runtime must-fixes

### Observer `overnight_range_location` dead copy

The working bar-derived ONH/ONL collector already supplies the relevant evidence. Repairing a redundant dead observer copy would add plumbing without changing the authoritative evidence path.

**Disposition:** `DO_NOT_TOUCH` unless a future prereg explicitly requires that exact observer field.

### 886 historical 5m rows in the 15m analysis window

`timeframe_minutes` identifies them and the completed analysis excludes them at analysis time.

**Disposition:** historical data-quality fact, not a current execution repair. Do not rewrite old journals.

### `range_signal` candidates lack per-candidate location annotation

This limits F10/F11-style context analysis for that family, but the first formal context-permission review produced 0/22 gate candidates and the context direction is itself awaiting the hard 2026-09-30 review.

**Disposition:** `DEFER`. Implement only if the context-permission program survives its preregistered review and the field is still needed by a new prereg.

### 12.1% impulse-phase disagreement between collectors

The two observational sources use different heuristics. Disagreement is therefore not, by itself, proof that one implementation is broken.

**Disposition:** preserve both provenance labels; do not force agreement or choose a winner without a definition-level prereg.

### Isolated MES 1-2-2 evidence under its own log directory

This is an intentional lane-isolation consequence. The defect risk is double-counting or omission by census/join tools, not execution behavior.

**Disposition:** readers/census must enumerate the isolated path explicitly. Do not collapse the lane into the shared journal merely for convenience.

## Do-not-touch list during current work

Unless separate evidence proves a defect, do not change:

- strategy entry/stop/target rules;
- risk caps or max trades/day;
- session permissions;
- broker routing;
- paper/live flags;
- active campaign epochs;
- context gates based on the first review;
- VWAP tolerance/admission status;
- historical journal contents.

## Current engineering priority

1. finish #623/M2K roll provenance and tranche-2 definitions in their own lane;
2. complete C8 repair/test package;
3. execute C14 generalized-session proof when the necessary Pine/calendar evidence is available;
4. deploy #612/C8 only through the sanctioned post-freeze paper release path;
5. otherwise let forward evidence accumulate rather than inventing features.