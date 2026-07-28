# 12HR Miyagi causal-stop rerun — draft drift note (historical investigation record)

> **Status: HISTORICAL INVESTIGATION NOTE. NOT RUNTIME AUTHORITY.**
>
> - Extracted from an **abandoned** 12HR Miyagi work-in-progress stash
>   (`stash@{0}`, based on `d59ff830f115e3815499ad49c5d3c5cdd723705d` = PR #362 tip,
>   preserved locally as `archive/stash-6-miyagi-wip-2026-07-27`).
> - **Not a replacement for PR #366.** PR #366's
>   `12HR_MIYAGI_CAUSAL_STOP_EVIDENCE_2026-07-27.md` is the authoritative evidence
>   record for the causal-stop rerun. This file preserves one section of an earlier
>   draft that #366 compressed to a single line.
> - **The final Miyagi classification is unchanged: BROKEN FOR CURRENT SYSTEM RISK
>   CONSTRAINTS.** Nothing here reopens, softens, or re-litigates that verdict.
> - **Reason retained:** an unresolved `.env` / IOC entry-tolerance provenance question
>   that affects how *older* evidence packages should be interpreted. The question is
>   system-level and outlives the Miyagi lane.

## Why this note exists

The abandoned draft's own numbers were superseded (it reported `n=0` / WAIT before the
gate-parity work in PR #365 landed; #366 supersedes it with the BROKEN finding). Its
tracked code changes were superseded by PR #365. Its scripts and results files are
byte-identical to copies already on PR #366.

The one piece with no other home on origin is the full articulation of an entry-tolerance
discrepancy noticed while pinning the run's fill model. PR #366 retains only a one-line
summary and refers to a "parked, separately-tracked `.env` drift investigation"; no such
tracked record exists in the repository. This file is that record.

## The finding, as originally written

Verbatim from the abandoned draft's "Parity findings" section:

> **Entry-tolerance .env drift (system-level)**: SYSTEM-LEVEL, NOT MIYAGI-SPECIFIC: this
> worktree's `.env` resolves ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ/_MES to
> {'MES': 8.0, 'MNQ': 16.0} -- HALF of config/settings.py's own documented "live box's
> known settings" fallback (MNQ=32/MES=16) that PR #349 (ORB) and PR #347 (VWAP) both
> asserted and relied on as canonical. This run pins the documented 32/16 values
> explicitly rather than trust the ambient value, so it stays comparable to those prior
> lanes -- but the discrepancy itself is unexplained and unresolved: either this dev
> worktree's .env has drifted from the box, or the box itself has moved to tighter
> tolerance and every prior 'honest-fill' canonical-evidence lane in this repo (ORB,
> VWAP, PR #346's corrected corpus) was run against a WIDER tolerance than the box
> actually uses. Reported to the operator as a separate open question, out of scope to
> resolve in this Miyagi-specific rerun.

Supporting context from the same draft's method section:

> **Entry model**: entry_fill_model='ioc_limit' (global PaperBroker setting, applies
> uniformly regardless of strategy -- verified in execution/paper_broker.py before
> writing this script) with generous per-root tolerance (MNQ=32/MES=16 ticks)
> approximates the module docstring's 'no IOC cap, fills at the exact trigger price'
> entry model [...]

## Why it may matter later

If the box's real entry tolerance is MNQ=16/MES=8 rather than the documented MNQ=32/MES=16,
then honest-fill evidence lanes that assumed the wider value modelled *easier* fills than
the deployed system actually achieves. That would bias those packages optimistically —
in the fill-rate direction, not the P&L-per-fill direction. Packages potentially affected:

| Evidence package | PR | Tolerance relied on |
|---|---|---|
| Corrected IOC Corpus v1 | #346 | documented 32/16 |
| VWAP Reclaim canonical evidence | #347 | documented 32/16 |
| ORB Breakout canonical evidence | #349 | documented 32/16 |

This is a **provenance** question, not a result. It is not evidence that any of the above
is wrong.

## Open audit item (not yet performed)

Establish provenance before any rerun:

1. Determine the canonical IOC entry tolerance actually configured on the live box.
2. Determine which tolerance each historical evidence package was actually run against.
3. Record the mapping.

**No reruns until provenance is established.** Re-running evidence against an unverified
tolerance would compound the ambiguity rather than resolve it.

## Provenance

- Source stash: `stash@{0}` — *"On claude/miyagi-12hr-demo-readiness: miyagi-branch work in
  progress: parity fixes + evidence"*, created 2026-07-27.
- Stash base: `d59ff830f115e3815499ad49c5d3c5cdd723705d` (PR #362 tip).
- The stash's remaining contents were audited and classified as superseded (PR #365),
  duplicate (PR #366), empty, or regenerable local corpus data — see the branch-preservation
  audit of 2026-07-27.
- The stash's untracked parent also carried ~452 MB of `data/replay_corpus_v1_5m` market
  data. That corpus is deliberately **not** committed here: it is regenerable via
  `scripts/polygon_to_replay.py --timeframe 5` and belongs local, not in repository history.
