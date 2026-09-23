# Architecture Gap Review — 2026-09-23

**Status: AUDIT / DOCUMENTATION ONLY. NO EXECUTION AUTHORITY.**

This document records a clean-sheet architecture review against the **current repository**, not against older audit handoffs. It does not authorize strategy changes, risk changes, broker changes, deployment, VPS changes, paper/demo activation, or live execution.

**Repository basis:** `main@e4c6f0d81559bbeb0c3dc2846580f0771e990576` (2026-09-23).

**Important boundary:** repository state is not proof of deployed/VPS state. Runtime claims still require box verification.

---

## 1. Verdict

The outside-model exercise did **not** show that the system needs a redesign or a large new strategy portfolio.

The current architecture already contains most of the controls the clean-sheet reviews independently recommended:

- preregistered research;
- fixed null thresholds;
- deterministic strategy/risk paths;
- replay/parity work;
- no-trade and rejected-candidate logging;
- paper/observer isolation;
- source/evidence provenance;
- fail-closed execution controls;
- explicit no-execution research lanes.

The remaining architecture gaps are narrower:

1. **REAL GAP — repo-enforced experiment/trial history.**
2. **REAL GAP — system-level fault-injection suite.**
3. **PARTIAL — automatic post-rejection counterfactual follow-through.**
4. **PARTIAL — standing three-way execution calibration: replay vs conservative internal sim vs broker paper/demo.**
5. **HARDENING — require complete evidence identity at report-generation time.**

No item in this review justifies enabling a strategy, adding a market, loosening risk, changing execution, or reopening a closed strategy result.

---

## 2. Corrections to the stale clean-sheet gap list

The prior external review was useful, but several items were incorrectly labeled "missing" because the review packet lagged the repo.

| Claimed gap | Current repo reality | Status |
|---|---|---|
| Formal hypothesis/preregistration registry | Present and actively used. At this audit point `docs/` contains **23** files named `prereg-*.md`; current studies explicitly freeze hypotheses, populations, thresholds, allowed variants, data windows, and prohibited retuning before the run. | **ESTABLISHED** |
| Trial / multiple-testing ledger | Per-study cell counts and family-wise/null thresholds exist, but there is no single repo-enforced pre-run ledger that guarantees every attempted variant is recorded before execution. | **REAL GAP** |
| Null / placebo testing | Present. Current research uses the fixed null baseline **p95 PF 1.94 / max-of-500 2.55**, and newer work also uses direction/time nulls where preregistered. | **ESTABLISHED** |
| Rejected-candidate counterfactual data | Rejections/no-trades are logged and have already supported gate-attribution studies. The weakness is that post-rejection outcome follow-through is not a universal automatic field/process. | **MOSTLY ESTABLISHED / PARTIAL AUTOMATION GAP** |
| Cross-strategy thesis deduplication | Important before multiple simultaneous executable strategies exist, but not a present blocker when the executable set is empty or tightly isolated. | **DEFERRED PRECONDITION** |
| Fault-injection suite | Some lane-specific fault-injection tests exist, including crash-safe/idempotent persistence tests. There is no consolidated system-level suite covering the major operational failure classes experienced across the project. | **REAL GAP** |
| Shadow execution calibration | Lane-specific slippage/parity work exists, but no standing three-way comparator continuously reconciles replay, conservative internal simulation, and broker paper/demo behavior. | **PARTIAL** |
| Immutable evidence identity | Strong provenance exists in many studies: code SHA, script hash, data fingerprints, source-drift checks, prereg references. It is not uniformly mandatory for every generated report. | **MOSTLY ESTABLISHED / HARDENING** |

### Evidence for the corrections

Current examples include:

- `docs/prereg-strat-htf-magnitude-ftfc-2026-09-23.md`
- `docs/prereg-mgc-4h-wide-forward-2026-09-23.md`
- `docs/prereg-futures-non-strat-coverage-2026-09-21.md`
- `docs/prereg-forward-one-min-trigger-evidence-review-2026-09-18.md`
- `scripts/strat_rules_magnitude_ftfc_audit.py`
- `scripts/counterfactual_stats_report.py`
- `tests/test_counterfactual_stats_report.py`
- `strategy/signal_engine.py`
- `tests/test_no_enabled_strategy_reason.py`
- `tests/test_asia_d_ema_paper_cohort.py`
- `docs/mes-122-per-leg-slippage-gate-2026-09-09.md`
- `scripts/afs-drift-gate.sh`
- `ops/project_check/proof_lanes.py`

