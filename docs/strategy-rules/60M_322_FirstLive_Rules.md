# 60M 3-2-2 FIRST LIVE STRATEGY
**Complete Trading Rules — MNQ Futures Only**
*Status: PROMISING BUT UNPROVEN — MNQ n=34. The 2026-09-18 trigger-timing
audit supersedes completed-5m IOC as the final entry-timing model: causal
pre-armed First Live produced 33 fills / 32 resolved wins (31 target, 1
day-only flatten) / 1 EOD_BAR_MISSING unresolved (2025-01-20 holiday early
close) / 1 bracket-invalid no-fill and +$2,471.64 at 3 adverse ticks
(derived from exit timestamps, pending corpus re-run), with both
chronological halves positive. Current real-account stop/R:R constraints
still block execution. See §7 and `docs/322-trigger-timing-ab-2026-09-18.md`.*

---

## 1. STRATEGY OVERVIEW

A 3-bar reversal setup on the 60-minute chart that plays out in the first three hours of the trading day. An outside bar at 8AM establishes the range. The 9AM bar moves directionally in one direction. The 10AM bar must reverse in the opposite direction — that reversal is the entry signal.

**"First Live" means entry is taken immediately when the 10AM candle first breaks the trigger level — no waiting for candle close.**

> ⛔ **MNQ ONLY.** MES is marginal (+$7.08). QQQ is unconfirmed. IWM is negative. Do not trade this setup on any other instrument.

---

## 2. CHART SETUP

### TradingView Requirements
- Timeframe: 60-Minute candles
- Extended hours: ON
- Timezone: Eastern Time (ET)

### Candle Windows (ET)

| Candle | Window | Bar Type | Role |
|---|---|---|---|
| 8AM candle | 8:00 AM – 9:00 AM ET | 3 (Outside) | Range reference — pre-market |
| 9AM candle | 9:00 AM – 10:00 AM ET | 2U or 2D | Direction — includes 9:30 AM open |
| 10AM candle | 10:00 AM – 11:00 AM ET | 2 opposite | Entry candle — First Live break |

---

## 3. SETUP CONDITIONS

### Step 1 — Confirm 8AM Outside Bar (check at 9:00 AM)
The 8AM candle must be an outside bar relative to the 7:00–8:00 AM ET candle (the immediately preceding 60-minute candle):
- High > prior candle high (7AM candle high)
- Low < prior candle low (7AM candle low)
- High > 7:00–8:00 AM candle high AND Low < 7:00–8:00 AM candle low

If the 8AM candle is NOT an outside bar → no setup. Stop.

### Step 2 — Classify 9AM Bar Direction (check at 10:00 AM)
The 9AM candle must be directional (2U or 2D). Mark its high and low.
- 2U (9AM broke above 8AM high) → expect PUTS setup
- 2D (9AM broke below 8AM low) → expect CALLS setup
- Inside bar or outside bar → no setup. Stop.

### Step 3 — Watch 10AM Bar for Opposite Direction (10:00 AM – 11:00 AM)

**PUTS (9AM was 2U):**
- 10AM candle must break BELOW the 9AM candle low
- Entry trigger = LOW of the 9AM candle

**CALLS (9AM was 2D):**
- 10AM candle must break ABOVE the 9AM candle high
- Entry trigger = HIGH of the 9AM candle

> ⛔ If the 10AM candle does not break the opposite 9AM boundary before 11:00 AM → setup void. No entry.

---

## 4. ENTRY RULES

**"First Live" entry — enter immediately when the 10AM candle first breaks the trigger level.**

- PUTS: enter when 10AM candle trades below the 9AM candle low
- CALLS: enter when 10AM candle trades above the 9AM candle high
- Entry window: 10:00 AM to 11:00 AM ET only
- No candle close required — enter on the first live break
- No 50% breach rule — enter immediately on break
- If break has not occurred by 11:00 AM → void. No entry.

Gap-open handling: if the 10AM candle opens beyond the trigger level at exactly 10:00 AM without trading through it tick by tick, the gap counts as a valid break. Enter at the 10AM candle open price.

> ⛔ **DO NOT apply the 50% breach rule.** Historical testing showed it materially damaged the MNQ candidate evidence; the strategy remains PROMISING BUT UNPROVEN.

---

## 5. STOP RULES

**Stop = opposite 9AM boundary from entry direction.**

- PUTS: stop = HIGH of the 9AM candle
- CALLS: stop = LOW of the 9AM candle

