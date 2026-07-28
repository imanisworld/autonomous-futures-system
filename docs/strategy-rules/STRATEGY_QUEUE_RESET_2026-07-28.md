# Strategy status / queue reset — 2026-07-28

**Correction pass (2026-07-28, second revision)**: the first revision of this document
contained four errors, all now fixed — (1) 4HR Re-Trigger MNQ/MES rows were stale (still
showed PR #334/#335's PROMISING BUT UNPROVEN / OVERFIT figures; PR #372, merged into this
branch, is the binding executable-parity result: **MNQ BROKEN, MES BROKEN/WAIT**); (2) one
narrative bullet wrote "MNQ `strat_122`" when the audited strategy is MES `strat_122`
(the table row itself was always correctly labeled MES); (3) ORB Reclaim's runtime posture
collapsed two distinct config layers into a single word ("live") without justification —
corrected below with the exact mechanism cited; (4) this document and PR #369's own
description were out of sync with each other and with #372/#373. This revision was verified
against `gh pr view`, `git log`, `risk_rules.yaml`, and `config/settings.py` on disk for
every claim below, not restated from the first revision.

Builds on PR #369 (`claude/strategy-inventory-reconciliation`, OPEN), which already
reconciled most of this on 2026-07-27; this pass merges in PR #372's binding 4HR result
(`git merge origin/claude/4hr-evidence-closure`, clean, no conflicts), corrects MES
`strat_122` per PR #373, and adds the specific queue columns requested (causal/executable
status, runtime posture, forward-evidence-collection status, duplicate/superseded flags,
smallest next action).

**No strategy, detector, runtime, risk, broker, or deployment code touched. No new strategy
variant created.**

## Consolidated queue