---

## 3. What is already structurally sound

### 3.1 Preregistration is now a real control

Preregistration is not just a documentation preference. Current studies freeze:

- the question;
- population;
- date partitions;
- inputs;
- setup definitions;
- fill model;
- costs;
- thresholds;
- nulls;
- permitted variants;
- prohibited post-hoc choices;
- conditions for a next step.

Examples explicitly prohibit retuning from forward evidence and separate prior-exposed cells from untouched evidence.

**Assessment:** do not build another hypothesis-registry system merely because an outside model recommended one. Improve enforcement around the existing process instead.

### 3.2 Null testing is already part of the evidence standard

The repo carries a fixed research null baseline:

- single-test reference: **PF 1.94**;
- stronger family-wise / max-of-500 reference: **PF 2.55**.

Recent preregs also use strategy-specific random-direction and random-time nulls where appropriate.

**Assessment:** null/placebo testing is not a missing architecture layer.

### 3.3 Rejected/no-trade evidence is already useful

The project has already reconstructed and analyzed blocked populations rather than treating "no trade" as no information.

A concrete example is the 2026-09-21 MNQ TRENDING analysis in `docs/prereg-mnq-volume-label-and-sunday-reopen-2026-09-21.md`, where 67 shadow setups were decomposed and the regime-only rejected subset was measured separately.

`scripts/counterfactual_stats_report.py` also aggregates already-produced counterfactual rows while deliberately refusing to recreate or alter research logic.

**Assessment:** the architecture exists. The remaining issue is making post-rejection follow-through standard and automatic across eligible lanes.

### 3.4 "No enabled strategy" is explicit, not silently misclassified

`strategy/signal_engine.py` has an explicit `NO_ENABLED_STRATEGY` path, and `tests/test_no_enabled_strategy_reason.py` protects it.

**Assessment:** an empty executable set can be represented truthfully instead of being blamed on whichever later gate happens to execute first.

### 3.5 Evidence provenance is substantially mature

Current studies and operations use combinations of:

- prereg references;
- exact git SHAs;
- script hashes;
- raw-data hashes;
- population fingerprints;
- release manifests;
- source-drift detection;
- exact strategy/config identities;
- evidence epochs.

The remaining issue is **uniform enforcement**, not invention of provenance from scratch.

---

## 4. Real gap #1 — repo-enforced experiment/trial ledger

### What exists

The project records many trial counts and family sizes inside individual preregs/results:

- all 18 cells;
- 0/47;
- 8 wide configurations;
- fixed null thresholds;
- prior-exposed cells disclosed;
- reruns prohibited on frozen corpora in several studies.

That is materially better than untracked optimization.

### What is missing

There is no single mandatory repo-controlled mechanism that answers:

> How many distinct hypotheses, parameter cells, variants, reruns, filters, and rescue attempts were examined before this result?

A failed or abandoned experiment can still be documented inconsistently across separate files rather than entering one enforced research-history record.

### Why it matters

Multiple-testing risk is not only a statistical formula problem. It is also an **accounting problem**.

A strong-looking result after 1 attempt and the same result after 60 variants do not carry the same evidentiary weight.

### Minimum safe design requirement

Do **not** build a large research database yet.

The smallest useful control would be a versioned append-only registry requiring, before a scored run:

- `experiment_id`;
- parent hypothesis/family;
- prereg path + prereg commit;
- population/data identity;
- parameter/variant identity;
- prior exposed attempts in the same family;
- run authorization state;
- result artifact path;
- final disposition: CLOSED / WAIT / PROMISING / BROKEN / OVERFIT / etc.

The enforcement question should be solved before adding sophisticated statistics.

---

## 5. Real gap #2 — system-level fault-injection suite

### What exists

The repo does contain targeted failure-path testing. For example:

- `tests/test_asia_d_ema_paper_cohort.py` includes explicit crash-safe/idempotent persistence fault-injection coverage;
- execution and deployment tests cover numerous individual safety paths;
- operational incidents have often produced focused regression tests.

So "there are no fault-injection tests" would be false.

### What is missing

There is no consolidated, promotion-relevant fault suite that deliberately exercises the system's critical failure classes end to end.

