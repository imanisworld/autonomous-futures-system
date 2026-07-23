# 60M 3-2-2 FIRST LIVE STRATEGY
**Complete Trading Rules — MNQ Futures Only**
*Validated: MNQ 31 strict entries | +$66.50 expectancy | Opposite 9AM boundary stop*

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
The 8AM candle must be an outside bar (3):
- High > prior candle high (7AM candle high)
- Low < prior candle low (7AM candle low)

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

> ⛔ **DO NOT apply the 50% breach rule.** Testing showed it materially damaged the validated MNQ edge.

---

## 5. STOP RULES

**Stop = opposite 9AM boundary from entry direction.**

- PUTS: stop = HIGH of the 9AM candle
- CALLS: stop = LOW of the 9AM candle

This is a fixed structural stop. It does not trail. If the 10AM candle reverses back through the opposite 9AM boundary, the 3-2-2 pattern is invalidated.

---

## 6. TARGET RULES

**Target = high or low of the 8AM outside bar.**

- PUTS: target = LOW of the 8AM outside bar
- CALLS: target = HIGH of the 8AM outside bar

Start with base hits — partial exits at sub-targets within the 8AM range — until the setup is proven in live trading. The 8AM outer boundary is the full target.

Sub-targets: use the 15-minute chart to find internal structure within the 8AM candle range for partial exits.

---

## 7. VALIDATED PERFORMANCE

| Instrument | n | Expectancy | Stop Rule | Status |
|---|---|---|---|---|
| MNQ | 31 | +$66.50/trade | Opp. 9AM boundary | VALIDATED |
| MES | marginal | +$7.08/trade | — | DO NOT TRADE |
| QQQ | thin | Unconfirmed | — | DO NOT TRADE |
| IWM | negative | −$0.20/share | — | REJECTED |

> ⚠️ n=31 is encouraging but not large. Trade 1 MNQ contract throughout the testing phase.

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
