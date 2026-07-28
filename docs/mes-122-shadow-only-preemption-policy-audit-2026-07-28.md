# Shadow-only candidate-selection preemption — policy audit (2026-07-28)

**Question audited (only this question)**: are `SHADOW_ONLY` strategies
intentionally permitted to win the single candidate-selection slot and
suppress an otherwise executable strategy, even when the shadow candidate is
later denied and no trade occurs?

**No code, config, ranking, fallback, permission, risk, deployment, or
routing changes were made during this audit.** Source-only investigation.

## 1. Exact selection → permission-gate call order

`strategy/signal_engine.py::DecisionEngine.evaluate()`, in order:

1. `_find_setup_candidates()` builds the ordered candidate list. With
   `selection_mode: "ranked"` (production `risk_rules.yaml:367`, validated by
   a 555-day Polygon replay), candidates are sorted by `rank_score`
   (confluence, R:R, priority) — a strategy's `strategy_status` is **not**
   part of this ranking; a `SHADOW_ONLY` strategy's candidate can rank first
   purely on its own confluence/R:R merits.
2. A `for idx, candidate in enumerate(candidates)` loop (`signal_engine.py:661-694`)
   tries each candidate in that ranked order through `_evaluate_candidate()`
   (checks `STRAT_DIRECTION_CONFLICT` / `ENTRY_DETACHED_FROM_PRICE` /
   `RR_BELOW_MINIMUM` / HTF alignment — **not** permission status). The first
   candidate that clears these gates is marked `candidate_audit[idx]["winner"] = True`,
   assigned to `setup`, and the loop **breaks** (`signal_engine.py:686`).
   Nothing about `strategy_status` is consulted anywhere in this loop.