The project has experienced enough real incidents that these cases should become reusable tests rather than lessons remembered by operators.

### Required failure classes

A future system-level suite should cover at least:

- stale or delayed market data;
- missing bars/data gaps;
- duplicate webhook/event delivery;
- malformed payload;
- duplicate order identity/idempotency failure;
- broker rejection;
- partial fill;
- unknown order state;
- disconnect after submit but before acknowledgement;
- service restart with pending state;
- corrupted or partially written collector state;
- collector/self-poisoned state;
- journal/broker mismatch;
- stale broker positions/orders;
- risk-state persistence across restart;
- absorbing risk-state defects;
- notification/reporting dependency hangs;
- deployment/release drift;
- collector source drift;
- market-closed/instrument-closed behavior.

### Promotion principle

Fault handling should eventually be a release/promotion gate:

**if a failure mode can create an unauthorized order, duplicate action, unprotected position, false evidence, or silent data corruption, the test must fail closed.**

This is a safety infrastructure task, not a strategy task.

---

## 6. Partial gap — automatic post-rejection counterfactual follow-through

### What exists

The project already preserves rejected candidates and has generated useful counterfactual studies from them.

The current evidence proves that manual/targeted reconstruction is possible.

### What is missing

There is no universal rule that every eligible rejected candidate automatically receives a later causal outcome record under the frozen hypothetical geometry.

Today the question:

> "What happened after this exact rejection?"

may require a separate audit or study.

### Safe target

For lanes where a valid counterfactual is defined **before outcomes are seen**, the evidence pipeline should be able to attach:

- rejection reason;
- rejected-at timestamp;
- hypothetical entry/stop/target or explicit "no valid geometry";
- fill/no-fill under the frozen execution model;
- MAE/MFE;
- terminal result/time exit;
- provenance/version identity.

This must remain observational. It must not silently convert rejected events into trades or retroactively invent geometry.

---

## 7. Partial gap — standing three-way execution calibration

### What exists

Execution realism is already taken seriously:

- IOC tolerance studies;
- pessimistic same-bar handling;
- gap-stop correction;
- slippage stress;
- production/replay parity work;
- lane-specific paper/demo execution checks.

### What is missing

There is no standing framework that continuously compares the same candidate across:

1. deterministic replay;
2. conservative internal simulator;
3. broker paper/demo result.

The comparison should focus on:

- entry eligibility;
- expected fill price;
- actual simulated/broker fill price;
- slippage;
- partial/no-fill state;
- stop/target lifecycle;
- resolution timestamp;
- terminal P&L;
- discrepancies beyond a preregistered tolerance.

### Why this remains partial

`docs/mes-122-per-leg-slippage-gate-2026-09-09.md` and other lane-specific work address parts of this problem. The missing piece is the **standing cross-lane calibration framework**, not execution realism in general.

---

## 8. Hardening item — mandatory evidence identity at report generation

### What exists

Evidence identity is already strong in many high-value studies.

### What can still improve

A report should be unable to present a scored result without the required identity bundle.

For scored research, the minimum identity should eventually include:

- code commit;
- prereg commit/path;
- script/version hash;
- data/corpus fingerprint;
- strategy/config identity;
- fill-model identity;
- cost/slippage assumptions;
- population fingerprint or exact candidate identity set;
- generated-at timestamp;
- whether the evidence was previously exposed.

Missing required identity should produce **UNKNOWN/BLOCKED**, not a normal-looking report.

This is hardening of an existing architecture, not a new subsystem requirement.

---

## 9. Cross-strategy thesis deduplication — defer until it becomes real

Outside reviewers correctly warned that multiple strategies can be disguised versions of the same bet.

Examples:

- MNQ breakout + MES breakout;
- ORB continuation + trend pullback;
- several "confirmations" that all reduce to U.S. equity-index directional exposure.

This matters **before** multiple strategies or correlated markets can take simultaneous executable risk.

It is not a reason to build a portfolio optimizer now.

### Required precondition before multiple executable lanes

Before a second simultaneously executable strategy/market is admitted, define at minimum:

- instrument/correlation cluster;
- shared-thesis identity;
- combined risk-at-stop;
- duplicate-signal handling;
- whether attribution is exclusive or shared.

Start with simple mutual exclusion/caps. Do not jump to adaptive portfolio optimization.

---

## 10. Strategy-family review

### Failed-auction / liquidity-sweep reversal

