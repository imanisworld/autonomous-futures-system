---
name: "source-command-futures-forward-measurement-gate"
description: "Migrated source command `futures-forward-measurement-gate`"
---

# source-command-futures-forward-measurement-gate

Use this skill when the user asks to run the migrated source command `futures-forward-measurement-gate`.

## Command Template

# /futures-forward-measurement-gate

Purpose:
Answer exactly one question with a pinned, non-negotiable number: has enough post-2026-07-07 evidence accumulated to trust forward measurement again? This exists so "enough post-07-07 trades accumulate" (the operator's second resume condition from the 2026-07-07 measurement-cleanup session) is never decided informally, under time pressure, or with a hand-picked sample.

Core rule: No proof, no run. The gate is checked against pinned thresholds defined below, not against however many trades happen to look convincing on the day someone asks.

Background (do not re-litigate, cite instead):
- PR #176 (`docs/proof-operator-overrides.md`) resolved all known reconciler-touched-row rulings.
- PR #179 (`ops/build_honest_baseline.py`) rebuilt the honest historical baseline and confirmed 23/23 reconciler-touched rows accounted for.
- The plain-CANCELLED audit (`/futures-cancelled-audit`, `ops/audit_plain_cancelled.py`) checks the much larger set of never-reconciler-touched CANCELLED rows for a distinct anomaly class.
- The no-fill taxonomy (PR #167) deployed 2026-07-07T18:35:33Z (release `3c7a6b044cc8`); 0 rows existed under it as of the 2026-07-07 evening baseline.

Required inputs:
- Live journal history from `2026-07-07T18:35:33Z` (taxonomy deploy) forward, across both instruments
- Output of `ops/build_honest_baseline.py` re-run against current journals
- Output of `/futures-cancelled-audit` re-run against current journals

## Minimum gate (required to exit HOLD at all)

- ≥ 30 post-07-07 resolved TRADE↔OUTCOME pairs, combined across both instruments
- ≥ 10 of those are filled trades (WIN/LOSS)
- If CANCELLED no-fills continue to occur post-deploy: ≥ 10 of them carry populated `no_fill_reason`/`order_type` taxonomy fields (proves the taxonomy is actually working in practice, not just deployed)
- 0 unclassified reconciler-touched rows (per `ops/build_honest_baseline.py`)
- 0 corrupt/unparseable journal rows (`READ_ERROR` entries)
- 0 unmatched filled-looking OUTCOME rows (an OUTCOME with no preceding TRADE, excluding known pre-existing test payloads)

## Preferred gate (stronger confidence, not required to exit HOLD)

- ≥ 50 post-07-07 resolved TRADE↔OUTCOME pairs
- ≥ 20 filled trades
- Every CANCELLED row in the post-07-07 window individually classified (via `/futures-cancelled-audit`) with 0 `MISLABELED_FILL_SUSPECT` findings left unresolved
- No measurement surprises: no new bug class found during the post-07-07 window that required a fresh override or a new audit command

## What does NOT pass the gate, regardless of how it looks

- A handful of clean trades (e.g. "5 clean trades," "one green week")
- Positive P&L alone, independent of sample size or classification completeness
- A high win rate where fill/no-fill classification is unclear or unverified
- "No new errors" purely because nobody has looked closely enough to find one — absence of a fresh audit is not the same as a clean result

Forbidden actions:
- Do not lower the minimum-gate thresholds to accommodate a smaller-than-hoped-for sample.
- Do not treat the preferred gate as required, or the minimum gate as merely advisory — they are two distinct thresholds, not a range to negotiate within.
- Do not use this command to justify resuming fill tuning, strategy tuning, scaling, runner promotion, GEX activation, or new strategy builds — passing this gate only re-establishes forward measurement trust; strategy edge is validated by unrelated, existing gates (see `docs/` go-live-gate material), not by this one.
- Do not run this gate check against a hand-picked subset of trades ("the good ones" or "the ones since the last fix") — always the full post-07-07T18:35:33Z window.

Required output format:

VERDICT: GATE_NOT_MET / MINIMUM_GATE_MET / PREFERRED_GATE_MET
WINDOW START: 2026-07-07T18:35:33Z
RESOLVED PAIRS (COMBINED):
FILLED TRADES (COMBINED):
CANCELLED WITH TAXONOMY FIELDS:
UNCLASSIFIED RECONCILER ROWS:
CORRUPT JOURNAL ROWS:
UNMATCHED OUTCOMES:
CANCELLED-AUDIT SUSPECT ROWS OUTSTANDING:
NEXT STEP:

Safe next step:
If `MINIMUM_GATE_MET`, forward measurement can be trusted enough to resume ordinary operation and monitoring — this alone does not authorize fill/strategy/scaling changes, which remain gated separately. If `GATE_NOT_MET`, the safe next step is always "keep waiting and keep journaling," not "check again in a smaller window" or "round up."
