# STRATEGY INVENTORY
**Autonomous Futures System — Master Reference**
*Evidence classifications reconciled: 2026-09-01; edge-decomposition audit verdicts applied: 2026-09-07*

---

## How to read this document

Every strategy is classified across eight dimensions, plus a diagnostic column from the 2026-09-07 edge-decomposition audit:

| Dimension | What it means |
|---|---|
| **Rules complete** | Written rules are objective and reproducible — two independent implementations produce the same signal |
| **Detector built** | Python function exists that reads bars and outputs signal/entry/stop/target |
| **Replay parity** | Live and replay formulas are proven identical |
| **Honest fills** | Results use IOC-faithful or realistic fill model, not always-fills |
| **Walk-forward** | Both chronological halves independently positive |
| **Slippage tested** | Edge survives at 2-tick and 3-tick adverse slippage |
| **Sample adequate** | Enough trades to draw directional conclusions (minimum 30 per cell) |
| **Primary failure stage** | Where the edge disappears in the standardized waterfall (`SIGNAL_NOT_DIRECTIONAL` → `BRACKET_DESTROYS_EDGE` → `RISK_GATES_REMOVE_EDGE` → `FILL_MODEL_REMOVES_EDGE` → `SURVIVES_*`); diagnostic, not a verdict — see `docs/edge-decomposition-audit-2026-09-07.md` |
| **Verdict** | Current classification. **Must stay the last column** — `ops/project_check/daily.py` reads the final cell of each Master Table row as the verdict |

Verdict taxonomy:
- **VALIDATED** — passes all eight dimensions
- **PAPER PROOF** — promoted to paper trading, accumulating live evidence
- **PROMISING BUT UNPROVEN** — positive replay evidence, not yet fully validated
- **WAIT** — rules incomplete or detector missing
- **RESEARCH ONLY** — concept only, no testable spec
- **BROKEN** — tested and fails honest fill or walk-forward
- **RETIRE** — negative results, no path to recovery

---

> **Runtime boundary (2026-09-01):** strategy verdicts below are evidence classifications. They do not prove the current VPS service, environment pins, enabled concepts, feeds, or broker account routing. Those remain box-side facts to verify separately.

