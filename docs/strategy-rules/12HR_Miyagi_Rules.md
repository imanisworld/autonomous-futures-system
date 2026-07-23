# 12HR MIYAGI STRATEGY
**Complete Trading Rules — QQQ Options & MNQ/MES Futures**
*Resolved executable-rule evidence: MNQ 13 setups, 6 midpoint-touch entries, 5/6 raw
T1 touches. Honest fills pending; prior MNQ 12/13 result is retired.*

---

## 1. STRATEGY OVERVIEW

A 4-candle reversal setup on the 12-hour chart. Three completed candles form a 1-3-1 pattern (inside → outside → inside). The fourth (live) candle's initial direction is counterintuitive — it opens in one direction and you trade the opposite. Entry is at the midpoint of the third candle (the trigger level) when price reverses back through it after market open.

**This is a REVERSAL setup. The live candle opens 2U → you take PUTS. Opens 2D → you take CALLS.**

> **Setup is not entry.** A detector signal at 9:30 AM only establishes a valid
> setup. An actual trade exists only if price subsequently touches the Candle 3
> midpoint from the required side. In the resolved MNQ sample, 6 of 13 setups
> produced midpoint-touch entries (46.2%).

---

## 2. CHART SETUP

### TradingView Requirements
- Timeframe: 12-Hour candles
- Extended hours: ON
- Timezone: Eastern Time (ET)
- Candle anchoring: 12H candles must align to 4AM/4PM ET boundaries

### 12-Hour Candle Windows (ET)
- **4PM candle:** 4:00 PM – 4:00 AM ET (Candle 1 or 3 in the sequence)
- **4AM candle:** 4:00 AM – 4:00 PM ET (Candle 2 or live in the sequence)

> ⚠️ **NOTE:** TradingView 12H bar anchoring may differ between equity (QQQ) and futures (MNQ/MES). Verify bar start times match 4AM/4PM ET before trading. A session-definition discrepancy was identified in cross-instrument testing — do not assume bars align without confirmation.

---

## 3. SETUP CONDITIONS — THE 1-3-1 PATTERN

| Candle | Bar Type | Condition | Role |
|---|---|---|---|
| Candle 1 | 1 (Inside) | High ≤ prior high AND Low ≥ prior low | Setup begins |
| Candle 2 | 3 (Outside) | High > prior high AND Low < prior low | Range expansion |
| Candle 3 | 1 (Inside) | High ≤ Candle 2 high AND Low ≥ Candle 2 low | Establishes trigger |
| Candle 4 | Live | 2U or 2D — determines trade direction | Entry signal |

**Trigger level = (High + Low of Candle 3) ÷ 2**
This is the midpoint/50% of the inside bar (Candle 3). Also expressible as the Fibonacci 50% of Candle 3's range.

---

## 4. DIRECTION LOGIC

### PUTS Setup (Bearish — counterintuitive)
- Candle 4 opens 2U: breaks ABOVE Candle 3's high
- Price must be ABOVE the trigger level at 9:30 AM ET
- Enter PUTS when price reverses and hits the trigger from above
- **Entry trigger = midpoint of Candle 3 (price crossing DOWN through it)**

### CALLS Setup (Bullish — counterintuitive)
- Candle 4 opens 2D: breaks BELOW Candle 3's low
- Price must be BELOW the trigger level at 9:30 AM ET
- Enter CALLS when price reverses and hits the trigger from below
- **Entry trigger = midpoint of Candle 3 (price crossing UP through it)**

Candle 4 direction is confirmed at 9:30 AM ET only. Check price location at the open:
- Price above Candle 3 high at 9:30 AM → 2U → PUTS
- Price below Candle 3 low at 9:30 AM → 2D → CALLS
- Price between Candle 3 high and low at 9:30 AM → no setup, do not enter

Do not classify Candle 4 direction from intrabar movement before 9:30 AM. A candle that briefly broke above Candle 3 high then pulled back below it before 9:30 AM is NOT a 2U setup.

---

## 5. PRE-ENTRY INVALIDATION

> ⛔ If Candle 3 (the inside bar) becomes an outside bar before 9:30 AM ET → setup is void. Do not enter.

This happens when pre-market price expands beyond Candle 3's high AND low before open.

> ⚠️ If price is respecting the trigger level during pre-market (holding above/below without reversing), wait for the first hour of trading to confirm before entering. The trigger may be acting as support/resistance rather than a reversal point.

---

## 6. STOP RULES