| Strategy | Current classification | Canonical evidence | Causal & executable under real config? | Runtime posture (risk_rules.yaml, verified) | Forward demo evidence? | Unresolved question | Duplicate/superseded/completed | Smallest legitimate next action |
|---|---|---|---|---|---|---|---|---|
| **ORB Reclaim — current/first_cross** (MNQ+MES, deployed) | **BROKEN — negative evidence** (2026-07-27) | PR #368 (isolated own-account audit) | Yes — real engine, own account; n=38, net −$213.74, PF 0.858, own drawdown breaker halts H2 | Four distinct layers, verified separately (`risk_rules.yaml` + `config/settings.py`): (1) `enabled_concepts` includes `orb_reclaim` for both instruments; (2) `strategy_status.orb_reclaim = PAPER_ELIGIBLE`; (3) `MNQ_ORB_RECLAIM_PROOF_MODE` (default `observe_only`) is a **separate, additive, MNQ-only research-observation layer** whose own code comment states `observe_only` means "the EXISTING orb_reclaim decision ... proceeds completely unaffected" — it does not gate the base strategy; (4) therefore the base strategy's actual ability to submit paper orders is governed only by (1)+(2), both of which say yes. **Net: the base strategy can currently submit real paper orders on this repo's config** (live env var overrides on the deployed box were not independently checked this pass). Verdict/config mismatch persists regardless of posture detail | Yes by config (base strategy uncontradicted); the deployed box's actual env state not reverified this pass — **verdict/runtime mismatch, operator decision pending** | None — closed | Completed (#368) | None authorized. Flag for operator: BROKEN verdict has not been demoted in `risk_rules.yaml` — a config decision, not evidence work |
| **ORB Reclaim V4-R** (NY + `prior_rejected_high`, PR #360 rule-anatomy candidate) | **WAIT** | PR #368 (preregistered study) | Yes — real engine, own account, preregistered; n=31, PF 1.338, +$449.37, fails frozen H2+concentration gates | Not deployed; no config entry (candidate only) | No — not wired | Closed on this population; longer corpus or rule refinement would be new work, not authorized | Completed (#368). **PR #360 itself (Pass 1, rule-anatomy) is superseded by #368 for this specific candidate** — still open/unmerged, retained for provenance only | None. Operator instruction on record: no V5/V6 immediately |
| **VWAP Hold** (MNQ NY) | PROMISING BUT UNPROVEN | 2026-07-26 canonical evidence (ioc_close) | Yes for the canonical NY-only ioc_close population (~55 filled, n=107 armed) | `enabled_concepts` yes, `strategy_status: SHADOW_ONLY` — journals NO_TRADE, never reaches broker (confirmed the exact mechanism that preempted 6/7 of PR #373's MES `strat_122` candidates) | No — SHADOW_ONLY means no real fills accumulate, only observation rows | **Exit-mode resolution (static vs runner vs partial_2ct_approx) — genuinely open, no PR answers it.** Entry-definition note flagged stale in the doc profile | Not duplicate — active open question | **Candidate for next task** (see below) — exit-mode resolution needs no rule/detector change, just an isolated honest-fill comparison of the 3 exit modes on the existing canonical population |
| **VWAP Reclaim** (MNQ NY) | WAIT | 2026-07-26 canonical evidence | Yes — isolated, ioc_limit; H2 negative, fails 3-tick, n=21 MNQ thin | `enabled_concepts` yes, `strategy_status: PAPER_ELIGIBLE` (MES excluded via `disabled_concepts_per_instrument`) | Yes for MNQ (still WAIT-classified, not promoted) | None — closed | Completed | None authorized |
| **VWAP Rejection** | BROKEN — unreachable predicate | doc profile | No — predicate structurally unreachable | `enabled_concepts` yes, `strategy_status: PAPER_ELIGIBLE` — **enabled but can never actually fire** | N/A | Pine deployment sequencing flagged open in PR #321 (unrelated to the predicate defect) | Completed (predicate finding); PR #321's Pine sequencing item still open, distinct issue | None on the predicate itself (unreachable = no evidence path). PR #321's item is operator's call, unrelated |
| **MES `strat_122`** (1-2-2) | **WAIT** (corrected this session — was stale PROMISING BUT UNPROVEN in #369) | PR #337 (canonical, real engine) + **PR #373 (this session): only 16/33 candidates executable under real production concept list** + shadow-only-preemption policy audit (proven legitimate, not a defect) | **No, not at #337's reported strength** — 51.5% of #337's population is non-executable in production (21% preempted by shadow-only `vwap_hold`, 30% blocked by `orb_reclaim`'s carried-forward position); executable subset n=16, WR 31.2%, net +$120.00 | `enabled_concepts` yes (MES only), `strategy_status: PAPER_ELIGIBLE` — live, collecting real fills | Yes, actively collecting (unaffected by the audit — no runtime change made) | Needs a larger executable-only sample before any further promotion decision | **#369's MES `strat_122` row is now stale — needs updating to WAIT, done in this pass below.** #373 open, not yet actioned | None beyond accumulating forward evidence under the real config. No re-run authorized (already-executable population, not a re-test) |
| **`strat_212`** (2-1-2, tested on both MNQ and MES — not an MNQ-only cell) | BROKEN | PR #337 (same canonical run as `strat_122`) | N/A — never reaches production | **Not in `enabled_concepts` at all** — cannot fire on any instrument regardless of `strategy_status: PAPER_ELIGIBLE` | No | None — closed, negative on MES (−$1,075.50 PF 0.80), only marginal/thin on MNQ (+$354.02 PF 1.12 comm-adj) — neither cell promoted | Completed | None authorized |
| **60M 3-2-2 First Live** | **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS** | PR #367 (parity validation) | No — 0/34 candidates ever reach fill through the real engine, including a hypothetical ceiling pass with every proven parity defect removed (`max_stop_ticks` alone eliminates 27/34) | `enabled_concepts` yes (MNQ only), `strategy_status: PAPER_ELIGIBLE` — **wired live but structurally cannot fill; already fail-closed by the real risk gates** | No (structurally can't) | None — closed | Completed (#367). PR #359 (demo-readiness wiring) still merged/deployed but inert | None authorized |
| **4HR Re-Trigger** (MNQ) | **BROKEN FOR CURRENT EXECUTABLE FORM** (corrected this pass — PR #372, merged into #369, supersedes #334/#335's PROMISING BUT UNPROVEN) | PR #334 (batch-1, standalone-function evidence, now superseded) + **PR #372 (binding): full-engine executable-parity audit** | **No** — #334's 80-fill population was produced by a standalone research function with no dependency on any real runtime gate. Running the same 81 known candidates through the real engine (incl. a hypothetical ceiling pass exempting every candidate parity-defect gate) collapses to **1/81 real fills (1.2%)** — the real blockers (`TREND_STRENGTH_BELOW_REQUIRED`, `stop_too_wide`) are legitimate, correctly-preserved gates, not parity defects | `enabled_concepts` yes, `strategy_status: PAPER_ELIGIBLE`, MES excluded via `disabled_concepts_per_instrument` — **runtime wiring (#335) unchanged/still deployed, already fail-closed per #372's own statement** | Technically still running (unchanged deployment), but #372 shows its own real-fill population is ~1/81 — not a meaningful "accumulating promising evidence" lane anymore | None — closed by #372 | Completed (#334/#335 superseded by #372) | None authorized. Do not re-test; do not attempt a parity patch (#372: "no parity patch justified") |
| **4HR Re-Trigger** (MES) | **BROKEN / WAIT** (corrected this pass — PR #372 supersedes #334's OVERFIT classification) | PR #334 (superseded) + **PR #372 (binding)** | **No at #334's reported strength** — ceiling pass rescues 7→12/76 fills (PF 1.854) but H2 is net −$273.75 vs H1 +$655.00, failing the frozen H2-negative bar | Excluded via `disabled_concepts_per_instrument.MES` — never reaches production regardless | No | None — closed by #372 | Completed (#334 superseded by #372) | None authorized |
| **12HR Miyagi** | **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS** | PR #366 (causal-stop closure) | No — MNQ 0/8, MES 2/10 of real triggered signals fit inside the account's own `max_stop_ticks` cap once the stop is computed causally (the original PF 2.81/1.98 evidence used a lookahead-defective stop formula) | Never merged — PR #362 (demo-readiness) closed without merge | No | None — closed | Completed (#366); #362 correctly closed no-merge | None authorized |
| **ORB Breakout** (MNQ, non-inverted) | WAIT | 2026-07-26 canonical evidence | Yes — isolated ioc_limit, both exits; H2 washout, fails 1-4 tick slippage, n=25 thin, own breaker halted mid-corpus | `enabled_concepts` yes, `strategy_status: PAPER_ELIGIBLE` — live but WAIT-classified, not promoted | Yes (unpromoted) | None — closed, negative | Completed | None authorized |
| **ORB Breakout — inverted** (MNQ, paper-only) | PROMISING BUT UNPROVEN | PR #364 (deployed) | Yes — n=111 fixed population / 108 causal, PF 2.392/2.251, all sub-periods/sessions/directions positive, survives +4-tick stress | Deployed via `MNQ_ORB_BREAKOUT_INVERSE_MODE=paper_sim` (env-gated, not deployed via `risk_rules.yaml`); legacy non-inverted proof lane forced `observe_only` while active | Yes, actively collecting since 2026-07-27T04:19:13Z epoch | None — open question, but explicitly "leave alone, accumulate" per the doc | Completed (#364, merged) | None. Accumulate forward evidence |

## What's genuinely untouched vs. what only looks untouched

Per the instruction to select a next task only if it's a real gap, not already answered by
an open/completed PR, and to produce HOLD rather than a guessed priority when evidence is
missing or conflicting:

- **Research question closed, no further work authorized**: ORB Reclaim (both current and
  V4-R), 60M 3-2-2, 12HR Miyagi, 4HR Re-Trigger (**both instruments, as of #372** — MNQ
  BROKEN FOR CURRENT EXECUTABLE FORM, MES BROKEN/WAIT), `strat_212` (both instruments
  tested, neither promoted), VWAP Reclaim, ORB Breakout non-inverted, VWAP Rejection's
  predicate defect. Every one of these has a completed evidence-closure PR (or, for
  `strat_212`, a completed canonical run) reaching a settled verdict. Re-touching any of
  them would be exactly the "post-result optimization" pattern the operator flagged for
  V4/V4-R. **Note**: "research closed" is not the same as "runtime stopped" — 4HR Re-Trigger
  MNQ's deployment (#335) stays live/unchanged per #372's own statement, it just no longer
  reads as a promising accumulating lane now that its real-fill rate is known (~1/81).
- **Actively accumulating and still reading as promising**: ORB Breakout inverted, MES
  `strat_122` (executable-population-corrected, n=16 base to grow from).
- **The one candidate that is genuinely untouched and not answered by any PR**: **VWAP
  Hold's exit-mode resolution** (static vs runner vs partial_2ct_approx). No PR has ever
  addressed it; it needs no rule or detector change, only an isolated honest-fill comparison
  of the three exit modes already implemented in `execution/paper_broker.py`, run against
  the existing canonical NY-only ioc_close population. This is the smallest, most clearly
  in-scope open question in the whole queue.
- **One operator-decision item, not a research task**: ORB Reclaim current/first_cross is
  formally BROKEN (PR #368) but `enabled_concepts` + `strategy_status: PAPER_ELIGIBLE` both
  still say yes in `risk_rules.yaml` — the base strategy can currently submit real paper
  orders (the separate `MNQ_ORB_RECLAIM_PROOF_MODE=observe_only` research layer does not
  block it; see the table row above for the full four-layer breakdown). Verdict and runtime
  config disagree. That's a config decision for the operator (demote/disable), not evidence
  work, and this audit does not touch `risk_rules.yaml` to resolve it.

**Verdict on "select the next research task": HOLD.** VWAP Hold's exit-mode question is the
only clean candidate, but selecting it over the alternative of doing nothing (the standing
evidence-phase directive's default) is itself a prioritization call, not a fact I can verify
from source — per the instruction, that's the operator's decision to make, not mine to guess.