This is a fixed structural stop. It does not trail. If the 10AM candle reverses back through the opposite 9AM boundary, the 3-2-2 pattern is invalidated.

No maximum stop distance cap. The stop is always the opposite 9AM boundary regardless of how wide the 9AM candle range is.

Log the 9AM candle range (high minus low in points) as a context field on every trade. A range-based cap may be added after 20–30 live setups if range width shows correlation with outcome.

---

## 6. TARGET RULES

**Target = high or low of the 8AM outside bar.**

- PUTS: target = LOW of the 8AM outside bar
- CALLS: target = HIGH of the 8AM outside bar

Start with base hits — partial exits at sub-targets within the 8AM range — until the setup is proven in live trading. The 8AM outer boundary is the full target.

Sub-targets: use the 15-minute chart to find internal structure within the 8AM candle range for partial exits.

### Common Day-Only Exit — 4:00 PM ET

This strategy is day-only. Its canonical stop (Section 5) and target (this section) remain
unchanged and retain authority through the final bar.

- On the 15:55–16:00 ET 5-minute bar, resolve the canonical stop or target first if either
  is reached. Stop/target resolution has precedence over the day-only exit on that bar.
- If the position remains unresolved, close it with exit reason `DAY_ONLY_FLATTEN`.
- For paper and replay, the exit price is the close of that exact 15:55–16:00 ET bar.
- If that exact bar is missing, record `EOD_BAR_MISSING` as unresolved evidence. Do not
  estimate or substitute a price, and do not count a `WIN`, `LOSS`, or `BREAKEVEN`.
- Tradovate demo may still flatten through the broker when the bar is missing. Use the
  actual broker fill price and exit reason `DAY_ONLY_FLATTEN`.

---

## 7. EVIDENCE STATUS

**Classification: PROMISING BUT UNPROVEN** — not VALIDATED.

### 2026-09-18 trigger-timing correction

The July completed-5m IOC replay remains provenance, but it is no longer the
final timing model for the documented First Live rule.

Frozen 34-candidate A/B:
- accepted research detector and canonical state machine matched 34/34 on
  date/direction/trigger/stop/target;
- at the completed crossing-bar close, **13/34 (38.24%)** were more than the
  32-tick IOC tolerance adversely detached from the First Live trigger;
- causal pre-armed First Live at 3 adverse ticks: **33 fills / 32 resolved
  wins / 1 EOD_BAR_MISSING / 1 bracket-invalid no-fill, +$2,471.64**
  (derived from exit timestamps, pending corpus re-run);
- 1 tick net **+$2,503.64**; the 32 wins are 31 TARGET_HIT + 1 positive
  DAY_ONLY_FLATTEN (2025-02-12);
- H1 **+$1,128.32**, H2 **+$1,343.32**;
- 12 same-trigger-bar target resolutions; all 12 had open → trigger → target
  causally ordered; zero trigger bars touched both stop and target.

Timing classification: **TIMING EDGE SURVIVES / PROMISING BUT UNPROVEN**.

This does not authorize execution. The sample remains thin and consumed, and
the current real-account stop-width/R:R architecture remains incompatible with
the historical population. See `docs/322-trigger-timing-ab-2026-09-18.md`.

### July 2026 provenance

See [`60M_322_EXPANDED_EVIDENCE_2026-07-26.md`](60M_322_EXPANDED_EVIDENCE_2026-07-26.md)
for the original coded detector + completed-5m IOC-faithful replay under the
current day-only exit contract.

| Instrument | Candidates | Fills | Resolved | W-L | Net | PF | Status |
|---|---|---|---|---|---|---|---|
| MNQ | 34 | 21 | 20 (1 `EOD_BAR_MISSING`) | 18W-2L | $1,595.70 | 10.36 | PROMISING BUT UNPROVEN |
| MES | marginal | — | — | — | +$7.08/trade (legacy manual study) | — | DO NOT TRADE |
| QQQ | thin | — | — | — | Unconfirmed (legacy manual study) | — | DO NOT TRADE |
| IWM | negative | — | — | — | −$0.20/share (legacy manual study) | — | REJECTED |

Positive both halves and both directions, 6/8 quarters, all 3 years; survives 1-4 tick
adverse slippage (PF stays >9.9). Zero gap-opens in the sample.

