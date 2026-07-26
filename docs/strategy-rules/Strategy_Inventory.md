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
| ORB Reclaim (MES) | ✅ | ✅ | ⚠️ trend-gate replay/live population gap, see profile | ✅ isolated ioc_limit (2026-07-26) | ❌ breaker-truncated; breaker-off diagnostic near-flat (H1 -$174.74/H2 +$3.12) | ❌ fails 1-4 tick (2026-07-26) | ✅ n=75 canonical / n=181 breaker-off diagnostic (2026-07-26) | **WAIT — see 2026-07-26 audit, downgraded from PAPER PROOF** |
| ORB Reclaim (MNQ) | ✅ | ✅ | ⚠️ trend-gate replay/live population gap, see profile | ✅ isolated ioc_limit (2026-07-26) | ❌ both halves negative even breaker-off (2026-07-26) | ❌ fails 1-4 tick (2026-07-26) | ⚠️ n=21 canonical thin / n=107 breaker-off diagnostic (2026-07-26) | **WAIT** |
| 4HR Re-Trigger | ✅ blockers resolved | ❌ | ❌ | Partial — external study | ✅ | Partial | ⚠️ n=32 MNQ | **WAIT — build detector** |
| 12HR Miyagi | ✅ blockers resolved | ✅ | Partial — standalone research module | ✅ | ✅ both halves (H2 thin) | ✅ 1-4 tick | ⚠️ n=15 MNQ / n=19 MES thin | **PROMISING BUT UNPROVEN** |
| 60M 3-2-2 First Live | ✅ blockers resolved | ✅ | Partial — standalone research module | ✅ IOC-faithful | ✅ both halves | ✅ 1-4 tick | ⚠️ n=34 MNQ thin | **PROMISING BUT UNPROVEN** |
| VWAP Hold (MNQ NY) | ❌ entry definition unclear (stale — see 2026-07-26 audit note in profile below) | Partial | ❌ (stale — see profile) | ✅ ioc_close, production-matching (2026-07-26) | ✅ both halves, all 3 exits, NY-only ioc_close (2026-07-26) | ✅ 1-3 tick, NY-only ioc_close (2026-07-26) | ⚠️ n=107 armed / **~55 filled, NY-only** (canonical — session-filtered from the 348-arm blended pop, which is provenance-context only) — thin, clears the 30-min literal bar but not comfortably | **PROMISING BUT UNPROVEN** |
| VWAP Reclaim (MNQ NY) | ✅ cleanest of the 3 VWAP predicates | Partial | ✅ isolated, confirmed no leaks (2026-07-26) | ✅ ioc_limit (2026-07-26) | ❌ H2 negative (2026-07-26) | ❌ fails 3-tick (2026-07-26) | ⚠️ n=70 combined / n=21 MNQ thin (2026-07-26) | **WAIT** |
| VWAP Rejection | ❌ | Partial | ❌ | ❌ | ❌ | ❌ | — | **BROKEN — unreachable predicate** |
| ORB Breakout (MNQ) | ✅ | ✅ | ⚠️ Pine stop offset stale, see profile | ✅ isolated ioc_limit both exits (2026-07-26) | ❌ H2 washout both exits (2026-07-26) | ❌ fails 1-4 tick both exits (2026-07-26) | ⚠️ n=25 thin, own breaker halted 2026-03-16 (2026-07-26) | **WAIT** |
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
**Verdict: WAIT — downgraded from PAPER PROOF, 2026-07-26 isolated honest-fill audit**

- Entry: price reclaims ORB high (LONG only — no SHORT branch exists in Python or Pine)
- Stop: `max(orb_low - 4t, entry - 40t)`; Target: `entry + 2.5x risk`
- **2026-07-26 AUDIT NOTE (supersedes the figures below)**: the previous "+$9.87/trade NY,
  VALIDATED, n=305" claim traces to `docs/mes-mnq-mechanical-research-2026-07-09.md`
  (MES orb_reclaim NY: n=105, exp $9.87, classified VALIDATED under both ioc_limit_static
  and ioc_limit_runner) — a study run **2026-07-09, predating PR #338 (market_condition
  parity fix), #339/#342 (replay engine cross-day carry-forward fixes), and #346 (corrected
  corpus)**. It is the basis risk_rules.yaml itself cites for promoting MES orb_reclaim to
  "the ONLY strategy with validated positive expectancy under honest fills" and disabling
  every other MES strategy.