### Initial Stop — Literal (Pre-Entry Last Completed 60-Min Boundary)

At the moment of entry, identify the most recently COMPLETED 60-minute candle before your entry time.

- **PUTS:** stop = HIGH of the last completed 60-minute candle at entry
- **CALLS:** stop = LOW of the last completed 60-minute candle at entry

This stop is set IMMEDIATELY at entry and held FIXED until T1 is reached.

> ⛔ **DO NOT use the first post-entry hourly candle as the initial stop.** Testing showed MNQ averaged 25.4 minutes (max 55 min) unprotected and MES averaged 29.8 minutes (max 60 min) unprotected. This is not an acceptable live stop gap.

> ⚠️ The ratchet variant (trailing stop that updates with each new completed 60-min candle) was tested and produced lower expectancy and win rate than the literal approach. MNQ literal: $102.35 expectancy vs ratchet: $96.62. **Literal stop is the correct rule — do not use ratchet.**

---

## 7. TARGET RULES

### T1 — Primary Target (Inside Bar)
- CALLS: T1 = High of Candle 3 (the inside bar)
- PUTS: T1 = Low of Candle 3 (the inside bar)

### T2 — Final Target (Outside Bar)
- CALLS: T2 = High of Candle 2 (the outside bar)
- PUTS: T2 = Low of Candle 2 (the outside bar)

### Sub-Targets
Drop to 4HR or 1HR charts to find internal highs/lows within the Candle 2 range for partial exits.

---

## 8. EXIT RULES BY CONTRACT COUNT

### 1 Contract (Testing Phase)
- 100% exit at T1
- Do NOT hold to T2 on a single contract
- Do NOT use T2-only exit

### 2 Contracts (Post-Validation)
- Exit 50% at T1
- Move remaining stop to BREAKEVEN immediately after T1 is hit
- Hold remainder to T2
- If 60-min flip fires before T2 with runner in profit: exit at flip

> ⛔ **DO NOT use T2-only exit.** The legacy superseded-rule study found materially
> worse T2-only performance and higher drawdown than T1-only management. Those
> numerical estimates are retired as executable-rule performance; the conservative
> T1-first management rule remains until resolved-rule honest replay supplies replacement
> evidence.

### Day-only exit

- Miyagi is day-only. Never carry a position overnight.
- If neither the active stop nor target has resolved the position, exit all
  remaining contracts at 3:55 PM ET using the 15:55 five-minute bar decision
  so the account is flat no later than 4:00 PM ET.
- For a two-contract position, the day-only exit applies to any runner still
  open after the T1 partial.
- This applies to QQQ options, MNQ, and MES.

---

## 9. PERFORMANCE EVIDENCE STATUS

The earlier performance study used the superseded pre-open range/midpoint direction
definition. Its MNQ 12/13 T1 result and MES 15/20 result are retained only as historical
research provenance and must not be used as executable-rule performance.

Under the resolved 9:30-open rule:

- MNQ valid setups: 13
- MNQ midpoint-touch entries: 6 (46.2% of setups)
- Raw T1 touches after entry by 4:00 PM ET: 5/6 (83.3%)
- IOC fills at two ticks adverse slippage each side: 3/13 signals (3/6 touches)
- Honest-fill result: 2 wins / 1 loss, +$59.28 net, PF 1.30, $197.24 maximum drawdown
- Walk-forward: H1 -$115.48; H2 +$174.76
- Direction split: LONG 0 fills; SHORT 3 fills

> ⚠️ The honest replay remains far too small for an edge claim. H1 is negative and
> LONG has no filled observations. The strategy verdict remains WAIT.

Setup fires approximately 1–2 times per month, and fewer than half of the resolved-rule
setups produced entries in the cached MNQ study.

---

## 10. QQQ OPTIONS — EXECUTION SPEC

**Platform:** Robinhood Agentic (account ••••9653)
**Instrument:** QQQ calls (bullish) or QQQ puts (bearish)

### Contract Selection
- Max premium: $2.50 per contract
- DTE: 0DTE or 1DTE (T1 typically resolves same day)
- Strike: ATM or first OTM strike under $2.50 cap
- Skip if spread exceeds $0.15
- Skip if no liquid strike exists under $2.50

### Position Sizing
- **Testing phase: 1 contract per trade**

---

## 11. MNQ / MES FUTURES — EXECUTION SPEC

**Platform:** Tradovate (VP bot — separate module)
**Instruments:** MNQ (preferred) or MES

