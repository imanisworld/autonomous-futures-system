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
| VWAP Hold (MNQ NY) | ❌ entry definition unclear | Partial | ❌ | ❌ incompatible studies | ❌ | ❌ | ⚠️ n=106 | **WAIT — isolated fill test pending** |
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
**Verdict: WAIT — isolated fill test pending**

- Short-only by design
- NY session only
- Positive result (+$22.72/trade) came from study with different sample, granularity, and exit model vs negative result — not a clean comparison
- Isolated fill test spec written and ready: same 348 signals, NY only, IOC vs market entry, static and runner exits separately
- Entry definition unclear — needs `signal_engine.py` review
- Next: run isolated fill test → if positive under both exit models, define entry rules → build detector

---

### VWAP Rejection
**Verdict: BROKEN — unreachable predicate**

- Trigger condition requires `state.vwap.reclaimed == True` AND
  `price_vs_vwap == "below"` on the same bar
- These cannot occur together under the current logic: `reclaimed` is only
  `True` on a bar where price has crossed above VWAP, which makes
  `price_vs_vwap == "above"`, never `"below"` — identically in Pine, live,
  and replay (see PR #308, `docs/vwap-hold-vs-vwap-rejection-overlap-audit-2026-07-23.md`)
- Confirmed structurally unfireable, not merely rare: 0 arms across 622
  days of replay and 0 live occurrences, while the sibling `vwap_reclaim`
  strategy (same `reclaimed` field, consistent `"above"` requirement) has
  fired multiple times live in the same window
- Does NOT overlap or co-fire with VWAP Hold — that risk was raised in an
  earlier pass of the audit and disproven by the completed reachability
  table; no state exists where both strategies are eligible
- Next: a separate strategy decision — retire, or redesign `reclaimed` as
  a persisted multi-bar flag so the intended "attempted reclaim, then
  failed back below" pattern becomes expressible. No implementation
  change made here.

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
| VWAP hold isolated fill test (IOC vs market, static vs runner) | VWAP hold verdict | External researcher |
| 4HR Re-Trigger honest fill replay | Strategy verdict | External researcher + Claude Code (after detector) |
| Miyagi walk-forward halves + slippage sensitivity | Strategy verdict | External researcher |
| 3-2-2 sample-size expansion (blocked pending new 5m MNQ data past 2026-06-26) | Strategy verdict | Claude Code |
| 4HR 1H stop backtest | Rules validation | External researcher |
| VWAP hold / rejection overlap resolution | Both strategy verdicts | Claude Code |
| VWAP hold entry definition from signal_engine.py | VWAP rules doc | Claude Code |
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
7. **VWAP hold isolated fill test** — parallel, external researcher
8. **VWAP hold entry definition** — Claude Code reads signal_engine.py
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
