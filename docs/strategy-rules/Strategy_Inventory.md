# STRATEGY INVENTORY
**Autonomous Futures System — Master Reference**
*Last updated: 2026-07-23*

---

## How to read this document

Every strategy is classified across eight dimensions:

| Dimension | What it means |
|---|---|
| **Rules complete** | Written rules are objective and reproducible — two independent implementations produce the same signal |
| **Detector built** | Python function exists that reads bars and outputs signal/entry/stop/target |
| **Replay parity** | Live and replay formulas are proven identical |
| **Honest fills** | Results use IOC-faithful or realistic fill model, not always-fills |
| **Walk-forward** | Both chronological halves independently positive |
| **Slippage tested** | Edge survives at 2-tick and 3-tick adverse slippage |
| **Sample adequate** | Enough trades to draw directional conclusions (minimum 30 per cell) |
| **Verdict** | Current classification |

Verdict taxonomy:
- **VALIDATED** — passes all eight dimensions
- **PAPER PROOF** — promoted to paper trading, accumulating live evidence
- **PROMISING BUT UNPROVEN** — positive replay evidence, not yet fully validated
- **WAIT** — rules incomplete or detector missing
- **RESEARCH ONLY** — concept only, no testable spec
- **BROKEN** — tested and fails honest fill or walk-forward
- **RETIRE** — negative results, no path to recovery

---

## Master Table

| Strategy | Rules | Detector | Replay parity | Honest fills | Walk-forward | Slippage | Sample | Verdict |
|---|---|---|---|---|---|---|---|---|
| ORB Reclaim (MES) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ n=305 | **PAPER PROOF** |
| ORB Reclaim (MNQ) | ✅ | ✅ | Partial | ✅ | ❌ insufficient | ✅ | ⚠️ n=253 thin | **PROMISING BUT UNPROVEN** |
| 4HR Re-Trigger | ✅ blockers resolved | ❌ | ❌ | Partial — external study | ✅ | Partial | ⚠️ n=32 MNQ | **WAIT — build detector** |
| 12HR Miyagi | ✅ blockers resolved | ✅ | Partial — standalone research module | ✅ | ✅ both halves (H2 thin) | ✅ 1-4 tick | ⚠️ n=15 MNQ / n=19 MES thin | **PROMISING BUT UNPROVEN** |
| 60M 3-2-2 First Live | ✅ blockers resolved | ✅ | Partial — standalone research module | ✅ IOC-faithful | ✅ both halves | ✅ 1-4 tick | ⚠️ n=34 MNQ thin | **PROMISING BUT UNPROVEN** |
| VWAP Hold (MNQ NY) | ❌ entry definition unclear (stale — see 2026-07-26 audit note in profile below) | Partial | ❌ (stale — see profile) | ✅ ioc_close, production-matching (2026-07-26) | ✅ both halves, all 3 exits, NY-only ioc_close (2026-07-26) | ✅ 1-3 tick, NY-only ioc_close (2026-07-26) | ⚠️ n=107 armed / **~55 filled, NY-only** (canonical — session-filtered from the 348-arm blended pop, which is provenance-context only) — thin, clears the 30-min literal bar but not comfortably | **PROMISING BUT UNPROVEN** |
| VWAP Reclaim (MNQ NY) | ❌ | Partial | ❌ | ❌ | ❌ | ❌ | ⚠️ n=29 thin | **WAIT** |
| VWAP Rejection | ❌ | Partial | ❌ | ❌ | ❌ | ❌ | — | **BROKEN — unreachable predicate** |
| ORB Breakout (MNQ) | ✅ | ✅ | Partial | ✅ | ⚠️ H2 thin | ✅ | ⚠️ n=60 | **WAIT — gated on runner exit** |
| PDL Reclaim | ✅ | ✅ | Partial | ✅ | ❌ too thin | — | ❌ n=13 | **RESEARCH ONLY — undersample** |
| PDH Reclaim | ✅ | ✅ | ✅ | ✅ | ❌ both halves neg | ❌ | ✅ n=67 | **RETIRE** |
| ICC (all variants) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | — | **RESEARCH ONLY** |
| ICT — FVG | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | — | **RESEARCH ONLY** |
| ICT — Order Block | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | — | **RESEARCH ONLY** |
| ICT — Liquidity Sweep | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | — | **RESEARCH ONLY** |
| 7HR Sweep | ❌ no source material | ❌ | ❌ | ❌ | ❌ | ❌ | — | **RESEARCH ONLY — undefined** |
| FOMC | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ n=16 | **RESEARCH ONLY — not portable** |
| Main Combos (naked) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | — | **RESEARCH ONLY — negative without context** |
| IPC Short | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ n=1615 | **RETIRE — fat tail artifact** |
| Structural Level Fade | ✅ | ✅ | ✅ | ✅ | ❌ both neg | ❌ | ✅ n=3396 | **RETIRE** |

