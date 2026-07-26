# 4HR Re-Trigger — Audit Handoff / Continuation Note

**CORRECTION (2026-07-26, operator-flagged): Section 3's ratcheting-stop claim below is
STALE.** This file was written 2026-07-23, before `4HR_ReTrigger_Rules.md` was finalized/
canonicalized by PR #317/#318. The current, controlling text of `4HR_ReTrigger_Rules.md`
states the opposite in three places: §5 ("The stop is FIXED at entry. It does not trail
as new candles complete."), §9 Step 5 ("Keep that stop fixed for the life of the trade;
never trail or ratchet it"), and §12 Hard Rules ("No overriding or trailing the fixed
completed-1H stop assigned at entry"). **`4HR_ReTrigger_Rules.md` is controlling.** The
documented, canonical 4HR stop is fixed-at-entry, non-trailing — there is no ratcheting
variant in the documented rule, and none should be built or tested. Section 3's
"ratcheting is the documented rule / pass-fail gate" claim and the Section 5 stop-variant
list below are retired by this note, kept in place for history only — do not act on them.
See `docs/4hr-retrigger-batch1-evidence-2026-07-26.md` for the Batch-1 evidence run this
correction applies to.

**Purpose:** Resume the 4HR Re-Trigger stop audit (Batch 1) in a fresh session or
environment without re-deriving context. Written 2026-07-23 from a read-only
reconnaissance pass. **No audit has been run. No execution code has been changed.**

**Repo state at handoff**
- Branch: `claude/strategy-gap-map-hhizgu`
- Base SHA before this work: `fd5aa0e` (== `origin/main`)
- Committed on this branch: `docs/strategy-rules/` (the three rules docs + README) and this file.

**Standing instruction:** HOLD — research only. Reproduce and measure; do not rewrite
strategy docs, do not implement rule changes, do not touch `strategy/signal_engine.py`,
config, or runtime. Read-only until the operator explicitly authorizes the run.

---

## 1. Why this audit exists (the core contradiction)

The operator's documented 4HR Re-Trigger stop is the **1H flip** (see
`4HR_ReTrigger_Rules.md` §5). The reported edge — MNQ 84.4% / MES 78.6% target
*touch*, "+$107.86"-class expectancy discussed in session — was produced with a
**fixed-distance stop**, not the 1H flip. The doc itself flags this in §14:
*"1H flip stop P&L (study used fixed distance stops — pending test)."*

Therefore the profitable number does **not** validate the documented strategy.
Batch 1 exists solely to answer: **does the 4HR Re-Trigger retain positive
expectancy under its actual documented (1H flip) stop?**

---

## 2. Reconnaissance findings (with evidence)

**F1 — The live code is NOT the documented strategy.**
`strategy/signal_engine.py:2513` `_try_strat_4hr_retrigger` is self-labeled a
*"Phase 1 approximation / proxy"* (docstring lines 2515–2533, 2575–2578). It fires on
a **NY-open ORB-high reclaim** gated by STRONG trend + VWAP — not the documented
4AM(2D)/8AM(2U)/retrace/4PM-target 4-hour-candle reversal. **Entries will not match.**
The 32-entry study was produced by a separate (local) script implementing the real
4H-candle rules, so the repo's live proxy is not the reference for reproduction.

**F2 — The documented stop is absent from the repo's live code.**
The live proxy uses a **static ORB-anchored** stop:
`signal_engine.py:2555–2564` (long) / `2608–2616` (short):
```
entry    = orb.high + 1 tick
raw_stop = orb.low  - 6 ticks
stop     = max(raw_stop, entry - MAX_ORB_STOP_TICKS*tick)   # MNQ cap 80 ticks = 20 pts
target   = entry + 2.0 * (entry - stop)
```
Constants: `signal_engine.py:191–206` (`MIN_STOP_TICKS[MNQ]=4`, `MAX_ORB_STOP_TICKS[MNQ]=80`).
No 1H-flip logic exists in code. The string "1H flip" appears in **no** branch.

**F3 — The 5-min data needed to reproduce is not present.**
Studies read `data/replay_polygon_5m/MNQ` ("621 daily files";
`scripts/mnq_5m_impulse_pullback_continuation_study.py:4-5,32`). That path is
**gitignored** (`.gitignore:25`) and **absent** (0 files, 0 tracked). Regenerate via
`scripts/polygon_backfill.py` / `scripts/polygon_to_replay.py` with `POLYGON_API_KEY`.

**F4 — Cost assumptions are inconsistent across research scripts.**
Impulse study: `COMMISSION_RT=1.48`, 2-tick slippage
(`mnq_5m_impulse_pullback_continuation_study.py:36-38`).
122 study: `COMMISSION_RT=5.0`, 1-tick slippage (`strat_122_stop_study.py:20-22`).
`execution/paper_broker.py` models **slippage only** (not commission — studies add it).
Any expectancy number is meaningless unless the exact commission + slippage is stated.

**F5 — A referenced rules doc was missing (now supplied).**
`strategy/shadow_setups.py:429` references `strat_4hr_retrigger_rules_v1.md`, which does
not exist. `docs/strategy-rules/4HR_ReTrigger_Rules.md` is now the authoritative spec.
(The code reference filename was left unchanged — reconcile only if asked.)

---

## 3. The executable 1H flip definition (Blocker B3 — CLEARED)

From `4HR_ReTrigger_Rules.md` §5:
- Stop = the 1-hour candle flip, not a fixed price.
- CALLS exit: price breaks **below** the low of the most recently **COMPLETED** 1H candle.
- PUTS exit: price breaks **above** the high of the most recently **COMPLETED** 1H candle.
- **Dynamic / RATCHETS:** "moves in your favor as new 1H candles form." Reference updates
  to each newly completed 1H candle. A live/open candle never counts.