> ⚠️ Sample still thin (n=34). Top-5 winners = 54% of net P&L (concentration flag). LONG
> side is 11-for-11 undefeated (small-sample-luck flag). **OOS expansion is blocked by data
> coverage** — no 5-minute MNQ bar cache exists past 2026-06-26 in this environment; do not
> substitute 15-minute data to manufacture a larger sample. Trade 1 MNQ contract throughout
> the testing phase. Preserve this baseline and collect new 5-minute data prospectively — do
> not tune these rules while waiting.
>
> The MNQ n=31/+$66.50/VALIDATED figure previously shown here predates the coded detector
> and the current day-only exit contract; it is provenance-only, not current evidence.
> MES/QQQ/IWM figures above are unchanged legacy manual-study numbers — this study was MNQ
> only, per its own scope.

---

## 8. MNQ FUTURES — EXECUTION SPEC

**Platform:** Tradovate
**Instrument:** MNQ (Micro E-mini NASDAQ-100) only
**Point value:** $2.00 per NQ point

### Position Sizing
- **Testing phase: 1 MNQ contract per trade, no exceptions**

### Correlation Warning
> ⛔ Miyagi and 3-2-2 co-fires were always directionally aligned. If both setups fire on the same day, choose one. Do not trade both — it is concentrated exposure to one thesis, not diversification.

---

## 9. DAILY DECISION TREE

**STEP 1 — Check 8AM candle at 9:00 AM ET**
- Is it an outside bar? High > 7AM high AND low < 7AM low?
- NO → No setup. Done for today.
- YES → Continue.

**STEP 2 — Check 9AM candle at 10:00 AM ET**
- Is it 2U (broke above 8AM high)? → PUTS setup possible
- Is it 2D (broke below 8AM low)? → CALLS setup possible
- Inside/outside bar → No setup. Done for today.

**STEP 3 — Mark the 9AM levels**
- Draw horizontal lines at 9AM candle HIGH and 9AM candle LOW
- Entry trigger = the boundary in the direction of the 10AM candle
- Stop = the opposite boundary

**STEP 4 — Watch 10AM candle (10:00 AM – 11:00 AM)**
- PUTS: first moment price trades below 9AM low → enter immediately
- CALLS: first moment price trades above 9AM high → enter immediately
- No break by 11:00 AM → void. No entry.

**STEP 5 — Set stop and target immediately at entry**
- Stop = opposite 9AM boundary (fixed, no trail)
- Target = 8AM outside bar high (calls) or low (puts)
- Hold until target or stop — no discretionary exits

---

## 10. SIGNA GATE

### Requirements
- Grade must be A or B (Grade C = skip)
- Weekly direction must match the trade
- Missing weekly direction = skip the trade (fail closed)
- Do not substitute daily direction for weekly

### Weekly Direction — Manual Logging Required
- Weekly direction
- Source: manual Signa observation
- Observation timestamp
- Symbol: QQQ for MNQ
- Trading week
- Operator identity

### Signa Internal Agreement
Log whether data.direction, engine.direction, and signa.action all agree or conflict. Internal conflict = defer to other validators.

---

## 11. MORNING CONTEXT CHECK — OBSERVE ONLY

Record for every setup, taken or skipped. NOT entry gates. Do not modify entry, stop, or target.

**Zone location at 10AM entry time:**
- Supply zone, demand zone, or mid-range
- Zone boundaries (price levels)
- Fresh or previously tested zone
- Whether an opposing zone blocks the path to the 8AM target
- PDH/PDL/PMH/PML confluence with the 9AM trigger level

**GEX snapshot at entry time:**
- Snapshot timestamp, GEX regime, flip level, nearest walls
- If unavailable: log "missing/unavailable"

**VWAP at entry time (QQQ RTH VWAP anchored 9:30 AM ET):**
- VWAP value, above/below, distance in points and %
- 5-minute condition at entry
- Trade direction aligned or conflicted with VWAP
- Does reaching the 8AM target require crossing VWAP?

**Outcome:**
- Taken or skipped, actual fill, costs, P&L
- If skipped: score through original stop and target anyway

> Clear-path filter (opposing zone blocks target) showed only mild improvement (62.2% → 64.9%) on proxy data. Logged field only — not a gate.

---

## 12. HARD RULES — NO EXCEPTIONS

- MNQ only — do not trade 3-2-2 on MES, QQQ, or IWM
- No entry after 11:00 AM ET
- No 50% breach filter — enter immediately on first live break
- Stop = opposite 9AM boundary, fixed — do not trail, do not move
- Do not trade both Miyagi and 3-2-2 on the same day
- Target = 8AM outside bar boundary — do not hold through target
- Testing phase: 1 MNQ contract only
- Missing Signa weekly direction = skip the trade
- Do not modify entry/stop/target based on context observations — log only
