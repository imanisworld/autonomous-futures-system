# Futures PRs #236–#245 Read-Only Audit — 2026-07-10

Recorded per the operator's own verdict after reviewing the read-only
audit findings. This note is the tracked record of that verdict --
nothing here changes code, config, or runtime behavior.

## Verdict

**No rollback indicated from the facts reported. #239 must be recorded
as a real runtime/config change, not treated as docs-only.**

## What #236–#245 actually were

Of the nine PRs in this range, eight (#236, #237, #240, #241, #242,
#243, #244, #245) were docs/read-only-analysis/tests only, each
verified by diff (not by PR description) to touch none of
`execution/`, `risk/`, `config/`, `webhook/`, `broker*`, `main.py`, or
`strategy/`. `proof_builder` was not implemented by any of them.

## The one exception: PR #239

- Touched `risk_rules.yaml` (14 lines) -- the only config change in
  this range.
- Changed active demo-bot behavior: narrowed MES to `orb_reclaim` as
  the sole active strategy, using the pre-existing
  `disabled_concepts_per_instrument` mechanism (present since PR
  #56/#126, not introduced here).
- Net effect is risk-reducing, not risk-expanding -- three
  unvalidated MES strategies were disabled, none newly enabled. MNQ
  untouched.
- No `execution/`, `risk/`, `broker*`, or `strategy/` code changed --
  only the YAML consumed by already-existing, already-tested gating
  logic.

## Lane distinction to keep

```text
proof_builder lane:
  HOLD / OBSERVATION ONLY
  No implementation, no config, no runtime change
  (PR #235 merged docs-only; nothing since)

existing futures demo bot lane:
  Already operating separately (live demo since 2026-06-04)
  PR #239 narrowed MES active concepts
  Needs an audit trail and ongoing monitoring, not a rollback
```

## Watch items (not acted on, flagged for future attention)

1. **PR #238 audit-trail gap.** Shows `merged: true` via the GitHub
   API, but its content (a shadow-strategy deep-dive on
   `impulse_first_pullback_observed` / `strat_22_reversal_observed`)
   is not reachable from `main` -- it was merged into PR #237's
   feature branch, which did not carry it forward when #237 itself
   later merged. Not a runtime issue; do not re-land it unless the
   audit trail itself is specifically being repaired.
2. **TradingView `MES1!`/`MNQ1!` alert misconfiguration** (found by
   PR #243): sending 5-minute bars under the wrong symbol format.
   Confirmed not to contaminate the real decision pipeline. Harmless
   noise, worth fixing on its own terms.
3. **MNQ's 3-day zero-trade streak** (found by PR #243): statistically
   unusual (~0.5% under a naive Poisson check) but not enough data to
   separate a genuinely quiet market from an undiscovered cause. Needs
   more days of data before any conclusion, not immediate action.

## Current state after this audit

```text
OPTIONS: complete through 25P, ready for real setup testing
FUTURES proof_builder: HOLD / design-only
FUTURES demo bot: PR #239 narrowed MES; monitor, no rollback
```

No `risk_rules.yaml` change is being made by this note or as a result
of this audit.