- A fresh **isolated single-strategy replay** under the POST-correction engine/corpus
  (`scripts/orb_reclaim_isolated_root_cause_audit.py`, own fresh account, no combined-book
  contamination) finds a materially different picture:
  - **Canonical (20% breaker ON, matches production)**: n=75 resolved, 29.3% WR, PF 0.831,
    net **-$441.00**. Breaker trips 2025-12-11, before H2 — walk-forward untestable at this
    setting.
  - **Breaker-off diagnostic (NON-CANONICAL, run solely to see what the breaker censored)**:
    n=181 resolved, 34.3% WR, PF 0.973, net **-$171.62** — near flat, not the "+$9.87/trade"
    claim. By half: H1 -$174.74 (94 resolved), H2 **+$3.12** (87 resolved, essentially
    breakeven). Session: New York alone is actually positive (n=29, PF 1.06, net +$67.08 on
    the canonical run) — London is the dominant loss driver (n=43, PF 0.644, net -$483.64).
  - Fails 1-4 tick slippage sensitivity on the canonical config.
- **Parity note (material, not fixed)**: Python's `_try_orb_reclaim` has no trend-direction
  gate; Pine's own alert-generation cascade requires `trend_dir=="UP"`. Replay's candidate
  population is therefore a strict superset of what live could ever produce.
- Live: still an active `paper_sim` lane as of this writing — this audit is evidence-only,
  no runtime/deployment change made. The operator should treat the "PAPER PROOF" status as
  stale pending review of this finding.
- Full detail: `docs/orb-reclaim-isolated-root-cause-audit-2026-07-26.md`.

---

### ORB Reclaim — MNQ
**Verdict: WAIT**