> ⛔ **DO NOT trade MNQ and MES simultaneously on the same Miyagi signal.** Co-fires were always directionally aligned — concentrated index exposure, not diversification. One instrument per signal.

### Position Sizing
- **Testing phase: 1 MNQ contract per trade**

---

## 12. DAILY DECISION TREE

**STEP 1 — Confirm the 1-3-1 pattern exists on the 12H chart**
- Candle 1 = inside, Candle 2 = outside, Candle 3 = inside
- NO → No setup. Stop.

**STEP 2 — Calculate the trigger level**
- Trigger = (Candle 3 High + Candle 3 Low) ÷ 2

**STEP 3 — Check Candle 4 direction at 4AM ET**
- Candle 4 opens 2U (above trigger) → PUTS possible
- Candle 4 opens 2D (below trigger) → CALLS possible
- Neither / inside / outside → No setup. Stop.

**STEP 4 — Check Candle 3 integrity at 9:30 AM ET**
- Has Candle 3 become an outside bar? → Setup void. Stop.
- Is price above trigger (puts) or below trigger (calls) at open? → Continue.
- Is price already through the trigger at 9:30? → Do not enter. Stop.

**STEP 5 — Wait for trigger to be hit during market hours**
- PUTS: wait for price to come back DOWN and hit the trigger
- CALLS: wait for price to come back UP and hit the trigger
- Enter on the hit — no 50% breach rule applies to Miyagi

**STEP 6 — Set stop immediately at entry**
- Identify last completed 60-min candle before entry
- PUTS: stop = that candle's HIGH
- CALLS: stop = that candle's LOW
- Hold fixed until T1

**STEP 7 — Manage to T1**
- 1 contract: exit 100% at T1
- 2 contracts: exit 50% at T1, move remaining stop to breakeven, hold to T2
- If unresolved, exit every remaining contract at 3:55 PM ET and be flat by
  4:00 PM ET

---

## 13. SIGNA GATE

### Requirements
- Grade must be A or B (Grade C = skip)
- Weekly direction must match the trade
- Missing weekly direction = skip the trade (fail closed)
- Do not substitute daily direction for weekly

### Weekly Direction — Manual Logging Required
- Weekly direction
- Source: manual Signa observation
- Observation timestamp
- Symbol: QQQ for MNQ, ES for MES
- Trading week
- Operator identity

### Signa Internal Agreement
Log whether data.direction, engine.direction, and signa.action all agree or conflict. Internal conflict = defer to other validators.

---

## 14. MORNING CONTEXT CHECK — OBSERVE ONLY

Record for every setup, taken or skipped. NOT entry gates. Do not modify entry, stop, or target.

**Zone location at signal time:**
- Supply zone, demand zone, or mid-range
- Zone boundaries (price levels)
- Fresh or previously tested zone
- Whether an opposing zone blocks the path to T1
- PDH/PDL/PMH/PML confluence with trigger or target

**GEX snapshot at signal time:**
- Snapshot timestamp, GEX regime, flip level, nearest walls
- If unavailable: log "missing/unavailable"

**VWAP at entry time (QQQ RTH VWAP anchored 9:30 AM ET):**
- VWAP value, above/below, distance in points and %
- 5-minute condition at entry
- Trade direction aligned or conflicted with VWAP
- Does reaching T1 require crossing VWAP?

**Outcome:**
- Taken or skipped, actual fill, costs, P&L
- If skipped: score through original stop and T1 anyway

> Leading hypothesis: target obstruction (opposing zone blocking T1 path). Miyagi showed strongest separation: 25/28 clear vs 3/6 blocked. Observe-only until 30–50 live setups confirm on real contemporaneous zone labels.

---

## 15. HARD RULES — NO EXCEPTIONS

- Do NOT use T2-only as exit
- Do NOT set initial stop on the first post-entry hourly candle
- Do NOT use ratchet stop — literal stop only
- Do NOT trade MNQ and MES simultaneously on the same signal
- Do NOT apply the 50% breach rule to Miyagi
- Do NOT enter if Candle 3 has become an outside bar
- Do NOT enter if price is already through the trigger at 9:30 AM
- Do NOT modify entry/stop/target based on context observations — log only
- Missing Signa weekly direction = skip the trade
- Testing phase: 1 contract only, 100% exit at T1
- No overnight holds — unresolved positions exit at 3:55 PM ET and must be
  flat by 4:00 PM ET
