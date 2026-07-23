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
| 4HR Re-Trigger | ✅ blockers resolved | ✅ reconciled | Partial — research engine | ✅ IOC-faithful | ✅ both positive | ✅ 1–4 ticks | ⚠️ 94 signals / 41 fills | **PROMISING BUT UNPROVEN** |
| 12HR Miyagi | ✅ blockers resolved | ✅ reconciled | Partial — research engine | ✅ IOC-faithful | ❌ H1 negative | ✅ 1–4 ticks overall | ❌ 13 signals / 3 fills | **WAIT — inadequate filled sample** |
| 60M 3-2-2 First Live | ✅ blockers resolved | ✅ reconciled | Partial — research engine | ✅ IOC-faithful | ✅ both positive | ✅ 1–4 ticks | ⚠️ 32 signals / 20 fills | **PAPER PROOF** |
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
**Verdict: PROMISING BUT UNPROVEN**

- Rules: complete as of 2026-07-23 (all blockers resolved)
- Detector: pure detector built and reconciled 94/94
- Timeframe: 4-hour candles, fixed ET windows
- Setup: 4AM = 2D/2U vs prior 4PM candle; 8AM reversal + 5-min close retrace before 9:30 AM
- Entry: break of 4AM high/low, 9:30–11:00 AM window
- Stop: last completed 1H candle at entry, fixed
- Target: prior 4PM candle high/low
- Exit: day-only; unresolved positions exit at 3:55 PM ET and must be flat by 4:00 PM ET
- Monday reference: MNQ/MES = Sunday 4PM–8PM ET; QQQ = Friday 4PM–8PM ET 4-hour bar
- Retrace confirmation: first 5-min bar CLOSE beyond 4AM level before 9:30 AM
- Honest replay at 2 ticks each side: 41/94 fills, 23 wins / 18 losses, +$1,960.16 net, PF 2.33, max drawdown $411.18
- Walk-forward: H1 +$230.46; H2 +$1,729.70. Both directions and all 1–4 tick sensitivity cases remained positive.
- Regime split: H1 covers 2024-07-02 through 2025-06-28; MNQ rose 13.8% with a 25.0% close-to-close drawdown and 1.83% mean intraday range. H2 covers 2025-06-29 through 2026-06-26; MNQ rose 28.7% with an 11.8% drawdown and 1.50% mean intraday range.
- H1 was choppier and less persistently trending. Its quarterly filled P&L was -$411.18 / +$356.82 / +$319.78 / -$34.96, while all four H2 quarters were positive. This is evidence of regime sensitivity, not merely one unlucky cluster.
- Non-fills: 16 never triggered, 32 crossed but exceeded the IOC cap, and 5 produced a non-protective stop after fill. IOC rejections were a median 33.75 points beyond the trigger versus the 8-point cap.
- Market counterfactual: 22/32 displaced entries retained valid brackets; 10 had already passed the target. Valid market entries returned +$793.72 overall, but H2 (-$65.90) and LONG (-$295.18) were negative.
- Combined 41 IOC + 22 valid market fills: +$2,753.88, PF 1.98, H1 +$1,090.08, H2 +$1,663.80.
- Historical TRENDING gate retained 45/63 fills: +$3,067.20, PF 2.71, H1 +$1,077.98, H2 +$1,989.22. It improved trade quality but left 2024 Q3 slightly negative and did not itself drive the H1 recovery.
- Evidence boundary: ordinary 4HR performance remains the 41-fill IOC result. The 22-fill market rescue and 45-fill combined TRENDING result are research-only counterfactuals and do not change execution policy.
- Research tranche closed: no more performance slicing or historical filter optimization
- **Execution blocker:** the existing Phase-1 runtime implementation has not been reconciled to the resolved detector/rules. Do not activate or treat runtime output as canonical.
- Next: narrow Claude delta audit, then later reconcile the actual Phase-1 runtime implementation to the resolved specification

---

### 12HR Miyagi
**Verdict: WAIT — inadequate filled sample**