---

## Detailed Strategy Profiles

---

### ORB Reclaim — MES
**Verdict: PAPER PROOF**

- Entry: price reclaims ORB high (long) or ORB low (short) after a failed break
- Stop: structural stop below/above ORB level
- Target: runner exit (1.0R activation, 0.5R trail)
- Session: New York strongest, all sessions positive
- Fill model: IOC-faithful
- Results: +$9.87/trade NY, both walk-forward halves positive
- Live: active paper_sim lane
- Next: accumulate live paper evidence

---

### ORB Reclaim — MNQ
**Verdict: PROMISING BUT UNPROVEN**

- Same definition as MES
- Results inconsistent across sessions under honest fills
- NY positive but thin; London negative
- Not yet walk-forward proven under IOC-faithful fills
- Next: dedicated MNQ NY-only honest fill test

---

### 4HR Re-Trigger
**Verdict: WAIT — build detector**

- Rules: complete as of 2026-07-23 (all blockers resolved)
- Timeframe: 4-hour candles, fixed ET windows
- Setup: 4AM = 2D/2U vs prior 4PM candle; 8AM reversal + 5-min close retrace before 9:30 AM
- Entry: break of 4AM high/low, 9:30–11:00 AM window
- Stop: last completed 1H candle at entry, fixed
- Target: prior 4PM candle high/low
- Monday reference: MNQ/MES = Sunday 4PM-8PM ET; QQQ = Friday 4PM close
- Retrace confirmation: first 5-min bar CLOSE beyond 4AM level before 9:30 AM
- External study results: MNQ 84.4% target touch (n=32), QQQ 72.4% (n=29)
- Gaps: no coded detector, no replay parity proof, no honest fill P&L, walk-forward not confirmed under detector
- Next: build detector → reconcile against manual samples → honest fill replay

---

### 12HR Miyagi
**Verdict: PROMISING BUT UNPROVEN** (2026-07-26 canonical evidence study)

- Rules: complete as of 2026-07-23 (blocker resolved)
- Timeframe: 12-hour candles, 4AM/4PM ET boundaries
- Setup: 1-3-1 candle sequence (inside → outside → inside)
- Direction: confirmed at 9:30 AM only — price location at open vs Candle 3 midpoint
- Entry: trigger = midpoint of Candle 3; enter when price hits trigger from correct side
- Stop: last completed 60-min candle at entry, fixed
- Target: T1 = Candle 3 high/low (single-contract, T1-only, per hard rule);
  T2 = Candle 2 high/low (recorded, not used for exit — 2-contract scale only,
  not the current validated mode)
- External study results (provenance context only, not reproduced or targeted):
  MNQ 92.3% T1 touch (n=13), MES 75.0% (n=20)
