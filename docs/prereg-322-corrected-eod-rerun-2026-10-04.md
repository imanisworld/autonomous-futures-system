# Preregistration — MNQ 60M 3-2-2 First Live — #1057-corrected EOD resolver rerun — 2026-10-04

trial_id: T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01

## Purpose

Produce the corrected aggregate that `docs/322-eod-resolver-erratum-2026-09-28.md`
withheld: re-score the frozen 2026-09-18 trigger-timing population with the
post-#1057 `scripts/edge_decomposition_audit.py::resolve_bracket`, which fails
closed as `EOD_BAR_MISSING` instead of walking same-ET-date evening bars when the
exact 15:55 ET bar is absent.

Offline evidence correction only. Authorized by the operator on 2026-10-04 as a
narrow lift of the 3-2-2 rerun HOLD. The global system HOLD, the $6,000
real-account restriction, and the no-promotion posture are untouched.

## Disclosure — execution order

The scoring run described below was executed in the same working session
**before** this registration line was committed. The study has zero free
parameters (frozen harness, frozen population, hash-pinned corpora, one
already-merged resolver fix), so there was nothing to select after seeing the
result, but the ledger's register-before-evidence ordering is satisfied only at
the commit level, not in wall-clock order. The operator / reconciliation role
may downgrade the ledger line to `UNREGISTERED_ATTEMPT` if that is judged
insufficient.

## Frozen inputs — nothing may change

- Rule source: `docs/prereg-322-trigger-timing-ab-2026-09-18.md` and
  `docs/strategy-rules/60M_322_FirstLive_Rules.md`, unchanged.
- Harness: `scripts/322_trigger_timing_ab_2026_09_18.py` exactly as on `main`
  (last changed by #748, content commit `6d05a62`). Not edited.
- Population: the same 34 candidates (17 LONG / 17 SHORT, 2024-08-02 →
  2026-06-11), re-derived by the harness's own research-detector vs
  state-machine cross-check; abort on any mismatch.
- Corpora (local, no fetch):
  - `data/replay_corpus_v1_5m_4hr_audit/MNQ`, tree SHA-256 must equal
    `7f09a7f82ee282e892f5db9b86be3130e6a5c1210d86828a56b9666bb06afd35`;
  - `data/replay_polygon/MNQ`, tree SHA-256 must equal
    `a8ec026dafffb1be7cb5c4643706d99bcfce2271780d1073867908937bc93a21`.
- Accounting: 1 contract, $2.00/pt, $1.48 round trip, 1/2/3 adverse ticks,
  stop-first on same-bar ambiguity, exact 15:55 bar or `UNRESOLVED`.
- Only difference from the archived run: resolver at or after `264d522`
  (#1057).

## Environment note

PaperBroker now refuses to open a position unless `MAX_CONTRACTS_HARD_CAP` is
set (#1053, 2026-10-02, after the archived run). The rerun supplies
`MAX_CONTRACTS_HARD_CAP=1` to the process environment. The harness already
trades `contracts=1`; this is plumbing, not a sizing or risk change.

## Reproduction-gate handling

The harness's `check_repro` pins plan/IOC 1-tick totals to the archived
pre-#1057 numbers. Those pins were produced by the defective resolver, so the
gate is expected to trip on any row the fix changes. The rerun records the gate
delta instead of raising, via a throwaway wrapper that imports the frozen
module and replaces only `check_repro`; the harness file itself is not edited.
Population parity (34 / 34, identical trigger/stop/target) remains a hard abort.

## Required outputs

- All outputs listed in the 2026-09-18 prereg, per model and per slippage.
- Row-level diff against the archived
  `scripts/322_trigger_timing_ab_2026-09-18.json`
  (SHA-256 `f0bde574e4fbf74d22e0554f27721976fc6a4629c46bbee18f77c5f713146254`).
- For every changed row: the date, the archived exit bar timestamp in ET, and
  proof from the corpus that the exact 15:55 ET bar is absent that day.
- Confirmation that no row changed for any reason other than the #1057 fix.

## Classification rule (frozen; not strategy validation)

Primary readout is pre-armed model C at **3 adverse ticks**:

- `PROMISING BUT UNPROVEN` if net > 0, H1 > 0, H2 > 0;
- `WAIT` if net > 0 but either half ≤ 0;
- `BROKEN` if net ≤ 0;
- `OVERFIT` is not reachable from a no-parameter rerun and is listed only to
  match the required vocabulary.

`VALIDATED` is not an allowed outcome of this trial.

## Prohibited after outcome

No parameter, stop, target, filter, slippage headline, population, or corpus
change. No second rerun. No 4HR, Polygon, deploy, or runtime work under this
trial. Any follow-up is a new preregistered study.