3. Only **after** the loop has exited and `setup` is fixed does the
   **separate** permission-gate block run (`signal_engine.py:740-786`):
   `status = self.config.strategy_status.get(setup.strategy, default_status)`.
   If `status != "PAPER_ELIGIBLE"` (and the one narrow vwap_hold proof-mode
   exception doesn't apply), the function returns `decision="NO_TRADE"`,
   `failed_gates=["STRATEGY_NOT_PAPER_ELIGIBLE"]`, **immediately** — there is
   no loop re-entry, no `continue`, no call back into step 2.

Confirmed there is exactly one enforcement point for `strategy_status` in the
whole file (`signal_engine.py:746`); `_find_setup_candidates` and
`_evaluate_candidate` never reference `strategy_status`/`strategy_permission`.
A second, unrelated usage (`_collect_blocked_candidate_audit`,
`signal_engine.py:~1596-1635`) only annotates an *already-NO_TRADE* bar's
audit trail with what permission status each candidate would have had — it
is diagnostic-only and does not feed back into selection.

## 2. Does a candidate fallback exist after a denied shadow candidate?

**No.** `strategy_fallback_enabled` (production default `False` — not set in
`risk_rules.yaml`, code default `False`, confirmed via `config/settings.py:327,839`)
is the ONLY fallback mechanism that exists, and its own introducing commit
(`5ebab9c`, 2026-06-30, PR #126 — eight days *before* the permission gate was
added) explicitly scopes it to exactly three reject codes: *"the engine picks
exactly one setup per bar ... and gives up on the whole bar if it fails
`STRAT_DIRECTION_CONFLICT` / `ENTRY_DETACHED_FROM_PRICE` / `RR_BELOW_MINIMUM`
downstream ... This adds `strategy_fallback_enabled`."* `STRATEGY_NOT_PAPER_ELIGIBLE`
is not in that list, and the permission-gate block (added later, `51d88b5`)
runs in a code region the fallback loop has already exited by the time it's
reached — there is no code path, with any config, that retries a
lower-ranked candidate after a permission denial.

## 3. Governing source proving the intended policy

- **`signal_engine.py:740-745`** (the block's own in-source comment):
  *"Separate from every gate above: those decide whether a setup is
  technically valid; this decides whether its strategy has earned the right
  to reach paper/live execution at all."* — explicitly frames permission as
  a **post-selection** concern, by design.
- **PR #228 commit message** (`51d88b5`, 2026-07-08, "Add strategy permission
  gate"): *"A new gate ... checks **the winning setup's strategy** against
  risk_rules.yaml's strategy_permission_gate map right before the TRADE
  decision is returned."* The word "winning" is used deliberately — the
  design was explicitly to gate the already-selected winner, not to
  participate in selection.
- **PR #126 commit message** (`5ebab9c`, 2026-06-30, predates #228 by 8 days):
  proves the multi-candidate fallback mechanism already existed when the
  permission gate was designed — its absence from the fallback trigger list
  is not an oversight born of the author not knowing multi-candidate
  fallback was possible; the two systems coexisted and the permission gate
  was deliberately not wired into it.
- **`tests/test_strategy_permission_gate.py`**: every test uses a
  single-candidate fixture (`fresh_market_state`, always resolves to
  `orb_reclaim` as the sole candidate). The suite proves the single-candidate
  blocked-and-returns-NO_TRADE behavior is intentional and covered, but
  **does not contain a multi-candidate test** exercising a `SHADOW_ONLY`
  winner with a lower-ranked `PAPER_ELIGIBLE` runner-up present. That
  specific consequence — a shadow candidate silently costing a different,
  executable strategy its slot — was never written down as a test
  assertion or a design note anywhere in `docs/`.
- **The one existing exception** (MNQ `vwap_hold` proof mode,
  `context/mnq_vwap_hold_proof.permission_gate_exception`, added later in PR
  #284) shows the team WAS aware `SHADOW_ONLY` status could block a
  would-be-good trade — but their answer, when they specifically confronted
  that problem, was to grant `vwap_hold` itself a narrow execution
  exception, never to add a fallback that hands the slot to a *different*
  strategy. That is suggestive of the team's general posture (block, don't
  reassign) but does not by itself decide the `strat_122` case specifically.

## 4. Classification of the seven preemptions

**Legitimate production exclusion — not an orchestration/parity defect.**
The architectural separation (permission checked strictly post-selection,
never a fallback trigger) is proven deliberate from two independent
commit messages written eight days apart by the same author, plus the
in-source comment's own framing, plus the fallback mechanism's explicitly
narrow, named scope that predates and excludes permission status. This is
not an unproven assumption and not an accidental interaction between two
systems that were never meant to touch — it is exactly the intended shape:
`strategy_status` was built as a strict post-hoc kill-switch on a fully-
resolved winner, full stop.

What is true, and worth stating precisely rather than glossing over: the
SPECIFIC consequence for `strat_122` — losing its 7 strongest historical
candidates (71.4% WR, +$432.50) to a strategy that is not even trying to
execute — was never itself analyzed, tested, or written down anywhere. The
*mechanism* is proven intentional; its *strategy-level cost to strat_122*
was not a deliberated tradeoff, just an unexamined consequence of a
correctly-working general-purpose gate. That distinction does not change the
classification (the behavior is governed, working-as-designed policy, not a
bug to fix), but it is exactly the kind of downstream effect that should now
be visible to the operator rather than silently absorbed into a "PROMISING
BUT UNPROVEN" label that never accounted for it.

## 5. Recomputed MES `strat_122` executable evidence, both interpretations

| Interpretation | n | W | L | WR | Net P&L |
|---|---|---|---|---|---|
| **A — actual/current system** (permission gate has no fallback; proven intentional; governs production today) | 16 | 5 | 11 | 31.2% | **+$120.00** |
| **B — hypothetical** (IF a shadow-denial fell back to the next-ranked candidate; NOT how the system works, NOT recommended, shown for reference only) | 23 | 10 | 13 | 43.5% | +$552.50 |

Interpretation B adds back the 7 preempted candidates at their isolated-run
values (identical direction/entry/stop/target — the same field-identity
verification already applied to the 16 confirmed survivors; each of these
7 candidates DID individually clear its own strategy gates in the isolated
run at that exact bar, so this is not a fabricated number, just a
counterfactual). It is presented only to show the magnitude of what the
proven-intentional design costs `strat_122` specifically — it is not a
proposal and nothing was changed to produce it.

## 6. Final verdict: **WAIT** (the downgrade from PR #373 stands, under interpretation A — the actual system)

The no-fallback-past-permission-denial behavior is proven intentional from
source and commit history, not undocumented and not a defect. Interpretation
A — the one that actually governs the currently-deployed system — is
therefore the correct basis for evidence classification, and it matches
PR #373's original recomputation exactly (n=16, WR 31.2%, net +$120.00).
Combined with the 10 legitimately-excluded carried-forward-position blocks
(uncontested, already agreed in the prior review), the full picture stands:
only 16 of #337's 33 canonical candidates are executable in the real,
currently-deployed production system, and that executable-only population
is too thin (n=16) and economically marginal (+$120.00 over ~14 months) to
sustain a "promising" label. **Recommend MES `strat_122`: PROMISING BUT
UNPROVEN → WAIT.**

This audit changed nothing about candidate selection, ranking, fallback
behavior, permissions, risk controls, deployment, or routing. It only
verified — from source, commit history, and existing tests, not inference —
that the behavior PR #373's original numbers rely on is the system's real,
intended design.
