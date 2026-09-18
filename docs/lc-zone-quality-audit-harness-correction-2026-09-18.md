# LC_ZONE Quality Audit — Harness Correction — 2026-09-18

## Status

**The first execution is INVALID as final evidence.**

The preregistration at commit
`b4a1f48003da41602fad69e4435f43487bd42ff7` says strict completed-HTF
semantics should wait until a 1H/4H clock bucket has completed while otherwise
holding the current detector unchanged.

The first harness implementation incorrectly added a stronger condition:
a completed 1H bucket had to contain all 4 expected 15m rows and a completed
4H bucket had to contain all 16 expected 15m rows.

That is not valid for CME futures because scheduled market-maintenance closures
can make a legitimate completed clock bucket contain fewer traded 15m bars.

## How the defect was discovered

A post-run diagnostic checked current-vs-strict disagreement only at exact HTF
completion points.

1H disagreement collapsed to approximately 0-1%, but 4H remained 28-34%.
The corpus then showed legitimate completed 4H bucket row counts including
12, 8 and 4 bars in addition to 16, consistent with scheduled/session gaps.

Therefore the v0 strict helper was not implementing the preregistered semantics.

## Invalid v0 artifact trace

The v0 outputs are not eligible for strategy or zone-quality conclusions.

- JSON SHA-256:
  `4c2ba3c5fb5a232be93e6a9cbbcc1c10713aa7b7b35a39f5a0f7f36814f05682`
- Markdown SHA-256:
  `7cf964de67865b302207939c0a08d195459e5e9e96eefa64cbcf6c06f5008d1d`

V0 printed:
- Gate 0: UNSAFE FOR TARGET-RULE VALIDATION;
- max nearest-zone disagreement 37.21%;
- 2 prior 4HR rows before defining HTF impulse completion;
- primary matched-zone uplift -3.44 pp.

**None of those outcome values are accepted until the corrected harness is rerun.**

## Correction

The only implementation change is:

- aggregate the same available market bars as the live detector;
- admit a bucket once its clock end is <= the as-of time;
- exclude only the still-forming HTF bucket;
- do not require a synthetic full row count across scheduled market closures.

No changes are made to:
- impulse threshold 1.2 MTR;
- control 0.20-0.80 MTR band;
- matching features;
- reaction thresholds;
- reaction horizon;
- pessimistic ambiguity rule;
- instrument/timeframe population;
- preregistered decision rule.

The corrected harness must be committed before the rerun.