**Not a newly discovered gap.**

The repo already contains extensive ORB reclaim/rejection/inverse-ORB work, including corrected evidence that retired optimistic earlier inverse-ORB results.

Relevant evidence includes:

- `docs/inverse-orb-decision-time-replay-2026-09-08.md`
- `docs/inverse-orb-recomputed-bracket-study-2026-09-08.md`
- ORB reclaim/rejection studies and proof lanes;
- current Strategy Inventory history.

Do not reopen this family because an outside model used a different label for it.

### Volatility contraction -> expansion

This remains a potentially distinct future research hypothesis.

However:

- it is not authorized;
- it should not interrupt active forward-blind campaigns;
- it needs its own preregistration;
- the expansion event must first prove conditional value before stop/target tuning.

**Status: FUTURE RESEARCH CANDIDATE ONLY.**

### Daily / multi-day trend following

This is conceptually distinct from the project's short intraday work, but the present historical-data constraint matters.

`docs/prereg-strat-htf-magnitude-ftfc-2026-09-23.md` records that the paid Polygon plan returned no futures bars before 2024-10 during the 2026-09-23 probe.

That leaves limited independent history for a low-frequency daily trend study.

Do not force a daily-trend campaign merely because several outside models suggested it. With inadequate sample/market-cycle coverage the correct verdict is **WAIT**.

---

## 11. Current research context that reviewers must not overwrite

As of the repository basis for this review:

- the cross-market grid/geometry work has already advanced beyond the older handoff;
- `docs/prereg-mgc-4h-wide-forward-2026-09-23.md` is a forward observation preregistration;
- its rules explicitly grant **no execution authority**;
- its historical result is motivation only, not forward evidence.

This gap review does not grade that campaign and must not be used to bypass its blind/forward gates.

Likewise, open PRs and runtime state must be checked separately before making any operational claim.

---

## 12. What not to build from this review

Do not use this document to justify:

- a strategy rewrite;
- a new strategy activation;
- a new instrument activation;
- MES/MNQ switching;
- options expansion;
- adaptive sizing;
- ML/probabilistic regime models;
- automatic strategy ranking;
- cross-market factor models;
- dynamic portfolio optimization;
- live execution;
- risk-limit increases;
- broker-route expansion;
- retuning a frozen study.

The architecture problem identified here is primarily **research accounting + failure testing + evidence automation**, not lack of trading ideas.

---

## 13. Ordered safe next steps

This document is itself the first step.

1. **Independent review of this document.**
   - Claude should verify every "already have" and "gap" claim against current main.
   - Perplexity or another outside reviewer should be given the same repo basis and asked to falsify this review, not merely agree with it.

2. **If the review survives, preregister the experiment-ledger design.**
   - Define the minimum schema and enforcement point.
   - Do not build a large research platform.

3. **Separately preregister the system-level fault-injection suite.**
   - Start from real incidents already experienced.
   - No broker/live expansion.

4. **Then evaluate automatic rejected-candidate follow-through.**
   - Only for populations with causally valid predeclared hypothetical geometry.

5. **Later hardening:** standing three-way execution calibration and mandatory report identity.

No implementation work above is authorized by this document alone.

---

## 14. Reviewer checklist

A reviewer should attempt to falsify each conclusion and return:

- **CONFIRMED** — evidence supports the claim;
- **PARTIAL** — claim is directionally right but materially incomplete;
- **FALSE** — repo contradicts the claim;
- **UNKNOWN** — current repo evidence cannot establish it.

For every disagreement, provide:

- exact file/path;
- exact code/test/doc evidence;
- whether it changes a present safety decision;
- smallest safe correction.

Do not propose strategy expansion until the architecture findings are resolved.

---

## 15. Source-of-truth rule for future outside audits

Future outside reviewers should **not** be handed an old audit document by itself.

Minimum review packet:

1. this architecture-gap review;
2. latest dated futures current-status/current-state handoff;
3. `docs/strategy-rules/Strategy_Inventory.md`;
4. current `main` SHA;
5. open PR list;
6. relevant prereg/result pair for the question being audited;
7. box/runtime proof separately when deployment state matters.

The reviewer must verify the packet against current repo evidence rather than accepting its conclusions.

That is the central process correction from this exercise: **stale reviewer context can create false gaps and duplicated work even when the underlying system is already correct.**