- Rules: complete as of 2026-07-23 (blocker resolved)
- Detector: pure detector built and reconciled 13/13 under the resolved executable rule
- Timeframe: 12-hour candles, 4AM/4PM ET boundaries
- Setup: 1-3-1 candle sequence (inside → outside → inside)
- Direction: confirmed at 9:30 AM only — open above Candle 3 high = SHORT; below Candle 3 low = LONG
- Entry: trigger = midpoint of Candle 3; enter when price hits trigger from correct side
- Stop: last completed 60-min candle at entry, fixed
- Target: T1 = Candle 3 high/low; T2 = Candle 2 high/low (2-contract scale only)
- Exit: day-only; unresolved positions and runners exit at 3:55 PM ET and must be flat by 4:00 PM ET
- Resolved MNQ scan: 13 valid setups, of which 6 later touched the midpoint and became entries (46.2%)
- Honest replay at 2 ticks each side: 6 midpoint touches produced only 3 IOC fills; 2 wins / 1 loss, +$59.28 net, PF 1.30
- Walk-forward: H1 -$115.48; H2 +$174.76. LONG produced zero fills; all three fills were SHORT.
- The result remained positive at 1–4 ticks overall, but this is not meaningful evidence with only three fills.
- Prior performance result (MNQ 12/13 T1) used the superseded direction rule and is not transferable
- Gaps: filled sample is extremely thin, H1 is negative, and LONG has no filled observations
- Next: collect substantially more resolved-rule setups; do not promote from this sample

---

### 60M 3-2-2 First Live
**Verdict: PAPER PROOF**

- Rules: complete as of 2026-07-23 (all blockers resolved)
- Detector: pure detector built and reconciled 32/32 under the executable rule
- Timeframe: 60-minute candles
- Setup: 8AM = outside bar vs 7AM candle; 9AM = directional; 10AM = opposite direction
- Entry: first live break of 9AM opposite boundary, 10:00–11:00 AM; gap-open counts
- Stop: opposite 9AM boundary, fixed, no cap
- Target: 8AM outside bar boundary
- Exit: day-only; unresolved positions exit at 3:55 PM ET and must be flat by 4:00 PM ET
- Instrument: MNQ only (MES marginal, QQQ unconfirmed, IWM negative)
- Reconciliation: all old 31 recovered; 2024-08-30 added after removing the undocumented pre-entry `invalidated_first` assumption
- Historical gap-open entries: zero; both directions are covered by detector tests
- Prior +$66.50 expectancy used the incomplete 31-entry set and is not the final executable-rule estimate
- Honest replay at 2 ticks each side: 20/32 fills, 17 wins / 3 losses, +$1,537.70 net, PF 8.00, max drawdown $167.24
- Walk-forward: H1 +$1,086.88; H2 +$450.82. LONG +$1,108.36; SHORT +$429.34.
- Net remained positive at 1–4 ticks adverse slippage. One target was already marketable at IOC arrival and one unresolved trade used the documented 15:55 EOD replay assumption.
- Fat-tail check: after removing the three largest net winners, the remaining 17 fills retained +$965.92 net, $56.82 expectancy per fill, and PF 5.40. The removed trades represented 37.2% of base-case profit.
- Gaps: only 20 fills, no observed historical gap-open case, and no live/replay parity proof
- Paper-proof promotion is a research verdict only; no runtime lane, configuration, or deployment change is authorized here
- Next: accumulate forward paper observations with explicit monitoring of the unobserved gap-open path

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
| 4HR replay parity / H2 concentration review | Promotion decision | Research review |
| Miyagi additional resolved-rule sample | Strategy verdict | Research collection |
| 3-2-2 replay parity / gap-open observation | Paper-proof evidence | Research review + paper observation |
| VWAP hold / rejection overlap resolution | Both strategy verdicts | Claude Code |
| VWAP hold entry definition from signal_engine.py | VWAP rules doc | Claude Code |
| Runner exit promotion | ORB breakout, VWAP hold/reclaim lanes | Claude Code |

---

## Build Queue (in order)

1. **4HR Re-Trigger detector** — complete and reconciled
2. **12HR Miyagi detector** — complete and reconciled
3. **60M 3-2-2 detector** — complete and reconciled
4. **Honest fill replay for 4HR, Miyagi, and 3-2-2** — complete
5. **Walk-forward and slippage sensitivity** — complete on resolved-rule signals
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