- Detector + honest-fill replay built (`research/detector_12hr_miyagi.py`,
  `research/bars_12hr_miyagi_loader.py`, `research/replay_12hr_miyagi_honest_fill.py`).
  Canonical study 2024-07-02..2026-06-26: MNQ 15 candidates / 8 resolved fills /
  7W-1L / net $516.33 / PF 2.81; MES 19 candidates / 10 resolved fills / 8W-2L /
  net $198.85 / PF 1.98. Both positive both halves (MNQ H2 is a single trade —
  not a meaningful check), both survive 1-4 tick slippage, 0 `EOD_BAR_MISSING`.
  MES SHORT direction is net slightly negative on its own (-$5.56, PF 0.97) —
  MES's aggregate result is carried entirely by LONG. Both instruments were net
  negative in 2024 and net positive only in 2025-2026. Detector reconciled via
  16 synthetic branch-coverage fixtures + 5 hand-verified real dates (21/21
  passed) — no dated manual-sample ground truth exists for this strategy, so
  synthetic coverage carries more of the correctness burden than the 3-2-2
  precedent's own gate could rely on. Step-5 pre-market granularity-ambiguity
  count: 0/0 (MNQ/MES) — see
  `docs/strategy-rules/12HR_MIYAGI_CANONICAL_EVIDENCE_2026-07-26.md` §1 for the
  underlying data-coverage correction (the original brief's "5m cache is
  RTH-only" premise was wrong for all but the first day of coverage).
- Gaps: samples (8/10 resolved fills) are thinner than the already-thin 3-2-2
  precedent's 20; MNQ LONG and MES's whole positive result rest on very small
  same-direction slices; over half of all detected candidates never fill at all
  (`TRIGGER_NOT_HIT`).
- Next: none authorized under the standing evidence-phase directive
  (no new strategies/gates/runtime changes until collector evidence suffices,
  deadline 2026-09-30). Remains disabled/unbuilt in runtime.

---

### 60M 3-2-2 First Live
**Verdict: PROMISING BUT UNPROVEN** (PR #340, 2026-07-26)

- Rules: complete as of 2026-07-23 (all blockers resolved)
- Timeframe: 60-minute candles
- Setup: 8AM = outside bar vs 7AM candle; 9AM = directional; 10AM = opposite direction
- Entry: first live break of 9AM opposite boundary, 10:00–11:00 AM; gap-open counts
- Stop: opposite 9AM boundary, fixed, no cap
- Target: 8AM outside bar boundary
- Instrument: MNQ only (MES marginal, QQQ unconfirmed, IWM negative)
- Detector + honest-fill replay built (`research/detector_322_first_live.py`,
  `research/replay_322_honest_fill.py`), current `EOD_BAR_MISSING`/`DAY_ONLY_FLATTEN`
  contract applied — corrected canonical baseline: 34 candidates, 21 fills, 20 resolved
  (1 `EOD_BAR_MISSING`), 18W-2L, net $1,595.70, PF 10.36. Positive both halves/directions,
  6/8 quarters, all 3 years; survives 1-4 tick slippage (PF stays >9.9). See
  [`60M_322_EXPANDED_EVIDENCE_2026-07-26.md`](60M_322_EXPANDED_EVIDENCE_2026-07-26.md).
- Gaps: sample still thin (n=34) — top-5 winners = 54% of net P&L (concentration flag),
  LONG side 11-for-11 undefeated (small-sample-luck flag). OOS expansion blocked by data
  coverage — no 5-minute MNQ bar cache exists past 2026-06-26 in this environment.
- Next: preserve baseline, collect new 5-minute MNQ data prospectively, do not tune rules
  while waiting.

---

### VWAP Hold — MNQ NY
**Verdict: PROMISING BUT UNPROVEN**

> **IOC re-scoring note (2026-07-26, `VWAP_HOLD_IOC_CLOSE_RESCORING_2026-07-26.md`,
> amended same day — see the NY-only correction note immediately below)**:
> the operator decided the open-vs-close IOC reference-price question flagged
> below: **`close` is canonical** (matches `webhook/runner.py`,
> `execution/mnq_strat_evidence.py`, `replay/replay_engine.py`, and
> `execution/paper_broker.py`'s own docstring — `open`, what PR #307 shipped,
> is now superseded/deprecated as the evidence-of-record). The locked
> 348-arm population was independently re-verified (sha256
> `18cbbc8427b8afc462b1145347125ae45bb2b6af97f4ef9f374a10565a96d880`, exact
> match) and the `ioc_close` matrix (already present in
> `scripts/vwap_hold_evidence_package_results.json`, generated by the
> evidence-package's pre-existing `field="close"` sensitivity path) was
> independently reproduced field-for-field with no discrepancy. This
> 348-arm figure is **all-session (london+new_york+asian) — see the
> amendment note below for why it is provenance context, not the canonical
> evidence**.

> **NY-only correction note (2026-07-26, same-day amendment)**: the
> strategy is **"VWAP Hold — MNQ NY"** — `risk_rules.yaml`'s global
> `allowed_sessions: [new_york]` gate means only New York-session signals
> were ever live-eligible. The 348-arm population above is only 30.7% NY
> (107/348; `{london: 145, new_york: 107, asian: 96}`) — the blended
> headline was materially diluted by 69.3% of signals that could never have
> executed live. **The canonical evidence is the NY-only subset: n=107
> armed, ~55 filled/resolved per exit mode** (not 146, not 348 — stated
> prominently per operator instruction). Recomputing the identical 9-cell
> `ioc_close` matrix on this NY-only subset (same unmodified fill/exit
> functions, same 1/2/3-tick cost sweep): **all 9 cells still pass
> both-halves-positive at every cost tier**, and PFs are numerically
> *higher* than the blended figures (static PF 2.18-2.30, runner PF
> 3.08-3.37, partial PF 2.96-3.10 vs blended's 1.63-2.24) — but this is a
> smaller-sample effect, not a stronger edge, confirmed by winner
> concentration: **runner exit's top-5 winners account for 71.2% of net P&L
> at 2-tick** (from just 31 total winners) — materially worse than the
> blended figure (49.3%) and worse than the already-flagged 60M 3-2-2
> precedent (54%). Static and partial are also elevated (53.6% and 57.4%
> top-5 respectively) though less severely. Non-NY (london+asian, n=241)
> does **not** clear both-halves-positive at 2-tick for any exit mode —
> confirming the NY subset was being diluted, not inflated, by the blended
> figure, but also confirming the edge (such as it is) is genuinely
> NY-specific, not a broad all-session phenomenon. Full 9-cell matrix, H1/H2
> splits, and concentration tables for both populations are in the amended
> re-scoring doc. **Verdict remains PROMISING BUT UNPROVEN** — the NY-only
> picture does clear walk-forward/honest-fill/slippage at every cost tier,
> which is what keeps this above WAIT, but the ~55-fill sample and the
> concentration findings (especially under runner) are real, not cosmetic,
> reasons this does not move to VALIDATED and should not be read as a
> stronger result than the original blended headline implied.

> **Audit note (2026-07-26, `VWAP_FAMILY_SOURCE_OF_TRUTH_AUDIT_2026-07-26.md`)**:
> the isolated fill test below is no longer "pending" — it ran (PR #307,
> `4458eff`, merged 2026-07-23) and was followed by a second, more complete
> evidence-package pass in the same doc. Its own conclusion was **HOLD, not
> approve**: market+runner is the strongest cell ($10.30/armed signal, PF
> 1.52, both walk-forward halves positive, n=348), but the IOC leg's
> marketability reference price (arrival-bar open) does not match what
> every production/replay call site actually uses (close) — an
> unresolved discrepancy, not a defect in the test's population (**resolved
> by operator decision as of 2026-07-26 — see the re-scoring note above**).
> The entry definition below is also not actually unclear in code — see
> `strategy/signal_engine.py:2074-2134` (`_try_vwap_hold`), fully specified:
> entry = VWAP − 2 ticks, stop = VWAP + 28 ticks (7pt MNQ), target = 3.0R,
> gated on trend DOWN + (if Strat context present) `two_down` bar type +
> optional BOS/MSS structure confirmation. The master table's "Rules" and
> "Replay parity" cells still read as stale per this note — left unedited by
> both this audit and the 2026-07-26 re-scoring task, which were not
> authorized/scoped to touch those two dimensions; see the full audit doc for
> detail.

- Short-only by design
- NY session only in practice today — not because `_try_vwap_hold` itself
  checks session, but because `risk_rules.yaml`'s global `allowed_sessions:
  [new_york]` applies to every strategy, and the only live-eligible path
  (the `MNQ_VWAP_HOLD_PROOF_MODE` proof-lane exception,
  `context/mnq_vwap_hold_proof.py`) is itself hard-scoped to
  MNQ+vwap_hold+new_york
- Positive result (+$22.72/trade) came from study with different sample, granularity, and exit model vs negative result — not a clean comparison under the *pre-PR #307* studies; PR #307/#308's five-locked-preconditions methodology (same sha256-fingerprinted 348-arm population, both fill legs) resolves this specific concern for the comparisons it covers — see audit note above
- Isolated fill test: **done** (PR #307, `docs/vwap-hold-isolated-fill-model-comparison-2026-07-23.md`) — same 348 signals, IOC vs market entry, static and runner exits, plus a follow-on evidence-package pass adding cost-tier sweep, chronological split, and the open IOC reference-price question
- Entry definition: specified in code, not unclear — `strategy/signal_engine.py:2074-2134`
- IOC reference price: **resolved 2026-07-26 — `close` is canonical**
  (operator decision); `open` is superseded. See
  `VWAP_HOLD_IOC_CLOSE_RESCORING_2026-07-26.md` for the full re-scored
  matrix, concentration, and drawdown figures.
- **Canonical evidence population: NY-only (n=107 armed, ~55 filled/resolved
  per exit mode)**, not the blended 348-arm (all-session) population — the
  348-arm figure is kept as robustness/provenance context only. See the
  NY-only correction note above for why and the re-scoring doc for the full
  NY-only matrix.
- Winner concentration under NY-only is elevated, especially for runner
  exit (71.2% of net P&L from top-5 of 31 winners, 2-tick) — a real
  robustness concern, not disqualifying on its own but weighed explicitly
  against any future upgrade
- Exit mode (static vs runner vs partial): **still unresolved**, a separate
  open question from the IOC reference-price question above — not decided
  by the 2026-07-26 re-scoring or its same-day amendment
- Next: accumulate more NY-session sample (55 fills is thin); pick the
  canonical exit mode for `vwap_hold` (operator decision) — no further
  replay/backtest work is required for the exit-mode call, the full matrix
  already exists for both populations

---

### VWAP Rejection
**Verdict: BROKEN — unreachable predicate**

> **Audit note (2026-07-26, `VWAP_FAMILY_SOURCE_OF_TRUTH_AUDIT_2026-07-26.md`)**:
> the same-bar-contradiction predicate described below was **fixed on
> `main` by PR #321** (`face9d2`, merged 2026-07-24), which replaced it with
> a causal one-bar-lookback `state.vwap.failed_reclaim` field
> (`strategy/signal_engine.py:2158`). It is **no longer structurally
> unreachable in replay**: `scripts/validation_vwap_rejection.json` records
> 8 resolved arms (62.5% WR, PF 4.432, net $453, all MNQ) from the Corpus v1
> replay run (`docs/corpus-v1-clean-baseline-report-2026-07-25.md`,
> `main@a5434794e`). It remains **unreachable live**: Pine
> (`tradingview/risksentinel_context.pine`) has never sent the required
> `vwap_failed_reclaim` payload field (confirmed via full-history
> `git log -S` search, zero commits ever), so `AlertPayload.vwap_failed_reclaim`
> defaults `False` and the live predicate can never evaluate true. PR #321's
> own merged description states this sequencing was deliberate and flags an
> unresolved operator decision (deploy the corrected Pine script — not yet
> done, no later PR addresses it). n=8 is far too thin to support any
> upgrade even if the live gap were closed. Verdict left unchanged here —
> "BROKEN" overstates the predicate's current mechanical state (it works,
> in replay) but the strategy has no live-eligible path today for an
> unrelated (Pine-side) reason, so no upgrade is credited either; see the
> full audit doc §9 for the reasoning and §11 for the exact pending
> decision.

- Trigger condition **used to require** `state.vwap.reclaimed == True` AND
  `price_vs_vwap == "below"` on the same bar (pre-PR #321)
- These cannot occur together under that old logic: `reclaimed` is only
  `True` on a bar where price has crossed above VWAP, which makes
  `price_vs_vwap == "above"`, never `"below"` — identically in Pine, live,
  and replay (see PR #308, `docs/vwap-hold-vs-vwap-rejection-overlap-audit-2026-07-23.md`).
  **Pine's own advisory `signal_strategy` labeling logic still has this
  exact bug, unfixed** (`tradingview/risksentinel_context.pine:443`,
  `vwap_reclaimed and close < vwap_val`) — cosmetic today only because the
  backend never accepts Pine's advisory bracket when the backend's own
  strategy doesn't independently agree.
- Was confirmed structurally unfireable pre-#321: 0 arms across 622 days of
  the old replay corpus and 0 live occurrences. Post-#321, replay produces
  8 arms (see audit note above); live remains 0 because Pine was never
  updated.
- Does NOT overlap or co-fire with VWAP Hold — that risk was raised in an
  earlier pass of the audit and disproven by the completed reachability
  table; no state exists where both strategies are eligible
- Next: the pending decision is now Pine deployment sequencing (operator
  call, flagged explicitly in PR #321, still open) — not a retire-vs-redesign
  question, since the redesign PR #321 asked for already merged. No
  implementation change made here.

---

### ORB Breakout — MNQ
**Verdict: WAIT — gated on runner exit**

- +$17.40/trade with market entry + runner (n=60)
- Fails walk-forward under static exit
- Runner exit promotion is prerequisite — do not build proof lane until runner is live
- Next: runner exit promotion → then ORB breakout proof lane

---

### PDL Reclaim
**Verdict: RESEARCH ONLY — undersample**

- +$45.63/trade, PF 4.96 (n=13)
- n=13 is too thin for any directional conclusion
- Keep in observation, do not gate or build lane
- Next: accumulate sample passively through live trading

---

### PDH Reclaim
**Verdict: RETIRE**

- Negative on both MNQ and MES
- No filter or session combination rescues it
- Remove from enabled concepts

---

### ICC / ICT Concepts
**Verdict: RESEARCH ONLY**

See `ICC_ICT_Research.md` for full breakdown.
- ICC is structurally embedded in existing strategies — not a new standalone
- FVG most testable — needs parameter definition first
- 7HR Sweep undefined — no source material
- All concepts blocked on rules definition before any detector work

---

### Retired Strategies

| Strategy | Reason |
|---|---|
| IPC Short | Fat tail artifact — top 10 trades carry entire result, median trade negative |
| Structural Level Fade | Negative all RR buckets, both halves, all sessions |
| PDH Reclaim | Negative both instruments, no rescue |

---

## Pending Research

| Item | Blocking | Who |
|---|---|---|
| ~~VWAP hold IOC reference-price resolution~~ — **done 2026-07-26**, operator chose `close` as canonical; see `VWAP_HOLD_IOC_CLOSE_RESCORING_2026-07-26.md` | — | — |
| VWAP hold exit-mode resolution (static vs runner vs partial_2ct_approx — separate from the IOC question above, still open) | VWAP hold canonical baseline | Operator decision |
| VWAP hold NY-only sample expansion — canonical live-relevant sample is only ~55 filled trades (n=107 armed); runner exit's winner concentration (71.2% top-5, 2-tick) is a real robustness flag on this thin sample | VWAP hold upgrade past PROMISING BUT UNPROVEN | Claude Code (accumulate passively; no rule/detector change needed) |
| 4HR Re-Trigger honest fill replay | Strategy verdict | External researcher + Claude Code (after detector) |
| Miyagi walk-forward halves + slippage sensitivity | Strategy verdict | External researcher |
| 3-2-2 sample-size expansion (blocked pending new 5m MNQ data past 2026-06-26) | Strategy verdict | Claude Code |
| 4HR 1H stop backtest | Rules validation | External researcher |
| VWAP rejection Pine deployment sequencing (send `vwap_failed_reclaim`; fix stale `signal_strategy` branch at `.pine:443`) | VWAP rejection live eligibility | Operator decision (flagged in PR #321, still open) |
| VWAP reclaim per-strategy walk-forward split of existing Corpus v1 journals (`scripts/strategy_validation_report.py --strategy vwap_reclaim`) | VWAP reclaim verdict | Claude Code |
| Runner exit promotion | ORB breakout, VWAP hold/reclaim lanes | Claude Code |

---

## Build Queue (in order)

1. **4HR Re-Trigger detector** — rules complete, build now
2. ~~12HR Miyagi detector~~ — done, PROMISING BUT UNPROVEN (2026-07-26, see
   `12HR_MIYAGI_CANONICAL_EVIDENCE_2026-07-26.md`)
3. ~~60M 3-2-2 detector~~ — done, PR #340 (2026-07-26)
4. **Reconcile each detector against manual samples** — before any backtest
5. **Honest fill replay for all three** — after reconciliation passes (3-2-2 done, PR #340)
6. **Runner exit promotion** — unblocks ORB breakout and VWAP lanes
7. ~~VWAP hold isolated fill test~~ — done, PR #307 + evidence-package
   addendum (2026-07-23); open item is now the IOC open-vs-close reference
   price decision (operator), not a test to run
8. ~~VWAP hold entry definition~~ — done, fully specified at
   `strategy/signal_engine.py:2074-2134`; see
   `VWAP_FAMILY_SOURCE_OF_TRUTH_AUDIT_2026-07-26.md`
9. **FVG parameter definition** — after above queue clears

---

## Pipeline Gates (nothing skips these)

Every strategy must pass in order:
1. Rules complete and reproducible
2. Detector built
3. Detector reconciled against manual samples
4. Honest fill replay (IOC-faithful or realistic)
5. Walk-forward both halves positive
6. Slippage test survives 3-tick adverse
7. Adequate sample (minimum 30 per cell, prefer 100+)
8. Drawdown within acceptable limits

**Only after all 8 gates: eligible for paper proof.**
**Only after paper proof accumulates sufficient live evidence: eligible for live consideration.**

---

## What Does Not Authorize Execution

- A positive target touch rate alone
- A positive result under always-fills (market entry legacy model)
- A positive result in one walk-forward half only
- A positive result in one session only without session restriction in the rules
- Rules doc completion
- A promising manual study without a coded detector
