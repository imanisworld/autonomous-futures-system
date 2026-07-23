# ICC / ICT STRATEGY RESEARCH
**Status: RESEARCH ONLY — No detector, no backtest, no rules doc yet**

---

## Current Classification

| Concept | Rules complete | Detector | Backtest | Verdict |
|---|---|---|---|---|
| ICC (Indication, Correction, Continuation) | No | No | No | RESEARCH ONLY |
| ICT — Fair Value Gap (FVG) | No | No | No | RESEARCH ONLY |
| ICT — Order Block | No | No | No | RESEARCH ONLY |
| ICT — Liquidity Sweep | No | No | No | RESEARCH ONLY |
| ICT — Kill Zones | No | No | No | RESEARCH ONLY |
| 7HR Sweep | No | No | No | RESEARCH ONLY |

---

## ICC (Indication, Correction, Continuation)

### What it is
A 3-phase price structure framework built on Strat candle types:
- **Indication:** New swing extreme — a 2U or 2D candle confirming directional movement
- **Correction:** Pullback against the indication direction
- **Continuation:** Return to original direction — the entry signal

### Relationship to existing strategies
The three validated AYCE strategies are already ICC patterns on their respective timeframes:

| Strategy | Timeframe | Indication | Correction | Continuation |
|---|---|---|---|---|
| 4HR Re-Trigger | 4H | 4AM = 2D/2U | 8AM reversal candle | Break of 4AM high/low |
| 12HR Miyagi | 12H | Candle 2 = outside bar | Candle 3 = inside bar | Candle 4 breaks midpoint |
| 60M 3-2-2 | 60M | 8AM outside bar | 9AM directional bar | 10AM opposite bar |

ICC as a standalone concept does not add a new strategy — it describes the shared structure of the existing ones.

### Pine Script audit findings
A Pine Script ICC indicator was audited and found to have 7 blockers preventing implementation:
1. LTF mode (≤15m) is not true ICC — it is a plain swing breakout
2. Visual and state-machine entries disagree on ≤15m charts
3. HTF-aligned ENTRY+ is logically unreachable on LTF charts
4. Alerts can fire intrabar and disappear before bar close
5. HTF state may change during an unfinished HTF bar
6. Same-bar stop and TP2 handling is ambiguous
7. It is an indicator, not a backtestable strategy

**Do not implement the Pine script as-is.**

### Next step
Define the smallest clean ICC detector: one fixed timeframe above 15m, confirmed pivots only, bar-close continuation confirmation, one continuation definition at a time. No TP/SL until signal detector matches TradingView bar-for-bar.

---

## ICT — Fair Value Gap (FVG)

### What it is
A 3-bar price imbalance where price moved too fast to fill orders, leaving a gap in the price ladder.

### Mechanical definition
- **Bullish FVG:** Bar N high < Bar N+2 low (gap upward — unfilled space between bar N top and bar N+2 bottom)
- **Bearish FVG:** Bar N low > Bar N+2 high (gap downward — unfilled space between bar N bottom and bar N+2 top)

### Why it matters
FVGs act as magnets — price tends to return to fill the imbalance. Can be used as:
- A target: does the prior 4PM target contain an unfilled FVG?
- A filter: is there a FVG blocking the path between entry and target?
- An entry zone: enter when price returns to fill a FVG in the trend direction

### Testability
Fully mechanical from price bars alone. Testable against Polygon 5-minute data.

### Next step
Define exact parameters: which timeframe (5m? 15m? 4H?), minimum gap size, maximum age before FVG is considered filled. Then test as a target filter on 4HR Re-Trigger entries.

---

## ICT — Order Block

### What it is
The last opposing candle before a strong impulsive move. Represents institutional order flow.

- **Bullish order block:** Last bearish (red) candle before a strong bullish impulse
- **Bearish order block:** Last bullish (green) candle before a strong bearish impulse

### Mechanical definition
- Identify a strong impulse move (define: minimum N points in M bars)
- Look back to the last candle that closed in the opposite direction before that impulse
- That candle's high/low defines the order block zone

### Why it matters
Price tends to return to order blocks and react. Can be used as entry zones or stop anchors.

### Testability
Requires defining "strong impulse" threshold. Testable from Polygon data once parameters are defined.

### Next step
Define impulse threshold before testing.

---

## ICT — Liquidity Sweep

### What it is
Price extends beyond a previous swing high or low, triggering stop orders, then immediately reverses. The sweep is the move that takes out liquidity before the real move begins.

### Relationship to existing strategies
The 4HR Re-Trigger is structurally a liquidity sweep pattern:
- 4AM bar sweeps below prior 4PM low (takes out buy stops)
- 8AM bar reverses back up (the real move)
- Entry on the break of 4AM high = entering after the sweep

### Mechanical definition
- Identify a previous swing high or low
- Price trades beyond it by at least N ticks
- Price then reverses and closes back within the prior range within M bars

### Next step
Define N (sweep distance) and M (reversal timeframe). Test whether formalizing the sweep improves or duplicates the existing 4HR setup detection.

---

## ICT — Kill Zones

### What it is
Fixed time windows where institutional order flow is highest:
- **Asian kill zone:** 8:00 PM – 12:00 AM ET (overnight session)
- **London kill zone:** 3:00 AM – 5:00 AM ET (European open)
- **New York kill zone:** 9:30 AM – 11:00 AM ET (US open)
- **New York PM kill zone:** 1:30 PM – 4:00 PM ET (afternoon session)

### Relationship to existing strategies
Kill zones are already partially embedded:
- 4HR Re-Trigger entry window (9:30–11:00 AM) = New York kill zone
- 60M 3-2-2 entry window (10:00–11:00 AM) = end of New York kill zone
- Session filters in VP bot (asian/london/new_york) map to kill zone windows

### Next step
No new test needed. Kill zones are already operationalized as session filters. Confirm session anchor times match exactly.

---

## 7HR Sweep

### What it is
Unknown. Referenced as a video link in the Beginners Trading Guide alongside the three AYCE strategies. No rules, candle sequence, entry, stop, or target definition exists in any source document.

### Hypothesis
Based on naming convention and the Strat framework context, likely a sweep of a key level at or around 7AM ET — possibly the overnight high/low or prior day's range — before a reversal into the NY session open.

### Next step
Source material required before any research can proceed. Options:
1. Review the original video
2. Describe the setup from memory or observation
3. Treat as unknown until source material is available

---

## Research Queue

In order of readiness:

1. **FVG as target filter on 4HR Re-Trigger** — most mechanical, most testable now
2. **Liquidity sweep formalization** — may overlap with 4HR Re-Trigger detection
3. **ICC clean detector (above 15m)** — after 4HR/Miyagi/3-2-2 detectors are built
4. **Order block entry zones** — needs impulse threshold definition first
5. **7HR Sweep** — blocked on source material
6. **Kill zones** — already operationalized, confirm anchor times only

---

## Hard Rules

- No concept moves from RESEARCH ONLY to WAIT until exact mechanical rules are written
- No concept moves from WAIT to detector until rules are reproducible by two independent implementations
- No backtest runs until detector reconciles against manual samples
- No execution until backtest passes honest fill, walk-forward, and slippage tests
