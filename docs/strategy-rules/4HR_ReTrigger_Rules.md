# 4HR RE-TRIGGER STRATEGY
**Complete Trading Rules — QQQ Options & MNQ Futures**
*Validated: 479 sessions Jul 2024–Jun 2026 | MNQ 84.4% target touch | MES 78.6% target touch*

---

## 1. STRATEGY OVERVIEW

A reversal setup on 4-hour candles. The 4AM candle must move directionally against the prior close. The 8AM candle must reverse and then retrace back below (calls) or above (puts) the trigger level before market open at 9:30 AM. Entry occurs at market open or when price reclaims/breaks the trigger level.

**This is a REVERSAL setup, NOT a continuation.**

---

## 2. CHART SETUP

### TradingView Requirements
- Timeframe: 4-Hour candles
- Extended hours: ON
- Timezone: Eastern Time (ET)
- Adjust for dividends: OFF
- Data package: US Stock Markets bundle required ($9.95/mo) for correct pre-market bars

### Candle Windows (ET)
- **4AM candle:** 4:00 AM – 8:00 AM ET (the setup candle)
- **8AM candle:** 8:00 AM – 12:00 PM ET (the trigger candle)
- **Prior 4PM candle:** previous day 4:00 PM – 8:00 PM ET (the target reference)

> ⚠️ **WARNING:** Alpaca/IBKR native 4H bars shift by 1 hour in winter (EST). Use 5-minute bars aggregated into fixed ET windows for year-round accuracy.

---

## 3. SETUP CONDITIONS

### CALLS Setup (Bullish Reversal)

**Step 1 — 4AM candle must be 2DOWN:**
- High ≤ prior 4PM candle high
- Low < prior 4PM candle low

**Step 2 — 8AM candle must be 2UP AND retrace:**
- 8AM candle high must break ABOVE the 4AM candle high (triggers the 2UP)
- The first 5-minute bar that CLOSES below the 4AM candle high before 9:30 AM ET confirms the retrace. Intrabar touches do not count.

**Entry trigger level = HIGH of the 4AM candle**
**Target = HIGH of the prior day's 4PM candle**

### PUTS Setup (Bearish Reversal)

**Step 1 — 4AM candle must be 2UP:**
- Low ≥ prior 4PM candle low
- High > prior 4PM candle high

**Step 2 — 8AM candle must be 2DOWN AND retrace:**
- 8AM candle low must break BELOW the 4AM candle low (triggers the 2DOWN)
- The first 5-minute bar that CLOSES above the 4AM candle low before 9:30 AM ET confirms the retrace. Intrabar touches do not count.

**Entry trigger level = LOW of the 4AM candle**
**Target = LOW of the prior day's 4PM candle**

---

## 4. ENTRY RULES

- Enter when price breaks through the trigger level during regular market hours
- Trigger is immediate on the break — no 50% rule, no candle close required
- **Entry window: 9:30 AM to 11:00 AM ET only**
- If trigger has not fired by 11:00 AM, the setup is VOID — do not enter
- Price often sits near the trigger level at 9:30 AM — be ready immediately at open

---

## 5. STOP RULES

- Stop = low of the last completed 1-hour candle at entry time (CALLS) or high (PUTS)
- "Last completed" means the most recently CLOSED 1H candle at the moment of entry. The currently open candle does not count.
- The stop is FIXED at entry. It does not trail as new candles complete.
- Examples: entry at 9:35 or 9:55 AM → stop = 8:00–9:00 AM candle. Entry at 10:05 or 10:35 AM → stop = 9:00–10:00 AM candle.

---

## 6. TARGET RULES

- CALLS: Target = High of the prior day's 4PM ET candle (4PM–8PM session)
- PUTS: Target = Low of the prior day's 4PM ET candle (4PM–8PM session)
- MNQ/MES Monday: prior candle = Sunday 4:00 PM–8:00 PM ET futures session
- QQQ Monday: prior candle = Friday 4:00 PM ET close (no Sunday session exists)
- The same reference candle defines both the 4AM classification and the target level.

**Exit the full position when target is reached. Do not hold through target.**

---

## 7. QQQ OPTIONS — EXECUTION SPEC

**Platform:** Robinhood Agentic (account ••••9653)
**Instrument:** QQQ calls (bullish setup) or QQQ puts (bearish setup)

### Contract Selection
- Max premium: $2.50 per contract
- DTE: 0DTE or 1DTE preferred (target resolves same day in most cases)
- Strike: ATM or first OTM strike under the $2.50 premium cap
- Skip if bid-ask spread exceeds $0.15
- If no contract exists under $2.50 at a liquid strike, skip the setup

### Position Sizing

| Phase | Contracts |
|---|---|
| Testing phase | 1 contract always |

### Live Sizing (Post-Validation)

| Account Balance | Contracts | Max Risk at $2.50 |
|---|---|---|
| Under $500 | 1 | $250 |
| $500–$999 | 2 | $500 |
| $1,000–$4,999 | 3 | $750 |

---

## 8. MNQ FUTURES — EXECUTION SPEC

**Platform:** Tradovate (VP bot — separate module)
**Instrument:** MNQ (Micro E-mini NASDAQ-100)
**Point value:** $2.00 per NQ point

### Target Reference on Futures
- Prior 4PM candle = the 4:00 PM–8:00 PM ET session on the continuous front-month contract
- For Monday: Sunday 4:00 PM–8:00 PM ET session
- Target expressed in NQ points, not dollars