> **Edge decomposition audit (2026-09-07, PR #483, `docs/edge-decomposition-audit-2026-09-07.md`):** every lane below marked with a *Primary failure stage* was pushed through one standardized waterfall — raw signal → next-bar time-exit control → documented bracket (plan-price vs resting fill) → structural `RiskEngine` gates → isolated `ReplayEngine` (floors off / frozen) → production IOC at 1/2/3 ticks — after reproducing the #334/#340/#366/#372 binding baselines to the cent. The lanes split into three families by *entry style*:
> 1. **Close-confirmed level predicates** (ORB Reclaim MNQ/MES, ORB Breakout, VWAP Hold): `SIGNAL_NOT_DIRECTIONAL`. Next-bar-open control ≈ $0 or negative; every historical positive was a plan-price fill artifact (plan − resting-order delta +$20.7k / +$9.8k / +$23.0k / +$102.9k). Gates admit 79–96% and the admitted set is still negative. Nothing here is being removed by the risk architecture.
> 2. **Armed-trigger day strategies** (4HR MNQ, 3-2-2, Miyagi MNQ): `RISK_GATES_REMOVE_EDGE`. Real directional signal (t ≈ 2.1–2.7), the documented bracket keeps it (PF 1.77 / 13.6 / 2.85), production IOC fills 54–63% at PF 2.0 / 12.2 / 2.4 — then `max_stop_ticks=120` + `min_rr_ratio=2.0` reject 95–100%. The rejected set *is* the edge (4HR: +$3,076 / PF 1.80 both halves). This is a risk-policy question about wide-stop, low-R:R day strategies, with #372's concentration caveat attached; it is not a detector, fill, or parity problem, and the IOC leg is not destroying edge anywhere.
> 3. **Transition failed-breakdown reclaim**: `BRACKET_DESTROYS_EDGE`. Weak positive drift (t ≤ 1.9) converted to PF 0.80 by the fixed bracket; structurally inadmissible as documented.
>
> A same-day correction (PR #484) applies: the 4HR "one-gate ablation" doc's arms were apply-only-that-gate arms; a true ablation admits 12 candidates (+$1,846 bracket) without the stop cap and 4 without the trending gate.
>
> **Policy decision for family 2 (2026-09-07, Option B+ — `docs/wide-stop-day-strategy-policy-options-2026-09-07.md`):** the global cap and R:R floor stay unchanged. 4HR MNQ, 3-2-2 and Miyagi MNQ are **parked as incompatible with the current account size** — their median stops are 8.5% / 16% / 17% of the $1,500 account per contract — and recorded as not tradeable below **$4,000 (4HR) / $6,000 (3-2-2, Miyagi)** real equity. They stay in shadow to accumulate sample, and a forward paper lane on an explicitly *hypothetical* $4k / $6k ledger (family caps 400 / 600 ticks, 1 contract, IOC-real fills, no promotion path) is queued to build the forward record. Re-open criteria are pre-registered in the memo.

## Master Table

| Strategy | Rules | Detector | Replay parity | Honest fills | Walk-forward | Slippage | Sample | Primary failure stage (2026-09-07) | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| ORB Reclaim — current/first_cross (MNQ+MES) | ✅ | ✅ | ✅ isolated own-account audit (#368) | ✅ ioc_limit | ❌ own drawdown breaker halts H2 | n/a — halted | ⚠️ n=38; MNQ −$164.44 / MES −$49.30 | `SIGNAL_NOT_DIRECTIONAL` — plan-fill artifact +$20.7k MNQ / +$9.8k MES; resting-fill bracket PF 0.78 / 0.89 on n=459 / 432 | **BROKEN — negative evidence** |
| ORB Reclaim V4-R candidate | ✅ preregistered | ✅ research detector | ✅ isolated own-account audit (#368) | ✅ ioc_limit | ❌ H2 −$451.20 vs H1 +$900.57 | not established | ⚠️ n=31 | not separately decomposed (same close-confirmed family as the row above) | **WAIT** — positive aggregate, fails frozen H2 + concentration gates |
| 4HR Re-Trigger (MNQ) | ✅ | ✅ | ✅ full-engine audit (#372) | ✅ 44/81 IOC fills, PF 2.00 (audit stage E); ❌ 1 approved / 0 filled through production gates | ✅ bracket H1 +$1,794.80 / H2 +$1,274.80; IOC H1 +$182.94 / H2 +$1,548.92 | ✅ 3-tick PF 1.94 (all candidates) | n=81 / 80 bracket-resolved / 1 production approval | `RISK_GATES_REMOVE_EDGE` — 75/81 over the 120-tick cap, 61/81 under 2.0 R:R; rejected set +$3,076 / PF 1.80 | **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS — signal and bracket real; PARKED below $4,000 equity (B+, 2026-09-07)** |
| 4HR Re-Trigger (MES) | ✅ | ✅ | ✅ full-engine audit (#372) | ❌ 50/76 IOC fills but net −$346.50, PF 0.75 | ❌ bracket H2 −$634.99 | ❌ 3-tick PF 0.65 | n=76 / 7 production attempts / 4 fills | `RISK_GATES_REMOVE_EDGE` on the mechanical rule, but the signal is weak (bracket PF 1.07, IOC negative) — no edge to recover | **BROKEN / WAIT** |
| 12HR Miyagi | ✅ | ✅ | ✅ causal-stop closure (#366) | ✅ MNQ 5/8 IOC fills PF 2.36 (audit); n/a in production — fails risk before fill | ❌ MNQ bracket H1 −$56.42 / H2 +$582.33 | ✅ MNQ 3-tick PF 2.31 | ❌ MNQ n=8, MES n=10 — far below any cell minimum | MNQ `RISK_GATES_REMOVE_EDGE` (t=2.67, bracket PF 2.85, 0/8 inside cap); MES `SIGNAL_NOT_DIRECTIONAL` (t=0.84) | **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS; MNQ PARKED below $6,000 equity (B+, 2026-09-07)** |
| 60M 3-2-2 First Live | ✅ | ✅ | ✅ full-engine closure (#367) | ✅ 20/34 IOC fills, PF 12.2 (audit, = #340); ❌ 0/34 through production gates | ✅ bracket H1 +$1,383.34 / H2 +$1,149.32; IOC H1 +$1,068.68 / H2 +$790.72 | ✅ 3-tick PF 11.9 | n=34 — thin; winner concentration already flagged | `RISK_GATES_REMOVE_EDGE` — 34/34 over the cap (median stop 486 ticks), 34/34 under 2.0 R:R; engine rejects at `TREND_STRENGTH_BELOW_REQUIRED` (26) first | **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS — signal and bracket real; PARKED below $6,000 equity (B+, 2026-09-07)** |
| ORB Breakout — inverted (MNQ evidence lane) | ✅ | ✅ | ✅ | ✅ IOC | ✅ historical sub-period/session/direction checks | ✅ through +4 ticks (#364) | ⚠️ n=111 historical study — not reproducible from current journals (63 arms reproducible, see profile) | `BRACKET_DESTROYS_EDGE` on the ungated 680-candidate population (IOC −$177 / PF 0.86); gated engine subset positive (+$271–293, PF 1.6–2.0) — the gates are the selection | **PROMISING BUT UNPROVEN** |
| MES 1-2-2 (`strat_122`) | ✅ | ✅ | ✅ executable audit (#373) | ✅ | ⚠️ executable subset thin | ✅ historical stress | 16/33 canonical candidates executable | not decomposed | **WAIT** |
| VWAP Hold (MNQ NY) | ✅ fully specified in `strategy/signal_engine.py` (`_try_vwap_hold`) | ✅ | ✅ replay-engine population reused (2026-09-07) | ❌ under the decision-bar IOC reference the replay/production use: NY-only 35/107 fills, −$326.92, PF 0.49 (2026-09-07). The 2026-07-26 ✅ was the arrival-bar close, 5 min after the order | ❌ both halves negative under the decision-bar reference (2026-09-07) | ❌ (moot — negative at 1 tick) | n=107 armed / 35 filled NY-only; 348 / 105 blended — the 55-fill figure counted 20 fills that exist only under the 5-minute look-ahead | `SIGNAL_NOT_DIRECTIONAL` on the raw predicate (n=4,579); detached-entry gate selects a weakly positive subset (t ≤ 1.9, not NY-specific); the NY cell's sign is a fill-reference artifact (`docs/vwap-hold-reconciliation-2026-09-07.md`) | **BROKEN — negative evidence** (downgraded from PROMISING BUT UNPROVEN 2026-09-07) |
| VWAP Reclaim (MNQ NY) | ✅ cleanest of the 3 VWAP predicates | Partial | ✅ isolated, confirmed no leaks (2026-07-26) | ✅ ioc_limit (2026-07-26) | ❌ H2 negative (2026-07-26) | ❌ fails 3-tick (2026-07-26) | ⚠️ n=70 combined / n=21 MNQ thin (2026-07-26) | not decomposed | **WAIT** |
| VWAP Rejection | ❌ | Partial | ❌ | ❌ | ❌ | ❌ | — | not decomposed | **BROKEN — unreachable predicate** |
| ORB Breakout (MNQ) | ✅ | ✅ | ⚠️ Pine stop offset stale, see profile | ✅ isolated ioc_limit both exits (2026-07-26); audit: 11% IOC fill on n=710, PF 0.59 | ❌ H2 washout both exits (2026-07-26); audit resting-fill bracket H1/H2 both negative | ❌ fails 1-4 tick both exits (2026-07-26); audit 3-tick PF 0.55 | ⚠️ n=25 thin (2026-07-26); audit n=710 raw / 226 bracket-resolved — no longer thin, and negative | `SIGNAL_NOT_DIRECTIONAL` — 0/4 horizons positive; plan-fill artifact +$23.0k; frozen engine halts 2026-03-16 exactly as the 2026-07-26 closure recorded | **BROKEN — negative evidence** (upgraded from WAIT 2026-09-07) |
| Transition failed-breakdown reclaim (MNQ/MES, shadow) | ✅ objective predicate in the audit script (`scripts/edge_decomposition_audit.py`); shadow detector pending commit | ⚠️ research/shadow only | n/a — never executable | ✅ resting + IOC (audit) | ❌ H1/H2 both negative on the full corpus | ❌ 3-tick PF 0.73 | ✅ n=3,292 MNQ full corpus; 299 MNQ / 405 MES re-anchored audit sets | `BRACKET_DESTROYS_EDGE` — weak drift (best t 1.86) → PF 0.80, −$10,768; 91% fail R:R, 83% WEAK/C, RANGE-conditioned so always fails `require_trending_condition` | **BROKEN — no path under the documented bracket** |
| PDL Reclaim | ✅ | ✅ | Partial | ✅ | ❌ too thin | — | ❌ n=13 | — | **RESEARCH ONLY — undersample** |
| PDH Reclaim | ✅ | ✅ | ✅ | ✅ | ❌ both halves neg | ❌ | ✅ n=67 | — | **RETIRE** |
| ICC (all variants) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | — | — | **RESEARCH ONLY** |
| ICT — FVG | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | — | — | **RESEARCH ONLY** |
| ICT — Order Block | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | — | — | **RESEARCH ONLY** |
| ICT — Liquidity Sweep | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | — | — | **RESEARCH ONLY** |
| 7HR Sweep | ❌ no source material | ❌ | ❌ | ❌ | ❌ | ❌ | — | — | **RESEARCH ONLY — undefined** |
| FOMC | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ n=16 | — | **RESEARCH ONLY — not portable** |
| Main Combos (naked) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | — | — | **RESEARCH ONLY — negative without context** |
| IPC Short | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ n=1615 | — | **RETIRE — fat tail artifact** |
| Structural Level Fade | ✅ | ✅ | ✅ | ✅ | ❌ both neg | ❌ | ✅ n=3396 | — | **RETIRE** |

---

## Detailed Strategy Profiles

---

### ORB Reclaim — current/first_cross
**Verdict: BROKEN — negative evidence**

- Binding evidence: PR #368 isolated the currently implemented `first_cross` rule on its own account under IOC-faithful execution.
- Result: n=38 resolved, net −$213.74, PF 0.858; MNQ −$164.44 and MES −$49.30.
- The strategy's own drawdown breaker stops the second half; the older MES PAPER PROOF / MNQ PROMISING figures are superseded for the executable rule.
- **Edge decomposition (2026-09-07, #483):** `SIGNAL_NOT_DIRECTIONAL`. Next-bar-open control is flat for MNQ (best t 1.19, 2/4 horizons) and negative at every horizon for MES (t −2.81 at 30 min). Filling at the plan level instead of a resting order at that level accounts for **+$20,680 (MNQ) / +$9,839 (MES)** of the historical positives; with honest resting fills the documented bracket is PF 0.78 / 0.89 on n=459 / 432 before any gate. The gates admit 90% / 79% of candidates and the admitted set is still negative; the isolated frozen engine trips its own breaker on 2025-12-11 for MES, the same date #368 recorded. There is no edge for the architecture to be removing.
- Runtime enablement is a separate deployment fact and must be read from the actual box/config; this document does not infer current runtime posture from the evidence verdict.

---

### ORB Reclaim — V4-R candidate
**Verdict: WAIT**

- Preregistered PR #368 variant: New York + prior rejected-high/low context.
- Result: n=31, PF 1.338, +$449.37 aggregate, but H2 was −$451.20 and one month carried 70.6% of net P&L.
- It failed the frozen H2 and concentration criteria. Do not iterate another variant from the same corpus without new evidence.

---

### 4HR Re-Trigger
**Verdict: MNQ BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS — signal and bracket real; MES BROKEN / WAIT**

- Binding full-engine audit: PR #372.
- MNQ: the prior 80-fill standalone population collapses to 1/81 real fills through `ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker`, including the hypothetical parity-defect ceiling pass.
- MES: ceiling improves 7 to 12 fills out of 76, PF 1.854, but H2 is −$273.75 versus H1 +$655.00.
- Legitimate preserved gates, not a parity patch, explain the MNQ collapse. No strategy/risk widening is justified by this evidence.
- **Edge decomposition (2026-09-07, #483/#484):** MNQ is `RISK_GATES_REMOVE_EDGE`, and the verdict wording changes from "executable form" to "system risk constraints" because the audit located the loss precisely. Raw signal positive at all four horizons (EOD t 2.49); documented bracket reproduces #334 to the cent (+$3,069.60 / PF 1.774, H1 +$1,794.80 / H2 +$1,274.80); production IOC over all 81 candidates fills 44 at PF 2.00 (+$1,731.86) and survives 3 ticks (PF 1.94). Structural gates then admit 4 of 81 — 75 exceed the 120-tick cap (median stop 254 ticks) and 61 fall under 2.0 R:R (median 0.94) — and the rejected 76 carry **+$3,076 / PF 1.80, positive in both halves**. The isolated engine reproduces #372's 38/23/11/8/1 dispositions exactly; the single approved trade is `ENTRY_NOT_FILLED` under production IOC. A true one-gate ablation (#484, correcting the same-day ablation doc) admits 12 candidates / +$1,846 bracket without the stop cap and only 4 without the trending gate: **the stop-width cap is the binding gate**, the trending gate is a good filter in isolation (its 38 rejects are −$824.74 / PF 0.70). #372's concentration caveat on those 12 (99.8% of profit in two months at ~2.7× the policy stop) still stands. What this lane needs is a sizing/stop-budget policy decision for wide-stop, low-R:R day strategies — not another detector, fill, or parity study.
- MES under the same waterfall: signal weak (best t 2.04, 3/4 horizons), bracket PF 1.07 with H2 −$634.99, IOC over all candidates negative (PF 0.75). Mechanically `RISK_GATES_REMOVE_EDGE`, but there is no edge behind the gates to recover; BROKEN / WAIT stands on the signal's own merits.
- **Policy decision (2026-09-07, Option B+):** MNQ parked as incompatible with the current account size — median stop 254 ticks = $127 = 8.5% of $1,500 per contract; 9 of 31 bracket losses exceed the $150 daily floor. Not tradeable below **$4,000 real equity** (5% rule on the 400-tick / $200 family cap, the cell that keeps essentially all of the bracket P&L: 63 admitted, +$3,111, PF 2.22; with R:R ≥ 1.0, 36 admitted, +$3,077, PF 3.13, both halves positive). Global cap and R:R unchanged. Stays `SHADOW_ONLY`; a hypothetical-$4k-ledger forward paper lane is queued. Re-open on any of: real equity ≥ $4,000; ≥ 40 IOC-filled forward trades with both halves positive and top-3-month concentration < 60%; shadow n ≥ 120. See `docs/wide-stop-day-strategy-policy-options-2026-09-07.md`.

---

### 12HR Miyagi
**Verdict: BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS**

- Binding causal-stop closure: PR #366.
- The earlier PF/P&L study used a stop-reference formula with a confirmed lookahead defect.
- With the causal stop corrected, MNQ 0/8 and MES 2/10 historical trigger events fit the account's existing `max_stop_ticks` risk cap.
- The cap was independently confirmed as an intentional account risk control and is not widened here. Any bounded-stop Miyagi idea would be a new strategy variant requiring new evidence.
- **Edge decomposition (2026-09-07, #483):** MNQ is `RISK_GATES_REMOVE_EDGE` — the 8 causal-stop triggers carry direction (t 2.67 at 120 min, 4/4 horizons), the documented bracket keeps it (PF 2.85, +$525.91), IOC fills 5/8 at PF 2.36, and 8/8 fail both the cap (median 564 ticks) and R:R (median 0.50). MES is `SIGNAL_NOT_DIRECTIONAL` (best t 0.84, bracket PF 1.09). Both samples are far too small for any directional conclusion; the MNQ result only says the family behaves like 4HR and 3-2-2, not that Miyagi has a proven edge. Verdict unchanged.
- **Policy decision (2026-09-07, Option B+):** MNQ parked with the family — median stop 522 ticks = $261 = 17% of $1,500 per contract; not tradeable below **$6,000 real equity**. Included in the hypothetical-ledger forward lane as a shadow member only (n=8 supports no cell). Global cap unchanged. See `docs/wide-stop-day-strategy-policy-options-2026-09-07.md`.

---

### 60M 3-2-2 First Live
**Verdict: BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS — signal and bracket real**

- Binding executable-parity closure: PR #367.
- The prior 34-candidate / 21-fill / PF 10.36 study was standalone research and did not exercise the account's real runtime controls.
- Full-engine result: 0/34 real historical candidates reach a fill; even the most favorable parity-defect ceiling still leaves the population blocked by legitimate risk architecture, principally stop width and confluence.
- Do not change those account controls to rescue this strategy.
- **Edge decomposition (2026-09-07, #483):** `RISK_GATES_REMOVE_EDGE`. Signal positive at 3/4 horizons (t 2.06 at 120 min); documented bracket +$2,532.66 / PF 13.6, both halves positive; production IOC (32-tick) reproduces #340's honest-fill population — 20 fills, PF 12.2, +$1,859.40, both halves positive, 3-tick PF 11.9. Then 34/34 exceed the 120-tick cap (median stop 486 ticks ≈ $243/contract, 16% of the account) and 34/34 fall under 2.0 R:R (median 0.27). The isolated engine rejects 26 at `TREND_STRENGTH_BELOW_REQUIRED` and 5 at `ENTRY_DETACHED_FROM_PRICE` before the cap is ever reached, so the cap is the second wall, not the first. n=34 with the previously flagged winner concentration remains the reason this cannot be read as a proven edge even if the gate policy changed.
- **Policy decision (2026-09-07, Option B+):** parked as incompatible with the current account size — median stop 472 ticks = $236 = 16% of $1,500 per contract; every loss exceeds the $150 daily floor; the R:R floor cannot be kept in any form (median 0.24, 0% ≥ 2.0). Not tradeable below **$6,000 real equity** (5% rule on the 600-tick / $300 family cap: 24 admitted, +$1,790, PF 9.9 — a 1-loss artifact until ≥ 5 losses are observed). Global cap and R:R unchanged. Stays `SHADOW_ONLY`; hypothetical-$6k-ledger forward lane queued. Re-open on: real equity ≥ $6,000, or shadow n ≥ 60 with ≥ 5 losses observed. See `docs/wide-stop-day-strategy-policy-options-2026-09-07.md`.

---

### VWAP Hold — MNQ NY
**Verdict: BROKEN — negative evidence** (downgraded from PROMISING BUT UNPROVEN, 2026-09-07)

> **Reconciliation (2026-09-07, `docs/vwap-hold-reconciliation-2026-09-07.md`,
> `scripts/vwap_hold_reconciliation_2026-09-07.py`):** the 2026-07-26 NY-only
> `ioc_close` cell was reproduced field-for-field from the committed package
> code and JSON, then re-run with only the population and the fill reference
> varied, on the same 5m corpus and the same exit engine. Three results:
> (1) the replay's `ENTRY_DETACHED_FROM_PRICE` gate — which rejected 1,598 of
> the 1,609 non-approved `vwap_hold` rows — is why the audit's ungated
> predicate filled 6–8% and the approved arms 42–51%; it selects a weakly
> positive subset (next-bar control t ≤ 1.9 on all 348) from a directionless
> one, but that subset is **not NY-specific** (non-NY approved t 1.69 vs NY
> 1.47). (2) Journal `bar_ts` is the 15m bar's open; the replay engine and
> production fill at the **decision bar's close** (`replay/replay_engine.py:755`),
> which is the arrival 5m bar's *open*. The package's canonical `ioc_close`
> fills at that arrival bar's **close — five minutes after the IOC would have
> been sent**. Under the decision-bar reference the NY-only static 2-tick cell
> is **35 fills, −$326.92, PF 0.49, both halves negative** (arrival-close:
> 55 fills, +$458.80, PF 2.18); the package's own `ioc_open` leg (−$143.92,
> PF 0.74) already showed this. Runner under the honest reference: −$3.26 /
> PF 1.00. (3) Neither the pre-2025-07 nor the audit-window period rescues it.
> The 2026-07-26 "`close` is canonical" decision was right about production
> and applied to the wrong bar. Exit-mode and sample-expansion items below are
> moot; the notes are kept as provenance.

> **Edge decomposition note (2026-09-07, #483):** the raw `_try_vwap_hold`
> predicate, run over the full corrected 15-minute corpus with no session or
> quality gate (n=4,579 candidates, all sessions), is
> `SIGNAL_NOT_DIRECTIONAL`: next-bar-open entry with no stop is positive at
> only 1 of 4 horizons (best t 1.18), and filling at the plan level instead of
> a resting order at VWAP − 2 ticks accounts for **+$102,862** of historical
> P&L — the largest fill-assumption artifact of any lane. With resting fills
> the documented bracket is PF 0.67 (−$3,942.70, n=815), both halves negative;
> structural gates admit 88% and the admitted set is PF 0.65; production IOC
> fills 6–8% and the filled set is PF 0.79. The isolated engine (122 approved,
> 28 filled, −$118.92) is the all-session gated population on the 313-day
> corpus, **not** the NY-only 348-arm `ioc_close` population the 2026-07-26
> re-scoring used, so this is not a re-verdict of that canonical cell. But the
> two results cannot both describe a tradable edge without an explanation:
> either the NY-session + trend-DOWN + Strat-context gating is doing all the
> selection work on a predicate that carries no direction on its own, or the
> ~55-fill NY-only cell is the small-sample / winner-concentration effect its
> own note already flags. Reconciling them (same corpus, same IOC reference,
> NY-only vs all-session, gated vs raw) was the blocking item for this lane —
> **done 2026-09-07, see the reconciliation note above: the NY-only cell does
> not survive the production-faithful fill reference.**

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
**Verdict: BROKEN — negative evidence** (upgraded from WAIT 2026-09-07; the
2026-07-26 note below stands as the honest-fill closure that WAIT was based on)

> **Edge decomposition note (2026-09-07, #483):** `SIGNAL_NOT_DIRECTIONAL`.
> The close-confirmed breakout predicate over the full corpus (n=710) is
> negative at all four next-bar-open horizons (best t −0.55), so the 2026-07-26
> WAIT — which left open whether `orb_stop_ticks=48` was miscalibrated for
> honest fills — is answered: no stop width can rescue a signal that has no
> direction on its own. Filling at the plan level accounts for **+$22,954** of
> historical P&L; with resting fills the bracket is PF 0.50 (−$2,427.48,
> n=226), both halves negative. Structural gates admit 96% and the admitted
> set is PF 0.49; production IOC fills 11% and the filled set is PF 0.59
> (3-tick PF 0.55). The isolated frozen engine trips its own 20% breaker on
> **2026-03-16**, the exact date the 2026-07-26 closure recorded. Per the
> verdict taxonomy ("tested and fails honest fill or walk-forward") this is
> BROKEN, not WAIT. The Pine stop-offset parity finding below remains a live
> -path risk independent of the verdict.

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
  this pass. **Answered 2026-09-07: the signal itself carries no direction
  (see note above), so stop calibration is moot.**

---

### ORB Breakout — inverted (MNQ evidence lane)
**Verdict: PROMISING BUT UNPROVEN** (unchanged 2026-09-07)

- The frozen inverse paper transform mirrors the source `orb_breakout`
  bracket (short where the source is long, stop/target mirrored), 8-tick IOC
  marketable tolerance, static exit; risk and confluence gates evaluate the
  *source* signal, only the broker order is mirrored.
- Historical study n=111, +$745.72 / PF 2.392 (#364). **That population is
  not reproducible from the current local canonical journals**: the same-day
  canonical IOC proof (`logs/retest_baseline_off`, 2026-09-07) finds 63
  approved `orb_breakout` arms, +$1,026.64 / PF 5.28, positive in both halves
  and in all three sessions — a WAIT on population mismatch, not a failure.
- **Edge decomposition (2026-09-07, #483):** on the *ungated* 680
  structurally admissible source candidates the inverted bracket is
  `BRACKET_DESTROYS_EDGE` (resting-fill PF 0.21; IOC 101 fills, −$176.98,
  PF 0.86). On the *gated* isolated-engine population (111 approved →
  34 fills floors-off, 80 → 23 frozen) the sign-flipped result is +$292.68 /
  PF 1.62 and +$270.96 / PF 1.98. The gates are doing real selection work
  here — the lane's entire evidence lives in a small gated set — which is
  exactly why n is the binding constraint and PROMISING BUT UNPROVEN is the
  right standing. Do not read the ungated negative as a refutation, or the
  gated positive as validation.

---

### Transition failed-breakdown reclaim (MNQ/MES, shadow)
**Verdict: BROKEN — no path under the documented bracket** (added 2026-09-07)

- RANGE_BOUND / CHOPPY / TRANSITION-conditioned long: a bar sweeps the recent
  range low and closes back inside, the next bar holds the reclaim; entry at
  the hold close, stop under the sweep low, fixed-R target. Evidence-only
  shadow setup; never executable.
- Prior audit (2026-07-08, May–Jul 2026 MNQ set): −$1,640 / PF 0.80 / WR 56%.
- **Edge decomposition (2026-09-07, #483):** `BRACKET_DESTROYS_EDGE`. On the
  full-corpus MNQ population (n=3,292, Polygon price basis) the long signal
  has a small positive forward drift at every horizon (best t 1.86 at
  60 min), but the fixed bracket converts it to **PF 0.80, −$10,768** with
  resting fills, both halves negative, 3-tick PF 0.73. The re-anchored audit
  sets reproduce the prior audit (MNQ −$1,306 / PF 0.82; MES −$1,327 /
  PF 0.72). Structurally inadmissible as documented: 91% fail
  `rr_below_minimum`, 83% grade WEAK/C, and it is RANGE-conditioned by
  construction so it always fails `require_trending_condition`. The signal
  (+2.7 pts mean at 60 min) is not strong enough to carry a 2R bracket even
  if the geometry were re-derived from its own volatility.
- Data note: the saved TradingView-derived candidate JSONs sit on a different
  contract-roll price basis than the Polygon corpus (MNQ 0 / +292.75, MES
  0 / +62.5); any future re-test must re-anchor per candidate via the
  recorded `sweep_low`, as the audit did.
- Next: none. Any bounded-bracket or volatility-scaled variant would be a new
  strategy requiring new evidence.

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
| ~~VWAP hold exit-mode resolution~~ — **moot 2026-09-07**: static −$326.92 / PF 0.49 and runner −$3.26 / PF 1.00 on the NY-only arms under the decision-bar fill reference; no exit mode has a positive cell to choose | — | — |
| ~~VWAP hold NY-only sample expansion~~ — **moot 2026-09-07**: the ~55-fill cell included 20 fills that exist only under the 5-minute-late arrival-close reference; the honest cell is 35 fills and negative | — | — |
| ~~4HR Re-Trigger honest fill replay~~ — **done 2026-09-07** (#483): 44/81 IOC fills, +$1,731.86 / PF 2.00, both halves positive, survives 3 ticks; edge is removed at the stop-cap / R:R gates, not at the fill | — | — |
| ~~Miyagi walk-forward halves + slippage sensitivity~~ — **done 2026-09-07** (#483): MNQ bracket H1 −$56 / H2 +$582, 3-tick PF 2.31 on n=8; MES not directional. Sample too small for a verdict either way | — | — |
| ~~Wide-stop / low-R:R day-strategy risk policy~~ — **decided 2026-09-07: Option B+** (`docs/wide-stop-day-strategy-policy-options-2026-09-07.md`). Global cap and R:R unchanged; 4HR MNQ / 3-2-2 / Miyagi MNQ parked as incompatible with the account size, not tradeable below $4,000 / $6,000 / $6,000 real equity; shadow journaling continues. Re-open criteria pre-registered: real equity ≥ threshold, or ≥ 40 IOC-filled forward 4HR trades with both halves positive and top-3-month concentration < 60%, or shadow n ≥ 120 (4HR) / ≥ 60 with ≥ 5 losses (3-2-2) | — | — |
| **Hypothetical-ledger forward paper lane (B+)** — isolated paper account explicitly labeled hypothetical $4,000 (4HR) / $6,000 (3-2-2, Miyagi shadow member), 1 contract, family caps 400 / 600 ticks, 4HR R:R ≥ 1.0, IOC-real fills, own daily floor, **no promotion path**; every result reported as "hypothetical ledger", never blended with the $1,500 book | Forward IOC-real record for the re-open criteria | Claude Code (build), operator (approve lane config) |
| ~~VWAP Hold reconciliation~~ — **done 2026-09-07** (`docs/vwap-hold-reconciliation-2026-09-07.md`): the detached-entry gate selects a weakly positive, non-NY-specific subset; the NY-only cell's positive sign came from filling at the arrival bar's close, 5 minutes after the decision-bar close that replay/production use; under the honest reference it is 35 fills, −$326.92, PF 0.49, both halves negative. Verdict → BROKEN — negative evidence | — | — |
| **Inverse ORB population reproduction** — the n=111 / PF 2.39 historical population is not reproducible from current journals (63 arms reproducible, +$1,027 / PF 5.28). Locate or regenerate the 111-arm population, or formally re-baseline the lane on the 63 | Inverse ORB promotion past PROMISING BUT UNPROVEN | Claude Code |
| 3-2-2 sample-size expansion (blocked pending new 5m MNQ data past 2026-06-26) — and moot for the executable form until the risk-policy item above is decided | Strategy verdict | Claude Code |
| 4HR 1H stop backtest | Rules validation | External researcher |
| VWAP rejection Pine deployment sequencing (send `vwap_failed_reclaim`; fix stale `signal_strategy` branch at `.pine:443`) | VWAP rejection live eligibility | Operator decision (flagged in PR #321, still open) |
| ~~VWAP reclaim per-strategy walk-forward split~~ — **done 2026-07-26**, isolated honest-fill run (not the old market-fill journals — see profile above for why): WAIT confirmed on 3 independent grounds (H2 negative, MNQ n=21 thin, fails 3-tick slippage); see `vwap-reclaim-canonical-evidence-2026-07-26.md` | — | — |
| Runner exit promotion — **ORB breakout resolved 2026-07-26: runner tested directly under honest fills (isolated, both exit modes), found WORSE than static (PF 0.381 vs 0.463), not a blocker that was gating a real edge. VWAP hold's exit-mode question remains separately open.** | VWAP hold lane only now | Operator decision (VWAP hold) |

---

## Build Queue (in order)

1. ~~4HR Re-Trigger detector~~ — done (canonical state machine, #317;
   research detector `research/detector_4hr_retrigger.py`)
2. ~~12HR Miyagi detector~~ — done, PROMISING BUT UNPROVEN (2026-07-26, see
   `12HR_MIYAGI_CANONICAL_EVIDENCE_2026-07-26.md`)
3. ~~60M 3-2-2 detector~~ — done, PR #340 (2026-07-26)
4. **Reconcile each detector against manual samples** — before any backtest
5. ~~Honest fill replay for all three~~ — done 2026-09-07 (#483) as the IOC
   stage of the edge-decomposition audit; all three fill 54–63% at PF 2.0 /
   12.2 / 2.4 and are then removed by the stop-cap / R:R gates. Next step is
   the risk-policy decision in Pending Research, not more replay
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
10. **Hypothetical-ledger forward paper lane for the wide-stop family (B+,
    2026-09-07)** — isolated account labeled hypothetical $4k / $6k, family
    caps 400 / 600 ticks, 1 contract, IOC-real, no promotion path; see
    `docs/wide-stop-day-strategy-policy-options-2026-09-07.md`. Requires a
    lane config approved by the operator before build.

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
- A positive result under plan-price fills for a close-confirmed level
  predicate — 2026-09-07 audit: this assumption alone was worth +$20.7k
  (ORB Reclaim MNQ), +$23.0k (ORB Breakout), +$102.9k (VWAP Hold) of
  historical P&L that a resting order at the same level never earns
- A positive result in one walk-forward half only
- A positive result in one session only without session restriction in the rules
- Rules doc completion
- A promising manual study without a coded detector