- Trade stays valid until a 1H flip fires, even on a deep pullback before target.

~~**Critical correction to the original Batch-1 plan:** the documented 4HR stop **is** the
ratcheting 1H flip. A "literal" (fixed-at-entry) 1H flip is a diagnostic variant, NOT the
documented rule. So the PASS/FAIL gate for "does the documented strategy hold" is the
**ratcheting** result.~~ **RETIRED 2026-07-26 — this was wrong.** `4HR_ReTrigger_Rules.md`
(the controlling doc, finalized after this file was written) is explicit and repeated:
the stop is fixed at entry and never trails/ratchets (§5, §9 Step 5, §12 Hard Rules). The
fixed-at-entry stop **is** the documented rule; there is no ratcheting variant to test.
(Contrast: `12HR_Miyagi_Rules.md` §6 is also literal/non-ratcheting — the two strategies
share the same stop philosophy, they are not opposites as this file originally claimed.)

---

## 4. Two edge cases still needing an operator decision

1. **1H candle anchoring.** Top-of-hour ET (…8–9, 9–10) vs 9:30-anchored equity RTH
   (9:30–10:30)? Not pinned in the doc. The 3-2-2 and Miyagi docs both use **top-of-hour**
   ET candles, so top-of-hour is the assumed convention — **confirm before running.** On a
   9:35 entry this decides whether the first stop reference is the 8–9 or 9–10 candle.
2. **Same-bar flip-vs-target at 5-min resolution.** Already resolvable:
   `execution/paper_broker.py` books **stop-first** when one bar contains both
   (`pessimistic_both_hit=True`, lines 101–109, 514). This is the conservative rule the
   audit wants — no operator input needed. Gap-through-trigger fills handled (lines 64, 348).

---

## 5. Batch 1 audit spec (run only when authorized)

**Scope:** offline study only. Import `execution.paper_broker.PaperBroker`; build 1H levels
from 5-min via `scripts/csv_to_htf.py` methodology; **no** import of the live runner; **no**
writes to journals/config/orders. Isolated file scope: `research/` + `scripts/` +
`execution/paper_broker.py`. Zero touch to `strategy/signal_engine.py`, config, runtime.

**Reproduce first:** the existing 32 MNQ strict entries **exactly** (requires the operator's
original entry list to reconcile — see B1 below). Do not change entries, filters, targets,
setup detection, or sample dates during the stop test.

~~**Stop variants to compare (same entries, same costs):**
1. Fixed-distance stop (baseline — what produced the reported edge)
2. Literal 1H flip (fixed at entry — diagnostic)
3. **Ratcheting 1H flip (the DOCUMENTED rule — this is the pass/fail gate)**~~
**RETIRED 2026-07-26** — there are only two variants worth comparing: the fixed-distance
baseline (what produced the original reported edge, kept for context/contrast only) and
the fixed-at-entry completed-1H-candle stop (`4HR_ReTrigger_Rules.md` §5/§9/§12 — this
**is** the documented rule and the pass/fail gate). There is no ratcheting variant to
build or test.

**Resolution/fills:** 5-min bars; strictly-prior closed bars only (no lookahead);
`pessimistic_both_hit=True` (stop-first on same-bar); adverse entry slippage; state the
exact commission + slippage explicitly (do NOT silently inherit a script default — see F4).

**Report per variant:** trade count; wins/losses; win rate; gross & net P&L; expectancy/trade;
profit factor; avg win / avg loss; max drawdown; MAE/MFE; **first vs second chronological
half**; **long vs short**; sensitivity at **1-, 2-, and 3-tick** slippage.

**Verdict rule (corrected 2026-07-26):** the documented 4HR strategy is validated only if
the **fixed-at-entry completed-1H-candle stop** (the actual documented rule — see the
correction note at the top of this file) holds positive expectancy with both chronological
halves surviving. Otherwise it is "PROMISING BUT UNPROVEN" or worse, not validated. Stop
after producing evidence — no doc rewrite beyond this correction, no implementation.

---

## 6. Remaining blockers to clear before the run

| # | Blocker | What's needed |
|---|---|---|
| **B1** | Original 32-entry study script + `results.json` not in repo (local/uncommitted) | Drop the script + entry list anywhere in `scripts/`; needed to reproduce "exactly" and to tell rules-interpretation drift from data drift |
| **B2** | `data/replay_polygon_5m/MNQ` gitignored & absent | Place the 5-min MNQ data (Jul 2024–Jun 2026) at that path, or set `POLYGON_API_KEY` and backfill |
| **B3** | 1H-flip definition — **CLEARED** (§3) | — |
| **edge** | 1H anchoring (§4.1) | Operator confirm top-of-hour ET |

**Out of scope for Batch 1 (do not attempt here):** QQQ options P&L — blocked until real
historical options-chain data exists. Do not substitute target-touch %, underlying returns,
or synthetic constant-delta estimates for options validation.

---

## 7. How to resume in the new environment

1. `git fetch origin claude/strategy-gap-map-hhizgu && git checkout claude/strategy-gap-map-hhizgu`
2. Read this file + `docs/strategy-rules/4HR_ReTrigger_Rules.md`.
3. Satisfy B1 + B2 (drop the local study script/entries and the Polygon 5-min data).
4. Confirm the §4.1 anchoring question with the operator.
5. Get explicit authorization, then implement the §5 spec as a new offline study.