- Same definition as MES (LONG only)
- **2026-07-26 isolated audit**: canonical (breaker ON) n=21 resolved, 23.8% WR, PF 0.546,
  net **-$337.33** — breaker trips 2025-09-29 (earlier than the shared-book's 2025-09-08,
  since there are no other strategies' wins to offset it).
- Breaker-off diagnostic (NON-CANONICAL): n=107 resolved, 29.9% WR, PF 0.772, net
  **-$826.86** — **both halves negative** (H1 -$694.78 n=61, H2 -$132.08 n=46). Unlike MES,
  removing the breaker censorship does NOT reveal a hidden positive picture for MNQ — it
  confirms the strategy is genuinely negative across the full period.
- London is the dominant loss driver (n=18, PF 0.70, net -$172.89); NY too thin to read
  (n=3).
- Fails 1-4 tick slippage sensitivity.
- Next: none recommended — evidence is decisive enough that further tuning passes are not
  warranted without new data or a rule-level reason (same standing as VWAP Reclaim).
- Full detail: `docs/orb-reclaim-isolated-root-cause-audit-2026-07-26.md`.

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

### VWAP Reclaim — MNQ NY (+MES diagnostic)
**Verdict: WAIT**

> **Canonical evidence note (2026-07-26,
> `vwap-reclaim-canonical-evidence-2026-07-26.md`)**: the audit doc's own
> next-step (`VWAP_FAMILY_SOURCE_OF_TRUTH_AUDIT_2026-07-26.md:594-596`) —
> "run a per-strategy walk-forward split of the existing Corpus v1
> `vwap_reclaim` journals" — was superseded by a cleaner method once PR #346
> established the corrected-posture bar for system-level evidence: the
> *existing* Corpus v1 journals are market-fill (not `ioc_limit`), so
> walk-forward-splitting them would not answer the question under the
> posture that now governs. Filtering **#346's own** corrected-posture
> combined-book run to `strategy=="vwap_reclaim"` isn't valid either — it
> yields only 10 attempts / 5 resolved because the account-level 20%
> drawdown breaker halted the *whole book* on 2025-09-08 (MNQ) /
> 2025-12-11 (MES), well before `vwap_reclaim` itself would necessarily
> have stopped trading. That is combined-book contamination, not
> `vwap_reclaim`'s own performance.
>
> This pass instead runs `vwap_reclaim` **isolated** (`enabled_concepts`
> patched to `["vwap_reclaim"]` only, own fresh account, so its own 20%
> breaker — it never actually trips — reflects only its own P&L) across
> the same corrected corpus, `entry_fill_model=ioc_limit`, canonical IOC
> tolerances (MES=16/MNQ=32 ticks), full 2025-07-24→2026-07-23 range, 1/2/3
> -tick slippage sensitivity. `risk_rules.yaml` verified byte-identical
> before/after. **MES was included for evidence purposes only** (production
> disable rationale for MES — a "40% WR" `risk_rules.yaml` comment — was
> already flagged by the audit doc as unsourced/unreproducible; this run
> neither confirms nor refutes that old comment, it is independent, newer,
> honest-fill evidence on a different corpus/timeframe). **No enablement,
> no runtime/config/Pine change of any kind.**
>
> **Result: combined (MNQ+MES) 136 attempts, 70 fills (51.5%), 70 resolved,
> 0 open, 37.1% WR, net after commission +$160.71, PF 1.074** — thin
> positive at 1-tick, but fails on three independent grounds: (1) **H2 is
> negative** (H1 +$243.10 / H2 −$82.39) — fails both-halves-positive
> walk-forward; (2) **MNQ alone is both thin and negative** (n=21, net
> −$66.32, PF 0.802) — the entire positive edge is carried by MES, which is
> not what the Master Table row tracks and is not currently live-eligible;
> (3) **fails 3-tick slippage** (PF drops 1.074 → 1.009 → 0.985 net
> negative across 1/2/3-tick). Quarter breakdown is volatile (Q2/Q3 strongly
> positive, Q1/Q4 strongly negative) — consistent with a thin, not-yet-
> robust sample rather than a stable edge.
>
> Historical n=29 (2026-07-09, MNQ NY) and n=50 (Corpus v1, 2026-07-25,
> market-fill) figures are kept as provenance/context only — neither is
> walk-forward split, both use a different fill model than this pass, and
> n=50 is additionally superseded as combined-book evidence by PR #346.
> Neither should be read as contradicting or confirming this result; they
> are not comparable studies.

- Predicate has the cleanest live/replay/Pine formula agreement of the
  three VWAP strategies (confirmed by the 2026-07-26 audit,
  independently reconfirmed here — zero isolation-leak trades of any
  other strategy appeared in this run's journals)
- LONG only (`state.vwap.reclaimed and state.vwap.holding and
  price_vs_vwap=="above"` + trend UP); entry = VWAP + 2 ticks, stop = VWAP
  − 28 ticks (7pt MNQ), target = entry + 3.0R
- **Sample now walk-forward split for the first time** (previously: two
  dated point-in-time figures, neither split) — the split is what moved
  this from "thin but unexamined" to "thin and examined, fails on 3
  independent grounds," not a change in verdict (WAIT unchanged)
- MES's production disable rationale remains unsourced/unreproducible
  (unchanged from the 2026-07-26 audit) — this pass's MES evidence
  (n=49, net +$227.03, PF 1.124, carrying the entire combined-book
  positive result) does not resolve that gap, it is simply new,
  independent information; no enablement decision follows from it
- Next: no further work authorized under the evidence-phase standing
  directive; if evidence continues to be sought, more sample (both
  instruments) and resolution of why MNQ underperforms MES here would be
  the open questions — not scoped or started by this pass

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
**Verdict: WAIT** (2026-07-26 canonical evidence — "gated on runner exit" wording retired,
see note)

> **Canonical evidence note (2026-07-26,
> `orb-breakout-canonical-evidence-2026-07-26.md`)**: the "+$17.40/trade with
> market entry + runner (n=60)" figure this row used to cite traces exactly
> to `docs/orb-breakout-entry-study-2026-07-11.md:27` — but that study used
> `entry_fill_model="market"` (not honest IOC), predates the #338/#339/#342
> replay-engine corrections, and its own provenance note
> (`scripts/ORB_BREAKOUT_ENTRY_STUDY_EVIDENCE_NOTE.md`) says its inputs are
> gitignored/unreproducible from a clean checkout. A later market-fill pass
> (`strategy-validation-pass-2026-07-24.md`) found this same cited edge is
> carried almost entirely by LONG+london (SHORT and NY separately near-
> breakeven-or-negative) — a concentration finding the old one-line summary
> never surfaced. The only post-correction figure before this pass (PR #346)
> was combined-book: the account-level 20% breaker, tripped mostly by OTHER
> strategies' losses, left only n=3 resolved and zero H2 data for this
> strategy specifically.
>
> This pass runs `orb_breakout` **isolated** (own fresh account, `MNQ` only
> — it's disabled for MES in production, "never the validated cell in the
> #236/#237/#238 evidence chain", no rule support or evidence reason to test
> it here), `entry_fill_model=ioc_limit`, canonical `orb_stop_ticks=48`
> asserted, **both static and runner exit on the identical candidate
> population**, 1/2/3/4-tick slippage sensitivity. `risk_rules.yaml` verified
> byte-identical before/after.
>
> **Result: BOTH exit modes are WAIT, decisively.** Combined MNQ, 1-tick:
> 106 attempts, only 25 filled (23.6% fill rate — IOC rarely fills this
> setup), n=25 resolved either way. **Static**: 28.0% WR, net −$343.50, PF
> 0.463. **Runner**: 32.0% WR, net −$372.75, PF 0.381 — runner is *worse*
> than static here (PF delta −0.082), not better; it does not "pass where
> static fails." Both fail every 1-4 tick slippage tier (never PF>1). H2 is
> a complete washout for both (0% WR, PF 0.0, only 5 resolved). SHORT
> direction is disastrous (WR 12.5%, PF 0.158, 100% of net P&L from a single
> winning trade). Top-5 winner concentration is 74-78% on n=25 — thin and
> concentrated. **The isolated account's OWN P&L tripped its OWN 20%
> drawdown breaker on 2026-03-16 for both exit modes** — this is
> `orb_breakout`'s own honest performance halting itself well before
> quarter-end, not a data-sparsity artifact; Q3/Q4 being near-empty is a
> real consequence of the strategy losing badly on its own, not an
> insufficient-sample technicality.
>
> **Material parity finding (reported, NOT fixed in this lane)**: Pine
> (`tradingview/risksentinel_context.pine:419,427`) hardcodes the ORB stop
> offset at a legacy 8 ticks; the Python backend
> (`strategy/signal_engine.py:1899`) reads `risk_rules.yaml`'s deliberately
> widened `orb_stop_ticks: {MNQ: 48}` instead. The backend's Pine-bracket
> -override path (`_apply_advisory_bracket`, `:1036-1112`) has no minimum
> -stop-distance floor — only structural checks — so a live orb_breakout
> alert with a complete Pine bracket would silently replace the wider,
> risk-validated stop with the stale narrower one. Confirmed this does NOT
> affect replay (replay never populates `state.raw`, verified by sampling
> the corpus) — live-path-only risk, flagged for the operator, not touched
> here. Also: `orb_stop_ticks=48` itself was tuned under a 622-day sweep
> that **assumed fills** (risk_rules.yaml's own comment: "Replay = fills
> assumed → live-shadow before trusting") — this pass is the first honest
> -fill test of that exact stop width, for both exit modes, and it fails.

- LONG only reaches marginal profitability territory (still net negative,
  PF 0.64 static/0.53 runner); SHORT is the primary drag
- Fill rate is a separate, compounding problem: only 23.6% of attempts fill
  at all under honest IOC — most of the "candidate" population never
  becomes a real trade
- Runner exit does not rescue this strategy — it is measurably worse than
  static on identical candidates, contradicting the old row's framing that
  runner promotion was the blocker
- Next: no further work authorized under the evidence-phase standing
  directive; if evidence continues to be sought, the open question is
  whether `orb_stop_ticks` itself (48 ticks, tuned under fills-assumed
  replay) is miscalibrated for honest IOC fills — not scoped or started by
  this pass

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
| ~~VWAP reclaim per-strategy walk-forward split~~ — **done 2026-07-26**, isolated honest-fill run (not the old market-fill journals — see profile above for why): WAIT confirmed on 3 independent grounds (H2 negative, MNQ n=21 thin, fails 3-tick slippage); see `vwap-reclaim-canonical-evidence-2026-07-26.md` | — | — |
| Runner exit promotion — **ORB breakout resolved 2026-07-26: runner tested directly under honest fills (isolated, both exit modes), found WORSE than static (PF 0.381 vs 0.463), not a blocker that was gating a real edge. VWAP hold's exit-mode question remains separately open.** | VWAP hold lane only now | Operator decision (VWAP hold) |

---

## Build Queue (in order)

1. **4HR Re-Trigger detector** — rules complete, build now
2. ~~12HR Miyagi detector~~ — done, PROMISING BUT UNPROVEN (2026-07-26, see
   `12HR_MIYAGI_CANONICAL_EVIDENCE_2026-07-26.md`)
3. ~~60M 3-2-2 detector~~ — done, PR #340 (2026-07-26)
4. **Reconcile each detector against manual samples** — before any backtest
5. **Honest fill replay for all three** — after reconciliation passes (3-2-2 done, PR #340)
6. ~~Runner exit promotion~~ — **ORB breakout: resolved 2026-07-26**, runner
   tested directly (isolated, honest fills, both exit modes on identical
   candidates) and found worse than static, not a gate that was hiding a
   real edge — strategy is WAIT on its own honest merits, not on runner's
   status. VWAP hold's own exit-mode question remains open separately.
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