### Position Sizing
- **Testing phase: 1 MNQ contract per trade**

### Correlation Warning
> ⚠️ MNQ and MES fired in the same direction on 16 of 16 co-firing dates in the study. Do NOT trade both simultaneously. One instrument per session.

---

## 9. DAILY DECISION TREE

Run this check every morning before 9:30 AM ET.

**STEP 1 — Check the 4AM candle (after 8:00 AM ET)**
- Is the 4AM candle 2DOWN vs prior 4PM? → CALLS setup possible
- Is the 4AM candle 2UP vs prior 4PM? → PUTS setup possible
- Neither / Inside / Outside? → NO SETUP. Stop here.

**STEP 2 — Check the 8AM candle**
- Did the 8AM candle break above the 4AM high (calls) or below the 4AM low (puts)?
- NO → NO SETUP. Stop here.
- YES → Continue to Step 3.

**STEP 3 — Check retrace at 9:30 AM ET**
- CALLS: Is price still BELOW the 4AM candle high at 9:30 AM?
- PUTS: Is price still ABOVE the 4AM candle low at 9:30 AM?
- NO → Setup invalidated. Do not enter.
- YES → Setup is valid. Watch for trigger.

**STEP 4 — Wait for entry trigger (9:30 AM – 11:00 AM only)**
- CALLS: Enter when price breaks ABOVE the 4AM candle high
- PUTS: Enter when price breaks BELOW the 4AM candle low
- Trigger not fired by 11:00 AM → Void setup. No entry.

**STEP 5 — Manage the trade**
- Drop to 1-hour chart immediately after entry
- Monitor for 1H candle flip (stop condition)
- Hold until target hit OR 1H flip fires — whichever comes first

---

## 10. SIGNA GATE

### Requirements
- Grade must be A or B (Grade C = skip the trade)
- Weekly direction must match the trade direction
- Missing weekly direction = skip the trade (fail closed)
- Do not substitute daily direction for weekly

### Weekly Direction — Manual Logging Required
Before any setup is tradeable, record from the Signa interface:
- Weekly direction
- Source: manual Signa observation
- Observation timestamp
- Symbol: QQQ for MNQ/QQQ, ES for MES
- Trading week
- Operator identity

### Signa Internal Agreement
Log whether data.direction, engine.direction, and signa.action all agree or conflict. Record all three raw values. Internal conflict is informative — defer to other validators when Signa conflicts internally.

---

## 11. MORNING CONTEXT CHECK — OBSERVE ONLY

Record the following for every setup, taken or skipped. These are NOT entry gates. Do not modify entry, stop, or target based on these fields.

**Zone location at signal time:**
- Supply zone, demand zone, or mid-range
- Zone boundaries (price levels)
- Fresh or previously tested zone
- Whether an opposing zone blocks the path to the target
- PDH/PDL/PMH/PML confluence

**GEX snapshot at signal time:**
- Snapshot timestamp
- GEX regime (positive/negative)
- GEX flip level
- Nearest wall above and below
- If unavailable: log "missing/unavailable" — never assume neutral

**VWAP at entry time (QQQ RTH VWAP anchored 9:30 AM ET):**
- VWAP value at entry timestamp
- Price above or below VWAP
- Distance from VWAP in points and %
- 5-minute condition: reclaim, rejection, holding above, holding below
- Trade direction aligned or conflicted with VWAP
- Does reaching the target require crossing VWAP?

**Outcome:**
- Taken or skipped decision
- Actual fill, costs, and realized P&L
- If skipped: score through original stop and target anyway

> Pre-registered hypotheses: H1 (mid-range underperforms), H5 (target blocked by opposing zone underperforms), H6 (fresh zone outperforms tested zone). Initial review at 30–50 total setups.

---

## 12. HARD RULES — NO EXCEPTIONS

- No entries after 11:00 AM ET
- No entries if setup is not confirmed at 9:30 AM (price already through trigger)
- No trading both MNQ and MES on the same session
- No holding through target — exit at target
- No overriding the 1H flip stop — if it fires, exit immediately
- No chasing if entry is missed — wait for next valid setup
- Testing phase: 1 contract only regardless of conviction level
- Missing Signa weekly direction = skip the trade
- Do not apply the 50% breach rule to this setup

---

## 13. WHAT THE STUDY SHOWS

| Instrument | Sessions | Strict Entries | Target Touch |
|---|---|---|---|
| QQQ (options) | 93 (Mar–Jul 2026) | 7 | 6/7 = 85.7% |
| MNQ (futures) | 479 (Jul 2024–Jun 2026) | 32 | 27/32 = 84.4% |
| MES (futures) | 479 (Jul 2024–Jun 2026) | 28 | 22/28 = 78.6% |

*Target touch rate ≠ profitability. These numbers reflect price reaching the target level, not option P&L after spread, theta, and slippage.*

Setup fires on ~6–7% of sessions. Expect 1–3 setups per month. This is not a daily trading system.

## 14. WHAT IS NOT YET VALIDATED

- Fixed 1H stop P&L — study used fixed distance stops, not the fixed-at-entry 1H candle rule. Pending replay test.
- Option P&L after premium, spread, and theta
- Calls vs puts P&L split
- Walk-forward stability of 1H flip stop
